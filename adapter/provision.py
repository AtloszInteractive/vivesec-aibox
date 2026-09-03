"""mTLS provisioning for the AIBox (J2: init/prepare + init/commit, spec sec 1).

The ViVeSecBox bootstraps the secure channel over plain HTTP:

  1. GET  /api/v1/init/prepare
        -> the AIBox generates an RSA-4096 server key and returns a CSR
           (server_req) for CN / SAN = local.aibox.vivesecbox.com.
  2. The ViVeSecBox (the CA) signs that CSR and POSTs the result back:
     POST /api/v1/init/commit {server_crt, ca_crt, client_crt, storage_key}
        -> the AIBox verifies, before trusting anything:
             * server_crt matches the server key generated in step 1
               (RSA modulus equality),
             * server_crt AND client_crt are signed by ca_crt,
             * server_crt is valid for local.aibox.vivesecbox.com,
           then persists the PKI material and initializes / unlocks the
           encrypted storage with storage_key. From then on the box can serve
           normal traffic over HTTPS with the client certificate enforced
           (build_server_ssl_context()).

Init itself is HTTP-only (the box has no certs yet); the box is restarted with
ADAPTER_TLS=on once provisioned to bring up the mutually-authenticated channel.

Pure stdlib + the openssl CLI (no pip), consistent with storage.py/cryptsetup.
"""
import os
import shutil
import subprocess
import tempfile

HOSTNAME = "local.aibox.vivesecbox.com"
# ViVeSec sign their test CSRs with /O=ViVeTech/C=HU; a CA using OpenSSL's
# default policy_match rejects a CSR that omits them, so mirror their example.
CSR_ORG = os.environ.get("ADAPTER_CSR_ORG", "ViVeTech")
CSR_COUNTRY = os.environ.get("ADAPTER_CSR_COUNTRY", "HU")
_CERT_MARK = "BEGIN CERTIFICATE"


class ProvisionError(Exception):
    pass


def find_openssl():
    """Locate the openssl CLI (PATH first, then the Git-for-Windows bundle)."""
    p = shutil.which("openssl")
    if p:
        return p
    for c in (r"C:\Program Files\Git\usr\bin\openssl.exe",
              r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if os.path.exists(c):
            return c
    return "openssl"


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Provisioner:
    def __init__(self, pki_dir, hostname=HOSTNAME, openssl_bin=None, runner=None):
        self.pki_dir = pki_dir
        self.hostname = hostname or HOSTNAME
        self.openssl = openssl_bin or find_openssl()
        self._run = runner or self._default_runner
        os.makedirs(self.pki_dir, exist_ok=True)

    # -- paths ---------------------------------------------------------------
    def _p(self, name):
        return os.path.join(self.pki_dir, name)

    @property
    def server_key(self):
        return self._p("server.key")

    @property
    def server_crt(self):
        return self._p("server.crt")

    @property
    def ca_crt(self):
        return self._p("ca.crt")

    @property
    def client_crt(self):
        return self._p("client.crt")

    # -- openssl runner ------------------------------------------------------
    def _default_runner(self, args, timeout=120):
        p = subprocess.run([self.openssl] + args, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return (p.returncode, (p.stdout or b"").decode("utf-8", "replace"),
                (p.stderr or b"").decode("utf-8", "replace"))

    def _ossl(self, args, timeout=120):
        return self._run(args, timeout=timeout)

    # -- state ---------------------------------------------------------------
    def is_initialized(self):
        # client.crt is OPTIONAL (aibox_more3 §2: the commit no longer carries
        # client_crt — the client is verified at the TLS layer against ca.crt).
        return (os.path.exists(self._p("initialized"))
                and os.path.exists(self.server_crt)
                and os.path.exists(self.ca_crt))

    # -- step 1: prepare -----------------------------------------------------
    def prepare(self):
        """Generate the RSA-4096 server key + CSR; return the CSR PEM."""
        if self.is_initialized():
            raise ProvisionError("already initialized")
        csr = self._p("server.csr")
        subject = "/CN=%s" % self.hostname
        if CSR_ORG:
            subject += "/O=%s" % CSR_ORG
        if CSR_COUNTRY:
            subject += "/C=%s" % CSR_COUNTRY
        rc, _, err = self._ossl([
            "req", "-new", "-newkey", "rsa:4096", "-nodes",
            "-keyout", self.server_key, "-out", csr,
            "-subj", subject,
            "-addext", "subjectAltName=DNS:%s" % self.hostname,
        ], timeout=180)
        if rc != 0:
            raise ProvisionError("CSR generation failed: %s" % err.strip())
        return _read(csr)

    # -- verification helpers ------------------------------------------------
    def _modulus_cert(self, certfile):
        rc, out, err = self._ossl(["x509", "-noout", "-modulus", "-in", certfile])
        if rc != 0:
            raise ProvisionError("cannot read certificate: %s" % err.strip())
        return out.strip()

    def _modulus_key(self, keyfile):
        rc, out, err = self._ossl(["rsa", "-noout", "-modulus", "-in", keyfile])
        if rc != 0:
            raise ProvisionError("cannot read server key: %s" % err.strip())
        return out.strip()

    def _signed_by(self, certfile, cafile):
        rc, _, _ = self._ossl(["verify", "-CAfile", cafile,
                               "-partial_chain", certfile])
        return rc == 0

    def _valid_for_host(self, certfile, host):
        rc, subj, _ = self._ossl(["x509", "-noout", "-subject", "-in", certfile])
        _, san, _ = self._ossl(["x509", "-noout", "-ext", "subjectAltName",
                               "-in", certfile])
        return host in ((subj or "") + "\n" + (san or ""))

    # -- step 2: commit ------------------------------------------------------
    def commit(self, server_crt, ca_crt, client_crt=None):
        """Verify the signed material and persist it. Raises ProvisionError on
        any verification failure (nothing is persisted in that case).

        `client_crt` is OPTIONAL (aibox_more3 §2): client authenticity is
        enforced at the TLS layer (CERT_REQUIRED against ca.crt), so the commit
        no longer needs a client certificate. When one IS supplied (legacy
        boxes), it is still verified and persisted.
        """
        if self.is_initialized():
            raise ProvisionError("already initialized")
        if not os.path.exists(self.server_key):
            raise ProvisionError("prepare has not been run (no server key)")

        material = [("server.crt", server_crt), ("ca.crt", ca_crt)]
        if client_crt:
            material.append(("client.crt", client_crt))
        staged = {}
        for name, pem in material:
            if not pem or _CERT_MARK not in pem:
                for s in staged.values():
                    _safe_remove(s)
                raise ProvisionError("missing or invalid %s" % name)
            path = self._p(name + ".staged")
            _write(path, pem)
            staged[name] = path

        try:
            if self._modulus_cert(staged["server.crt"]) != self._modulus_key(self.server_key):
                raise ProvisionError("server_crt does not match the prepared server key")
            to_verify = ["server.crt"] + (["client.crt"] if "client.crt" in staged else [])
            for n in to_verify:
                if not self._signed_by(staged[n], staged["ca.crt"]):
                    raise ProvisionError("%s is not signed by ca_crt" % n)
            if not self._valid_for_host(staged["server.crt"], self.hostname):
                raise ProvisionError("server_crt is not valid for %s" % self.hostname)
        except ProvisionError:
            for s in staged.values():
                _safe_remove(s)
            raise

        for name, path in staged.items():
            os.replace(path, self._p(name))
        _write(self._p("initialized"), "ok\n")

    # -- factory reset (de-provision) ---------------------------------------
    def reset(self):
        """Wipe all PKI material so the box returns to the un-initialized state
        (factory reset, spec sec 1). After this the ViVeSecBox can re-run the
        init/prepare -> commit pairing from scratch. Returns the number of files
        removed."""
        removed = 0
        for name in ("initialized", "server.key", "server.csr", "server.crt",
                     "ca.crt", "client.crt",
                     "server.crt.staged", "ca.crt.staged", "client.crt.staged"):
            p = self._p(name)
            if os.path.exists(p):
                _safe_remove(p)
                removed += 1
        return removed

    # -- TLS transport (normal operation after init) -------------------------
    def build_server_ssl_context(self):
        """Server-side TLS 1.2+ context that REQUIRES a client certificate
        signed by the committed CA (mutual TLS)."""
        import ssl
        if not self.is_initialized():
            raise ProvisionError("not initialized; cannot build TLS context")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            pass
        ctx.load_cert_chain(self.server_crt, self.server_key)
        ctx.load_verify_locations(self.ca_crt)
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
