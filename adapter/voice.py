"""Voice I/O for the AIBox UI: speech in (STT) and speech out (TTS).

Both directions run ON the box - the audio never leaves the appliance, which is
the whole point of the product. The two engines are reached over loopback HTTP
so this module stays stdlib-only and the models can be swapped without touching
the adapter:

    STT  an OpenAI-compatible transcription endpoint
         (faster-whisper-server / speaches / whisper.cpp server):
         POST multipart/form-data -> {"text": "..."}
    TTS  a Piper HTTP server: POST the text -> WAV bytes.

An unconfigured URL means "not available", never a hard failure: /api/v1/status
reports the capability and the UI hides the microphone / falls back to the
browser's own offline speech synthesis.

OPERATIONAL NOTE: the browser records Opus in a WebM container. Backends that
shell out to ffmpeg (faster-whisper-server, speaches) accept it as-is; the plain
whisper.cpp server wants a 16 kHz WAV, so put a converting front-end in front of
it or use one of the former.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Empty = the capability is switched off (the default: an adapter with no voice
# backend deployed must keep working exactly as before).
STT_URL = (os.environ.get("ADAPTER_STT_URL", "") or "").strip()
STT_MODEL = (os.environ.get("ADAPTER_STT_MODEL", "") or "").strip()
STT_TIMEOUT = float(os.environ.get("ADAPTER_STT_TIMEOUT", "120") or 120)
# Whisper guesses the language when none is given; guessing is measurably worse
# on short utterances, so the UI's language is passed through when it has one.
STT_AUTODETECT = (os.environ.get("ADAPTER_STT_AUTODETECT", "") or "").strip().lower() in (
    "1", "true", "yes", "on")

TTS_URL = (os.environ.get("ADAPTER_TTS_URL", "") or "").strip()
TTS_TIMEOUT = float(os.environ.get("ADAPTER_TTS_TIMEOUT", "120") or 120)
# Piper serves one voice per process; a multi-voice front-end takes ?voice=.
# Format: "hu=hu_HU-anna-medium,en=en_US-lessac-medium".
TTS_VOICES = (os.environ.get("ADAPTER_TTS_VOICES", "") or "").strip()
# Synthesis is linear in the text length and a spoken demo answer is short;
# reading out a 20-page analysis would block the box for minutes.
TTS_MAX_CHARS = int(os.environ.get("ADAPTER_TTS_MAX_CHARS", "2000") or 2000)

MAX_AUDIO_BYTES = int(os.environ.get("ADAPTER_STT_MAX_BYTES", str(25 * 1024 * 1024)))

_LANG_CODES = {
    "en": "en", "eng": "en", "english": "en", "angol": "en",
    "hu": "hu", "hun": "hu", "hungarian": "hu", "magyar": "hu",
    "da": "da", "dan": "da", "danish": "da", "dansk": "da",
    "de": "de", "ger": "de", "deu": "de", "german": "de", "deutsch": "de",
}


class VoiceError(Exception):
    """Backend unreachable, misconfigured or refused the request."""


def normalize_lang(value):
    """Accept both the UI's i18n code and the generator's language NAME."""
    if not value:
        return None
    key = str(value).strip().lower().replace("_", "-")
    if key in _LANG_CODES:
        return _LANG_CODES[key]
    return _LANG_CODES.get(key.split("-", 1)[0])


def stt_available():
    return bool(STT_URL)


def tts_available():
    return bool(TTS_URL)


def status():
    """Capability block for /api/v1/status so the UI can hide what is absent."""
    return {"stt": stt_available(), "tts": tts_available(),
            "stt_model": STT_MODEL or None}


def _voice_for(lang):
    for item in TTS_VOICES.split(","):
        code, _, name = item.partition("=")
        if code.strip().lower() == (lang or "") and name.strip():
            return name.strip()
    return None


_CITATION_RE = re.compile(r"\[#\d+\]")
_MD_NOISE_RE = re.compile(r"[*_`>#|]+")


def speakable_text(text):
    """Strip what only makes sense on screen: citation markers, markdown noise,
    table pipes. Read aloud, "[#3]" becomes "bracket hash three"."""
    out = _CITATION_RE.sub("", text or "")
    out = _MD_NOISE_RE.sub(" ", out)
    out = re.sub(r"^\s*[-+]\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _truncate_for_speech(text, limit):
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    # Prefer a sentence boundary so the audio does not stop mid-word.
    for sep in (". ", "! ", "? ", "\n"):
        idx = cut.rfind(sep)
        if idx > limit // 2:
            return cut[:idx + 1].strip(), True
    return cut.rsplit(" ", 1)[0].strip(), True


def _multipart(fields, field_name, filename, content, content_type):
    boundary = "----vivesec" + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append((
            '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
            % (boundary, key, value)).encode("utf-8"))
    parts.append((
        '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
        'Content-Type: %s\r\n\r\n'
        % (boundary, field_name, filename, content_type)).encode("utf-8"))
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _open(req, timeout, what):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise VoiceError("%s backend HTTP %s %s" % (what, e.code, detail).strip())
    except Exception as e:  # noqa: BLE001
        raise VoiceError("%s backend unreachable: %s" % (what, e))


def transcribe(audio, content_type=None, lang=None, filename=None):
    """Audio bytes -> recognised text. Raises VoiceError when unavailable."""
    if not stt_available():
        raise VoiceError("speech-to-text is not configured on this box")
    if not audio:
        raise VoiceError("empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("audio too large (%d bytes)" % len(audio))
    code = normalize_lang(lang)
    fields = {"response_format": "json"}
    if STT_MODEL:
        fields["model"] = STT_MODEL
    if code and not STT_AUTODETECT:
        fields["language"] = code
    ctype = (content_type or "audio/webm").split(";", 1)[0].strip() or "audio/webm"
    name = filename or ("speech." + (ctype.rsplit("/", 1)[-1] or "webm"))
    body, multipart_type = _multipart(fields, "file", name, audio, ctype)
    req = urllib.request.Request(STT_URL, data=body,
                                 headers={"Content-Type": multipart_type})
    with _open(req, STT_TIMEOUT, "speech-to-text") as r:
        raw = r.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        # Some builds answer text/plain for response_format=text.
        return raw.decode("utf-8", "replace").strip()
    text = payload.get("text")
    if text is None and isinstance(payload.get("segments"), list):
        text = " ".join(str(s.get("text") or "") for s in payload["segments"])
    return (text or "").strip()


def synthesize(text, lang=None):
    """Text -> (audio bytes, mime type). Raises VoiceError when unavailable."""
    if not tts_available():
        raise VoiceError("text-to-speech is not configured on this box")
    spoken = speakable_text(text)
    if not spoken:
        raise VoiceError("nothing to speak")
    spoken, truncated = _truncate_for_speech(spoken, TTS_MAX_CHARS)
    url = TTS_URL
    voice = _voice_for(normalize_lang(lang))
    if voice:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode({"voice": voice})
    # Piper's HTTP server takes the utterance as the raw request body.
    req = urllib.request.Request(url, data=spoken.encode("utf-8"),
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    with _open(req, TTS_TIMEOUT, "text-to-speech") as r:
        audio = r.read()
        mime = r.headers.get("Content-Type") or "audio/wav"
    if not audio:
        raise VoiceError("text-to-speech backend returned no audio")
    return audio, mime.split(";", 1)[0].strip(), truncated
