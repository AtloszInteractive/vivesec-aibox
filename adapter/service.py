"""ViVeSec v2 -> RAG adapter (the AIBox external face).

Front (what the ViVeSecBox / simulator calls, ViVeSec v2):
    GET  /api/v1/status
    POST /api/v1/status                                         (preferred)
    GET  /api/v1/init/prepare                                   (mTLS bootstrap)
    POST /api/v1/init/commit  {server_crt,ca_crt,client_crt,storage_key}
    GET  /api/v1/ui-version                                     text/plain
    GET  /api/v1/ui/init       + VVS-Drive/VVS-User/VVS-Session handshake
    POST /api/v1/index/get                    {path}
    POST /api/v1/index/get/children           {path}
    POST /api/v1/index/upsert/directory       {path}
    POST /api/v1/index/upsert/file/check      {path,size,mtime,head[,content_type]}
    POST /api/v1/index/upsert/file/content/{token}   (body = raw file bytes)
    POST /api/v1/index/drop/tree              {path,keep_exact}
    POST /api/v1/ui/query                     {query,top_k}  + VVS-Drive/VVS-User
    POST /api/v1/ui/ask                       {query,top_k}  -> {job_id}  (async)
    POST /api/v1/ui/poll                      {job_id,timeout}  (long-poll result)
    POST /api/v1/ui/save                      {name,text|content_b64[,format]}
    POST /api/v1/ui/feedback                  {audit_id,rating[,reason,comment]}
    GET  /api/v1/ui/files                     (session-stored generated files)
    GET  /api/v1/ui/files/download?name=...   (tunnel download fallback)
    POST /api/v1/ui/stt                       {audio_b64,content_type,lang} -> {text}
    POST /api/v1/ui/tts                       {text,lang} -> audio bytes
    POST /api/v1/storage/unlock              {storage_key}

The VVS-Drive header is urlsafe base64 of the UTF-8 drive path (corpus.py).
The status response carries ui-ready / fs-ready / features so the ViVeSecBox
knows when it may forward user queries (ui-ready) and start sync (fs-ready).

Agentic queries are multi-turn: the conversation for a (VVS-User, VVS-Drive)
pair is kept in memory and spilled to disk on inactivity (session.py). /ui/query
answers synchronously; /ui/ask + /ui/poll are the bounded long-poll channel for
slow answers (spec sec 2.4).

Storage is a LUKS2 volume (storage.py): /storage/unlock opens+mounts it, and a
presence watchdog locks it again if no authenticated status poll arrives within
ADAPTER_WATCHDOG_SECONDS. In the default 'off' storage mode there is no LUKS
layer and the presence-lock is disabled (dev/demo deployment stays unchanged).

Back (what this adapter calls, the RAG contract in drive_sync_api_spec.txt):
    POST /index/upsert/directory   /index/upsert/file/check
    POST /index/upsert/file/content/{token}
    POST /index/drop/tree          /rag/search_context
    GET  /health  /stats

Translation responsibilities:
  * derive corpus_id from the path / VVS-Drive header (corpus.py)
  * pin tenant_id to a constant (one physical box = one customer)
  * mirror doc metadata so /index/get* (diff-sync) work (meta.py)
  * map the RAG search contexts to the ViVeSec hit shape; corpus isolation IS
    the mandatory VVS-Drive hard filter (one drive = one corpus)

The RAG service is reached over the loopback/host network and is never exposed
externally; this adapter is the only thing in front of it.
"""
import json
import base64
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confidence  # noqa: E402
import corpus  # noqa: E402
import discovery  # noqa: E402
import docgen  # noqa: E402
import factory_reset  # noqa: E402
import feedback  # noqa: E402
import filestore  # noqa: E402
import jobstore  # noqa: E402
import llm  # noqa: E402
import provision  # noqa: E402
import scheduler  # noqa: E402
import scope  # noqa: E402
import session  # noqa: E402
import chat_policy
import storage  # noqa: E402
import voice  # noqa: E402
import wsfs  # noqa: E402
from meta import MetaMirror  # noqa: E402

HOST = os.environ.get("ADAPTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADAPTER_PORT", "8080"))
RAG_URL = os.environ.get("RAG_URL", "http://127.0.0.1:8090").rstrip("/")
RAG_API_KEY = os.environ.get("RAG_API_KEY", "")
TENANT_ID = os.environ.get("ADAPTER_TENANT_ID", "default")
META_PATH = os.environ.get("ADAPTER_META_PATH") or None
WATCHDOG_SECONDS = int(os.environ.get("ADAPTER_WATCHDOG_SECONDS", "90"))
MAX_CONTEXT_TOKENS = int(os.environ.get("ADAPTER_MAX_CONTEXT_TOKENS", "4000"))
# Whole-document analysis (#analyze) feeds the ENTIRE file to the model, so it
# needs both a wider context budget and a matching model context window —
# without the latter Ollama truncates the prompt and the tail of the document
# is analysed as if it did not exist.
ANALYZE_MAX_CONTEXT_TOKENS = int(
    os.environ.get("ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS", "60000"))
ANALYZE_NUM_CTX = int(os.environ.get("ADAPTER_ANALYZE_NUM_CTX", "65536") or 0)
# The RAG budgets context by a WORD-based estimate, which underestimates dense
# content badly: measured on this corpus, prose runs 4.6 chars/token but a
# firmware test log runs 1.75 (the estimate was 6.4x under, which is what
# silently overflowed the window and left no room to generate). Characters
# bound the real token count predictably, so the window is defended in chars,
# derived from the WORST measured ratio:
#   (num_ctx - num_predict - prompt overhead) * 1.75 ~= 109k chars at 65536.
ANALYZE_MAX_CHARS = int(os.environ.get("ADAPTER_ANALYZE_MAX_CHARS", "100000"))
# A structured analysis is never this short; anything shorter means generation
# was starved of context window.
ANALYZE_MIN_ANSWER_CHARS = int(
    os.environ.get("ADAPTER_ANALYZE_MIN_ANSWER_CHARS", "120"))
# Cap on `#search files:` hits. Browsing goes through /index/get/children, so
# this only bounds the flat filename lookup.
FILE_SEARCH_LIMIT = int(os.environ.get("ADAPTER_FILE_SEARCH_LIMIT", "2000"))
UI_VERSION = (os.environ.get("ADAPTER_UI_VERSION", "latest") or "latest").strip()


# Explicit content types for the RAG-supported formats where stdlib mimetypes is
# unreliable or missing (e.g. markdown). Extension -> MIME.
_EXT_CONTENT_TYPE = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".txt": "text/plain", ".text": "text/plain", ".log": "text/plain",
    ".rst": "text/x-rst", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".yaml": "application/x-yaml", ".yml": "application/x-yaml",
    ".ini": "text/plain", ".xml": "application/xml",
    ".pdf": "application/pdf", ".rtf": "application/rtf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html", ".htm": "text/html",
}


def _content_type_from_head(head_b64):
    """Best-effort magic-byte sniff for an extensionless file's head sample."""
    if not head_b64:
        return None
    try:
        head = base64.b64decode(head_b64)
    except Exception:  # noqa: BLE001
        return None
    if not head:
        return None
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head[:4] == b"PK\x03\x04":
        return "application/zip"  # docx/xlsx/pptx container; extension disambiguates
    if head.startswith(b"{\\rtf"):
        return "application/rtf"
    if b"\x00" in head:
        return "application/octet-stream"
    try:
        head.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def resolve_content_type(path, head_b64, supplied):
    """Determine a file's content type for the RAG /check call.

    Priority: the ViVeSecBox-supplied content_type (authoritative, if it sends
    one) > the file extension > a magic-byte sniff of the head sample >
    application/octet-stream. This lets the adapter fill the field in itself
    when the box does not provide it.
    """
    if supplied and str(supplied).strip():
        return str(supplied).strip()
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_CONTENT_TYPE:
        return _EXT_CONTENT_TYPE[ext]
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    return _content_type_from_head(head_b64) or "application/octet-stream"


def _parse_features(raw):
    """Feature flags = the agent types this box is licensed for (from the
    ViVeSecBox license). 'basic' is always implicitly available."""
    out = ["basic"]
    for item in (raw or "").replace(";", ",").split(","):
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


# Licensed agent types (comma-separated), e.g. "accounting,hr". 'basic' implicit.
FEATURES = _parse_features(os.environ.get("ADAPTER_FEATURES", ""))

# Encrypted-storage layer (LUKS2 or 'off'). fs-ready = unlocked & present.
STORAGE = storage.StorageManager.from_env()

# mTLS provisioning (init/prepare + init/commit; spec sec 1). The committed PKI
# material lives under ADAPTER_PKI_DIR; ADAPTER_TLS=on serves mutual TLS once
# initialized (init itself is always HTTP).
PKI_DIR = os.environ.get("ADAPTER_PKI_DIR", "/data/pki")
TLS_HOSTNAME = os.environ.get("ADAPTER_TLS_HOSTNAME") or provision.HOSTNAME
PROVISIONER = provision.Provisioner(PKI_DIR, hostname=TLS_HOSTNAME)
TLS_ENABLED = (os.environ.get("ADAPTER_TLS", "") or "").strip().lower() in ("1", "true", "yes", "on")
# The ViVeSecBox does init over plain HTTP and everything else over mTLS, so one
# process serves both ports; a second container would fight over the mirror file.
TLS_PORT = int(os.environ.get("ADAPTER_TLS_PORT", "443"))
_TLS_SERVER = [None]
_TLS_LOCK = threading.Lock()
# Presence-lock enforcement: only meaningful when there is real storage to
# protect. 'auto' -> enforce iff a LUKS volume is configured; 'on'/'off' override.
_WD_MODE = (os.environ.get("ADAPTER_WATCHDOG_ENABLED", "auto") or "auto").strip().lower()
WATCHDOG_ENFORCES = (_WD_MODE == "on") or (_WD_MODE == "auto" and STORAGE.mode != "off")
# Set once the watchdog has locked the box after losing the ViVeSecBox presence;
# cleared only by a successful /storage/unlock.
_PRESENCE_LOST = [False]
# Armed by the first authenticated status poll (and re-armed on unlock).
_WD_ARMED = [False]

CONTENT_PREFIX = "/api/v1/index/upsert/file/content/"

MIRROR = MetaMirror(persist_path=META_PATH)
_LAST_STATUS_TS = [time.time()]
# Maps a pending RAG upload token -> the path it belongs to, so the mirror can
# be finalized when the content is committed.
_TOKEN_PATHS = {}

# Read scope: which drives one request may search. Without the box header the
# scope is the active VVS-Drive, so the default behaviour is the single-drive
# one; the entitlements file exists so multi-drive can be exercised against a
# ViVeSecBox that does not send the header yet.
ENTITLEMENTS = scope.Entitlements.from_env()
SCOPE_ALL_DRIVES = (os.environ.get("ADAPTER_SCOPE_ALL_DRIVES", "").strip().lower()
                    in ("1", "on", "true", "yes"))


def _known_drive_roots():
    """Every drive root the mirror has seen. Demo-only scope source: it has no
    per-user filtering, so it must never be enabled on a real deployment."""
    prefix = corpus.norm(corpus.DRIVE_PREFIX)
    return [corpus.norm(entry.get("path") or "")
            for entry in MIRROR.get_children(prefix) if not entry.get("file")]


def _resolve_scope(headers, user, drive):
    return scope.resolve_request(
        drive, headers.get(scope.HEADER_OTHER_DRIVES), user,
        entitlements=ENTITLEMENTS,
        all_drives=_known_drive_roots if SCOPE_ALL_DRIVES else None,
        on_warning=lambda message: sys.stderr.write("[adapter] scope: %s\n" % message))

# Multi-turn conversation memory (spec sec 2.4 + ViVeSecBox team: keep the
# user's conversation, spill to disk on inactivity, reload on return).
SESSIONS = session.SessionManager.from_env()

# AIBox->ViVeSecBox file-save channel (aibox_more3 §1): the box connects to
# GET /api/v1/ws/fs while fs_ready; generated files are session-stored FIRST
# (FILES), then transferred over the channel with put-file (retry on
# 'temporary', keep + download fallback on 'permission'/no channel).
FILES = filestore.FileStore(
    (os.environ.get("ADAPTER_FILES_DIR", "/data/generated") or "").strip() or None)
WSFS = wsfs.FsChannelHub()

# Answer-rating capture (customer pilot testing): POST /api/v1/ui/feedback
# appends the rating joined with the server-side answer trace to JSONL under
# ADAPTER_FEEDBACK_DIR. Rated exchanges are the seed of a per-customer gold set.
FEEDBACK = feedback.FeedbackStore.from_env()
TRACES = feedback.TraceRegistry(
    cap=int(os.environ.get("ADAPTER_FEEDBACK_TRACES", "200") or 200))
PUTFILE_RETRIES = int(os.environ.get("ADAPTER_PUTFILE_RETRIES", "3") or 3)
PUTFILE_RETRY_DELAY = float(os.environ.get("ADAPTER_PUTFILE_RETRY_DELAY", "1.0") or 1.0)
PUTFILE_TIMEOUT = float(os.environ.get("ADAPTER_PUTFILE_TIMEOUT", "30") or 30)
GETFILE_TIMEOUT = float(os.environ.get("ADAPTER_GETFILE_TIMEOUT", "60") or 60)
FILES_DOWNLOAD_PATH = "/api/v1/ui/files/download"
DRIVE_FILE_PATH = "/api/v1/ui/file"

# Types the document viewer may render INLINE. Anything else is sent as an
# attachment: an inline .html or .svg would execute in the UI's own origin.
_INLINE_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "markdown": "text/plain; charset=utf-8",
    "csv": "text/plain; charset=utf-8",
    "tsv": "text/plain; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "json": "text/plain; charset=utf-8",
    "yaml": "text/plain; charset=utf-8",
    "yml": "text/plain; charset=utf-8",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Async UI query channel (spec sec 2.4: the answer takes time, so /ui/ask
# submits a job and /ui/poll long-polls for it). Long-poll waits are bounded so
# proxies/tunnels do not time out; finished-but-unpolled jobs are reaped.
LONGPOLL_SECONDS = float(os.environ.get("ADAPTER_LONGPOLL_SECONDS", "25") or 25)
LONGPOLL_MAX = float(os.environ.get("ADAPTER_LONGPOLL_MAX", "55") or 55)
JOBS = jobstore.JobStore.from_env()
JOB_QUEUE_MAX = int(os.environ.get("ADAPTER_JOB_QUEUE_MAX", "5") or 5)


def status_payload():
    """Build the ViVeSecBox readiness/status response.

    ui-ready : the box may forward user queries (adapter up + RAG reachable +
               storage unlocked).
    fs-ready : the box may start index sync (storage unlocked / mounted).
    features : licensed agent types ('basic' always present).
    """
    _LAST_STATUS_TS[0] = time.time()
    # An authenticated status poll re-establishes presence and arms the watchdog
    # (but a poll never clears a presence-lock once tripped -- only unlock does).
    if WATCHDOG_ENFORCES and not _PRESENCE_LOST[0]:
        _WD_ARMED[0] = True
    index = {}
    rag_ok = False
    try:
        index = rag_get("/stats").get("stats", {})
        rag_ok = True
    except Exception:  # noqa: BLE001
        index = {"error": "rag unavailable"}
    locked = STORAGE.is_locked() or _PRESENCE_LOST[0]
    fs_ready = not locked
    ui_ready = rag_ok and not locked
    # aibox_more3 §2: the canonical keys are snake_case (ui_ready/fs_ready)
    # plus storage_locked; the legacy dash keys are kept for one transition
    # release so older ViVeSecBox builds / the demo UI do not break.
    return {"ok": True,
            "ui_ready": ui_ready, "fs_ready": fs_ready,
            "storage_locked": locked,
            "ui-ready": ui_ready, "fs-ready": fs_ready,  # legacy (transition)
            "features": FEATURES, "locked": locked,
            "presence_lost": _PRESENCE_LOST[0],
            "watchdog_seconds": WATCHDOG_SECONDS,
            "storage": STORAGE.status(),
            "sessions": SESSIONS.stats(),
            "scope": {"header": scope.HEADER_OTHER_DRIVES,
                      "entitlements": ENTITLEMENTS is not None,
                      "all_drives": SCOPE_ALL_DRIVES},
            "ws_fs": {"connected": WSFS.connected()},
            "files": FILES.stats(),
            "voice": voice.status(),
            "chat_policy": chat_policy.settings(),
            "mirror": MIRROR.stats(), "index": index}


def _watchdog_loop():
    """Presence-based lock: if no authenticated status poll arrives within
    WATCHDOG_SECONDS, the ViVeSecBox is assumed gone -> lock the storage and
    stop serving agentic queries (spec sec 2.2)."""
    interval = max(2, min(15, (WATCHDOG_SECONDS // 3) or 2))
    while True:
        time.sleep(interval)
        if not WATCHDOG_ENFORCES or not _WD_ARMED[0] or _PRESENCE_LOST[0]:
            continue
        if time.time() - _LAST_STATUS_TS[0] > WATCHDOG_SECONDS:
            _PRESENCE_LOST[0] = True
            _WD_ARMED[0] = False
            try:
                STORAGE.lock()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("[adapter] watchdog lock failed: %s\n" % e)
            # Stop serving agentic queries AND evict in-memory conversations:
            # the ViVeSecBox is gone, so no cleartext-derived context should
            # linger in RAM (disk spill survives for a clean reload on unlock).
            try:
                SESSIONS.flush_memory()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("[adapter] watchdog session flush failed: %s\n" % e)
            sys.stderr.write("[adapter] presence lost (%ds without status) -> "
                             "storage locked\n" % WATCHDOG_SECONDS)


def factory_reset_action():
    """Wipe the box back to factory state (J6 physical reset pin, spec sec 1):
    de-provision the mTLS PKI, clear the metadata mirror, drop all conversation
    memory + disk spill, lock the storage, and clear the RAG index. After this
    the ViVeSecBox can re-run init/prepare -> commit from scratch."""
    sys.stderr.write("[adapter] FACTORY RESET triggered (physical pin)\n")
    try:
        n = PROVISIONER.reset()
        sys.stderr.write("[adapter] factory reset: removed %d PKI files\n" % n)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset PKI wipe failed: %s\n" % e)
    try:
        MIRROR.clear()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset mirror clear failed: %s\n" % e)
    try:
        SESSIONS.purge()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset session purge failed: %s\n" % e)
    try:
        JOBS.purge()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset job purge failed: %s\n" % e)
    try:
        n = FILES.purge()
        sys.stderr.write("[adapter] factory reset: removed %d generated files\n" % n)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset file purge failed: %s\n" % e)
    try:
        rag_post_json("/index/rebuild", {"clear": True})
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset RAG clear failed: %s\n" % e)
    try:
        STORAGE.lock()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("[adapter] factory reset storage lock failed: %s\n" % e)
    _PRESENCE_LOST[0] = True
    _WD_ARMED[0] = False



class RagError(Exception):
    def __init__(self, status, body):
        super().__init__("RAG %s" % status)
        self.status = status
        self.body = body


class BodyTooLarge(Exception):
    pass


# Real VVS-User / VVS-Drive values only ever arrive from the ViVeSecBox tunnel,
# so log each pair once — they are needed to address a user's drive on ws-fs.
_SEEN_IDENTITIES = set()


def _log_new_identity(user, drive, raw_drive):
    key = (user, drive)
    if key in _SEEN_IDENTITIES:
        return
    _SEEN_IDENTITIES.add(key)
    sys.stderr.write("[adapter] VVS identity: user=%r drive=%r (header=%s)\n"
                     % (user, drive, raw_drive))
    sys.stderr.flush()


# Upload bodies are read fully into memory, so cap them; the RAG's own check
# already refuses oversized files before the content call.
MAX_BODY_BYTES = int(os.environ.get("ADAPTER_MAX_BODY_BYTES", str(512 * 1024 * 1024)))
_CHUNK_LINE_MAX = 65536


# -- quick actions (function specification v2) --------------------------------
# The box UI sends an explicit {"action": "search", "mode": "files"|"text"}
# payload; a typed "#search ..." / "#summary ..." (and #report/#tracking/
# #presentation/#memo) query prefix selects the same action. For #search a
# leading "files:"/"file:" marker selects filename mode. The synthesis actions
# (summary/report/tracking/presentation/memo) all run the grounded path with a
# structured-output task instruction; presentation/memo additionally accept
# `audience`/`purpose` payload parameters (the box UI dialog answers).
_ACTIONS = ("search", "summary", "report", "tracking", "presentation", "memo",
            "analyze")
_ACTION_RE = re.compile(r"^[#/](\w+)\s*(.*)$", re.S)

# Bare "#action" (no query text): a retrieval seed aimed at the task's material.
# #analyze is absent on purpose: it never retrieves, and without a file it is
# rejected anyway.
_BARE_QUERY = {
    "summary": "meeting transcript decisions action items",
    "report": "weekly status update completed in-progress tasks blockers milestones",
    "tracking": "project milestones tasks status timeline owners deadlines",
    "presentation": "project overview data analysis results proposal next steps",
    "memo": "decision options analysis risks costs implications alternatives",
}
# Synthesis tasks need broader context coverage than a point question.
_DEFAULT_TOP_K = {"summary": 8, "report": 10, "tracking": 10,
                  "presentation": 10, "memo": 10}
# Task parameters accepted from the payload: the quick-action dialog answers
# ("AI Box Quick-action function upgrade"). They shape framing and depth only;
# the grounding rules always win.
_PARAM_KEYS = ("audience", "purpose", "coverage", "report_type", "aspect",
               "keywords", "outcome", "situation", "extra")

# An analysis that silently covered only part of the file would be worse than
# no analysis, so a truncated whole-document read says so, in the user's
# language (adapter/llm.py _REFUSALS uses the same language names).
_ANALYZE_TRUNCATED_NOTE = {
    "Hungarian": ("_Megjegyzés: a dokumentum túl hosszú volt egyetlen "
                  "elemzéshez — az első %d szakasz (a(z) összesen %d-ből) "
                  "került feldolgozásra._"),
    "English": ("_Note: the document was too long for a single analysis — the "
                "first %d of %d sections were processed._"),
    "Danish": ("_Bemærk: dokumentet var for langt til én analyse — de første "
               "%d af %d afsnit blev behandlet._"),
    "German": ("_Hinweis: Das Dokument war für eine einzelne Analyse zu lang — "
               "die ersten %d von %d Abschnitten wurden ausgewertet._"),
}


def _analyze_truncation_note(doc, lang):
    template = _ANALYZE_TRUNCATED_NOTE.get(lang or "", _ANALYZE_TRUNCATED_NOTE["English"])
    return template % (doc.get("chunks_used") or 0, doc.get("chunks_total") or 0)


# When the document does not fit the model at all, say so plainly instead of
# letting a one-word stub be rendered as a confident analysis.
_ANALYZE_TOO_LARGE = {
    "Hungarian": ("Ez a dokumentum túl nagy ahhoz, hogy egyetlen elemzésben "
                  "feldolgozható legyen (%d szakasz fért be a(z) %d-ből). "
                  "Kérdezz rá konkrét részletre, vagy válassz rövidebb "
                  "dokumentumot."),
    "English": ("This document is too large to analyse in a single pass "
                "(%d of %d sections fit). Ask about a specific part of it, or "
                "choose a shorter document."),
    "Danish": ("Dette dokument er for stort til at blive analyseret på én gang "
               "(%d af %d afsnit kunne medtages). Spørg om en bestemt del af "
               "det, eller vælg et kortere dokument."),
    "German": ("Dieses Dokument ist zu groß für eine einzelne Analyse "
               "(%d von %d Abschnitten haben gepasst). Fragen Sie nach einem "
               "bestimmten Teil oder wählen Sie ein kürzeres Dokument."),
}


def _analyze_too_large_message(doc, lang):
    template = _ANALYZE_TOO_LARGE.get(lang or "", _ANALYZE_TOO_LARGE["English"])
    return template % (doc.get("chunks_used") or 0, doc.get("chunks_total") or 0)


_GENERATION_INCOMPLETE = {
    "Hungarian": "A válasz előállítása nem fejeződött be. Próbáld újra.",
    "English": "The answer generation did not complete. Please try again.",
    "Danish": "Svaret blev ikke færdiggjort. Prøv igen.",
    "German": "Die Antworterstellung wurde nicht abgeschlossen. Bitte versuchen Sie es erneut.",
}


def _generation_incomplete_message(lang):
    return _GENERATION_INCOMPLETE.get(lang or "", _GENERATION_INCOMPLETE["English"])


def _fit_analyze_window(contexts, document):
    """Bound the whole-document context by characters so the prompt cannot eat
    the model's context window and starve generation. Reports the cut."""
    kept, used = [], 0
    for c in contexts:
        text = c.get("text") or ""
        if kept and used + len(text) > ANALYZE_MAX_CHARS:
            break
        used += len(text)
        kept.append(c)
    if len(kept) < len(contexts):
        document = dict(document)
        document["chunks_used"] = len(kept)
        document["truncated"] = True
        document["limited_by"] = "model_context_window"
    return kept, document


def parse_action(payload, query):
    """Return (action, mode, query) for one /ui request. Unknown actions are
    ignored (plain grounded ask); explicit payload fields win over the typed
    #prefix form. Modes only exist for the search action."""
    action = (payload.get("action") or "").strip().lower() or None
    mode = (payload.get("mode") or "").strip().lower() or None
    q = query
    if not action:
        m = _ACTION_RE.match(query or "")
        if m and m.group(1).lower() in _ACTIONS:
            action = m.group(1).lower()
            q = m.group(2).strip()
    if action not in _ACTIONS:
        return None, None, query
    if action == "search":
        low = q.lower()
        if mode not in ("files", "text"):
            mode = "files" if low.startswith(("files:", "file:")) else "text"
        if low.startswith(("files:", "file:")):
            q = q.split(":", 1)[1].strip()
    else:
        mode = None
    return action, mode, q


def _file_search(req_scope, pattern):
    """#search files: — filename lookup over the metadata mirror, across every
    drive in scope (spec F7 UX 2.A). No LLM involved; deterministic. The
    active drive is searched first, so it keeps its share of a capped result."""
    budget = FILE_SEARCH_LIMIT + 1
    matches = []
    for root in req_scope.drive_roots:
        if len(matches) >= budget:
            break
        matches.extend(MIRROR.find(root, pattern, files_only=True,
                                   limit=budget - len(matches)))
    truncated = len(matches) > FILE_SEARCH_LIMIT
    if truncated:
        matches = matches[:FILE_SEARCH_LIMIT]
    lines = ["%s" % m["path"] for m in matches]
    header = "Talált fájlok (%d%s):" % (len(matches), "+" if truncated else "")
    answer = header + ("\n- " + "\n- ".join(lines) if lines else " nincs találat")
    return {"ok": True, "drive": req_scope.active_root, "action": "search",
            "mode": "files",
            "pattern": pattern, "files": matches, "answer": answer,
            "truncated": truncated, "limit": FILE_SEARCH_LIMIT,
            "backend": "mirror", "citations": [], "hits": []}


def _answer(user, drive, query, top_k, lang, action=None, mode=None, files=None,
            params=None, agent=None, history_snapshot=None, cancel_event=None,
            progress_callback=None, req_scope=None, profile="grounded"):
    """Run the agentic query pipeline for one (user, drive) turn: retrieve
    context (corpus = the VVS-Drive hard filter), synthesize a grounded answer
    with the conversation history, and record the exchange. Shared by the sync
    /ui/query path and the async /ui/ask job. May raise RagError.

    Quick actions: 'search'/files is a mirror filename lookup (no LLM); the
    synthesis actions (summary/report/tracking/presentation/memo) reuse the
    grounded path with a structured-output task instruction, an optional
    `files` source-filter (the box UI file picker) and optional `params`
    (audience/purpose dialog answers for presentation/memo). 'analyze' is the
    one action that bypasses retrieval: it reads the named file WHOLE, so the
    analysis cannot miss a section just because the question did not name it.
    Every generated answer carries a confidence score (spec: mandatory), and a
    detected hallucination (ungrounded number) suppresses the answer."""
    if req_scope is None:
        req_scope = scope.resolve(drive)
    if action is not None:
        profile = "grounded"
    session_scope = chat_policy.history_scope(req_scope.session_scope, profile)
    if action == "search" and mode == "files":
        return {**_file_search(req_scope, query), "profile": "grounded"}
    # The UI never sends `lang`: detect it from the question so the guard, the
    # refusal and the audit footer speak the user's language.
    if not lang:
        lang = llm.detect_lang(query) or None
    corpus_id = req_scope.corpus_id
    history = (SESSIONS.history(user, session_scope)
               if history_snapshot is None
               else [dict(turn) for turn in history_snapshot])
    document = None
    # Conversational mode (plain asks only — quick actions are standalone
    # commands): a follow-up is rewritten into a self-contained retrieval
    # question, and a turn about the conversation itself skips retrieval and
    # is answered from the history instead of being refused for no-context.
    chat_only = False
    retrieval_query = query
    if action is None and history:
        retrieval_query, chat_only = llm.condense(query, history, lang)
    source_paths = [str(source) for source in (files or []) if source]
    if not source_paths and action is None:
        source_paths = llm.source_files(query)
    evidence_ids = []
    if action is None and not source_paths:
        from session import followup_evidence
        evidence_ids = followup_evidence(query, history, req_scope.corpus_ids)
    if source_paths or evidence_ids:
        chat_only = False
    if action == "analyze":
        # The user pointed at ONE file and asked what is in it. Similarity
        # retrieval would only return the parts that match the question, so the
        # whole document is fetched instead — that is the point of the action.
        target = next((str(f) for f in (files or []) if f), "")
        if not target:
            raise RagError(400, {"ok": False,
                                 "error": "analyze requires a source file"})
        try:
            target_corpus = req_scope.corpus_id_for_path(target)
        except ValueError:
            # A bare filename (no drive prefix) is resolved by name inside the
            # first corpus in scope; a path outside the scope is never reached.
            target_corpus = req_scope.corpus_ids[0]
        res = rag_post_json("/rag/document_context",
                            {"corpus_id": target_corpus, "tenant_id": TENANT_ID,
                             "source_path": corpus.norm(target),
                             "max_context_tokens": ANALYZE_MAX_CONTEXT_TOKENS})
        contexts = res.get("contexts", [])
        document = res.get("document") or {}
        contexts, document = _fit_analyze_window(contexts, document)
    elif chat_only:
        contexts = []
    else:
        res = rag_post_json("/rag/search_context",
                            {"corpus_id": corpus_id, "tenant_id": TENANT_ID,
                             "corpus_ids": list(req_scope.corpus_ids),
                             "question": retrieval_query, "top_k": top_k,
                             "max_context_tokens": MAX_CONTEXT_TOKENS,
                             "source_paths": source_paths,
                             "evidence_chunk_ids": evidence_ids,
                             "include_debug": True})
        contexts = res.get("contexts", [])
    hits = []
    for i, c in enumerate(contexts, 1):
        snippet = " ".join((c.get("text") or "")[:200].split())
        hits.append({"rank": i, "path": c.get("source_path"),
                     "chunk_id": c.get("chunk_id"),
                     "page_number": c.get("page_number"),
                     "score": c.get("score"), "snippet": snippet})
    answer, backend = llm.generate(query, contexts, lang=lang, history=history,
                                   task=action, params=params,
                                   num_ctx=ANALYZE_NUM_CTX if action == "analyze" else None,
                                   chat_only=chat_only, agent=agent,
                                   cancel_event=cancel_event,
                                   progress_callback=progress_callback,
                                   **({"profile": profile} if profile == "hybrid" else {}))
    degenerate = (action == "analyze" and contexts and answer is not None
                  and len(answer.strip()) < ANALYZE_MIN_ANSWER_CHARS)
    if degenerate:
        sys.stderr.write("[adapter] analyze: generation starved (%d chars, %s/%s chunks) %r\n"
                         % (len(answer.strip()), document.get("chunks_used"),
                            document.get("chunks_total"), answer[:200]))
        answer = _analyze_too_large_message(document, lang)
        backend = (backend or "") + " (did not fit the context window)"
    conf = confidence.score(contexts, answer, question=query, history=history,
                            conversational=chat_only) if profile == "grounded" else None
    if answer and conf and conf["suppress"]:
        # Spec: detected hallucination (ungrounded figure) -> the defensive
        # standard response replaces the fabricated text.
        bad = (conf["components"].get("context_adherence") or {}).get("ungrounded")
        sys.stderr.write("[adapter] suppress (action=%s): ungrounded=%s answer=%r\n"
                         % (action, bad, (answer or "")[:400]))
        answer = confidence.standard_refusal(lang)
        backend = (backend or "") + " (suppressed: ungrounded numbers)"
    if not (answer or "").strip():
        # Generation produced nothing (model off, unreachable, or the reasoning
        # budget ran out). The score only measures retrieval, so leaving it as
        # is would render an empty card wearing a green badge.
        answer = _generation_incomplete_message(lang)
        if conf is not None:
            conf["score"] = 0
            conf["band"] = "red"
    _, citations = llm.build_context(contexts)
    if profile == "hybrid" and answer:
        valid_refs = {str(citation["ref"]) for citation in citations}
        answer = re.sub(r"\[#(\d+)\]", lambda match: match.group(0)
                        if match.group(1) in valid_refs else "", answer)
        used_refs = set(re.findall(r"\[#(\d+)\]", answer))
        citations = [citation for citation in citations if str(citation["ref"]) in used_refs]
    # Only completed exchanges go into the conversation memory (an empty answer
    # from a disabled/unreachable model is not a turn worth remembering). The
    # history stays footer-free so audit blocks never leak into later prompts.
    if answer:
        cited = set(re.findall(r"\[#(\d+)\]", answer))
        sources = [{"chunk_id": context["chunk_id"], "corpus_id": context.get("corpus_id"),
                    "source_path": context.get("source_path")}
                   for position, context in enumerate(contexts, 1)
                   if str(position) in cited and context.get("chunk_id")]
        SESSIONS.record(user, session_scope, query, answer, sources=sources)
    # Spec (C6): mandatory "Adatkontroll & Audit Info" footer + audit id on
    # every displayed output; band message (+ degraded metrics on amber).
    aid = confidence.audit_id(user, drive, query, answer or "")
    display = answer
    if answer and not degenerate and document and document.get("truncated"):
        display = answer + "\n\n" + _analyze_truncation_note(document, lang)
    if display:
        display = display + "\n\n" + (confidence.footer(conf, citations, aid, lang)
                if conf is not None else
                "---\n**Adatkontroll & Audit Info**\n* Profile: hybrid\n* Audit ID: " + aid)
    result = {"ok": True, "drive": corpus.norm(drive), "user": user,
              "corpus_id": corpus_id, "answer": display, "backend": backend,
              "action": action, "profile": profile, "audit_id": aid,
              "agent": (agent or "").strip().lower() or llm.DEFAULT_AGENT,
              "confidence": {"score": conf["score"], "band": conf["band"],
                             "message": confidence.band_message(conf["band"], lang),
                             "degraded": conf.get("degraded") or [],
                             "audit_id": aid,
                             "components": conf["components"]} if conf is not None else None,
              "citations": citations, "hits": hits}
    if action != "analyze" and not chat_only:
        result["retrieval_debug"] = res.get("debug", {})
        result["source_paths"] = source_paths
        result["evidence_chunk_ids"] = evidence_ids
    if params:
        result["params"] = params
    if retrieval_query != query:
        # The rewritten standalone question that actually hit the retrieval —
        # visible so a bad rewrite is diagnosable from the response alone.
        result["retrieval_query"] = retrieval_query
    if document is not None:
        result["document"] = document
    # Keep the trace so a later /ui/feedback rating can be stored WITH what the
    # box actually retrieved and answered (audit id = the join key).
    TRACES.put(aid, {
        "ts": time.time(), "user": user, "drive": corpus.norm(drive),
        "corpus_id": corpus_id, "scope": req_scope.as_dict(), "action": action,
        "agent": result["agent"], "lang": lang, "profile": profile,
        "question": query,
        "retrieval_query": retrieval_query if retrieval_query != query else None,
        "answer": answer, "backend": backend,
        "confidence": result["confidence"],
        "citations": citations, "hits": hits,
    })
    return result


def _job_worker(job_id, user, drive, query, top_k, lang, action=None, mode=None,
                files=None, params=None, agent=None, history_snapshot=None,
                cancel_event=None):
    if JOBS.start(user, drive, job_id) is None:
        return
    job = JOBS.get(user, drive, job_id) or {}
    # The request headers are long gone by the time a queued job runs, so the
    # scope travels with the job record instead of being re-derived.
    req_scope = scope.rebuild(drive, (job.get("request") or {}).get("scope_drives"))
    progress = lambda chars, tokens: JOBS.progress(user, drive, job_id, chars, tokens)
    try:
        code, payload = 200, _answer(user, drive, query, top_k, lang, action,
                                     mode, files, params, agent, history_snapshot,
                                     cancel_event, progress, req_scope,
                                     (job.get("request") or {}).get("profile", "grounded"))
    except llm.GenerationCancelled as e:
        code, payload = 499, {"ok": False, "error": str(e)}
    except RagError as e:
        code, payload = e.status, e.body
    except Exception as e:  # noqa: BLE001
        code, payload = 500, {"ok": False, "error": str(e)}
    JOBS.finish(user, drive, job_id, code, payload)


HEAVY_SCHEDULER = scheduler.HeavyScheduler(
    _job_worker, max_queued_per_user=JOB_QUEUE_MAX)


def _maintenance_loop():
    """Periodic housekeeping for conversations and retained query jobs."""
    while True:
        time.sleep(30)
        try:
            SESSIONS.sweep()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("[adapter] session sweep failed: %s\n" % e)
        try:
            JOBS.sweep()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("[adapter] job sweep failed: %s\n" % e)


def _rag_headers(extra=None):
    h = {}
    if RAG_API_KEY:
        h["X-API-Key"] = RAG_API_KEY
    if extra:
        h.update(extra)
    return h


def rag_post_json(path, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(RAG_URL + path, data=data, method="POST",
                                 headers=_rag_headers({"Content-Type": "application/json"}))
    return _rag_call(req)


def rag_post_raw(path, raw):
    req = urllib.request.Request(RAG_URL + path, data=raw, method="POST",
                                 headers=_rag_headers({"Content-Type": "application/octet-stream"}))
    return _rag_call(req)


def rag_get(path):
    req = urllib.request.Request(RAG_URL + path, method="GET", headers=_rag_headers())
    return _rag_call(req)


def _rag_call(req):
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"ok": False, "error": "rag http %s" % e.code}
        raise RagError(e.code, body)


class Handler(BaseHTTPRequestHandler):
    server_version = "ViVeSecAIBox/1.0"

    # -- io helpers ----------------------------------------------------------
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = (text or "").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, content_type, data, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        """Read a request body sent either with Content-Length or chunked.

        The ViVeSecBox (aiohttp) streams file uploads with
        `Transfer-Encoding: chunked`, so a Content-Length-only reader silently
        yields an empty body and the upload fails downstream.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding", "") or "").lower():
            parts = []
            total = 0
            while True:
                line = self.rfile.readline(_CHUNK_LINE_MAX)
                if not line:
                    break
                size_field = line.split(b";", 1)[0].strip()
                try:
                    n = int(size_field, 16)
                except ValueError:
                    break
                if n == 0:
                    while True:  # trailer section, ends on the blank line
                        trailer = self.rfile.readline(_CHUNK_LINE_MAX)
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                total += n
                if total > MAX_BODY_BYTES:
                    raise BodyTooLarge()
                parts.append(self.rfile.read(n))
                self.rfile.read(2)  # trailing CRLF of the chunk
            return b"".join(parts)
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY_BYTES:
            raise BodyTooLarge()
        return self.rfile.read(length) if length else b""

    def _read_json(self):
        raw = self._read_body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _read_raw(self):
        return self._read_body()

    def _fail_rag(self, err):
        # Surface the RAG status/body verbatim so contract error codes
        # (400/401/409/503) propagate to the ViVeSecBox unchanged.
        self._send(err.status, err.body)

    def _locked_guard(self):
        # Block index writes / queries while the storage is locked (either not
        # yet unlocked, or locked by the presence watchdog). 'off' mode never
        # trips this. Returns True (and sends 503) if the request must stop.
        if STORAGE.is_locked() or _PRESENCE_LOST[0]:
            self._send(503, {"ok": False, "error": "storage locked"})
            return True
        return False

    def log_message(self, fmt, *args):
        sys.stderr.write("[adapter] %s\n" % (fmt % args))

    # -- GET -----------------------------------------------------------------
    def do_GET(self):
        path, _, query = self.path.partition("?")
        path = path.rstrip("/")
        if path == "/api/v1/status":
            self._send(200, status_payload())
            return
        if path == "/api/v1/init/prepare":
            self._init_prepare()
            return
        if path == "/api/v1/ui-version":
            self._ui_version()
            return
        if path == "/api/v1/ui/init":
            self._ui_init()
            return
        if path == "/api/v1/ws/fs":
            self._ws_fs()
            return
        if path == "/api/v1/ui/files":
            self._files_list()
            return
        if path == "/api/v1/ui/scope":
            self._scope_info()
            return
        if path == "/api/v1/ui/jobs":
            self._jobs_list()
            return
        if path == FILES_DOWNLOAD_PATH:
            self._files_download(query)
            return
        if path == DRIVE_FILE_PATH:
            self._drive_file(self._drive_file_path_from_query(query))
            return
        if self.path in ("/", ""):
            self._send(200, {"ok": True, "service": "ViVeSec AIBox adapter"})
            return
        self._send(404, {"ok": False, "error": "Not found: %s" % self.path})

    # -- POST ----------------------------------------------------------------
    def do_POST(self):
        # A query string on a POST is legitimate, so it must not take part in
        # route matching -- otherwise POST /ui/file?path=... would 404.
        path, _, self._post_query = self.path.partition("?")
        try:
            if path.startswith(CONTENT_PREFIX):
                return self._content(path[len(CONTENT_PREFIX):])
            route = {
                "/api/v1/status": self._status,
                "/api/v1/init/commit": self._init_commit,
                "/api/v1/index/get": self._get_doc,
                "/api/v1/index/get/children": self._get_children,
                "/api/v1/index/upsert/directory": self._upsert_dir,
                "/api/v1/index/upsert/file/check": self._check,
                "/api/v1/index/drop/tree": self._drop_tree,
                "/api/v1/ui/query": self._query,
                "/api/v1/ui/ask": self._ask,
                "/api/v1/ui/poll": self._poll,
                "/api/v1/ui/jobs/get": self._jobs_get,
                "/api/v1/ui/jobs/seen": self._jobs_seen,
                "/api/v1/ui/jobs/cancel": self._jobs_cancel,
                "/api/v1/ui/save": self._save,
                "/api/v1/ui/file": self._drive_file_post,
                "/api/v1/ui/feedback": self._feedback,
                "/api/v1/ui/stt": self._stt,
                "/api/v1/ui/tts": self._tts,
                "/api/v1/storage/unlock": self._unlock,
            }.get(path.rstrip("/"))
            if route is None:
                self._send(404, {"ok": False, "error": "Not found: %s" % path})
                return
            route()
        except RagError as e:
            self._fail_rag(e)
        except BodyTooLarge:
            self._send(413, {"ok": False, "error": "request body too large"})
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})

    # -- diff-sync reads (served from the mirror) ----------------------------
    def _status(self):
        self._read_json()  # tolerate (and ignore) an optional request body
        self._send(200, status_payload())

    def _ui_version(self):
        self._send_text(200, UI_VERSION)

    def _ui_init(self):
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        # The embedded UI cannot learn its drive any other way: the box injects
        # VVS-Drive as a header and the browser never sees it.
        self._send(200, {"ok": True, "accepted": True,
                         "drive": drive, "user": user,
                         "session": self.headers.get("VVS-Session", "")})

    # -- mTLS provisioning (HTTP-only bootstrap; spec sec 1) -----------------
    def _init_prepare(self):
        try:
            csr = PROVISIONER.prepare()
        except provision.ProvisionError as e:
            # Spec: already-init is exactly 400 {"status":"already initialized"} —
            # no extra keys, the ViVeSecBox parses this body verbatim.
            if "already initialized" in str(e):
                self._send(400, {"status": "already initialized"})
            else:
                self._send(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})
            return
        self._send(200, {"server_req": csr})

    def _init_commit(self):
        payload = self._read_json()
        try:
            # client_crt is optional per aibox_more3 §2 (TLS-layer check).
            PROVISIONER.commit(payload.get("server_crt", ""),
                               payload.get("ca_crt", ""),
                               payload.get("client_crt") or None)
        except provision.ProvisionError as e:
            if "already initialized" in str(e):
                self._send(400, {"status": "already initialized"})
            else:
                self._send(400, {"ok": False, "error": str(e)})
            return
        # Provisioning verified: initialize / unlock the encrypted storage with
        # the supplied key (spec couples mTLS init with the secure storage).
        storage_key = payload.get("storage_key") or ""
        try:
            STORAGE.unlock(storage_key)
            _PRESENCE_LOST[0] = False
            _LAST_STATUS_TS[0] = time.time()
            if WATCHDOG_ENFORCES:
                _WD_ARMED[0] = True
        except storage.StorageError as e:
            # Init succeeded but the volume could not be opened; the box stays
            # initialized but locked until a later /storage/unlock.
            if TLS_ENABLED:
                _ensure_tls_listener()
            self._send(200, {"status": "ok", "ok": True, "initialized": True,
                             "storage_error": str(e),
                             "locked": STORAGE.is_locked()})
            return
        # The ViVeSecBox accepts the commit only on a string "status" == "ok".
        if TLS_ENABLED:
            _ensure_tls_listener()
        self._send(200, {"status": "ok", "ok": True, "initialized": True,
                         "locked": STORAGE.is_locked()})

    def _get_doc(self):
        payload = self._read_json()
        # Spec: the body IS the metadata object (or null) — not an envelope.
        self._send(200, MIRROR.get(payload.get("path", "")))

    def _get_children(self):
        payload = self._read_json()
        self._send(200, MIRROR.get_children(payload.get("path", "")))

    # -- sync writes (translated to the RAG contract) ------------------------
    def _upsert_dir(self):
        if self._locked_guard():
            return
        payload = self._read_json()
        path = payload.get("path", "")
        corpus_id = corpus.corpus_id_of_path(path)
        rag_post_json("/index/upsert/directory",
                      {"corpus_id": corpus_id, "tenant_id": TENANT_ID,
                       "path": path, "metadata": {"file": False}})
        MIRROR.upsert(path, file=False, mtime=None, size=None)
        self._send(200, {"ok": True})

    def _check(self):
        if self._locked_guard():
            return
        payload = self._read_json()
        path = payload.get("path", "")
        size = payload.get("size")
        mtime = payload.get("mtime")
        corpus_id = corpus.corpus_id_of_path(path)
        content_type = resolve_content_type(path, payload.get("head"), payload.get("content_type"))
        res = rag_post_json("/index/upsert/file/check",
                            {"corpus_id": corpus_id, "tenant_id": TENANT_ID,
                             "path": path, "size": size, "mtime": mtime,
                             "content_type": content_type,
                             "head": payload.get("head")})
        # Per ViVeSec v2: record the file metadata regardless of the token.
        MIRROR.upsert(path, file=True, mtime=mtime, size=size)
        token = res.get("token")
        if token:
            _TOKEN_PATHS[token] = path
        self._send(200, {"ok": True, "token": token, "reason": res.get("reason")})

    def _content(self, token):
        if self._locked_guard():
            return
        raw = self._read_raw()
        res = rag_post_raw("/index/upsert/file/content/" + token, raw)
        _TOKEN_PATHS.pop(token, None)
        self._send(200, {"ok": True, "chunks": res.get("chunks"),
                         "doc_id": res.get("doc_id"), "pages": res.get("pages")})

    def _drop_tree(self):
        # aibox_more3 §2 retry-consistency: the vector store is dropped FIRST,
        # the metadata mirror second; both operations are idempotent, so if a
        # timeout hits between the two, a retried call converges (bottom-up).
        if self._locked_guard():
            return
        payload = self._read_json()
        path = payload.get("path", "")
        keep_exact = bool(payload.get("keep_exact", False))
        corpus_id = corpus.corpus_id_of_path(path)
        res = rag_post_json("/index/drop/tree",
                            {"corpus_id": corpus_id, "tenant_id": TENANT_ID,
                             "path": path, "keep_exact": keep_exact})
        MIRROR.drop_tree(path, keep_exact)
        self._send(200, {"ok": True, "removed": res.get("deleted_documents", 0),
                         "deleted_pages": res.get("deleted_pages", 0),
                         "deleted_chunks": res.get("deleted_chunks", 0)})

    # -- agentic query (VVS-Drive hard filter = corpus isolation) ------------
    def _read_query(self, payload):
        """Validate the VVS-Drive header + query body shared by the sync and
        async UI paths. Returns (user, drive, query, top_k, lang, action, mode,
        files, params, agent) or None (after having sent the appropriate
        400)."""
        raw_drive = self.headers.get("VVS-Drive")
        if not raw_drive:
            self._send(400, {"ok": False, "error": "Missing VVS-Drive header"})
            return None
        try:
            drive = corpus.decode_vvs_drive(raw_drive)
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return None
        query = (payload.get("query") or payload.get("question") or "").strip()
        if not query:
            self._send(400, {"ok": False, "error": "Missing 'query'"})
            return None
        user = self.headers.get("VVS-User", "")
        _log_new_identity(user, drive, raw_drive)
        action, mode, query = parse_action(payload, query)
        if action in _BARE_QUERY and not query:
            # bare "#action": still retrievable — aim at the task's material.
            query = _BARE_QUERY[action]
        top_k = int(payload.get("top_k") or _DEFAULT_TOP_K.get(action, 4))
        files = payload.get("files")
        files = [str(f) for f in files if f] if isinstance(files, list) else []
        params = {k: str(payload.get(k)).strip() for k in _PARAM_KEYS
                  if payload.get(k)}
        agent = (str(payload.get("agent") or "")).strip().lower() or None
        return (user, drive, query, top_k, payload.get("lang"), action, mode,
                files, params, agent)

    def _scope_for(self, user, drive, payload):
        """Resolved scope, narrowed by the body's optional `drives` list.
        Returns None after sending 403 when the client named a drive it was
        not entitled to -- the selection may only ever narrow."""
        resolved = _resolve_scope(self.headers, user, drive)
        requested = payload.get("drives")
        if not isinstance(requested, list) or not requested:
            return resolved
        try:
            return resolved.narrow([str(d) for d in requested if d])
        except ValueError as e:
            self._send(403, {"ok": False, "error": str(e)})
            return None

    def _profile_for(self, payload, action):
        try:
            return chat_policy.resolve(payload.get("profile"), action)
        except PermissionError as error:
            self._send(403, {"ok": False, "error": str(error)})
        except ValueError as error:
            self._send(400, {"ok": False, "error": str(error)})
        return None

    def _query(self):
        if self._locked_guard():
            return
        payload = self._read_json()
        parsed = self._read_query(payload)
        if parsed is None:
            return
        req_scope = self._scope_for(parsed[0], parsed[1], payload)
        if req_scope is None:
            return
        profile = self._profile_for(payload, parsed[5])
        if profile is None:
            return
        self._send(200, _answer(*parsed, req_scope=req_scope, profile=profile))

    # -- async UI channel: submit a job, then long-poll for it (spec sec 2.4) -
    def _ask(self):
        if self._locked_guard():
            return
        payload = self._read_json()
        parsed = self._read_query(payload)
        if parsed is None:
            return
        job_id = uuid.uuid4().hex
        user, drive = parsed[0], parsed[1]
        req_scope = self._scope_for(user, drive, payload)
        if req_scope is None:
            return
        profile = self._profile_for(payload, parsed[5])
        if profile is None:
            return
        history_snapshot = SESSIONS.history(user, chat_policy.history_scope(req_scope.session_scope, profile))
        # Where the request was STARTED decides where its result belongs: a chat
        # question answers in the conversation, a quick action in the job list.
        origin = (str(payload.get("origin") or "chat")).strip().lower()
        if origin not in ("chat", "background"):
            origin = "chat"
        request = {"query": parsed[2], "top_k": parsed[3], "lang": parsed[4],
                   "action": parsed[5], "mode": parsed[6], "files": parsed[7],
                   "params": parsed[8], "agent": parsed[9],
                   "scope_drives": list(req_scope.drive_roots),
                   "origin": origin, "history": history_snapshot, "profile": profile}
        JOBS.create(job_id, user, drive, request)
        action, mode = parsed[5], parsed[6]
        heavy = action is not None and not (action == "search" and mode == "files")
        queue_position = None
        if heavy:
            try:
                queue_position = HEAVY_SCHEDULER.submit(
                    job_id, user, drive, (job_id,) + parsed + (history_snapshot,))
            except scheduler.QueueFull as e:
                JOBS.cancel_queued(user, drive, job_id)
                self._send(429, {"ok": False, "error": str(e)})
                return
        else:
            threading.Thread(target=_job_worker,
                             args=(job_id,) + parsed + (history_snapshot,),
                             daemon=True).start()
        self._send(202, {"ok": True, "job_id": job_id, "status": "pending",
                         "job_status": "queued",
                         "queue_position": queue_position})

    def _poll(self):
        payload = self._read_json()
        job_id = payload.get("job_id") or ""
        identity = self._read_vvs()
        if identity is None:
            return
        user, drive = identity
        job = JOBS.get(user, drive, job_id)
        if job is None:
            self._send(404, {"ok": False, "status": "unknown",
                             "error": "unknown job_id"})
            return
        timeout = payload.get("timeout")
        timeout = LONGPOLL_SECONDS if timeout is None else float(timeout)
        timeout = max(0.0, min(timeout, LONGPOLL_MAX))
        job = JOBS.wait(user, drive, job_id, timeout)
        if job.get("status") not in ("done", "error", "cancelled", "interrupted"):
            self._send(200, {"ok": True, "status": "pending", "job_id": job_id,
                             "job_status": job.get("status"),
                             "progress_chars": job.get("progress_chars", 0),
                             "progress_tokens": job.get("progress_tokens", 0),
                             "queue_position": HEAVY_SCHEDULER.position(job_id, user, drive)})
            return
        code = job.get("code") or (200 if job.get("status") in
                       ("done", "cancelled", "interrupted") else 500)
        result = job.get("result") or {"ok": False, "error": job.get("error")}
        if isinstance(result, dict):
            result = dict(result)
            result["status"] = job.get("status")
            result["job_id"] = job_id
        self._send(code, result)

    def _jobs_list(self):
        identity = self._read_vvs()
        if identity is None:
            return
        user, drive = identity
        jobs = []
        for job in JOBS.list(user, drive):
            request = job.get("request") or {}
            if request.get("origin") != "background":
                continue
            jobs.append({"job_id": job.get("job_id"), "status": job.get("status"),
                         "action": request.get("action"), "query": request.get("query"),
                         "created": job.get("created"), "started": job.get("started"),
                         "finished": job.get("finished"), "seen_ts": job.get("seen_ts"),
                         "error": job.get("error"),
                         "progress_chars": job.get("progress_chars", 0),
                         "progress_tokens": job.get("progress_tokens", 0),
                         "cancel_requested": bool(job.get("cancel_requested")),
                         "queue_position": HEAVY_SCHEDULER.position(
                             job.get("job_id"), user, drive)})
        self._send(200, {"ok": True, "jobs": jobs})

    def _jobs_get(self):
        payload = self._read_json()
        identity = self._read_vvs()
        if identity is None:
            return
        user, drive = identity
        job = JOBS.get(user, drive, payload.get("job_id") or "")
        if job is None:
            self._send(404, {"ok": False, "error": "unknown job_id"})
            return
        self._send(200, {"ok": True, "job": job})

    def _jobs_seen(self):
        payload = self._read_json()
        identity = self._read_vvs()
        if identity is None:
            return
        user, drive = identity
        job = JOBS.mark_seen(user, drive, payload.get("job_id") or "")
        if job is None:
            self._send(404, {"ok": False, "error": "unknown job_id"})
            return
        self._send(200, {"ok": True, "job_id": job.get("job_id"),
                         "seen_ts": job.get("seen_ts")})

    def _jobs_cancel(self):
        payload = self._read_json()
        identity = self._read_vvs()
        if identity is None:
            return
        user, drive = identity
        job_id = payload.get("job_id") or ""
        job = JOBS.get(user, drive, job_id)
        if job is None:
            self._send(404, {"ok": False, "error": "unknown job_id"})
            return
        state = HEAVY_SCHEDULER.cancel(job_id, user, drive)
        if state == "queued":
            job = JOBS.cancel_queued(user, drive, job_id)
            self._send(200, {"ok": True, "job_id": job_id,
                             "status": job.get("status")})
            return
        if state == "running" or job.get("status") == "running":
            JOBS.request_cancel(user, drive, job_id)
            self._send(202, {"ok": True, "job_id": job_id, "status": "running",
                             "cancel_requested": True})
            return
        self._send(409, {"ok": False, "job_id": job_id,
                         "status": job.get("status"),
                         "error": "only queued jobs can be cancelled"})

    # -- ws-fs: AIBox -> ViVeSecBox file-save channel (aibox_more3 sec 1) -----
    def _read_vvs(self):
        """Decode the VVS-Drive header (+ VVS-User). Returns (user, drive) or
        None after sending the 400."""
        raw_drive = self.headers.get("VVS-Drive")
        if not raw_drive:
            self._send(400, {"ok": False, "error": "Missing VVS-Drive header"})
            return None
        try:
            drive = corpus.decode_vvs_drive(raw_drive)
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return None
        user = self.headers.get("VVS-User", "")
        _log_new_identity(user, drive, raw_drive)
        return user, drive

    def _ws_fs(self):
        """RFC6455 upgrade; the connection thread then serves the channel until
        the box drops it. A newer connection replaces the current one."""
        key = self.headers.get("Sec-WebSocket-Key")
        upgrade = (self.headers.get("Upgrade") or "").lower()
        if upgrade != "websocket" or not key:
            self._send(400, {"ok": False, "error": "websocket upgrade required"})
            return
        resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % wsfs.accept_key(key))
        self.wfile.write(resp.encode("ascii"))
        self.wfile.flush()
        self.close_connection = True
        channel = wsfs.FsChannel(self.rfile, self.wfile, on_close=WSFS.detach)
        WSFS.attach(channel)
        sys.stderr.write("[adapter] ws-fs channel connected (%s)\n"
                         % (self.client_address[0],))
        channel.serve()  # blocks on this connection thread
        sys.stderr.write("[adapter] ws-fs channel closed\n")

    def _save(self):
        """Save a generated file: render to the requested format, session store
        FIRST, then transfer over the ws-fs channel. permission / no channel ->
        the copy stays stored and is offered for download over the tunnel;
        'temporary' was already retried by the channel.

        The target is ALWAYS the active VVS-Drive, even when the read scope
        spans several drives: a generated file needs one unambiguous owner."""
        if self._locked_guard():
            return
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        payload = self._read_json()
        name = (payload.get("name") or "").strip()
        if not name:
            self._send(400, {"ok": False, "error": "Missing 'name'"})
            return
        requested = payload.get("format")
        if requested and docgen.normalize_format(requested) is None:
            self._send(400, {"ok": False, "error": "unsupported format: %s"
                                                   % requested})
            return
        if payload.get("content_b64"):
            # Pre-rendered bytes: stored as sent, only the name is sanitized.
            try:
                content = base64.b64decode(payload["content_b64"])
            except Exception:  # noqa: BLE001
                self._send(400, {"ok": False, "error": "invalid content_b64"})
                return
            fmt = docgen.normalize_format(requested) or ""
        elif payload.get("text") is not None:
            content, fmt = docgen.render(str(payload["text"]), requested,
                                         title=str(payload.get("title") or ""))
            name = docgen.with_extension(name, fmt)
        else:
            self._send(400, {"ok": False, "error": "Missing 'content_b64' or 'text'"})
            return
        stored = FILES.save(user, drive, name, content)
        download = "%s?%s" % (FILES_DOWNLOAD_PATH,
                              urllib.parse.urlencode({"name": stored}))
        try:
            reply = WSFS.put_file(user, corpus.norm(drive), stored, content,
                                  retries=PUTFILE_RETRIES,
                                  retry_delay=PUTFILE_RETRY_DELAY,
                                  timeout=PUTFILE_TIMEOUT) or {}
        except wsfs.ChannelError:
            self._send(200, {"ok": True, "transferred": False, "stored": True,
                             "name": stored, "format": fmt, "size": len(content),
                             "reason": "no-channel", "download": download})
            return
        if reply.get("path"):
            # Delivered to the drive: the session copy is no longer needed.
            FILES.delete(user, drive, stored)
            self._send(200, {"ok": True, "transferred": True, "name": stored,
                             "format": fmt, "size": len(content),
                             "path": reply["path"]})
            return
        reason = reply.get("error") or "unknown"
        self._send(200, {"ok": True, "transferred": False, "stored": True,
                         "name": stored, "format": fmt, "size": len(content),
                         "reason": reason, "download": download})

    def _feedback(self):
        """Rate one generated answer (up/down + optional reason/comment).
        The stored JSONL record carries the server-side answer trace joined by
        the audit id, so rated exchanges can be curated into a gold set."""
        if self._locked_guard():
            return
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        payload = self._read_json()
        audit_id = str(payload.get("audit_id") or "").strip()
        rating = str(payload.get("rating") or "").strip().lower()
        if not audit_id:
            self._send(400, {"ok": False, "error": "Missing 'audit_id'"})
            return
        if rating not in feedback.RATINGS:
            self._send(400, {"ok": False,
                             "error": "rating must be one of %s"
                                      % (feedback.RATINGS,)})
            return
        trace = TRACES.get(audit_id)
        entry = {"ts": time.time(), "user": user, "drive": corpus.norm(drive),
                 "audit_id": audit_id, "rating": rating,
                 "reason": str(payload.get("reason") or "").strip()[:64] or None,
                 "comment": str(payload.get("comment") or "").strip()[:2000] or None,
                 "trace": trace}
        if trace is None:
            # Evicted/restarted: keep the client's echo so the rating still
            # points at SOME record of the exchange.
            entry["client"] = {
                "question": str(payload.get("question") or "")[:2000],
                "answer": str(payload.get("answer") or "")[:8000]}
        try:
            stored = FEEDBACK.record(entry)
        except OSError as e:
            self._send(500, {"ok": False,
                             "error": "feedback store unavailable: %s" % e})
            return
        self._send(200, {"ok": True, "stored": stored,
                         "trace": trace is not None})

    def _files_list(self):
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        self._send(200, {"ok": True, "files": FILES.list(user, drive),
                         "download_path": FILES_DOWNLOAD_PATH})

    def _scope_info(self):
        """The drives this caller may search. The UI cannot work this out on
        its own: the entitlement never passes through the browser."""
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        resolved = _resolve_scope(self.headers, user, drive)
        self._send(200, {
            "ok": True,
            "active_drive": resolved.active_root,
            "source": resolved.source,
            "drives": [{"path": root,
                        "name": root.rsplit("/", 1)[-1],
                        "active": root == resolved.active_root}
                       for root in resolved.drive_roots]})

    def _files_download(self, query):
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        name = (urllib.parse.parse_qs(query or "").get("name") or [""])[0]
        data = FILES.get(user, drive, name) if name else None
        if data is None:
            self._send(404, {"ok": False, "error": "no such stored file"})
            return
        safe = filestore.safe_name(name)
        ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
        ctype = (docgen.content_type(ext) if docgen.normalize_format(ext)
                 else "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % safe.replace('"', "_"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _drive_file_path_from_query(self, query):
        return (urllib.parse.parse_qs(query or "").get("path") or [""])[0]

    def _drive_file_post(self):
        """POST twin of the GET below.

        The ViVeSecBox tunnel forwards the request path but NOT the query
        string, so an embedded UI cannot name the file in the URL; measured on
        the demo box, where every embedded GET arrived as a bare
        `/api/v1/ui/file` and was answered 400. The body survives the tunnel.

        The query is still honoured as a fallback: which of the two the tunnel
        preserves is its implementation detail, and the client sends both.
        """
        payload = self._read_json()
        path = (payload.get("path") or "").strip()
        if not path:
            path = self._drive_file_path_from_query(getattr(self, "_post_query", ""))
        self._drive_file(path)

    def _drive_file(self, raw_path):
        """Serve one drive file, fetched from the ViVeSecBox over ws-fs.

        The document itself never lives on the AI Box, so a citation can only
        be shown in full by asking the box for it. The box applies its own
        access rules; the scope is checked here as well, so naming a file in a
        drive this request was not granted never reaches the channel.
        """
        if self._locked_guard():
            return
        vvs = self._read_vvs()
        if vvs is None:
            return
        user, drive = vvs
        if not raw_path:
            self._send(400, {"ok": False, "error": "Missing 'path'"})
            return
        path = corpus.norm(raw_path)
        req_scope = _resolve_scope(self.headers, user, drive)
        if not req_scope.contains_path(path):
            self._send(403, {"ok": False,
                             "error": "file outside the request scope"})
            return
        try:
            header, content = WSFS.get_file(user, path, timeout=GETFILE_TIMEOUT)
        except wsfs.ChannelError as e:
            self._send(503, {"ok": False, "reason": "no-channel",
                             "error": "ViVeSecBox not connected: %s" % e})
            return
        error = (header or {}).get("error")
        if error:
            status = {"permission": 403, "not-found": 404,
                      "missing": 404, "temporary": 503}.get(error, 502)
            self._send(status, {"ok": False, "error": error})
            return
        if not content:
            self._send(502, {"ok": False,
                             "error": "the box returned no file content"})
            return
        name = path.rsplit("/", 1)[-1] or "document"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        ctype = _INLINE_TYPES.get(ext)
        disposition = "inline" if ctype else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        # Without nosniff the browser could still sniff an octet-stream body
        # into HTML and run it in our origin.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition",
                         '%s; filename="%s"' % (disposition, name.replace('"', "_")))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    # -- voice I/O (on-box STT/TTS; voice.py) --------------------------------
    def _stt(self):
        """Recorded utterance -> text. The caller then sends that text through
        the normal /ui/ask path, so voice input changes nothing downstream:
        same retrieval, same grounding, same citations."""
        payload = self._read_json()
        raw_b64 = payload.get("audio_b64") or ""
        if not raw_b64:
            self._send(400, {"ok": False, "error": "Missing 'audio_b64'"})
            return
        try:
            audio = base64.b64decode(raw_b64)
        except Exception:  # noqa: BLE001
            self._send(400, {"ok": False, "error": "invalid audio_b64"})
            return
        try:
            text = voice.transcribe(audio,
                                    content_type=payload.get("content_type"),
                                    lang=payload.get("lang"))
        except voice.VoiceError as e:
            # 503, not 500: the pipeline is fine, this box just has no ears.
            self._send(503, {"ok": False, "error": str(e),
                             "available": voice.stt_available()})
            return
        self._send(200, {"ok": True, "text": text,
                         "lang": voice.normalize_lang(payload.get("lang"))})

    def _tts(self):
        """Answer text -> spoken audio. Returns the audio bytes directly so the
        UI can hand the blob straight to an <audio> element."""
        payload = self._read_json()
        text = str(payload.get("text") or "").strip()
        if not text:
            self._send(400, {"ok": False, "error": "Missing 'text'"})
            return
        try:
            audio, mime, truncated = voice.synthesize(text, lang=payload.get("lang"))
        except voice.VoiceError as e:
            self._send(503, {"ok": False, "error": str(e),
                             "available": voice.tts_available()})
            return
        # Header, not body: the body is audio, and the UI must still be able to
        # tell the user that only part of a long answer was read out.
        self._send_bytes(200, mime, audio,
                         {"X-Speech-Truncated": "1" if truncated else "0"})

    # -- storage unlock (LUKS2 luksOpen + mount; spec sec 2.2) ---------------
    def _unlock(self):
        payload = self._read_json()
        key = payload.get("storage_key") or payload.get("key") or ""
        try:
            STORAGE.unlock(key)
        except storage.StorageError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return
        # A successful unlock clears any presence-lock and re-arms the watchdog.
        _PRESENCE_LOST[0] = False
        _LAST_STATUS_TS[0] = time.time()
        if WATCHDOG_ENFORCES:
            _WD_ARMED[0] = True
        self._send(200, {"ok": True, "locked": STORAGE.is_locked(),
                         "storage": STORAGE.status()})


def _ensure_tls_listener():
    with _TLS_LOCK:
        if _TLS_SERVER[0] is not None:
            return _TLS_SERVER[0]
        tls_server = ThreadingHTTPServer((HOST, TLS_PORT), Handler)
        try:
            tls_server.socket = PROVISIONER.build_server_ssl_context().wrap_socket(
                tls_server.socket, server_side=True)
        except BaseException:
            tls_server.server_close()
            raise
        threading.Thread(target=tls_server.serve_forever, daemon=True).start()
        _TLS_SERVER[0] = tls_server
        print("  TLS         : mutual (client cert required), TLS 1.2+ on :%d" % TLS_PORT)
        return tls_server


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ViVeSec AIBox adapter on http://%s:%d" % (HOST, PORT))
    print("  RAG backend :", RAG_URL, "(auth=%s)" % ("on" if RAG_API_KEY else "off"))
    print("  Tenant      :", TENANT_ID)
    print("  Drive prefix:", corpus.DRIVE_PREFIX)
    print("  Features    :", ", ".join(FEATURES))
    print("  Mirror      :", META_PATH or "(in-memory)")
    print("  Storage     :", "%s (watchdog=%s, %ds)" % (
        STORAGE.mode, "on" if WATCHDOG_ENFORCES else "off", WATCHDOG_SECONDS))
    print("  Sessions    :", "%s (idle=%ds, %d turns)" % (
        SESSIONS.spill_dir or "(in-memory)", SESSIONS.idle_seconds, SESSIONS.max_turns))
    print("  Read scope  :", "%s header | entitlements=%s | all-drives=%s" % (
        scope.HEADER_OTHER_DRIVES,
        ENTITLEMENTS.path if ENTITLEMENTS else "off",
        "ON (demo, no per-user filter)" if SCOPE_ALL_DRIVES else "off"))
    print("  Provisioning:", "%s | TLS=%s" % (
        "initialized" if PROVISIONER.is_initialized() else "not initialized",
        "on" if TLS_ENABLED else "off"))
    print("  Generation  :", "%s via %s (mode=%s)" % (llm.GEN_MODEL, llm.OLLAMA_URL, llm.GENERATE))
    print("  WS-fs       :", "GET /api/v1/ws/fs (put-file retries=%d, delay=%.1fs)"
          % (PUTFILE_RETRIES, PUTFILE_RETRY_DELAY))
    print("  Files       :", FILES.root or "(disabled)")
    # SSDP/UPnP discovery so the ViVeSecBox finds us on the LAN (spec sec 2.5).
    responder = discovery.from_env(default_port=PORT, uuid_dir=PKI_DIR)
    if responder is not None:
        print("  Discovery   :", "SSDP %s -> %s" % (responder.st, responder.current_location()))
        threading.Thread(target=responder.run, daemon=True).start()
    else:
        print("  Discovery   : off")
    # Physical factory-reset pin (spec sec 1: reset is a physical action).
    reset_mon = factory_reset.from_env(factory_reset_action)
    if reset_mon is not None:
        print("  Factory pin :", "armed (hold %.0fs)" % reset_mon.hold_seconds)
        threading.Thread(target=reset_mon.run, daemon=True).start()
    else:
        print("  Factory pin : off")
    if TLS_ENABLED:
        if PROVISIONER.is_initialized():
            try:
                _ensure_tls_listener()
            except OSError as e:
                print("  TLS         : cannot bind :%d -> %s" % (TLS_PORT, e))
        else:
            print("  TLS         : requested but box not initialized -> HTTP only")
    if WATCHDOG_ENFORCES:
        threading.Thread(target=_watchdog_loop, daemon=True).start()
    threading.Thread(target=_maintenance_loop, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
