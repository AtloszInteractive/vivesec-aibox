"""E27 S2: the box's own CA (tls-pki.sh) and the HTTPS front (https-front.sh).

Runs the real scripts with bash and openssl (Git Bash on Windows). The nginx
end-to-end check needs docker and network access for the images, so it only
runs with AIBOX_STANDALONE_DOCKER_TEST=1.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKI = os.path.join(HERE, "tls-pki.sh").replace("\\", "/")
FRONT = os.path.join(HERE, "https-front.sh").replace("\\", "/")
NAMES = {"AIBOX_TLS_DNS": "AIBox-01, aibox-01.lan", "AIBOX_TLS_IPS": "192.168.10.50",
         "AIBOX_TLS_CA_PERMIT_IPS": "10.20.0.0/16"}


def find_bash():
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                          r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(candidate):
                return candidate
        return None
    return shutil.which("bash")


BASH = find_bash()


def tool_available():
    if not BASH:
        return False
    try:
        return subprocess.run([BASH, "-c", "command -v openssl"], capture_output=True,
                              timeout=30, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(tool_available(), "bash with openssl is not available")
class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name.replace("\\", "/")
        self.tls = self.root + "/tls"

    def env(self, **extra):
        env = {key: value for key, value in os.environ.items() if not key.startswith("AIBOX_")}
        env.update(NAMES)
        env["AIBOX_TLS_DIR"] = self.tls
        env.update(extra)
        return env

    def bash(self, args, check=True, **extra):
        result = subprocess.run([BASH] + args, env=self.env(**extra), capture_output=True,
                                text=True, encoding="utf-8", timeout=120,
                                stdin=subprocess.DEVNULL)
        if check and result.returncode != 0:
            self.fail("%s failed (%d):\n%s\n%s" % (args, result.returncode, result.stdout,
                                                   result.stderr))
        return result

    def openssl(self, *args):
        return self.bash(["-c", 'openssl "$@"', "openssl"] + list(args)).stdout

    def read(self, relative):
        with open(os.path.join(self.tls, relative), "rb") as handle:
            return handle.read()


class TlsPkiTest(ScriptTestCase):
    def init(self, **extra):
        return self.bash([PKI, "init"], **extra)

    def test_init_creates_a_constrained_ca_and_a_server_certificate(self):
        out = self.init().stdout
        ca = self.openssl("x509", "-in", self.tls + "/ca/ca.crt", "-noout", "-text")
        self.assertRegex(ca, r"Basic Constraints: critical\s+CA:TRUE, pathlen:0")
        self.assertRegex(ca, r"Key Usage: critical\s+Certificate Sign, CRL Sign")
        self.assertIn("Name Constraints: critical", ca)
        for name in ("DNS:aibox-01", "DNS:aibox-01.lan", "IP:192.168.10.50/255.255.255.255",
                     "IP:10.20.0.0/255.255.0.0"):
            self.assertIn(name, ca)
        self.assertIn("prime256v1", ca)
        server = self.openssl("x509", "-in", self.tls + "/server/server.crt", "-noout", "-text")
        self.assertIn("DNS:aibox-01, DNS:aibox-01.lan, IP Address:192.168.10.50", server)
        self.assertIn("TLS Web Server Authentication", server)
        self.assertRegex(server, r"Basic Constraints: critical\s+CA:FALSE")
        self.assertIn("CA certificate", out)
        self.bash([PKI, "check"])

    def test_published_file_hash_is_the_printed_fingerprint(self):
        out = self.init().stdout
        der = self.read("public/aibox-ca.cer")
        digest = hashlib.sha256(der).hexdigest().upper()
        self.assertIn("SHA-256 (Windows) : %s" % digest, out)
        self.assertIn(":".join(digest[i:i + 2] for i in range(0, 64, 2)), out)
        pem_as_der = self.bash(["-c", 'openssl x509 -in "$1" -outform DER | openssl dgst -sha256 -r',
                                "x", self.tls + "/ca/ca.crt"]).stdout.split()[0]
        self.assertEqual(pem_as_der.upper(), digest)

    def rogue_certificate(self, san):
        """A server certificate for `san`, signed with the box's CA key."""
        script = r'''
cd "$1"
printf '[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=x\n[v]\nsubjectAltName=%s\nextendedKeyUsage=serverAuth\n' "$2" > r.cnf
openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout r.key -config r.cnf -out r.csr 2>/dev/null
openssl x509 -req -in r.csr -CA "$3/ca/ca.crt" -CAkey "$3/ca/ca.key" -set_serial 7 -days 5 \
  -extfile r.cnf -extensions v -out r.crt 2>/dev/null
openssl verify -CAfile "$3/ca/ca.crt" -purpose sslserver r.crt
'''
        return self.bash(["-c", script, "x", self.root, san, self.tls], check=False)

    def test_a_stolen_ca_key_cannot_certify_other_names(self):
        self.init()
        for san in ("DNS:evil.example", "DNS:aibox-01.lan.evil.example", "IP:8.8.8.8",
                    "IP:192.168.10.51"):
            result = self.rogue_certificate(san)
            self.assertNotEqual(result.returncode, 0, san)
            self.assertIn("permitted subtree violation", result.stdout + result.stderr, san)
        for san in ("DNS:x.aibox-01.lan", "IP:10.20.5.5"):
            self.assertEqual(self.rogue_certificate(san).returncode, 0, san)

    def test_without_addresses_the_ca_may_certify_none(self):
        self.init(AIBOX_TLS_IPS="", AIBOX_TLS_CA_PERMIT_IPS="")
        ca = self.openssl("x509", "-in", self.tls + "/ca/ca.crt", "-noout", "-ext",
                          "nameConstraints")
        self.assertIn("IP:0.0.0.0/255.255.255.255", ca)
        result = self.rogue_certificate("IP:192.168.10.50")
        self.assertNotEqual(result.returncode, 0)

    def test_init_refuses_to_replace_an_existing_ca(self):
        self.init()
        ca = self.read("ca/ca.crt")
        result = self.bash([PKI, "init"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("a CA already exists", result.stderr)
        self.assertEqual(self.read("ca/ca.crt"), ca)

    def test_new_ca_is_explicit_and_keeps_the_old_one(self):
        self.init()
        ca = self.read("ca/ca.crt")
        self.bash([PKI, "init", "--new-ca"])
        self.assertNotEqual(self.read("ca/ca.crt"), ca)
        replaced = [n for n in os.listdir(os.path.join(self.tls, "ca")) if "replaced" in n]
        self.assertEqual(len(replaced), 2)
        self.assertEqual(self.read("public/aibox-ca.cer"),
                         bytes.fromhex(self.bash(["-c", 'openssl x509 -in "$1" -outform DER | od -An -tx1 -v | tr -d " \\n"',
                                                  "x", self.tls + "/ca/ca.crt"]).stdout.strip()))

    def test_renew_issues_a_new_key_from_the_same_ca(self):
        self.init()
        ca, key = self.read("ca/ca.crt"), self.read("server/server.key")
        self.bash([PKI, "renew"])
        self.assertEqual(self.read("ca/ca.crt"), ca)
        self.assertNotEqual(self.read("server/server.key"), key)
        self.assertEqual(self.read("server/server.key.prev"), key)
        self.bash([PKI, "check"])
        self.bash([PKI, "renew"], AIBOX_TLS_IPS="10.20.7.7")
        self.bash([PKI, "check"], AIBOX_TLS_IPS="10.20.7.7")

    def test_renew_outside_the_constraints_changes_nothing(self):
        self.init()
        cert = self.read("server/server.crt")
        result = self.bash([PKI, "renew"], check=False, AIBOX_TLS_IPS="192.168.99.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside its constraints", result.stderr)
        self.assertEqual(self.read("server/server.crt"), cert)

    def test_check_reports_names_that_differ_from_the_configuration(self):
        self.init()
        result = self.bash([PKI, "check"], check=False, AIBOX_TLS_DNS="aibox-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differ from the configuration", result.stdout)

    def test_check_reports_a_due_renewal(self):
        self.init()
        result = self.bash([PKI, "check"], check=False, AIBOX_TLS_RENEW_DAYS="900")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run renew", result.stdout)

    def test_check_reports_a_foreign_published_ca(self):
        self.init()
        with open(os.path.join(self.tls, "public", "aibox-ca.cer"), "ab") as handle:
            handle.write(b"x")
        result = self.bash([PKI, "check"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not the current CA", result.stdout)

    def test_invalid_input_is_rejected(self):
        for extra, message in (({"AIBOX_TLS_DNS": "bad_name"}, "invalid host name"),
                               ({"AIBOX_TLS_DNS": "-x.lan"}, "invalid host name"),
                               ({"AIBOX_TLS_IPS": "300.1.1.1"}, "invalid IPv4"),
                               ({"AIBOX_TLS_IPS": "10.1.1"}, "invalid IPv4"),
                               ({"AIBOX_TLS_CA_PERMIT_IPS": "10.0.0.0/33"}, "invalid subnet")):
            result = self.bash([PKI, "init"], check=False, **extra)
            self.assertNotEqual(result.returncode, 0, extra)
            self.assertIn(message, result.stderr, extra)
            self.assertFalse(os.path.exists(os.path.join(self.tls, "ca", "ca.key")), extra)


class FrontRenderTest(ScriptTestCase):
    def render(self, check=True, **extra):
        return self.bash([FRONT, "render"], check=check, **extra)

    def test_listens_on_the_configured_addresses_only(self):
        conf = self.render(AIBOX_HTTPS_LISTEN="192.168.10.50, 10.20.7.7").stdout
        for line in ("listen 192.168.10.50:443 ssl;", "listen 10.20.7.7:443 ssl;",
                     "listen 192.168.10.50:80;", "listen 192.168.10.50:443 ssl default_server;",
                     "listen 10.20.7.7:80 default_server;"):
            self.assertIn(line, conf)
        self.assertNotRegex(conf, r"listen (80|443)\b")
        self.assertIn("server_name aibox-01 aibox-01.lan 192.168.10.50;", conf)

    def test_unknown_hosts_are_dropped(self):
        conf = self.render().stdout
        self.assertEqual(conf.count("return 444;"), 2)
        self.assertNotIn("ssl_reject_handshake", conf)
        # An address in the URL means no SNI: the TLS default must still
        # complete the handshake with the box certificate.
        default_tls = conf.split("ssl default_server;", 1)[1].split("}", 1)[0]
        self.assertIn("ssl_certificate /etc/aibox-tls/server.crt;", default_tls)
        self.assertIn("return 444;", default_tls)

    def test_tls_and_security_headers(self):
        conf = self.render().stdout
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", conf)
        self.assertIn("ssl_session_tickets off;", conf)
        self.assertEqual(conf.count('add_header Strict-Transport-Security "max-age=31536000" always;'), 2)
        self.assertIn("frame-ancestors 'none'", conf)
        self.assertIn("server_tokens off;", conf)

    def test_relay_overwrites_forwarding_and_drops_identity_headers(self):
        conf = self.render().stdout
        self.assertIn("proxy_pass http://127.0.0.1:8080;", conf)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", conf)
        self.assertIn('proxy_set_header Forwarded "";', conf)
        for header in ("VVS-Drive", "VVS-User", "VVS-Session", "VVS-Identity", "VVS-Other-Drives"):
            self.assertIn('proxy_set_header %s "";' % header, conf)
        self.assertIn("return 301 https://$host$request_uri;", conf)
        self.assertIn("location = /aibox-ca.cer", conf)
        self.assertNotIn("$args", conf)

    def test_upstream_must_be_loopback(self):
        for upstream in ("192.168.10.50:8080", "0.0.0.0:8080", "example.com:8080", "127.0.0.1"):
            result = self.render(check=False, AIBOX_HTTPS_UPSTREAM=upstream)
            self.assertNotEqual(result.returncode, 0, upstream)
            self.assertIn("loopback", result.stderr, upstream)
        self.assertIn("proxy_pass http://[::1]:3000;",
                      self.render(AIBOX_HTTPS_UPSTREAM="[::1]:3000").stdout)

    def test_refuses_to_guess_where_to_listen(self):
        result = self.render(check=False, AIBOX_TLS_IPS="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing to listen on", result.stderr)
        self.assertIn("listen 0.0.0.0:443 ssl;",
                      self.render(AIBOX_TLS_IPS="", AIBOX_HTTPS_LISTEN="0.0.0.0").stdout)


def docker_ready():
    if os.environ.get("AIBOX_STANDALONE_DOCKER_TEST") != "1" or not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0


@unittest.skipUnless(tool_available() and docker_ready(),
                     "set AIBOX_STANDALONE_DOCKER_TEST=1 with a running docker daemon")
class FrontEndToEndTest(ScriptTestCase):
    """The rendered configuration in the real nginx image, with a stub UI on
    loopback, checked by `https-front.sh verify` inside the same container."""

    IMAGE = os.environ.get("AIBOX_HTTPS_IMAGE", "nginx:1.28-alpine")

    def test_front_serves_redirects_and_drops(self):
        names = {"AIBOX_TLS_DNS": "aibox-01", "AIBOX_TLS_IPS": "127.0.0.1",
                 "AIBOX_TLS_CA_PERMIT_IPS": "", "AIBOX_INTERNAL_PORTS": "8080"}
        self.bash([PKI, "init"], **names)
        script = r'''
set -eu
apk add --no-cache bash openssl curl iproute2 python3 >/dev/null
mkdir -p /etc/aibox-tls /srv/aibox-public
cp /work/tls/server/* /etc/aibox-tls/ && cp /work/tls/public/* /srv/aibox-public/
bash /scripts/https-front.sh render /etc/nginx/nginx.conf
nginx -t -q
nginx
mkdir -p /ui && echo ok > /ui/index.html
(cd /ui && python3 -m http.server 8080 --bind 127.0.0.1 >/dev/null 2>&1 &)
sleep 1
bash /scripts/https-front.sh verify
curl -sS -o /dev/null -D - --cacert /work/tls/ca/ca.crt https://aibox-01/ --resolve aibox-01:443:127.0.0.1 | tr -d '\r' | grep -i '^x-frame-options: DENY'
'''
        env = dict(names, AIBOX_TLS_DIR="/work/tls")
        args = ["docker", "run", "--rm", "-i", "--entrypoint", "sh",
                "-v", "%s:/work" % self.root, "-v", "%s:/scripts:ro" % HERE.replace("\\", "/")]
        for key, value in env.items():
            args += ["-e", "%s=%s" % (key, value)]
        result = subprocess.run(args + [self.IMAGE, "-c", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=600, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("FAIL", result.stdout)
        for check in ("PASS https://127.0.0.1/ via 127.0.0.1 trusted by the box CA",
                      "PASS unknown Host header is dropped",
                      "PASS unknown TLS server name gets no answer",
                      "PASS a known server name with a foreign Host header is not served"):
            self.assertIn(check, result.stdout)

    def test_hardened_container_starts(self):
        """The exact options of the service (read-only root, dropped
        capabilities, tmpfs, no-new-privileges) with its own loopback only."""
        names = {"AIBOX_TLS_DNS": "aibox-01", "AIBOX_TLS_IPS": "127.0.0.1",
                 "AIBOX_TLS_CA_PERMIT_IPS": ""}
        self.bash([PKI, "init"], **names)
        conf = self.root + "/nginx.conf"
        self.bash([FRONT, "render", conf], **names)
        args = self.bash([FRONT, "docker-args", conf], **names).stdout.split("\n")
        args = [a for a in args if a]
        self.assertEqual(args[:2], ["--network", "host"])
        name = "aibox-https-test-%d" % os.getpid()
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        started = subprocess.run(["docker", "run", "-d", "--name", name, "--network", "none"]
                                 + args[2:] + [self.IMAGE, "-g", "daemon off;"],
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(started.returncode, 0, started.stderr)
        time.sleep(3)
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True,
                              timeout=30)
        self.assertEqual(state, "true", logs.stdout + logs.stderr)
        self.assertNotIn("[emerg]", logs.stderr)
        workers = subprocess.run(["docker", "exec", name, "ps", "-o", "user,args"],
                                 capture_output=True, text=True, timeout=30).stdout
        self.assertIn("nginx: worker process", workers)
        self.assertRegex(workers, r"nginx\s+nginx: worker process")


if __name__ == "__main__":
    unittest.main()
