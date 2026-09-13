"""End-to-end smoke test for the ViVeSec v2 -> RAG adapter.

Boots a throwaway RAG service (fallback embeddings, temp index) and the adapter
in front of it, then drives the adapter with ViVeSec v2 calls exactly like the
ViVeSecBox simulator would. Asserts the translation, the diff-sync metadata
mirror, corpus isolation (the VVS-Drive hard filter), and drop/tree.

Pure stdlib. Run:  python adapter/smoke_test.py
"""
import json
import os
import base64
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  ", name)
    else:
        FAIL += 1
        print("  FAIL", name, "->", detail)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post_json(base, path, obj, headers=None):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post_raw(base, path, raw):
    req = urllib.request.Request(base + path, data=raw, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post_json_expect_error(base, path, obj, headers=None):
    try:
        post_json(base, path, obj, headers)
        return None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get_json(base, path, headers=None):
    req = urllib.request.Request(base + path)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get_raw(base, path, headers=None):
    req = urllib.request.Request(base + path)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def get_text(base, path, headers=None):
      req = urllib.request.Request(base + path)
      for k, v in (headers or {}).items():
            req.add_header(k, v)
      with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")


def vvs(path):
    """Encode a VVS-Drive header value the way the ViVeSecBox does:
    urlsafe base64 of the UTF-8 drive path."""
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii")


def other_drives(*paths):
    """Encode a VVS-Other-Drives header: urlsafe base64 of the UTF-8 drive
    paths joined by NUL bytes (docs/aibox_patch_0902.md)."""
    blob = "\x00".join(paths).encode("utf-8")
    return base64.urlsafe_b64encode(blob).decode("ascii")


def wait_up(base, path, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            get_json(base, path)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def test_storage_unit():
    """Unit-test the LUKS state machine with a fake cryptsetup runner (no real
    device, so it runs anywhere). Asserts the key is passed on stdin, never on
    the argv, and that the open/format/close transitions are correct."""
    sys.path.insert(0, HERE)
    import storage  # noqa: E402

    def make_runner(state):
        def runner(cmd, input_bytes=None, timeout=120):
            if cmd[0] == "cryptsetup":
                if "isLuks" in cmd:
                    return (0 if state["is_luks"] else 1, b"", b"")
                if "status" in cmd:
                    return (0 if state["active"] else 4, b"", b"")
                if "luksFormat" in cmd:
                    state["is_luks"] = True
                    state["key"] = input_bytes
                    return (0, b"", b"")
                if "luksOpen" in cmd:
                    if input_bytes != state["key"]:
                        return (2, b"", b"No key available with this passphrase")
                    state["active"] = True
                    return (0, b"", b"")
                if "luksClose" in cmd:
                    state["active"] = False
                    return (0, b"", b"")
            if cmd[0].startswith("mkfs."):
                return (0, b"", b"")
            if cmd[0] in ("mount", "umount"):
                return (0, b"", b"")
            return (0, b"", b"")
        return runner

    # off mode: always unlocked, no cryptsetup calls.
    off = storage.StorageManager(mode="off")
    check("storage off starts unlocked", off.is_unlocked() is True, off.status())
    off.lock()
    check("storage off lock is a no-op", off.is_unlocked() is True, off.status())

    # luks first-init: format (allowed) -> open -> unlocked.
    log = []
    state = {"is_luks": False, "active": False, "key": None}
    base_runner = make_runner(state)

    def logging_runner(cmd, input_bytes=None, timeout=120):
        log.append((list(cmd), input_bytes))
        return base_runner(cmd, input_bytes, timeout)

    sm = storage.StorageManager(mode="luks", device="/dev/fake", name="vstest",
                                mount="", allow_format=True, runner=logging_runner)
    check("luks starts locked", sm.is_locked() is True, sm.status())
    sm.unlock("s3cret-key")
    check("luks unlocked after key", sm.is_unlocked() is True, sm.status())
    check("luksFormat got key on stdin", state["key"] == b"s3cret-key", state)
    check("key NEVER on argv",
          all(b"s3cret-key" not in b" ".join(c.encode() for c in cmd)
              for cmd, _ in log), [c for c, _ in log])
    sm.lock()
    check("luks locked after lock()", sm.is_locked() is True, sm.status())

    # second unlock: already a LUKS container -> open only (no re-format).
    sm.unlock("s3cret-key")
    check("luks re-open without reformat", sm.is_unlocked() is True, sm.status())

    # wrong key fails to open.
    state_w = {"is_luks": True, "active": False, "key": b"right"}
    smw = storage.StorageManager(mode="luks", device="/dev/fakew", name="vstw",
                                 mount="", allow_format=False, runner=make_runner(state_w))
    raised = False
    try:
        smw.unlock("wrong")
    except storage.StorageError:
        raised = True
    check("luks wrong key rejected", raised and smw.is_locked() is True, smw.status())

    # un-formatted device without ADAPTER_LUKS_FORMAT -> refuse.
    state_n = {"is_luks": False, "active": False, "key": None}
    smn = storage.StorageManager(mode="luks", device="/dev/faken", name="vstn",
                                 mount="", allow_format=False, runner=make_runner(state_n))
    refused = False
    try:
        smn.unlock("k")
    except storage.StorageError:
        refused = True
    check("luks refuses to format without flag", refused, smn.status())


def test_watchdog(rag_base, tmp):
    """E2E presence-lock: with the watchdog forced on and a 2s timeout, the box
    locks itself after the status polls stop, refuses queries (503), and a
    /storage/unlock brings it back. Runs in 'off' storage mode so no real LUKS
    device is needed (the state machine is what we exercise)."""
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({
        "ADAPTER_HOST": "127.0.0.1", "ADAPTER_PORT": str(port),
        "RAG_URL": rag_base,
        "ADAPTER_META_PATH": os.path.join(tmp, "wd_meta.json"),
        "ADAPTER_DRIVE_PREFIX": "/storage/drives",
        "ADAPTER_GENERATE": "off",
        "ADAPTER_WATCHDOG_ENABLED": "on",
        "ADAPTER_WATCHDOG_SECONDS": "2",
    })
    proc = subprocess.Popen([PY, os.path.join(HERE, "service.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_up(base, "/api/v1/status"):
            check("watchdog adapter up", False, "did not start")
            return
        # Arm the watchdog with a status poll; fresh presence -> fs-ready true.
        _, st0 = post_json(base, "/api/v1/status", {})
        check("watchdog fresh fs-ready", st0.get("fs-ready") is True
              and st0.get("presence_lost") is False, st0)
        # Stop polling and let the 2s watchdog trip.
        time.sleep(4.0)
        _, st1 = post_json(base, "/api/v1/status", {})
        check("watchdog tripped -> locked", st1.get("locked") is True
              and st1.get("presence_lost") is True, st1)
        check("watchdog tripped -> fs-not-ready", st1.get("fs-ready") is False, st1)
        # Queries are refused while locked.
        err = post_json_expect_error(base, "/api/v1/ui/query", {"query": "x"},
                                     headers={"VVS-Drive": vvs("/storage/drives/finance/"),
                                              "VVS-User": "u-1"})
        check("locked query -> 503", err and err[0] == 503, err)
        # Index writes are refused too.
        werr = post_json_expect_error(base, "/api/v1/index/upsert/directory",
                                      {"path": "/storage/drives/finance"})
        check("locked write -> 503", werr and werr[0] == 503, werr)
        # Unlock restores service (off mode ignores the key value).
        _, unl = post_json(base, "/api/v1/storage/unlock", {"storage_key": "x"})
        check("unlock clears lock", unl.get("locked") is False, unl)
        _, st2 = post_json(base, "/api/v1/status", {})
        check("fs-ready after unlock", st2.get("fs-ready") is True
              and st2.get("presence_lost") is False, st2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


# -- openssl CA helpers (the smoke test plays the ViVeSecBox CA) -------------
def _ossl_run(openssl_bin, args):
    p = subprocess.run([openssl_bin] + args, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return p.returncode, p.stdout, p.stderr


def make_ca(openssl_bin, d, name="ca", cn="Test ViVeSec CA"):
    key = os.path.join(d, name + ".key")
    crt = os.path.join(d, name + ".crt")
    _ossl_run(openssl_bin, ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", key, "-out", crt,
                            "-subj", "/CN=%s" % cn, "-days", "2"])
    return key, crt


def sign_csr(openssl_bin, d, csr_pem, ca_key, ca_crt, san_host, tag="srv"):
    csr = os.path.join(d, tag + ".csr")
    crt = os.path.join(d, tag + ".crt")
    with open(csr, "w") as f:
        f.write(csr_pem)
    extra = []
    if san_host:
        ext = os.path.join(d, tag + ".ext")
        with open(ext, "w") as f:
            f.write("subjectAltName=DNS:%s\n" % san_host)
        extra = ["-extfile", ext]
    _ossl_run(openssl_bin, ["x509", "-req", "-in", csr, "-CA", ca_crt,
                            "-CAkey", ca_key, "-CAcreateserial", "-out", crt,
                            "-days", "2"] + extra)
    with open(crt) as f:
        return f.read()


def csr_text(openssl_bin, d, csr_pem, tag="csr"):
      csr = os.path.join(d, tag + ".csr")
      with open(csr, "w") as f:
            f.write(csr_pem)
      rc, out, err = _ossl_run(openssl_bin, ["req", "-noout", "-subject", "-text", "-in", csr])
      if rc != 0:
            return err.decode("utf-8", "replace")
      return out.decode("utf-8", "replace")


def make_client_cert(openssl_bin, d, ca_key, ca_crt, tag="client", cn="vivesecbox-client"):
    key = os.path.join(d, tag + ".key")
    csr = os.path.join(d, tag + ".csr")
    crt = os.path.join(d, tag + ".crt")
    _ossl_run(openssl_bin, ["req", "-new", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", key, "-out", csr, "-subj", "/CN=%s" % cn])
    _ossl_run(openssl_bin, ["x509", "-req", "-in", csr, "-CA", ca_crt,
                            "-CAkey", ca_key, "-CAcreateserial", "-out", crt,
                            "-days", "2"])
    with open(crt) as f:
        return f.read()


def _have_openssl(openssl_bin):
    try:
        rc, _, _ = _ossl_run(openssl_bin, ["version"])
        return rc == 0
    except Exception:  # noqa: BLE001
        return False


def test_provision_unit():
    """Unit-test the mTLS init/commit verification (spec sec 1). Needs the
    openssl CLI; skipped gracefully where it is unavailable."""
    sys.path.insert(0, HERE)
    import provision  # noqa: E402
    import ssl

    openssl_bin = provision.find_openssl()
    if not _have_openssl(openssl_bin):
        print("  skip  provisioning unit test (openssl not available)")
        return
    work = tempfile.mkdtemp(prefix="vivesec-prov-")
    cak, cac = make_ca(openssl_bin, work, "ca")
    with open(cac) as f:
        ca_pem = f.read()

    # -- happy path: prepare -> CA signs -> commit ---------------------------
    pr = provision.Provisioner(os.path.join(work, "box"), openssl_bin=openssl_bin)
    check("provision starts uninitialized", pr.is_initialized() is False)
    csr = pr.prepare()
    check("prepare returns a CSR", "CERTIFICATE REQUEST" in csr, csr[:40])
    csr_dump = csr_text(openssl_bin, work, csr, "srv-prepared")
    check("prepare CSR uses ViVeTech CN", "CN = %s" % provision.HOSTNAME in csr_dump,
          csr_dump[:300])
    check("prepare CSR uses ViVeTech SAN", "DNS:%s" % provision.HOSTNAME in csr_dump,
          csr_dump[:500])
    srv = sign_csr(openssl_bin, work, csr, cak, cac, provision.HOSTNAME, "srv")
    cli = make_client_cert(openssl_bin, work, cak, cac, "cli")
    pr.commit(srv, ca_pem, cli)
    check("commit -> initialized", pr.is_initialized() is True)
    ctx = pr.build_server_ssl_context()
    check("TLS context requires client cert", ctx.verify_mode == ssl.CERT_REQUIRED)
    reinit = False
    try:
        pr.prepare()
    except provision.ProvisionError:
        reinit = True
    check("prepare after init rejected", reinit)

    # -- client_crt is OPTIONAL (aibox_more3 §2) ------------------------------
    pr_nc = provision.Provisioner(os.path.join(work, "box_nc"), openssl_bin=openssl_bin)
    csr_nc = pr_nc.prepare()
    srv_nc = sign_csr(openssl_bin, work, csr_nc, cak, cac, provision.HOSTNAME, "srvnc")
    pr_nc.commit(srv_nc, ca_pem)  # no client_crt
    check("commit without client_crt -> initialized",
          pr_nc.is_initialized() is True)
    ctx_nc = pr_nc.build_server_ssl_context()
    check("TLS still requires client cert without client.crt",
          ctx_nc.verify_mode == ssl.CERT_REQUIRED)

    # -- negative: server cert does not match the prepared key ---------------
    pr2 = provision.Provisioner(os.path.join(work, "box2"), openssl_bin=openssl_bin)
    pr2.prepare()
    bad = False
    try:
        pr2.commit(cli, ca_pem, cli)  # 'cli' was made from a different key
    except provision.ProvisionError:
        bad = True
    check("commit rejects mismatched server cert",
          bad and pr2.is_initialized() is False)

    # -- negative: server cert signed by a DIFFERENT CA ----------------------
    pr3 = provision.Provisioner(os.path.join(work, "box3"), openssl_bin=openssl_bin)
    csr3 = pr3.prepare()
    srv3 = sign_csr(openssl_bin, work, csr3, cak, cac, provision.HOSTNAME, "srv3")
    cak2, cac2 = make_ca(openssl_bin, work, "ca2", "Other CA")
    with open(cac2) as f:
        ca_pem2 = f.read()
    cli2 = make_client_cert(openssl_bin, work, cak2, cac2, "cli2")
    badca = False
    try:
        pr3.commit(srv3, ca_pem2, cli2)  # srv3 signed by ca, not ca2
    except provision.ProvisionError:
        badca = True
    check("commit rejects cert not signed by ca_crt",
          badca and pr3.is_initialized() is False)

    # -- hostname predicate rejects a cert without the hostname --------------
    _, other_crt = make_ca(openssl_bin, work, "host_other", "evil.example.com")
    check("hostname check rejects wrong host",
          pr._valid_for_host(other_crt, provision.HOSTNAME) is False)
    check("hostname check accepts the server cert",
          pr._valid_for_host(pr.server_crt, provision.HOSTNAME) is True)


def test_init_e2e(rag_base, tmp, work, openssl_bin):
    """E2E mTLS bootstrap over the HTTP server: prepare -> sign -> commit, then
    a second prepare is rejected (already initialized)."""
    if not _have_openssl(openssl_bin):
        print("  skip  init e2e test (openssl not available)")
        return

    # Separate ephemeral ports exercise the HTTP-to-mTLS transition.
    http_port = free_port()
    mtls_port = free_port()
    base = "http://127.0.0.1:%d" % http_port
    pki_dir = os.path.join(tmp, "e2e_pki")
    process_env = dict(os.environ)
    process_env.update({
        "ADAPTER_HOST": "127.0.0.1", "ADAPTER_PORT": str(http_port),
        "RAG_URL": rag_base,
        "ADAPTER_META_PATH": os.path.join(tmp, "e2e_meta.json"),
        "ADAPTER_PKI_DIR": pki_dir,
        "ADAPTER_DRIVE_PREFIX": "/storage/drives",
        "ADAPTER_GENERATE": "off",
        "ADAPTER_TLS": "on",
        "ADAPTER_TLS_PORT": str(mtls_port),
    })
    proc = subprocess.Popen([PY, os.path.join(HERE, "service.py")],
                            env=process_env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        if not wait_up(base, "/api/v1/status"):
            check("init adapter up", False, "did not start")
            return
        import provision  # noqa: E402
        _, pr = get_json(base, "/api/v1/init/prepare")
        check("init prepare returns CSR",
              "CERTIFICATE REQUEST" in (pr.get("server_req") or ""), pr)
        cak, cac = make_ca(openssl_bin, work, "e2eca")
        with open(cac) as f:
            ca_pem = f.read()
        srv = sign_csr(openssl_bin, work, pr["server_req"], cak, cac,
                       provision.HOSTNAME, "e2esrv")
        cli = make_client_cert(openssl_bin, work, cak, cac, "e2ecli")
        # aibox_more3 §2: the box does NOT send client_crt any more — the
        # primary path commits without it (TLS-layer verification instead).
        _, cm = post_json(base, "/api/v1/init/commit",
                          {"server_crt": srv, "ca_crt": ca_pem,
                           "storage_key": "box-key"})
        check("init commit ok (no client_crt)", cm.get("initialized") is True, cm)
        check("init commit status ok", cm.get("status") == "ok", cm)
        import ssl
        tls_context = ssl.create_default_context(cafile=cac)
        tls_context.check_hostname = False
        tls_context.load_cert_chain(os.path.join(work, "e2ecli.crt"),
                                    os.path.join(work, "e2ecli.key"))
        tls_req = urllib.request.Request(
            "https://127.0.0.1:%d/api/v1/status" % mtls_port)
        with urllib.request.urlopen(tls_req, timeout=10,
                                    context=tls_context) as response:
            tls_status_code = response.status
            tls_status = json.loads(response.read().decode("utf-8"))
        check("TLS listener starts immediately after init commit",
              tls_status_code == 200 and tls_status.get("ok") is True,
              tls_status)
        # A second prepare must be refused now that the box is initialized.
        err = None
        try:
            get_json(base, "/api/v1/init/prepare")
        except urllib.error.HTTPError as e:
            err = (e.code, json.loads(e.read().decode("utf-8")))
        check("prepare after init -> 400 already initialized",
              err and err[0] == 400
              and err[1].get("status") == "already initialized", err)
        # A commit on an already-initialized box is refused (400, not 200).
        bad = post_json_expect_error(base, "/api/v1/init/commit",
                                     {"server_crt": cli, "ca_crt": ca_pem,
                                      "client_crt": cli, "storage_key": "x"})
        check("commit on initialized box -> 400", bad and bad[0] == 400, bad)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


def test_session_unit():
    """Unit-test the multi-turn conversation manager: record/history, drive
    isolation, window trim, disk spill+reload, and idle eviction."""
    sys.path.insert(0, HERE)
    import session  # noqa: E402

    d = tempfile.mkdtemp(prefix="vivesec-sess-")
    sm = session.SessionManager(spill_dir=d, idle_seconds=900, max_turns=3)
    check("session starts empty", sm.history("u1", "/d/a/") == [])
    sm.record("u1", "/d/a/", "Q1", "A1")
    h = sm.history("u1", "/d/a/")
    check("session records exchange",
          len(h) == 2 and h[0]["role"] == "user" and h[0]["content"] == "Q1"
          and h[1]["role"] == "assistant" and h[1]["content"] == "A1", h)
    # Same user, different drive -> separate, isolated conversation.
    check("session isolated per drive", sm.history("u1", "/d/b/") == [])
    # Window trim: max_turns=3 exchanges -> at most 6 messages.
    for i in range(5):
        sm.record("u1", "/d/a/", "q%d" % i, "a%d" % i)
    h2 = sm.history("u1", "/d/a/")
    check("session window trimmed", len(h2) == 6 and h2[-1]["content"] == "a4", len(h2))
    # Disk spill + reload via a fresh manager over the same dir.
    sm2 = session.SessionManager(spill_dir=d, idle_seconds=900, max_turns=3)
    h3 = sm2.history("u1", "/d/a/")
    check("session reloaded from disk", len(h3) == 6 and h3[-1]["content"] == "a4", h3)
    # Idle eviction removes from memory but keeps the disk copy.
    sm3 = session.SessionManager(spill_dir=d, idle_seconds=0, max_turns=3)
    sm3.history("u1", "/d/a/")
    time.sleep(0.02)
    evicted = sm3.sweep()
    check("idle session evicted from memory", evicted >= 1, evicted)
    check("evicted session still on disk",
          len(sm3.history("u1", "/d/a/")) == 6, "reload after evict")
    # Presence-lock flush: memory cleared but disk spill survives (J6).
    sm4 = session.SessionManager(spill_dir=d, idle_seconds=900, max_turns=3)
    sm4.record("u9", "/d/a/", "keep", "me")
    check("flush drops memory", sm4.flush_memory() >= 1
          and sm4.stats()["active"] == 0)
    check("flush keeps disk copy", len(sm4.history("u9", "/d/a/")) == 2)
    # Factory-reset purge: memory AND disk spill wiped (J6).
    sm4.purge()
    check("purge clears memory", sm4.stats()["active"] == 0)
    sm5 = session.SessionManager(spill_dir=d, idle_seconds=900, max_turns=3)
    check("purge wiped disk spill", sm5.history("u9", "/d/a/") == []
          and sm5.history("u1", "/d/a/") == [])


def test_jobstore_unit():
    """Persistent jobs survive reload, remain scope-isolated and are reusable."""
    sys.path.insert(0, HERE)
    import jobstore  # noqa: E402

    directory = tempfile.mkdtemp(prefix="vivesec-jobs-")
    store = jobstore.JobStore(directory, retention_days=7, max_per_user=2)
    store.create("j1", "u1", "/d/a", {"query": "Q1", "action": "report"})
    check("job starts queued", store.get("u1", "/d/a", "j1")["status"] == "queued")
    check("job hidden from other user", store.get("u2", "/d/a", "j1") is None)
    check("job hidden from other drive", store.get("u1", "/d/b", "j1") is None)
    store.start("u1", "/d/a", "j1")
    store.finish("u1", "/d/a", "j1", 200, {"ok": True, "answer": "A1"})
    first = store.get("u1", "/d/a", "j1")
    second = store.get("u1", "/d/a", "j1")
    check("completed job is idempotent", first == second and second["result"]["answer"] == "A1")
    store.create("j2", "u1", "/d/a", {"query": "Q2"})
    store.start("u1", "/d/a", "j2")
    reloaded = jobstore.JobStore(directory, retention_days=7, max_per_user=2)
    check("unfinished job becomes interrupted after restart",
          reloaded.get("u1", "/d/a", "j2")["status"] == "interrupted")
    check("completed job survives restart", reloaded.get("u1", "/d/a", "j1")["status"] == "done")
    reloaded.mark_seen("u1", "/d/a", "j1")
    check("seen marker persists", reloaded.get("u1", "/d/a", "j1")["seen_ts"] is not None)
    reloaded.create("j3", "u1", "/d/b", {"query": "Q3"})
    check("per-user retention removes oldest job", reloaded.get("u1", "/d/a", "j1") is None)
    check("job purge removes retained files", reloaded.purge() == 2
          and reloaded.list("u1", "/d/a") == [])


def test_discovery_unit():
    """Unit-test the SSDP responder message builders and parser (no real
    multicast socket needed): M-SEARCH matching, the 200 reply + NOTIFY shape,
    request parsing, and env wiring (J6, spec sec 2.5)."""
    sys.path.insert(0, HERE)
    import discovery  # noqa: E402

    st = "urn:vivesecbox-com:device:AIBox:1"
    r = discovery.SsdpResponder(
        st=st,
        usn="uuid:abc::" + st,
        location="http://10.0.0.5:8088/api/v1/status")
    # M-SEARCH ST matching.
    check("ssdp matches own ST", r.matches(st))
    check("ssdp matches ssdp:all", r.matches("ssdp:all"))
    check("ssdp matches rootdevice", r.matches("upnp:rootdevice"))
    check("ssdp ignores other ST",
          not r.matches("urn:schemas-upnp-org:device:MediaServer:1"))
    check("ssdp ignores empty ST", not r.matches(""))
    # Unicast 200 reply shape.
    resp = r.build_search_response().decode("utf-8")
    check("ssdp resp 200", resp.startswith("HTTP/1.1 200 OK"), resp[:30])
    check("ssdp resp has LOCATION",
          "LOCATION: http://10.0.0.5:8088/api/v1/status" in resp)
    check("ssdp resp has ST", "ST: " + st in resp)
    check("ssdp resp has rootdevice USN", "USN: uuid:abc::upnp:rootdevice" in resp)
    check("ssdp resp echoes asked ST",
          "ST: ssdp:all" in r.build_search_response("ssdp:all").decode("utf-8"))
    check("ssdp resp crlf-terminated", resp.endswith("\r\n\r\n"))
    # NOTIFY announcement shape.
    alive = r.build_notify("ssdp:alive").decode("utf-8")
    check("ssdp notify line", alive.startswith("NOTIFY * HTTP/1.1"))
    check("ssdp notify NTS alive", "NTS: ssdp:alive" in alive)
    check("ssdp notify NT", "NT: " + st in alive)
    check("ssdp notify USN device-type", "USN: uuid:abc::" + st in alive)
    # Request parsing.
    msearch = (b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
               b'MAN: "ssdp:discover"\r\nST: ssdp:all\r\nMX: 2\r\n\r\n')
    method, headers = discovery.SsdpResponder.parse_request(msearch)
    check("ssdp parse method", method == "M-SEARCH", method)
    check("ssdp parse ST header", headers.get("st") == "ssdp:all", headers)
    check("ssdp parse junk not M-SEARCH",
          discovery.SsdpResponder.parse_request(b"\x00\x01")[0] != "M-SEARCH")
    # Env wiring.
    check("ssdp env off disables",
          discovery.from_env({"ADAPTER_DISCOVERY": "off"}) is None)
    on = discovery.from_env({"ADAPTER_DISCOVERY": "on",
                             "ADAPTER_DISCOVERY_PORT": "8088",
                             "ADAPTER_DISCOVERY_LOCATION": "http://x:8088/api/v1/status"})
    check("ssdp env on builds responder",
          on is not None and on.location == "http://x:8088/api/v1/status", on)
    check("ssdp env uses ViVeTech ST", on is not None and on.st == st, on)

    dynamic = discovery.from_env({"ADAPTER_DISCOVERY": "on",
                                  "ADAPTER_DISCOVERY_PORT": "8088"})
    original_local_ip = discovery.local_ip
    try:
        discovery.local_ip = lambda default="127.0.0.1": "10.1.2.3"
        resolved = dynamic.build_search_response().decode("utf-8")
    finally:
        discovery.local_ip = original_local_ip
    check("ssdp resolves LAN address per message",
          "LOCATION: http://10.1.2.3:8088/api/v1/status" in resolved, resolved[:200])

    class OneShotSocket:
        def sendto(self, _data, _address):
            pass

        def recvfrom(self, _size):
            raise OSError("done")

        def close(self):
            pass

    attempts = []

    def flaky_socket_factory():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError(19, "No such device")
        return OneShotSocket()

    retrying = discovery.SsdpResponder(sock_factory=flaky_socket_factory)
    retrying.run(retry_interval=0)
    check("ssdp retries boot-time socket failure", len(attempts) == 2, attempts)


def test_factory_reset_unit():
    """Unit-test the physical reset-pin state machine with a fake pin reader:
    a sustained hold fires once, a still-held pin does not refire, and a release
    re-arms it. Plus the env-driven reader selection (J6, spec sec 1)."""
    sys.path.insert(0, HERE)
    import factory_reset  # noqa: E402

    pressed = {"v": False}
    fired = {"n": 0}
    mon = factory_reset.FactoryResetMonitor(
        pin_reader=lambda: pressed["v"],
        on_reset=lambda: fired.__setitem__("n", fired["n"] + 1),
        hold_seconds=5.0)
    t = 1000.0
    check("reset idle no fire", mon.poll_once(now=t) is False and fired["n"] == 0)
    pressed["v"] = True
    check("reset press-start no fire", mon.poll_once(now=t + 0.5) is False)
    check("reset short hold no fire",
          mon.poll_once(now=t + 3.0) is False and fired["n"] == 0)
    check("reset long hold fires",
          mon.poll_once(now=t + 5.5) is True and fired["n"] == 1)
    check("reset still-held no refire",
          mon.poll_once(now=t + 9.0) is False and fired["n"] == 1)
    pressed["v"] = False
    check("reset release re-arms", mon.poll_once(now=t + 10.0) is False)
    pressed["v"] = True
    check("reset re-press no immediate", mon.poll_once(now=t + 10.5) is False)
    check("reset second hold fires again",
          mon.poll_once(now=t + 16.0) is True and fired["n"] == 2)
    # Env reader selection.
    _, off_reader = factory_reset.make_reader_from_env({"ADAPTER_RESET_PIN_MODE": "off"})
    check("reset env off -> no reader", off_reader is None)
    fd = tempfile.mkdtemp(prefix="vivesec-reset-")
    sentinel = os.path.join(fd, "RESET")
    _, freader = factory_reset.make_reader_from_env(
        {"ADAPTER_RESET_PIN_MODE": "file", "ADAPTER_RESET_PIN_FILE": sentinel})
    check("reset file reader absent -> released", freader() is False)
    open(sentinel, "w").close()
    check("reset file reader present -> pressed", freader() is True)


def test_provision_reset_unit():
    """Unit-test the de-provision (factory reset) PKI wipe: after reset the box
    reports un-initialized and the cert files are gone (J6, spec sec 1)."""
    sys.path.insert(0, HERE)
    import provision  # noqa: E402

    d = tempfile.mkdtemp(prefix="vivesec-prov-reset-")
    p = provision.Provisioner(d)
    for name in ("initialized", "server.key", "server.crt", "ca.crt", "client.crt"):
        with open(os.path.join(d, name), "w") as f:
            f.write("x")
    check("provision initialized before reset", p.is_initialized() is True)
    removed = p.reset()
    check("provision reset removes files", removed >= 5, removed)
    check("provision not initialized after reset", p.is_initialized() is False)
    check("provision reset idempotent", p.reset() == 0)


def test_mirror_clear_unit():
    """Unit-test the metadata mirror clear (factory reset wipes the diff-sync
    mirror; J6)."""
    sys.path.insert(0, HERE)
    from meta import MetaMirror  # noqa: E402

    m = MetaMirror()
    m.upsert("/storage/drives/a/f1.txt", True, 1, 10)
    m.upsert("/storage/drives/a", False, 0, 0)
    check("mirror has docs", m.stats()["documents"] == 2, m.stats())
    removed = m.clear()
    check("mirror clear removes all", removed == 2 and m.stats()["documents"] == 0, removed)


def test_content_type_unit():
    """Unit-test the content_type resolver: a supplied value wins, else the
    extension, else a magic-byte sniff of the head sample, else octet-stream."""
    sys.path.insert(0, HERE)
    import service  # noqa: E402

    def b64(b):
        return base64.b64encode(b).decode("ascii")

    # 1. A ViVeSecBox-supplied content_type is authoritative.
    check("ct supplied wins",
          service.resolve_content_type("/d/a/x.bin", b64(b"%PDF-1.7"), "application/json")
          == "application/json")
    # 2. Extension-based when the box sends nothing.
    check("ct from .pdf ext",
          service.resolve_content_type("/d/a/report.pdf", "", None) == "application/pdf")
    check("ct from .md ext",
          service.resolve_content_type("/d/a/notes.md", "", None) == "text/markdown")
    check("ct from .docx ext",
          service.resolve_content_type("/d/a/q4.docx", "", None).endswith("wordprocessingml.document"))
    # 3. Magic-byte sniff for an extensionless file.
    check("ct sniff pdf head",
          service.resolve_content_type("/d/a/blob", b64(b"%PDF-1.4 trailer"), None) == "application/pdf")
    check("ct sniff text head",
          service.resolve_content_type("/d/a/blob", b64(b"plain words here"), None) == "text/plain")
    check("ct sniff binary head",
          service.resolve_content_type("/d/a/blob", b64(b"\x00\x01\x02bin"), None) == "application/octet-stream")
    # 4. Nothing usable anywhere -> octet-stream.
    check("ct fallback octet-stream",
          service.resolve_content_type("/d/a/blob", "", None) == "application/octet-stream")


def test_confidence_unit():
    """Unit-test the confidence scorer (function spec v2): CR components,
    CG proxies, band mapping and the hallucination-suppress rule."""
    sys.path.insert(0, HERE)
    import confidence  # noqa: E402
    import time as _t

    now = _t.time()

    def ctx(score, path="/d/f/a.txt", mtime=None, text="revenue was 42"):
        return {"score": score, "source_path": path, "text": text,
                "metadata": {"mtime": mtime}}

    # perfect case: high sim + 1 file + fresh + grounded number + valid cite
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "The revenue was 42 [#1].", question="revenue?", now=now)
    check("conf perfect -> 100/green",
          c["score"] == 100 and c["band"] == "green" and not c["suppress"], c)

    # ungrounded number -> adherence 0 -> suppress
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "The revenue was 999 [#1].", question="revenue?", now=now)
    check("conf hallucination -> suppress",
          c["suppress"] and c["components"]["context_adherence"]["points"] == 0, c)

    # no citations -> density 0 (but no suppress)
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "The revenue was 42.", question="revenue?", now=now)
    check("conf no citation -> density 0",
          c["components"]["citation_density"]["points"] == 0 and not c["suppress"], c)

    # generation off (answer None): CR-only, CG excluded
    c = confidence.score([ctx(0.9, mtime=now - 86400)], None, now=now)
    check("conf no answer -> CR-only",
          c["max_achievable"] == 50 and "context_adherence" not in c["components"], c)

    # missing mtime -> temporal component excluded (max 0)
    c = confidence.score([ctx(0.9)], None, now=now)
    check("conf no mtime -> temporal excluded",
          c["components"]["temporal_relevance"]["max"] == 0
          and c["max_achievable"] == 40, c)

    # weak retrieval: low sim + 3 files -> red
    ctxs = [ctx(0.5, path="/d/f/%d.txt" % i) for i in range(3)]
    c = confidence.score(ctxs, None, now=now)
    check("conf weak retrieval -> red", c["band"] == "red", c)

    # old mtime -> 5/10
    c = confidence.score([ctx(0.9, mtime=now - 400 * 86400)], None, now=now)
    check("conf old doc -> temporal 5",
          c["components"]["temporal_relevance"]["points"] == 5, c)

    # structural numbering (task format apparatus) is NOT a hallucination:
    # numbered sections + "Slide N:" pass adherence when the facts are grounded
    outline = ("1. Presentation cover — Q4 results [#1]\n"
               "2. Structure [#1]\n"
               "Slide 1: Revenue | was 42 [#1]\n"
               "Slide 2: Outlook | steady [#1]")
    c = confidence.score([ctx(0.9, mtime=now - 86400)], outline,
                         question="presentation", now=now)
    check("conf enum+slide numbering not suppressed",
          not c["suppress"]
          and c["components"]["context_adherence"]["points"] == 25, c)
    # ...but a fabricated FACT number inside a numbered line still suppresses
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "1. Revenue was 999 [#1]", question="revenue?", now=now)
    check("conf fabricated fact in enum line still suppressed",
          c["suppress"], c)

    # a long synthesis answer must not be thrown away over ONE stray figure
    # (a reformatted date used to zero adherence and suppress everything)
    grounded = ctx(0.9, mtime=now - 86400,
                   text="1 2 3 4 5 6 7 8 revenue was 42")
    many = " ".join("item %d [#1]" % n for n in range(1, 9))
    c = confidence.score([grounded], many + " and 999 [#1]",
                         question="report?", now=now)
    check("conf one stray figure among many -> reduced, not suppressed",
          not c["suppress"]
          and c["components"]["context_adherence"]["points"] == 10
          and c["components"]["context_adherence"]["ungrounded"] == ["999"], c)
    check("conf reduced adherence still flagged as degraded",
          "context_adherence" in c["degraded"], c)
    # too many strays is fabrication again (separated by words: the number
    # regex deliberately merges digit runs split only by spaces/punctuation)
    c = confidence.score([grounded],
                         many + " and 991 or 992 or 993 or 994 [#1]",
                         question="report?", now=now)
    check("conf many stray figures -> suppressed", c["suppress"], c)
    # short answers keep the strict rule
    c = confidence.score([grounded], "1 2 3 999 [#1]", question="q?", now=now)
    check("conf short answer with a stray figure -> suppressed", c["suppress"], c)
    # citation apparatus is grounded by construction: a date that appears only
    # in the SOURCE FILENAME (part of the [#n] tag) is quotable
    c = confidence.score(
        [{"score": 0.9, "source_path": "/d/f/Transcript_2025-11-12.txt",
          "text": "the board approved the plan",
          "metadata": {"mtime": now - 86400}}],
        "Date: 2025-11-12 — the board approved the plan [#1]",
        question="when?", now=now)
    check("conf filename date grounded",
          not c["suppress"]
          and c["components"]["context_adherence"]["points"] == 25, c)

    # C6 — token-probability approximation (max 10, approximated flag)
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "The revenue was 42 [#1].", question="revenue?", now=now)
    tp = c["components"]["token_probability"]
    check("conf token-prob full on overlap",
          tp["points"] == 10 and tp["max"] == 10 and tp["approximated"], c)
    check("conf perfect still 100/green with token-prob",
          c["score"] == 100 and c["band"] == "green", c)
    c = confidence.score([ctx(0.9, mtime=now - 86400)],
                         "Quantum blockchain synergy paradigm revolution "
                         "disruption metaverse [#1]",
                         question="revenue?", now=now)
    check("conf token-prob 0 on foreign vocabulary",
          c["components"]["token_probability"]["points"] == 0, c)
    # degraded metric list flags every non-max component
    check("conf degraded lists token_probability",
          "token_probability" in c["degraded"], c)

    # C6 — band messages + standard refusal + audit id + footer
    check("band message hu default",
          confidence.band_message("green") ==
          "Biztos válasz a vállalati dokumentáció alapján.")
    check("band message en on English",
          "verify sources" in confidence.band_message("amber", "English"))
    check("standard refusal hu spelled per spec",
          confidence.standard_refusal("Hungarian").endswith(
              "megnyugtató pontossággal."))
    check("standard refusal en",
          confidence.standard_refusal("English").startswith("System message"))
    check("standard refusal da",
          confidence.standard_refusal("Danish").startswith("Systembesked"))
    check("standard refusal de",
          confidence.standard_refusal("German").startswith("Systemmeldung"))
    check("band message da",
          "kilderne" in confidence.band_message("amber", "Danish"))
    check("band message de",
          "Quellen" in confidence.band_message("amber", "German"))
    a1 = confidence.audit_id("u", "/d/f", "q", "a", now=1234567890)
    check("audit id deterministic",
          a1 == confidence.audit_id("u", "/d/f", "q", "a", now=1234567890)
          and len(a1) == 12, a1)
    check("audit id varies by input",
          a1 != confidence.audit_id("u2", "/d/f", "q", "a", now=1234567890))
    conf_ok = confidence.score([ctx(0.9, mtime=now - 86400)],
                               "The revenue was 42 [#1].", question="revenue?",
                               now=now)
    ftr = confidence.footer(conf_ok,
                            [{"ref": 1, "path": "/d/f/a.txt", "page_number": 2}],
                            "abc123", "English")
    check("footer has audit block",
          "**Adatkontroll & Audit Info:**" in ftr and "abc123" in ftr, ftr)
    check("footer lists source with page", "a.txt, p.2" in ftr, ftr)
    check("footer score matches",
          ("**Confidence Score:** %d%%" % conf_ok["score"]) in ftr, ftr)


def test_chat_unit():
    """Unit-test the conversational mode: follow-up condensing (rewrite vs
    CHAT_ONLY vs fail-open), the chat-only generate branch, the history-aware
    guard, and history-aware confidence scoring."""
    sys.path.insert(0, HERE)
    import confidence  # noqa: E402
    import llm  # noqa: E402

    hist = [{"role": "user", "content": "What was the Q2 2026 revenue?"},
            {"role": "assistant", "content": "Revenue was 14.7 M EUR [#1]."}]

    # -- condense ------------------------------------------------------------
    check("condense no history passthrough",
          llm.condense("and Q1?", [], "English") == ("and Q1?", False))
    check("condense empty question passthrough",
          llm.condense("  ", hist, "English") == ("  ", False))
    old_chat, old_up, old_flag = llm._chat, llm._ollama_up, llm.CHAT
    try:
        llm.CHAT = False
        check("condense switched off passthrough",
              llm.condense("and Q1?", hist, "English") == ("and Q1?", False))
        llm.CHAT = True
        llm._ollama_up = lambda *a, **k: True
        seen = {}

        def fake_chat(system, user, history=None, timeout=600, num_ctx=None):
            seen["system"] = system
            seen["user"] = user
            seen["history"] = history
            return seen.pop("reply", ""), None, None

        llm._chat = fake_chat
        seen["reply"] = "What was the Q1 2026 revenue?"
        check("condense rewrites follow-up",
              llm.condense("and Q1?", hist, "English")
              == ("What was the Q1 2026 revenue?", False))
        check("condense prompt carries the conversation",
              "ASSISTANT: Revenue was 14.7 M EUR" in seen["user"]
              and "FOLLOW-UP MESSAGE: and Q1?" in seen["user"], seen["user"])
        check("condense itself is single-shot (no history param)",
              seen["history"] is None)
        seen["reply"] = '"What was the Q1 2026 revenue?"'
        check("condense strips wrapping quotes",
              llm.condense("and Q1?", hist, "English")[0]
              == "What was the Q1 2026 revenue?")
        seen["reply"] = "What was the Q1 2026 revenue?\nExplanation: resolved."
        check("condense keeps first line only",
              llm.condense("and Q1?", hist, "English")[0]
              == "What was the Q1 2026 revenue?")
        seen["reply"] = "CHAT_ONLY"
        check("condense detects chat-only turn",
              llm.condense("summarize what you said", hist, "English")
              == ("summarize what you said", True))
        seen["reply"] = ""
        check("condense empty reply fails open",
              llm.condense("and Q1?", hist, "English") == ("and Q1?", False))

        def boom(*a, **k):
            raise OSError("down")

        llm._chat = boom
        check("condense error fails open",
              llm.condense("and Q1?", hist, "English") == ("and Q1?", False))

        # -- generate: chat-only branch --------------------------------------
        llm._chat = fake_chat
        seen["reply"] = "So far we covered Q2 revenue: 14.7 M EUR [#1]."
        ans, backend = llm.generate("summarize what you said", [],
                                    lang="English", history=hist,
                                    chat_only=True)
        check("generate chat-only answers from history",
              ans == "So far we covered Q2 revenue: 14.7 M EUR [#1]."
              and "(chat)" in backend, (ans, backend))
        check("generate chat-only passes the history", seen["history"] == hist)
        check("generate chat-only guard forbids new facts",
              "Do not introduce any fact" in seen["system"], seen["system"])
        check("generate chat-only has no CONTEXT block",
              "CONTEXT:" not in seen["user"], seen["user"])
        # chat_only without history stays a deterministic refusal
        ans, backend = llm.generate("summarize", [], lang="English",
                                    history=[], chat_only=True)
        check("generate chat-only without history refuses",
              backend == "no-context" and ans == llm.refusal("English"),
              (ans, backend))
        # plain empty-context turn is untouched by the feature
        ans, backend = llm.generate("q?", [], lang="English", history=hist)
        check("generate empty context still refuses",
              backend == "no-context", (ans, backend))
        # grounded turn with history: guard allows reusing earlier answers
        seen["reply"] = "Grounded [#1]."
        llm.generate("q?", [{"source_path": "/d/a.txt", "text": "fact 42"}],
                     lang="English", history=hist)
        check("guard mentions earlier answers when history exists",
              "your OWN earlier answers" in seen["system"]
              and "neither in CONTEXT nor in your earlier answers"
              in seen["system"], seen["system"])

        # -- agent personas ---------------------------------------------------
        check("default agent is operations",
              llm.DEFAULT_AGENT == "operations"
              and "Operations Assistant" in llm.persona_for(), llm.DEFAULT_AGENT)
        check("unknown agent falls back to the generic persona",
              llm.persona_for("legal") == llm.PERSONA)
        check("agent id is case/space tolerant",
              llm.persona_for("  Operations ") == llm.PERSONAS["operations"])
        check("persona never loosens grounding (rules appended intact)",
              "You answer strictly from the retrieved context"
              in llm.PERSONAS["operations"])
        seen["reply"] = "Grounded [#1]."
        llm.generate("q?", [{"source_path": "/d/a.txt", "text": "fact 42"}],
                     lang="English")
        check("generate uses the operations persona by default",
              seen["system"].startswith(llm.PERSONAS["operations"])
              and "STRICT RULES:" in seen["system"], seen["system"][:120])
        seen["reply"] = "Grounded [#1]."
        llm.generate("q?", [{"source_path": "/d/a.txt", "text": "fact 42"}],
                     lang="English", agent="legal")
        check("generate with unknown agent uses the generic persona",
              seen["system"].startswith(llm.PERSONA), seen["system"][:120])
        seen["reply"] = "From the chat [#1]."
        llm.generate("summarize", [], lang="English", history=hist,
                     chat_only=True)
        check("chat-only branch carries the persona too",
              seen["system"].startswith(llm.PERSONAS["operations"]),
              seen["system"][:120])
    finally:
        llm._chat, llm._ollama_up, llm.CHAT = old_chat, old_up, old_flag
    check("guard unchanged without history",
          "earlier answers" not in llm._build_guard("English"))

    # -- confidence: history-aware grounding ----------------------------------
    ctxs = [{"score": 0.9, "source_path": "/d/a.txt", "text": "margin was 31",
             "metadata": {}}]
    c = confidence.score(ctxs, "Earlier I said 14.7 [#1], margin was 31 [#1].",
                         question="and the margin?", history=hist)
    check("conf history number grounds the answer",
          not c["suppress"]
          and c["components"]["context_adherence"]["points"] == 25, c)
    c = confidence.score(ctxs, "The value was 14.7 [#1].",
                         question="and the margin?")
    check("conf same number without history suppresses", c["suppress"], c)
    user_only = [{"role": "user", "content": "is it 77?"}]
    c = confidence.score(ctxs, "It was 77 [#1].", question="q?",
                         history=user_only)
    check("conf user turn is not a fact source", c["suppress"], c)

    # -- confidence: conversational (chat-only) scoring ------------------------
    c = confidence.score([], "You asked about revenue: 14.7 M EUR [#1].",
                         question="summarize what you said", history=hist,
                         conversational=True)
    check("conf conversational excludes retrieval components",
          "vector_similarity" not in c["components"]
          and "source_diversity" not in c["components"]
          and "citation_density" not in c["components"], c)
    check("conf conversational max is adherence+token",
          c["max_achievable"] == 35, c)
    check("conf conversational grounded by history",
          not c["suppress"]
          and c["components"]["context_adherence"]["points"] == 25, c)
    c = confidence.score([], "The answer is 999.", question="summarize",
                         history=hist, conversational=True)
    check("conf conversational new number still suppresses", c["suppress"], c)


def test_action_parse_unit():
    """Unit-test the quick-action parser + the mirror filename lookup."""
    sys.path.insert(0, HERE)
    import llm  # noqa: E402
    import service  # noqa: E402
    from meta import MetaMirror  # noqa: E402

    check("action plain query untouched",
          service.parse_action({}, "what is x") == (None, None, "what is x"))
    check("action #search text",
          service.parse_action({}, "#search what is x") == ("search", "text", "what is x"))
    check("action #search files",
          service.parse_action({}, "#search files: budget") == ("search", "files", "budget"))
    check("action #SEARCH case-insensitive",
          service.parse_action({}, "#SEARCH x") == ("search", "text", "x"))
    check("action explicit payload wins",
          service.parse_action({"action": "search", "mode": "files"}, "budget")
          == ("search", "files", "budget"))
    check("action unknown prefix ignored",
          service.parse_action({}, "#frobnicate x") == (None, None, "#frobnicate x"))
    check("action #summary parsed, no mode",
          service.parse_action({}, "#summary board meeting")
          == ("summary", None, "board meeting"))
    check("action summary explicit payload",
          service.parse_action({"action": "summary"}, "weekly sync")
          == ("summary", None, "weekly sync"))
    # C3 — the report family (F2 weekly report + F6 project tracking)
    check("action #report parsed, no mode",
          service.parse_action({}, "#report last week")
          == ("report", None, "last week"))
    check("action #tracking parsed, no mode",
          service.parse_action({}, "#tracking apollo project")
          == ("tracking", None, "apollo project"))
    check("action tracking explicit payload",
          service.parse_action({"action": "tracking"}, "apollo")
          == ("tracking", None, "apollo"))
    # C4 — the generation family (F3 presentation + F5 memo)
    check("action #presentation parsed, no mode",
          service.parse_action({}, "#presentation Q4 results")
          == ("presentation", None, "Q4 results"))
    check("action #memo parsed, no mode",
          service.parse_action({}, "#memo vendor selection")
          == ("memo", None, "vendor selection"))
    check("action memo explicit payload",
          service.parse_action({"action": "memo"}, "expansion")
          == ("memo", None, "expansion"))
    check("bare-query seeds cover synthesis actions",
          set(service._BARE_QUERY)
          == {"summary", "report", "tracking", "presentation", "memo"}
          and all(service._BARE_QUERY.values()), service._BARE_QUERY)
    check("default top_k widened for synthesis actions",
          service._DEFAULT_TOP_K["report"] >= 8
          and service._DEFAULT_TOP_K["tracking"] >= 8
          and service._DEFAULT_TOP_K["presentation"] >= 8
          and service._DEFAULT_TOP_K["memo"] >= 8
          and service._DEFAULT_TOP_K["summary"] == 8, service._DEFAULT_TOP_K)
    check("task instructions exist for every synthesis action",
          all(a in llm.TASK_INSTRUCTIONS
              for a in ("summary", "report", "tracking", "presentation", "memo")),
          sorted(llm.TASK_INSTRUCTIONS))
    check("param keys cover the quick-action dialog answers",
          set(service._PARAM_KEYS) == {"audience", "purpose", "coverage",
                                       "report_type", "aspect", "keywords",
                                       "outcome", "situation", "extra"},
          service._PARAM_KEYS)
    # trailing-refusal strip (structured tasks): appended refusal removed,
    # pure refusal answer left intact
    ref = llm.refusal("English")
    check("trailing refusal stripped",
          llm._strip_trailing_refusal("Report body [#1]\n\n" + ref, "English")
          == "Report body [#1]")
    check("pure refusal kept",
          llm._strip_trailing_refusal(ref, "English") == ref)
    check("double refusal stripped",
          llm._strip_trailing_refusal("Body [#1]\n" + ref + "\n" + ref, "English")
          == "Body [#1]")

    # question-language detection (the UI never sends lang)
    check("detect hu", llm.detect_lang(
        "Mennyi a napidíj Magyarországon és Dániában?") == "Hungarian")
    check("detect hu unique letter", llm.detect_lang(
        "Melyik telephelyen volt a legtöbb beavatkozás?") == "Hungarian")
    check("detect en", llm.detect_lang(
        "What was the revenue in Q2 2026?") == "English")
    check("detect de", llm.detect_lang(
        "Wie hoch war der Umsatz im zweiten Quartal?") == "German")
    check("detect da", llm.detect_lang(
        "Hvor mange medarbejdere har Voltara i alt?") == "Danish")
    check("detect da unique letter", llm.detect_lang(
        "Hvilket anlæg havde flest indgreb?") == "Danish")
    check("detect empty is neutral", llm.detect_lang("") == "")
    check("detect numbers-only is neutral", llm.detect_lang("42 99.79") == "")

    m = MetaMirror()
    m.upsert("/d/fin", file=False, mtime=None, size=None)
    m.upsert("/d/fin/budget_hr.txt", file=True, mtime=1, size=10)
    m.upsert("/d/fin/budget_it.txt", file=True, mtime=2, size=20)
    m.upsert("/d/fin/report.txt", file=True, mtime=3, size=30)
    m.upsert("/d/other/budget_x.txt", file=True, mtime=4, size=40)
    hits = m.find("/d/fin", "budget")
    check("mirror find scoped+matched",
          [h["path"] for h in hits] == ["/d/fin/budget_hr.txt", "/d/fin/budget_it.txt"], hits)
    check("mirror find empty pattern lists all files",
          len(m.find("/d/fin", "")) == 3, m.find("/d/fin", ""))
    check("mirror find limit", len(m.find("/d/fin", "", limit=2)) == 2)


def test_wsfs_unit():
    """Unit-test the RFC6455 codec, the sec 1 line protocol and the put-file
    retry semantics (no sockets)."""
    import io
    sys.path.insert(0, HERE)
    import filestore  # noqa: E402
    import wsfs  # noqa: E402

    # RFC6455 known test vector
    check("ws accept key rfc vector",
          wsfs.accept_key("dGhlIHNhbXBsZSBub25jZQ==")
          == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    # frame roundtrip: unmasked + masked, all three length encodings
    for n in (5, 300, 70000):
        payload = bytes(i % 251 for i in range(n))
        for mask in (False, True):
            fin, op, out = wsfs.read_frame(
                io.BytesIO(wsfs.encode_frame(wsfs.OP_BINARY, payload, mask=mask)))
            check("ws frame roundtrip n=%d mask=%s" % (n, mask),
                  fin and op == wsfs.OP_BINARY and out == payload)

    # message build/parse: header only, header+blob, junk header
    hdr, blob = wsfs.parse_message(wsfs.build_message({"type": "keepalive", "num": 1}))
    check("ws msg header only", hdr == {"type": "keepalive", "num": 1} and blob == b"")
    hdr, blob = wsfs.parse_message(
        wsfs.build_message({"type": "put-file", "num": 2, "name": "a.txt"},
                           b"\x00binary\nwith newline"))
    check("ws msg header+blob",
          hdr and hdr["name"] == "a.txt" and blob == b"\x00binary\nwith newline")
    hdr, _ = wsfs.parse_message(b"not json\nblob")
    check("ws msg junk header -> None", hdr is None)

    # put-file retry: 'temporary' retried with delay, then success; the
    # permission error is returned immediately (no retry).
    ch = wsfs.FsChannel(io.BytesIO(), io.BytesIO())
    replies = [{"error": "temporary"}, {"error": "temporary"}, {"path": "V/x.txt"}]
    calls = []
    ch.request = lambda h, blob=None, timeout=0: (calls.append(h), (replies[len(calls) - 1], b""))[1]
    naps = []
    out = ch.put_file("u", "/d/f", "x.txt", b"data", retries=3,
                      retry_delay=0.5, sleep=naps.append)
    check("ws put-file retries temporary then succeeds",
          out == {"path": "V/x.txt"} and len(calls) == 3 and naps == [0.5, 0.5], (calls, naps))
    calls.clear()
    replies[:] = [{"error": "permission"}]
    out = ch.put_file("u", "/d/f", "x.txt", b"data", retries=3, sleep=naps.append)
    check("ws put-file permission not retried",
          out == {"error": "permission"} and len(calls) == 1, (out, calls))
    replies[:] = [{"error": "temporary"}] * 3
    calls.clear()
    out = ch.put_file("u", "/d/f", "x.txt", b"data", retries=3,
                      retry_delay=0, sleep=lambda s: None)
    check("ws put-file exhausts retries -> temporary",
          out == {"error": "temporary"} and len(calls) == 3, (out, calls))

    # get-file: the reply BODY is the whole point of the read, and the ack
    # handler used to drop it -- a regression here would silently return an
    # empty document.
    ch = wsfs.FsChannel(io.BytesIO(), io.BytesIO())
    slot = {"event": threading.Event(), "reply": None, "blob": b""}
    ch._pending[7] = slot
    ch._resolve_ack({"ack": 7, "path": "/storage/drives/finance/a.pdf"}, b"%PDF-1.4")
    check("ws ack carries the payload",
          slot["blob"] == b"%PDF-1.4" and slot["event"].is_set(), slot)

    ch = wsfs.FsChannel(io.BytesIO(), io.BytesIO())
    ch.request = lambda h, blob=None, timeout=0: ({"path": h["path"], "size": 4}, b"DATA")
    hdr, body = ch.get_file("u", "/storage/drives/finance/a.pdf")
    check("ws get-file returns the content",
          body == b"DATA" and hdr.get("size") == 4, (hdr, body))
    ch.request = lambda h, blob=None, timeout=0: ({"error": "not-found"}, b"")
    hdr, body = ch.get_file("u", "/storage/drives/finance/missing.pdf")
    check("ws get-file error carries no content",
          hdr.get("error") == "not-found" and body == b"", (hdr, body))

    # The ViVeSecBox tunnel forwards the request path but NOT the query string,
    # so an embedded UI can only name the file in a body: both entry points have
    # to exist, or the viewer works over IP and fails inside the box.
    import service  # noqa: E402

    check("drive file endpoint has a POST twin",
          hasattr(service.Handler, "_drive_file_post"))
    check("drive file query parser reads the path",
          service.Handler._drive_file_path_from_query(None, "path=%2Fa%2Fb+c.txt")
          == "/a/b c.txt")
    check("drive file query parser tolerates a missing query",
          service.Handler._drive_file_path_from_query(None, "") == "")

    # Which of body/query their tunnel preserves is its own business, so the
    # POST route must resolve either way -- and a query must never take part in
    # route matching, or POST /ui/file?path=... would 404.
    seen = []

    class _PostProbe(object):
        _read_json = lambda self: self._body  # noqa: E731
        _drive_file = lambda self, p: seen.append(p)  # noqa: E731
        _drive_file_path_from_query = service.Handler._drive_file_path_from_query
        _drive_file_post = service.Handler._drive_file_post

    p = _PostProbe()
    p._body = {"path": "/storage/drives/finance/a.pdf"}
    p._post_query = ""
    p._drive_file_post()
    p._body = {}
    p._post_query = "path=%2Fstorage%2Fdrives%2Ffinance%2Fb.pdf"
    p._drive_file_post()
    p._body = {"path": "/storage/drives/finance/c.pdf"}
    p._post_query = "path=%2Fignored.pdf"
    p._drive_file_post()
    check("drive file POST reads the body, falls back to the query, body wins",
          seen == ["/storage/drives/finance/a.pdf",
                   "/storage/drives/finance/b.pdf",
                   "/storage/drives/finance/c.pdf"], seen)

    # session file store: sanitization + isolation + purge
    check("filestore traversal stripped",
          filestore.safe_name("../../etc/passwd") == "passwd")
    check("filestore backslash path stripped",
          filestore.safe_name("a\\b\\c.txt") == "c.txt")
    check("filestore dotfile stripped", filestore.safe_name(".hidden") == "hidden")
    check("filestore empty -> unnamed", filestore.safe_name("  ") == "unnamed")
    froot = tempfile.mkdtemp(prefix="vivesec-files-")
    fs = filestore.FileStore(froot)
    name = fs.save("u1", "/d/finance/", "memo.md", b"# hello")
    check("filestore save+get", fs.get("u1", "/d/finance/", name) == b"# hello")
    listed = fs.list("u1", "/d/finance/")
    check("filestore listed",
          len(listed) == 1 and listed[0]["name"] == "memo.md"
          and listed[0]["size"] == 7 and isinstance(listed[0]["mtime"], int), listed)
    check("filestore session isolation",
          fs.list("u1", "/d/hr/") == [] and fs.get("u2", "/d/finance/", name) is None)
    check("filestore delete", fs.delete("u1", "/d/finance/", name) is True
          and fs.list("u1", "/d/finance/") == [])
    fs.save("u1", "/d/finance/", "a.txt", b"1")
    fs.save("u2", "/d/hr/", "b.txt", b"2")
    check("filestore purge", fs.purge() == 2 and fs.stats()["files"] == 0, fs.stats())


def ws_connect(host, port, path="/api/v1/ws/fs"):
    """Minimal ViVeSecBox-side ws client for the e2e test: HTTP upgrade, then
    (socket, buffered-reader). Frames are sent masked, as RFC6455 requires
    from clients."""
    s = socket.create_connection((host, port), timeout=15)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
           % (path, host, port, key))
    s.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("ws handshake failed: connection closed")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    if "101" not in head.split("\r\n")[0]:
        raise RuntimeError("ws handshake failed: %s" % head.splitlines()[0])
    return s, s.makefile("rb")


def test_thinking_budget_unit():
    """A reasoning model spends num_predict on `thinking` first. If the budget
    runs out there, Ollama returns an empty content with a full thinking field
    -- that must fail loudly, not surface as a confident blank answer."""
    sys.path.insert(0, HERE)
    import llm  # noqa: E402

    replies = {}

    class FakeResponse(object):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(replies["body"]).encode("utf-8")

    old_urlopen = llm.urllib.request.urlopen
    try:
        llm.urllib.request.urlopen = lambda *a, **k: FakeResponse()

        replies["body"] = {"message": {"content": "Revenue was 14.7 M EUR [#1]."},
                           "eval_count": 12, "eval_duration": 1000000}
        check("chat returns the answer when content is present",
              llm._chat("sys", "user")[0] == "Revenue was 14.7 M EUR [#1].")

        replies["body"] = {"message": {"content": "The answer is 14.7 [#1].",
                                       "thinking": "Let me check the context..."}}
        check("thinking alongside content is ignored, content wins",
              llm._chat("sys", "user")[0] == "The answer is 14.7 [#1].")

        replies["body"] = {"message": {"content": "",
                                       "thinking": "Let me work through this..."}}
        raised = None
        try:
            llm._chat("sys", "user")
        except llm.ThinkingBudgetExhausted as e:
            raised = str(e)
        check("reasoning-only reply raises instead of returning empty", bool(raised))
        check("the error names the knob to turn",
              raised is not None and "ADAPTER_NUM_PREDICT" in raised, raised)

        replies["body"] = {"message": {"content": ""}}
        check("a genuinely empty reply (no thinking) still returns empty",
              llm._chat("sys", "user")[0] == "")
    finally:
        llm.urllib.request.urlopen = old_urlopen


def main():
    test_storage_unit()
    test_provision_unit()
    test_provision_reset_unit()
    test_session_unit()
    test_discovery_unit()
    test_factory_reset_unit()
    test_mirror_clear_unit()
    test_content_type_unit()
    test_confidence_unit()
    test_chat_unit()
    test_thinking_budget_unit()
    test_action_parse_unit()
    test_wsfs_unit()
    tmp = tempfile.mkdtemp(prefix="vivesec-adapter-smoke-")

    # The integration half drives a stub RAG from the repo. Production images
    # ship only adapter/, so say so and exit clean instead of crashing — a
    # traceback here used to read as "tests ran" to the deploy script.
    rag_stub = os.path.join(ROOT, "rag_service", "service.py")
    if not os.path.exists(rag_stub):
        print("\n%d passed, %d failed" % (PASS, FAIL))
        print("INTEGRATION SKIPPED: %s not present (unit tests only)" % rag_stub)
        sys.exit(1 if FAIL else 0)

    rag_index = os.path.join(tmp, "rag_index.json")
    meta_path = os.path.join(tmp, "adapter_meta.json")
    rag_port = free_port()
    adapter_port = free_port()
    rag_base = "http://127.0.0.1:%d" % rag_port
    adapter_base = "http://127.0.0.1:%d" % adapter_port

    rag_env = dict(os.environ)
    rag_env.update({
        "RAG_HOST": "127.0.0.1", "RAG_PORT": str(rag_port),
        "RAG_INDEX_PATH": rag_index, "VIVESEC_BACKEND": "fallback",
    })
    adapter_env = dict(os.environ)
    adapter_env.update({
        "ADAPTER_HOST": "127.0.0.1", "ADAPTER_PORT": str(adapter_port),
        "RAG_URL": rag_base, "ADAPTER_META_PATH": meta_path,
        "ADAPTER_DRIVE_PREFIX": "/storage/drives",
        "ADAPTER_FEATURES": "accounting,hr",  # exercise the license feature flags
        "ADAPTER_GENERATE": "off",  # deterministic: don't depend on dev Ollama
      "ADAPTER_CHAT_POLICY": "locked_grounded",
        "ADAPTER_FILES_DIR": os.path.join(tmp, "generated"),
        "ADAPTER_FEEDBACK_DIR": os.path.join(tmp, "feedback"),
      "ADAPTER_JOBS_DIR": os.path.join(tmp, "jobs"),
        "ADAPTER_PUTFILE_RETRY_DELAY": "0.1",  # fast 'temporary' retries
    })

    rag = subprocess.Popen([PY, os.path.join(ROOT, "rag_service", "service.py")],
                           env=rag_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    adapter = subprocess.Popen([PY, os.path.join(HERE, "service.py")],
                               env=adapter_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_up(rag_base, "/health"):
            raise RuntimeError("RAG service did not come up")
        if not wait_up(adapter_base, "/api/v1/status"):
            raise RuntimeError("adapter did not come up")

        fin = "/storage/drives/finance"
        hr = "/storage/drives/hr"

        # -- status --------------------------------------------------------
        _, stg = get_json(adapter_base, "/api/v1/status")
        check("status GET still works", stg.get("ok") is True, stg)
        _, st = post_json(adapter_base, "/api/v1/status", {})
        check("status ok (POST)", st.get("ok") and st.get("locked") is False, st)
        check("status watchdog", st.get("watchdog_seconds", 0) > 0, st)
        check("status has index", "index" in st, st)
        check("status ui-ready", st.get("ui-ready") is True, st)
        check("status fs-ready", st.get("fs-ready") is True, st)
        # aibox_more3 §2: snake_case canonical keys + storage_locked
        check("status ui_ready (snake)", st.get("ui_ready") is True, st)
        check("status fs_ready (snake)", st.get("fs_ready") is True, st)
        check("status storage_locked", st.get("storage_locked") is False, st)

        # -- UI tunnel init/version ---------------------------------------
        vvs_h = {"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                 "VVS-Session": "sess-a"}
        code, ctype, ver = get_text(adapter_base, "/api/v1/ui-version")
        check("ui-version returns text", code == 200 and ctype.startswith("text/plain"),
              (code, ctype, ver))
        check("ui-version returns configured default", ver == "latest", ver)
        _, init_ok = get_json(adapter_base, "/api/v1/ui/init", headers=vvs_h)
        check("ui init accepts valid VVS headers",
              init_ok.get("ok") is True and init_ok.get("accepted") is True
              and init_ok.get("session") == "sess-a", init_ok)
        # The embedded UI reads its drive and identity from here: it never sees
        # the VVS headers the box injects.
        check("ui init returns the session drive",
              init_ok.get("drive") == fin + "/", init_ok)
        check("ui init returns the session user",
              init_ok.get("user") == "u-1", init_ok)
        init_bad = None
        try:
            get_json(adapter_base, "/api/v1/ui/init", headers={"VVS-Drive": "not-base64"})
        except urllib.error.HTTPError as e:
            init_bad = (e.code, json.loads(e.read().decode("utf-8")))
        check("ui init rejects invalid VVS-Drive", init_bad and init_bad[0] == 400,
              init_bad)
        check("status features has basic", "basic" in (st.get("features") or []), st)
        check("status features from license",
              "accounting" in (st.get("features") or [])
              and "hr" in (st.get("features") or []), st)

        # -- directory upsert + mirror read --------------------------------
        post_json(adapter_base, "/api/v1/index/upsert/directory", {"path": fin})
        _, doc = post_json(adapter_base, "/api/v1/index/get", {"path": fin})
        check("dir mirrored", doc and doc.get("file") is False, doc)
        # aibox_more3 sec 2: no matching document -> null (not an error)
        _, miss = post_json(adapter_base, "/api/v1/index/get",
                            {"path": fin + "/nonexistent.txt"})
        check("index/get unknown -> null", miss is None, miss)

        # -- file check + content ------------------------------------------
        fpath = fin + "/q4.txt"
        body = b"Q4 revenue grew to 12.4 million euros, up 18 percent year over year."
        _, chk = post_json(adapter_base, "/api/v1/index/upsert/file/check",
                           {"path": fpath, "size": len(body),
                            "mtime": 1000, "head": ""})
        token = chk.get("token")
        check("check returns token", bool(token), chk)
        _, cont = post_raw(adapter_base,
                          "/api/v1/index/upsert/file/content/" + token, body)
        check("content indexed chunks>0", (cont.get("chunks") or 0) > 0, cont)

        # -- mirror reflects the file --------------------------------------
        _, doc2 = post_json(adapter_base, "/api/v1/index/get", {"path": fpath})
        d2 = doc2 or {}
        check("file mirrored", d2.get("file") is True and d2.get("size") == len(body), doc2)

        _, kids = post_json(adapter_base, "/api/v1/index/get/children", {"path": fin})
        paths = {c["path"] for c in (kids or [])}
        check("children lists file", fpath in paths, paths)

        # -- query (VVS-Drive hard filter) ---------------------------------
        _, q = post_json(adapter_base, "/api/v1/ui/query",
                        {"query": "What was Q4 revenue?", "top_k": 3},
                        headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        hits = q.get("hits", [])
        check("query returns hit", len(hits) > 0 and hits[0]["path"] == fpath, q)
        check("query echoes user", q.get("user") == "u-1", q)
        check("query has answer field", "answer" in q and "backend" in q, q)
        check("query has citations", isinstance(q.get("citations"), list)
              and len(q["citations"]) == len(hits), q)
        check("generation graceful w/o ollama",
              q.get("backend") in ("unavailable", "no-context", "disabled")
              or str(q.get("backend", "")).startswith("ollama"), q)
        fin_corpus = q.get("corpus_id")

        # -- confidence is mandatory on every /ui answer (spec v2) ----------
        conf = q.get("confidence") or {}
        check("query carries confidence",
              isinstance(conf.get("score"), int)
              and conf.get("band") in ("green", "amber", "red"), q)
        check("confidence has components",
              "vector_similarity" in (conf.get("components") or {}), conf)
        # C6: band message + audit id + degraded list on the payload
        check("confidence has band message",
              bool(conf.get("message")), conf)
        check("confidence has audit id",
              isinstance(conf.get("audit_id"), str) and len(conf["audit_id"]) == 12,
              conf)
        check("confidence has degraded list",
              isinstance(conf.get("degraded"), list), conf)
        # C6: the displayed answer ends with the audit footer (when generated;
        # with generation off in dev the answer is None and there is no footer)
        if q.get("answer"):
            check("answer carries audit footer",
                  "**Adatkontroll & Audit Info:**" in q["answer"]
                  and conf["audit_id"] in q["answer"], q.get("answer"))

        # -- /ui/feedback: rate the answer above, trace joined by audit id --
        fb_headers = {"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"}
        _, fb = post_json(adapter_base, "/api/v1/ui/feedback",
                          {"audit_id": conf["audit_id"], "rating": "up"},
                          headers=fb_headers)
        check("feedback accepted", fb.get("ok") is True and fb.get("stored"), fb)
        check("feedback joined the answer trace", fb.get("trace") is True, fb)
        _, fb2 = post_json(adapter_base, "/api/v1/ui/feedback",
                           {"audit_id": conf["audit_id"], "rating": "down",
                            "reason": "wrong-source", "comment": "t\u00e9ves forr\u00e1s"},
                           headers=fb_headers)
        check("feedback re-rating accepted", fb2.get("ok") is True, fb2)
        _, fb3 = post_json(adapter_base, "/api/v1/ui/feedback",
                           {"audit_id": "nosuchaudit00", "rating": "down",
                            "question": "q?", "answer": "a."},
                           headers=fb_headers)
        check("feedback without trace still stored",
              fb3.get("ok") is True and fb3.get("trace") is False, fb3)
        fb_err = post_json_expect_error(adapter_base, "/api/v1/ui/feedback",
                                        {"audit_id": conf["audit_id"],
                                         "rating": "meh"}, headers=fb_headers)
        check("feedback rejects bad rating", fb_err and fb_err[0] == 400, fb_err)
        fb_err2 = post_json_expect_error(adapter_base, "/api/v1/ui/feedback",
                                         {"rating": "up"}, headers=fb_headers)
        check("feedback requires audit_id", fb_err2 and fb_err2[0] == 400, fb_err2)
        fb_dir = os.path.join(tmp, "feedback")
        fb_files = sorted(os.listdir(fb_dir)) if os.path.isdir(fb_dir) else []
        lines = []
        for fn in fb_files:
            with open(os.path.join(fb_dir, fn), encoding="utf-8") as f:
                lines += [json.loads(ln) for ln in f if ln.strip()]
        check("feedback JSONL has 3 records", len(lines) == 3, lines)
        by_last = {e["audit_id"]: e for e in lines}
        rated = by_last.get(conf["audit_id"]) or {}
        check("feedback record carries the question in the trace",
              (rated.get("trace") or {}).get("question") == "What was Q4 revenue?",
              rated)
        check("feedback record keeps rating+reason (last wins)",
              rated.get("rating") == "down"
              and rated.get("reason") == "wrong-source", rated)
        check("feedback trace carries hits",
              (rated.get("trace") or {}).get("hits") and
              rated["trace"]["hits"][0]["path"] == fpath, rated)

        # -- #search quick action (F7): typed prefix, text mode -------------
        _, sq = post_json(adapter_base, "/api/v1/ui/query",
                          {"query": "#search What was Q4 revenue?", "top_k": 3},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#search text routes+echoes action",
              sq.get("action") == "search" and len(sq.get("hits", [])) > 0, sq)

        # -- #search files: mirror filename lookup (no LLM) ------------------
        _, sf = post_json(adapter_base, "/api/v1/ui/query",
                          {"query": "#search files: q4", "top_k": 3},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        files = sf.get("files") or []
        check("#search files finds q4.txt",
              sf.get("mode") == "files" and len(files) == 1
              and files[0]["path"] == fpath, sf)
        check("#search files answer lists path", fpath in (sf.get("answer") or ""), sf)
        check("#search files backend mirror", sf.get("backend") == "mirror", sf)
        _, sf2 = post_json(adapter_base, "/api/v1/ui/query",
                           {"query": "#search files: nosuchfile", "top_k": 3},
                           headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#search files no-hit graceful",
              (sf2.get("files") or []) == [] and "nincs" in (sf2.get("answer") or ""), sf2)

        # -- #summary quick action (F1): grounded path + task instruction ----
        _, sm = post_json(adapter_base, "/api/v1/ui/query",
                          {"query": "#summary Q4 revenue meeting"},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#summary routes+echoes action",
              sm.get("action") == "summary" and len(sm.get("hits", [])) > 0, sm)
        check("#summary carries confidence",
              (sm.get("confidence") or {}).get("band") in ("green", "amber", "red"), sm)
        # files-filter: restrict sources to q4.txt (basename form)
        _, smf = post_json(adapter_base, "/api/v1/ui/query",
                           {"query": "#summary revenue", "files": ["q4.txt"]},
                           headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#summary files-filter keeps only q4",
              len(smf.get("hits", [])) > 0
              and all(h["path"].endswith("/q4.txt") for h in smf["hits"]), smf)

        # -- C3 report family: #report (F2) + #tracking (F6) ----------------
        _, rp = post_json(adapter_base, "/api/v1/ui/query",
                          {"query": "#report"},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#report bare routes+echoes action",
              rp.get("action") == "report" and len(rp.get("hits", [])) > 0, rp)
        check("#report carries confidence",
              (rp.get("confidence") or {}).get("band") in ("green", "amber", "red"), rp)
        _, tr = post_json(adapter_base, "/api/v1/ui/query",
                          {"action": "tracking", "query": "Q4 project status"},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("tracking payload routes+echoes action",
              tr.get("action") == "tracking" and len(tr.get("hits", [])) > 0, tr)
        check("tracking carries confidence",
              (tr.get("confidence") or {}).get("band") in ("green", "amber", "red"), tr)

        # -- C4 generation family: #presentation (F3) + #memo (F5) ----------
        _, pr = post_json(adapter_base, "/api/v1/ui/query",
                          {"query": "#presentation Q4 results",
                           "audience": "board of directors",
                           "purpose": "Board update"},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("#presentation routes+echoes action",
              pr.get("action") == "presentation" and len(pr.get("hits", [])) > 0, pr)
        check("#presentation echoes params",
              (pr.get("params") or {}).get("audience") == "board of directors"
              and (pr.get("params") or {}).get("purpose") == "Board update", pr)
        check("#presentation carries confidence",
              (pr.get("confidence") or {}).get("band") in ("green", "amber", "red"), pr)
        _, mo = post_json(adapter_base, "/api/v1/ui/query",
                          {"action": "memo", "query": "Q4 spending decision",
                           "audience": "CFO"},
                          headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("memo payload routes+echoes action",
              mo.get("action") == "memo" and len(mo.get("hits", [])) > 0, mo)
        check("memo echoes audience param",
              (mo.get("params") or {}).get("audience") == "CFO", mo)
        _, nop = post_json(adapter_base, "/api/v1/ui/query",
                           {"query": "What was Q4 revenue?", "top_k": 3},
                           headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("plain query carries no params key", "params" not in nop, nop)

        # -- async UI channel (J5): /ui/ask -> /ui/poll long-poll ----------
        code, sub = post_json(adapter_base, "/api/v1/ui/ask",
                              {"query": "What was Q4 revenue?", "top_k": 3,
                               "origin": "background"},
                              headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                       "VVS-Session": "sess-a"})
        check("ask returns job_id", code == 202 and bool(sub.get("job_id"))
              and sub.get("status") == "pending", sub)
        wrong_scope = post_json_expect_error(adapter_base, "/api/v1/ui/poll",
                                             {"job_id": sub.get("job_id"), "timeout": 0},
                                             headers={"VVS-Drive": vvs(hr + "/"),
                                                      "VVS-User": "u-1",
                                                      "VVS-Session": "sess-b"})
        check("poll from other drive -> 404",
              wrong_scope and wrong_scope[0] == 404, wrong_scope)
        _, pol = post_json(adapter_base, "/api/v1/ui/poll",
                           {"job_id": sub.get("job_id"), "timeout": 30},
                           headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                    "VVS-Session": "sess-b"})
        check("poll returns done", pol.get("status") == "done"
              and pol.get("ok") is True, pol)
        check("poll result carries hits", len(pol.get("hits", [])) > 0
              and pol["hits"][0]["path"] == fpath, pol)
        _, again = post_json(adapter_base, "/api/v1/ui/poll",
                             {"job_id": sub.get("job_id"), "timeout": 0},
                             headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("completed job can be polled again", again.get("status") == "done"
              and again.get("job_id") == sub.get("job_id"), again)
        _, listed = get_json(adapter_base, "/api/v1/ui/jobs",
                             headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("job list restores completed job", len(listed.get("jobs", [])) == 1
              and listed["jobs"][0]["job_id"] == sub.get("job_id"), listed)
        _, full_job = post_json(adapter_base, "/api/v1/ui/jobs/get",
                                {"job_id": sub.get("job_id")},
                                headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("job get returns complete result",
              full_job.get("job", {}).get("result", {}).get("hits", [])[0]["path"] == fpath,
              full_job)
        _, seen = post_json(adapter_base, "/api/v1/ui/jobs/seen",
                            {"job_id": sub.get("job_id")},
                            headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("job can be marked seen", seen.get("seen_ts") is not None, seen)
        _, chat_sub = post_json(adapter_base, "/api/v1/ui/ask",
                                {"query": "What was Q4 revenue?", "top_k": 3},
                                headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        _, chat_done = post_json(adapter_base, "/api/v1/ui/poll",
                                 {"job_id": chat_sub.get("job_id"), "timeout": 30},
                                 headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("chat job still answers on its own channel",
              chat_done.get("status") == "done", chat_done)
        _, listed2 = get_json(adapter_base, "/api/v1/ui/jobs",
                              headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("chat job stays out of the background list",
              [j["job_id"] for j in listed2.get("jobs", [])] == [sub.get("job_id")],
              listed2)
        unk = post_json_expect_error(adapter_base, "/api/v1/ui/poll",
                                     {"job_id": "deadbeef"},
                                     headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("unknown job_id -> 404", unk and unk[0] == 404, unk)
        nod = post_json_expect_error(adapter_base, "/api/v1/ui/ask", {"query": "x"})
        check("ask without VVS-Drive -> 400", nod and nod[0] == 400, nod)

        # -- B6: ws-fs file-save channel (aibox_more3 sec 1) -----------------
        import wsfs as _ws  # noqa: E402  (sys.path set in the unit tests)
        vvs_h = {"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"}
        sock, rf = ws_connect("127.0.0.1", adapter_port)
        try:
            # keepalive num -> ack
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "keepalive", "num": 7}), mask=True))
            _, data = _ws.read_message(rf)
            hdr, _b = _ws.parse_message(data)
            check("wsfs keepalive acked",
                  hdr == {"type": "keepalive", "ack": 7}, hdr)
            # unknown type -> {"error": "type"}
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "frobnicate", "num": 9}), mask=True))
            _, data = _ws.read_message(rf)
            hdr, _b = _ws.parse_message(data)
            check("wsfs unknown type -> error type",
                  hdr and hdr.get("ack") == 9 and hdr.get("error") == "type", hdr)
            # unparseable basic fields -> NO response (verified indirectly: the
            # next keepalive is answered first)
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, b"this is not json",
                                          mask=True))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "keepalive", "num": 8}), mask=True))
            _, data = _ws.read_message(rf)
            hdr, _b = _ws.parse_message(data)
            check("wsfs junk gets no reply", hdr and hdr.get("ack") == 8, hdr)
            _, stw = post_json(adapter_base, "/api/v1/status", {})
            check("status ws_fs connected",
                  (stw.get("ws_fs") or {}).get("connected") is True, stw)

            def save_async(body):
                out = {}

                def run():
                    try:
                        out["resp"] = post_json(adapter_base, "/api/v1/ui/save",
                                                body, headers=vvs_h)[1]
                    except Exception as e:  # noqa: BLE001
                        out["error"] = str(e)
                t = threading.Thread(target=run, daemon=True)
                t.start()
                return t, out

            def read_putfile():
                while True:
                    _, data = _ws.read_message(rf)
                    h, b = _ws.parse_message(data)
                    if h and h.get("type") == "put-file":
                        return h, b

            # happy path: the box acks with the stored drive path
            t, out = save_async({"name": "memo.md", "text": "# Q4 memo"})
            hdr, blob = read_putfile()
            check("wsfs put-file fields+binary",
                  hdr.get("user") == "u-1" and hdr.get("drive") == fin
                  and hdr.get("name") == "memo.md" and blob == b"# Q4 memo", (hdr, blob))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"],
                 "path": "ViveSec/2026/07/memo.md"}), mask=True))
            t.join(20)
            resp = out.get("resp") or {}
            check("wsfs save transferred",
                  resp.get("transferred") is True
                  and resp.get("path") == "ViveSec/2026/07/memo.md", out)
            _, fl = get_json(adapter_base, "/api/v1/ui/files", headers=vvs_h)
            check("wsfs transferred copy removed", fl.get("files") == [], fl)

            # permission: stays in the session store + download fallback
            t, out = save_async({"name": "report.md", "text": "# weekly"})
            hdr, blob = read_putfile()
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"], "error": "permission"}),
                mask=True))
            t.join(20)
            resp = out.get("resp") or {}
            check("wsfs permission keeps stored",
                  resp.get("transferred") is False
                  and resp.get("reason") == "permission"
                  and bool(resp.get("download")), out)
            data = get_raw(adapter_base, resp["download"], headers=vvs_h)
            check("wsfs download fallback bytes", data == b"# weekly", data)
            _, fl = get_json(adapter_base, "/api/v1/ui/files", headers=vvs_h)
            check("wsfs stored file listed",
                  [f["name"] for f in fl.get("files", [])] == ["report.md"], fl)

            # temporary: retried with a fresh num, then success
            t, out = save_async({"name": "deck.md", "text": "# slides"})
            hdr, _b = read_putfile()
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"], "error": "temporary"}),
                mask=True))
            hdr2, blob2 = read_putfile()
            check("wsfs temporary retried fresh num",
                  hdr2["num"] != hdr["num"] and blob2 == b"# slides", (hdr, hdr2))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr2["num"],
                 "path": "ViveSec/2026/07/deck.md"}), mask=True))
            t.join(20)
            resp = out.get("resp") or {}
            check("wsfs retry then transferred", resp.get("transferred") is True, out)

            # format rendering: the drive gets real PDF / DOCX / PPTX bytes, and the name
            # carries the extension of the chosen format (not the UI default).
            t, out = save_async({"name": "memo.md", "text": "# Q4 memo\n\nbody",
                                 "format": "pdf"})
            hdr, blob = read_putfile()
            check("wsfs pdf rendered",
                  hdr.get("name") == "memo.pdf" and blob.startswith(b"%PDF-")
                  and blob.rstrip().endswith(b"%%EOF"), (hdr, blob[:16]))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"],
                 "path": "ViveSec/2026/07/memo.pdf"}), mask=True))
            t.join(20)
            check("wsfs pdf save reports format",
                  (out.get("resp") or {}).get("format") == "pdf", out)

            t, out = save_async({"name": "memo", "format": "docx",
                                 "text": "# Q4 memo\n\n- vezet\u0151i d\u00f6nt\u00e9s"})
            hdr, blob = read_putfile()
            check("wsfs docx rendered",
                  hdr.get("name") == "memo.docx" and blob.startswith(b"PK")
                  and b"word/document.xml" in blob, (hdr, blob[:16]))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"],
                 "path": "ViveSec/2026/07/memo.docx"}), mask=True))
            t.join(20)
            check("wsfs docx save reports format",
                  (out.get("resp") or {}).get("format") == "docx", out)

            t, out = save_async({"name": "deck", "format": "pptx",
                                 "text": "# Deck\n\n## Slide 1: Intro\n- one"})
            hdr, blob = read_putfile()
            check("wsfs pptx rendered",
                  hdr.get("name") == "deck.pptx" and blob.startswith(b"PK")
                  and b"ppt/presentation.xml" in blob, (hdr, blob[:16]))
            sock.sendall(_ws.encode_frame(_ws.OP_TEXT, _ws.build_message(
                {"type": "put-file", "ack": hdr["num"],
                 "path": "ViveSec/2026/07/deck.pptx"}), mask=True))
            t.join(20)
            check("wsfs pptx transferred",
                  (out.get("resp") or {}).get("transferred") is True, out)
        finally:
            try:
                # graceful WS close so the server loop exits promptly
                sock.sendall(_ws.encode_frame(_ws.OP_CLOSE, b"", mask=True))
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        disconnected = False
        for _ in range(30):  # poll: the server thread reaps the channel
            _, stw2 = post_json(adapter_base, "/api/v1/status", {})
            if (stw2.get("ws_fs") or {}).get("connected") is False:
                disconnected = True
                break
            time.sleep(0.2)
        check("status ws_fs disconnected", disconnected, stw2)
        _, resp = post_json(adapter_base, "/api/v1/ui/save",
                            {"name": "x.md", "text": "x"}, headers=vvs_h)
        check("wsfs save without channel stored",
              resp.get("transferred") is False and resp.get("reason") == "no-channel"
              and bool(resp.get("download")), resp)
        errs = post_json_expect_error(adapter_base, "/api/v1/ui/save",
                                      {"name": "x", "text": "x"})
        check("save needs VVS-Drive", errs and errs[0] == 400, errs)
        errs = post_json_expect_error(adapter_base, "/api/v1/ui/save",
                                      {"name": "x", "text": "x", "format": "xlsx"},
                                      headers=vvs_h)
        check("save rejects unknown format", errs and errs[0] == 400, errs)
        _, resp = post_json(adapter_base, "/api/v1/ui/save",
                            {"name": "brief.md", "text": "# brief", "format": "pdf"},
                            headers=vvs_h)
        check("save without channel renders pdf",
              resp.get("name") == "brief.pdf" and resp.get("format") == "pdf"
              and resp.get("transferred") is False, resp)

        # -- writes stay drive-scoped even when reads are not ---------------
        # A wider read scope must not make the write target ambiguous: the
        # active drive owns the generated file.
        wide_h = dict(vvs_h)
        wide_h["VVS-Other-Drives"] = other_drives(hr + "/")
        _, wide_save = post_json(adapter_base, "/api/v1/ui/save",
                                 {"name": "scoped.md", "text": "# scoped"},
                                 headers=wide_h)
        saved_name = wide_save.get("name")
        check("save succeeds with a wider read scope",
              wide_save.get("ok") is True and bool(saved_name), wide_save)
        _, active_files = get_json(adapter_base, "/api/v1/ui/files", headers=wide_h)
        check("generated file lands on the active drive",
              any(f.get("name") == saved_name
                  for f in active_files.get("files", [])), active_files)
        _, other_files = get_json(adapter_base, "/api/v1/ui/files",
                                  headers={"VVS-Drive": vvs(hr + "/"),
                                           "VVS-User": "u-1"})
        check("a drive that was only readable does not receive it",
              all(f.get("name") != saved_name
                  for f in other_files.get("files", [])), other_files)
        data = get_raw(adapter_base, resp["download"], headers=vvs_h)
        check("stored pdf downloads as pdf", data.startswith(b"%PDF-"), data[:16])

        # -- corpus isolation: a second drive ------------------------------
        hpath = hr + "/policy.txt"
        hbody = b"Employees accrue 25 vacation days per year under the handbook."
        post_json(adapter_base, "/api/v1/index/upsert/directory", {"path": hr})
        _, hchk = post_json(adapter_base, "/api/v1/index/upsert/file/check",
                           {"path": hpath, "size": len(hbody), "mtime": 1, "head": ""})
        post_raw(adapter_base, "/api/v1/index/upsert/file/content/" + hchk["token"], hbody)

        _, q2 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(hr + "/"), "VVS-User": "u-2"})
        h2 = q2.get("hits", [])
        check("hr drive different corpus", q2.get("corpus_id") != fin_corpus, q2)
        check("hr query isolated", all(x["path"].startswith(hr) for x in h2) and len(h2) > 0, q2)

        _, q3 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("finance never returns hr docs",
              all(not x["path"].startswith(hr) for x in q3.get("hits", [])), q3)

        # -- read scope: VVS-Other-Drives widens the search -----------------
        # Same question, same active drive as q3: only the header may make the
        # second drive reachable, so this is the whole feature in one check.
        _, q4 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                  "VVS-Other-Drives": other_drives(hr + "/")})
        check("other-drives header reaches the second drive",
              any(x["path"].startswith(hr) for x in q4.get("hits", [])), q4)
        check("widened answer still reports the active corpus",
              q4.get("corpus_id") == fin_corpus, q4)
        _, q5 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                  "VVS-Other-Drives": "not-base64!!"})
        check("malformed other-drives degrades to the active drive",
              all(not x["path"].startswith(hr) for x in q5.get("hits", [])), q5)
        _, q6 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                  "VVS-Other-Drives": other_drives("/etc")})
        check("other-drives outside the drive prefix is ignored",
              all(not x["path"].startswith(hr) for x in q6.get("hits", [])), q6)

        # -- the client may narrow the scope, never widen it ----------------
        _, sc = get_json(adapter_base, "/api/v1/ui/scope",
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                  "VVS-Other-Drives": other_drives(hr + "/")})
        check("scope endpoint lists the entitled drives",
              {d["path"] for d in sc.get("drives", [])} == {fin, hr}, sc)
        check("scope endpoint marks the active drive",
              [d["path"] for d in sc.get("drives", []) if d.get("active")] == [fin], sc)
        _, narrowed = post_json(adapter_base, "/api/v1/ui/query",
                               {"query": "vacation days", "top_k": 3,
                                "drives": [hr + "/"]},
                               headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                                        "VVS-Other-Drives": other_drives(hr + "/")})
        check("narrowing keeps only the selected drive",
              narrowed.get("hits") and
              all(x["path"].startswith(hr) for x in narrowed["hits"]), narrowed)
        widened = post_json_expect_error(
            adapter_base, "/api/v1/ui/query",
            {"query": "vacation days", "drives": [hr + "/"]},
            headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("selecting an unentitled drive -> 403",
              widened and widened[0] == 403, widened)

        # The async path persists the scope in the job record; restoring it
        # must not quietly put the active drive back into the search.
        wide_ask_h = {"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1",
                      "VVS-Other-Drives": other_drives(hr + "/")}
        _, nsub = post_json(adapter_base, "/api/v1/ui/ask",
                            {"query": "vacation days", "top_k": 3,
                             "drives": [hr + "/"]}, headers=wide_ask_h)
        _, npol = post_json(adapter_base, "/api/v1/ui/poll",
                            {"job_id": nsub.get("job_id"), "timeout": 30},
                            headers=wide_ask_h)
        check("queued job keeps the narrowed scope",
              npol.get("hits") and
              all(x["path"].startswith(hr) for x in npol["hits"]), npol)

        # -- bad path (not under drive prefix) -----------------------------
        err = post_json_expect_error(adapter_base, "/api/v1/index/upsert/directory",
                                    {"path": "/etc/passwd"})
        check("non-drive path rejected", err and err[0] == 400, err)

        # -- query without VVS-Drive ---------------------------------------
        err2 = post_json_expect_error(adapter_base, "/api/v1/ui/query",
                                     {"query": "x"})
        check("query needs VVS-Drive", err2 and err2[0] == 400, err2)

        # -- malformed (non-base64) VVS-Drive is rejected ------------------
        err3 = post_json_expect_error(adapter_base, "/api/v1/ui/query",
                                     {"query": "x"},
                                     headers={"VVS-Drive": fin + "/", "VVS-User": "u-1"})
        check("raw (non-base64) VVS-Drive rejected", err3 and err3[0] == 400, err3)

        # -- base64 VVS-Drive round-trips spaces / unicode -----------------
        spaced = "/storage/drives/\u0151zik\u00e9s bets?/"
        _, qs = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "anything", "top_k": 3},
                         headers={"VVS-Drive": vvs(spaced), "VVS-User": "u-3"})
        check("base64 VVS-Drive decoded to path",
              qs.get("drive") == "/storage/drives/\u0151zik\u00e9s bets?", qs)

        # -- drop/tree removes from RAG and mirror -------------------------
        _, dr = post_json(adapter_base, "/api/v1/index/drop/tree",
                         {"path": fin, "keep_exact": False})
        check("drop removed docs", (dr.get("removed") or 0) >= 1, dr)
        _, gone = post_json(adapter_base, "/api/v1/index/get", {"path": fpath})
        check("file gone from mirror", gone is None, gone)
        _, q4 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "What was Q4 revenue?", "top_k": 3},
                         headers={"VVS-Drive": vvs(fin + "/"), "VVS-User": "u-1"})
        check("finance empty after drop", len(q4.get("hits", [])) == 0, q4)
        _, q5 = post_json(adapter_base, "/api/v1/ui/query",
                         {"query": "vacation days", "top_k": 3},
                         headers={"VVS-Drive": vvs(hr + "/"), "VVS-User": "u-2"})
        check("hr intact after finance drop", len(q5.get("hits", [])) > 0, q5)

        # -- mirror persisted to disk --------------------------------------
        check("mirror file on disk", os.path.exists(meta_path), meta_path)

        # -- presence watchdog (J1): self-lock + unlock --------------------
        test_watchdog(rag_base, tmp)

        # -- mTLS bootstrap (J2): prepare -> sign -> commit ----------------
        sys.path.insert(0, HERE)
        import provision  # noqa: E402
        openssl_bin = provision.find_openssl()
        work = tempfile.mkdtemp(prefix="vivesec-init-")
        test_init_e2e(rag_base, tmp, work, openssl_bin)

    finally:
        for p in (adapter, rag):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                p.kill()

    print("\n%d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
