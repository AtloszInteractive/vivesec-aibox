# Standalone mode: HTTPS with the box's own CA (E27 S2)

In standalone mode (`ADAPTER_INTEGRATION=standalone`) the box serves its web UI
to the client machines itself. It has no internal CA to rely on, so it creates
its own, and an nginx container terminates TLS in front of the web UI.

```text
Browser ──HTTPS :443──▶ nginx (vivesec-https) ──HTTP, loopback──▶ web UI :8080 ──▶ adapter …
        ──HTTP  :80 ──▶ redirect to HTTPS + /aibox-ca.cer
```

| File | Purpose |
| --- | --- |
| `tls-pki.sh` | Creates the CA and the server certificate, renews, shows, checks |
| `https-front.sh` | Renders the nginx configuration, deploys the container, verifies it |
| `lib.sh` | Shared name and address validation |
| `standalone_tls_test.py` | Tests of both scripts (bash + openssl; docker end-to-end optional) |
| `auth_deploy.py` | Authentication service container and the standalone switch (S4, below) |
| `auth_deploy_test.py` | Tests of the switch and the image recipe |
| `clamav_deploy.py` | clamd container for the upload scan (S5, below) |
| `clamav_deploy_test.py` | Tests of its settings, container recipe and EICAR check |

## Security properties

- **The CA can only certify the box.** It carries critical name constraints
  for the configured host names and for the /24 subnet of each configured
  address (`AIBOX_TLS_CA_PERMIT_IPS` overrides it; `none` pins the CA to the
  addresses themselves). Firefox, Chrome (since version 126), Edge and
  OpenSSL enforce them on a locally installed root, so even a stolen CA key
  cannot impersonate another site to the clients. Without any configured
  address the CA may certify none (`0.0.0.0/32`).
- The CA key (`ca/ca.key`, 0600 in a 0700 directory) never leaves the box and
  is not mounted into any container. Keys are ECDSA P-256; the CA is valid for
  10 years and the server certificate for 825 days.
- nginx serves only the configured names: a request with any other `Host`
  header is dropped without an answer (`444`), which also stops DNS
  rebinding. The TLS default server presents the box certificate, because a
  browser opening `https://<address>/` sends no server name. nginx listens on
  every interface by default, so a changed address does not stop it.
- TLS 1.2 and 1.3 with ECDHE + AEAD ciphers only, no session tickets, HSTS,
  `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `no-referrer`,
  and a permissions policy that allows only the microphone (voice input).
- The relay overwrites `X-Forwarded-For` / `X-Real-IP`, removes `Forwarded`,
  and removes every client-sent `VVS-*` identity header: only the box sets them.
- The access log carries no query strings (they can contain file names).
- The container runs read-only with every capability dropped except the four
  nginx needs, and `no-new-privileges`.
- `https-front.sh deploy` refuses to start while an internal service (UI 8080,
  adapter 8088, RAG 8090, authentication 8097, Ollama 11434) listens on a
  non-loopback address, because HTTPS could then be bypassed.

## Procedure on the box

```bash
cd ~/vivesec_iabox_app/scripts/jetson/standalone
export AIBOX_TLS_DNS="aibox-01,aibox-01.bv.local" AIBOX_TLS_IPS="192.168.10.50"

sudo -E bash tls-pki.sh init        # once; prints the CA fingerprint
sudo -E bash https-front.sh deploy  # validates, starts vivesec-https, verifies, rolls back on failure
sudo -E bash https-front.sh verify  # any time
sudo -E bash tls-pki.sh show        # fingerprint and expiry dates
```

Write the SHA-256 fingerprint printed by `tls-pki.sh show` on the handover
sheet. A client compares it with the file it downloads before installing it.

The web UI behind the front must listen on loopback only: run it with
`HOST=127.0.0.1 NITRO_HOST=127.0.0.1` (the `loopback` exposure of
`40-app-deploy-jp6.sh`). In `vivesecbox` mode the adapter owns :80 and :443,
so the front cannot run there; `deploy` stops with a port conflict instead.

**Offline box:** the nginx image must be present. On a connected machine:
`docker pull --platform linux/arm64 nginx:1.28-alpine && docker save nginx:1.28-alpine -o nginx-image.tar`,
then on the box `docker load -i nginx-image.tar`.

### Renewal and changes

| Situation | Action |
| --- | --- |
| Server certificate expires within `AIBOX_TLS_RENEW_DAYS` (`tls-pki.sh check` fails) | `tls-pki.sh renew`, then `https-front.sh deploy` |
| The box gets a new address in the same /24 (or another permitted subnet) | update `AIBOX_TLS_IPS`, `renew`, `deploy`; the clients keep their CA |
| New host name, or an address outside the CA's constraints | `tls-pki.sh init --new-ca`, `deploy`, install the new CA on every client |

The box address can change, so clients should open the box **by name**
(internal DNS, or a `hosts` entry on the 2–3 client machines): after an
address change only the DNS/hosts entry and the certificate change, and the
URL stays the same.

`renew` verifies the new certificate against the CA before replacing anything,
and keeps the previous pair as `server.*.prev`. `init --new-ca` keeps the old
CA as `ca.*.replaced-<timestamp>`.

## Parameters

Every parameter is in [`CONFIGURATION.md`](../../../CONFIGURATION.md)
(Installation kit section).

| Parameter | Default | Meaning |
| --- | --- | --- |
| `AIBOX_TLS_DIR` | `/data/tls` | CA, server certificate and the public CA copy |
| `AIBOX_TLS_DNS` | host name | Host names in the certificate and the CA constraints |
| `AIBOX_TLS_IPS` | *(none)* | IPv4 addresses in the certificate and the CA constraints |
| `AIBOX_TLS_CA_PERMIT_IPS` | /24 of each address | Subnets (CIDR) the CA may certify; `none` = the addresses only |
| `AIBOX_TLS_RENEW_DAYS` | `30` | `check` fails this many days before expiry |
| `AIBOX_HTTPS_LISTEN` | `0.0.0.0` | Addresses nginx binds |
| `AIBOX_HTTPS_UPSTREAM` | `127.0.0.1:8080` | The web UI; loopback only |
| `AIBOX_HTTPS_IMAGE` | `nginx:1.28-alpine` | nginx image |
| `AIBOX_HTTPS_CONF_DIR` | `/data/https` | Rendered `nginx.conf` (+ `.prev`) |
| `AIBOX_HTTPS_MAX_BODY` | `64m` | Largest request body (at or above the upload limit); bodies stream to the UI unbuffered |
| `AIBOX_INTERNAL_PORTS` | `8080 8088 8090 8097 11434` | Ports that must listen on loopback only |

## Tests

```powershell
Push-Location scripts\jetson\standalone; python -m unittest standalone_tls_test auth_deploy_test clamav_deploy_test; Pop-Location
```

With `AIBOX_STANDALONE_DOCKER_TEST=1` and a running docker daemon, the
rendered configuration also runs in the real nginx image against a stub UI,
checked by `https-front.sh verify`.

The installation guide for the client machines (Windows, Firefox) is a
customer document in `docs/hu/`.

## Login: authentication service and the mode switch (E27 S4)

`auth_deploy.py` runs the [authentication service](../../../auth_service/README.md)
as the `vivesec-auth` container and switches the adapter and the web UI between
`vivesecbox` and `standalone` mode. It needs docker only (no sudo).

| Command | What it does |
| --- | --- |
| `deploy <source-dir>` | Runs the auth tests in the base image, builds `vivesec-auth:release-<stamp>` (service files only, user 10001), creates `AIBOX_AUTH_DIR` (0700, owner 10001) and its keys (`manage.py init`, never overwrites), and (re)starts `vivesec-auth` on `127.0.0.1:AIBOX_AUTH_PORT`: read-only, every capability dropped, `no-new-privileges`, 256 MB. A failed health check brings the previous container back. |
| `enable` | Recreates the adapter with `ADAPTER_INTEGRATION=standalone`, `ADAPTER_IDENTITY_MODE=require`, `ADAPTER_DIRECTORY=token`, HS256 with the issuer and audience of the service, without `ADAPTER_SCOPE_ALL_DRIVES`, and `identity-hs256.key` mounted read-only; then the web UI with `ADAPTER_INTEGRATION=standalone`, `AUTH_URL` and only `service.key` mounted read-only. Same image, settings and binds otherwise. If either does not come up in the new mode, both are put back. When the adapter has no `ADAPTER_ACL_RULES`, `enable` sets it to `AIBOX_ADAPTER_ACL_RULES` (default `/data/acl-rules.json`, a path in the adapter container; it must lie in one of its writable mounts, else `enable` stops) and creates the file as `{}` (no rules, owner-only) when it is missing, so the administration page (S7) can edit the permission rules. An existing file is never touched. |
| `disable` | The reverse: removes exactly those settings and mounts (`ADAPTER_ACL_RULES` only when `enable` set it; the file stays). |
| `status` | Mode, image and key mounts of the three containers, auth health. |

`<source-dir>` contains `auth_service/` and `adapter/`: the `build` directory of a
release stage (`172-release-box.py pack` includes `auth_service`) or a checkout.
The previous containers stay stopped as `<name>-prev-<stamp>`.

```bash
python3 auth_deploy.py deploy ~/vivesec-release-<stamp>-*/build
docker exec -it vivesec-auth python /app/auth_service/manage.py create-user admin \
    --display-name "Administrator" --role admin --drive /storage/drives/<room>/ --temporary
python3 auth_deploy.py enable
```

The first login asks for the temporary password, then enrols the authenticator
app with a QR code, then requires a new password. Later releases keep standalone
mode (they inherit the container settings and mounts); in standalone mode
`172-release-box.py verify` signs its own two-minute token with the adapter's
key. After a release that changes `auth_service/`, run `deploy` again. On a box
that was switched before S7, run `enable` once more: it only adds the
permission rules file.

**Administration (S7):** an administrator (role `admin`) opens it from the
account menu (`/admin`): users, two-factor resets, the AD group mapping, rooms
and permission rules, the clock and the virus definitions, the event logs.

**Active Directory (S6):** `deploy` passes every `AUTH_*` variable of the
caller's environment to the container and keeps the previous container's, so
the directory settings (`AUTH_LDAP_URLS`, `AUTH_LDAP_TLS_PINS`,
`AUTH_LDAP_BASE_DN`, `AUTH_LDAP_BIND_DN`) are given once at a `deploy`. The
service account password and the group map go into `AIBOX_AUTH_DIR` with
`manage.py ldap-password` and `ldap-map import` (see the
[authentication service README](../../../auth_service/README.md#directory-login-active-directory-s6)).
The container uses the host network, so it reaches the domain controllers like
the box does (TCP 636, or 389 with StartTLS). For tests,
`auth_service/samba_ad/samba_ad.py` runs a Samba AD test domain on loopback.

## Uploads: the virus scanner (E27 S5)

In standalone mode the box keeps the uploaded files itself (see the
[adapter README](../../../adapter/README.md#file-store-and-uploads-e27-s5)).
Every upload waits in quarantine until clamd has scanned it.
`clamav_deploy.py` runs clamd as the `vivesec-clamav` container
(`clamav/clamav-debian`, arm64, signature database in the image). It needs
docker only (no sudo).

| Command | What it does |
| --- | --- |
| `deploy` | Pulls the image if missing, (re)starts `vivesec-clamav` published on `127.0.0.1:AIBOX_CLAMAV_PORT` only (bridge network, every capability dropped except the five its start script needs, `no-new-privileges`, memory limit), waits until clamd answers, then checks that the EICAR test string is found and a clean text is not. On failure the previous container runs again. |
| `status` | Container state, clamd answer and the signature database version. |
| `test` | The EICAR check alone. |

clamd's `StreamMaxLength` and `MaxFileSize` are `AIBOX_CLAMAV_MAX_MB` (300 MB,
well above the 20 MiB upload limit, so the upload limit can grow with the RAG
limit) and `AlertExceedsMax` is on, so a file clamd
cannot scan whole is reported as a finding, never passed as clean. freshclam is
off by default (offline box); `AIBOX_CLAMAV_FRESHCLAM=on` lets a connected
development box update its signatures. On an offline box the image must be
present: `docker pull --platform linux/arm64 clamav/clamav-debian:1.4 && docker save ... -o clamav-image.tar`,
then `docker load -i clamav-image.tar` on the box.

The adapter's defaults match (`ADAPTER_CLAMD=127.0.0.1:3310`,
`ADAPTER_UPLOAD_SCAN=required`), so no adapter setting has to change. While
clamd is down, uploads stay in quarantine and the web UI says so.

The nginx front must let the uploads through: `AIBOX_HTTPS_MAX_BODY` (default
`64m`, above the 20 MiB upload limit) and unbuffered request bodies
(`proxy_request_buffering off`, because the container's `/tmp` is a small
tmpfs). After raising the upload limit beyond it, raise this too and run
`https-front.sh deploy` again (root).

| Parameter | Default | Meaning |
| --- | --- | --- |
| `AIBOX_CLAMAV_IMAGE` | `clamav/clamav-debian:1.4` | clamd image |
| `AIBOX_CLAMAV_PORT` | `3310` | Loopback port |
| `AIBOX_CLAMAV_MEMORY` | `3g` | Memory limit (clamd needs about 1-1.5 GB) |
| `AIBOX_CLAMAV_MAX_MB` | `300` | Largest stream/file clamd scans whole |
| `AIBOX_CLAMAV_FRESHCLAM` | `off` | `on`: signature updates from the internet |
