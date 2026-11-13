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
| `clamav_deploy.py` | clamd container for the upload scan (S5), offline signature updates (S8) |
| `clamav_deploy_test.py` | Tests of its settings, container recipe, EICAR check and update plan |
| `vault.py` | Encrypted data volume: create, unlock, lock, migrate, keys, boot unit (S8, below) |
| `backup.py` | Encrypted backup disk: generations, verify, restore drill, restore, timer (S8) |
| `timesync.py` | The customer's time source for chrony / systemd-timesyncd (S8) |
| `standalone_install.py` | The standalone phases in order, resumable (S8) |
| `standalone.conf.example` | Settings template for `/etc/aibox/standalone.conf` |
| `kitlib.py` | Shared helpers of the S8 tools (settings file, commands, docker) |
| `vault_test.py`, `backup_test.py`, `timesync_test.py`, `standalone_install_test.py` | Their tests; `backup_test` runs the whole cycle on LUKS files as root with `AIBOX_STANDALONE_ROOT_TEST=1` |

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
Push-Location scripts\jetson\standalone; python -m unittest standalone_tls_test auth_deploy_test clamav_deploy_test vault_test backup_test timesync_test standalone_install_test; Pop-Location
```

The LUKS, mount and docker steps of S8 need root on Linux:
`sudo AIBOX_STANDALONE_ROOT_TEST=1 python3 -m unittest backup_test` (on a box;
it formats two small LUKS files in `/var/tmp` and needs 24 GiB free there).

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
cannot scan whole is reported as a finding, never passed as clean. clamd writes
each stream to a temporary file first: the container's `/tmp` is a tmpfs
(memory), so upload contents never reach the plaintext docker disk. freshclam is
off by default (offline box); `AIBOX_CLAMAV_FRESHCLAM=on` lets a connected
development box update its signatures. On an offline box the image must be
present: `docker pull --platform linux/arm64 clamav/clamav-debian:1.4 && docker save ... -o clamav-image.tar`,
then `docker load -i clamav-image.tar` on the box.

**Offline signature updates (S8).** The database lives in `AIBOX_CLAMAV_DB_DIR`
(default `/var/lib/aibox/clamav`, seeded from the image at the first `deploy`):

| Command | Where | What it does |
| --- | --- | --- |
| `fetch-defs DIR` | any machine with internet and docker | freshclam into an empty directory, which yields whole `main.cvd`, `daily.cvd`, `bytecode.cvd`; checks their ClamAV signatures and writes `aibox-clamav-defs.json` (versions, SHA-256). Copy `DIR` to a USB stick. |
| `update-defs DIR [--allow-older]` | the box | Checks every `.cvd` of `DIR` with `sigtool` in a container without network (unsigned or tampered: nothing installed), ignores unsigned `.cld`/`.cdiff` files, refuses a version older than the installed one, installs only the newer files, keeps the previous ones in `.prev/`, reloads clamd, waits for the new `daily` version and checks EICAR; any failure puts the previous files back. |

The administration page shows the age of the loaded definitions and warns after
`ADAPTER_CLAMAV_MAX_AGE_DAYS` (7): that is the reminder to bring a new stick.

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
| `AIBOX_CLAMAV_DB_DIR` | `/var/lib/aibox/clamav` | Signature database on the host (offline updates) |

## Encrypted volume, backup and time (E27 S8)

The box holds the only copy of the customer's documents. Everything with
documents, indexes, conversations, keys or secrets lies on one LUKS2 volume
(E05), a daily encrypted backup copies it, and the clock follows the customer's
time source (TOTP). All settings are in `/etc/aibox/standalone.conf`
(`standalone.conf.example`; the environment overrides it).

```text
/data/aibox-vault.img (LUKS2) ──open──▶ /data/vault ◀── /data/adapter, /data/auth, /data/rag, ... (symlinks)
        key slots: unlock (passphrase | usb | keyfile) + recovery (paper)
/data/vault ──rsync, 2 passes, docker pause──▶ backup disk (LUKS2: backup key on the volume + recovery key)
```

### The volume (`vault.py`, `aibox-vault`)

| Command | What it does |
| --- | --- |
| `init [--yes-erase DEV] [--recovery-out FILE]` | Creates the LUKS2 volume (AES-XTS-512, argon2id) on `AIBOX_VAULT_DEVICE` or in `AIBOX_VAULT_FILE`, with two key slots whose roles are recorded in the header: the unlock key of `AIBOX_VAULT_UNLOCK` and a 160-bit recovery key. The recovery key is shown once on the terminal (never on stdout, which install logs capture) and must be typed back; `--recovery-out` writes it to a 0600 file off the volume instead. Formats ext4 and mounts it at `AIBOX_VAULT_MOUNT`. |
| `unlock [--recovery] [--key-stdin] [--no-start]` | Opens (the key from the terminal, the stick, the key file, or stdin), checks the file system, mounts (`nodev,nosuid`) and starts the containers that use it. |
| `lock` | Stops those containers (in parallel), unmounts, closes, and makes the empty mount point immutable. |
| `migrate` | Moves every `AIBOX_VAULT_LINKS` directory onto the volume (rsync, then a dry-run comparison) and leaves a relative symlink; the containers are stopped meanwhile. The plaintext originals stay as `*.pre-vault-<stamp>`; `wipe-plaintext --confirm` deletes them (and trims). |
| `rotate unlock\|recovery [--method M] [--recovery]` | New key in a free slot, tested, recorded, then the old slot is destroyed. Also switches the unlock method. After a recovery rotation, run `aibox-backup rekey` with each backup disk. |
| `header-backup FILE` | LUKS header copy, for the recovery-key envelope (it accepts the keys valid when it was made). |
| `erase --confirm HOSTNAME` | Cryptographic erase: every key slot destroyed (and the key file). Backup disks are separate volumes. |
| `install-unit` | `aibox-vault.service` (after docker: unlock at boot in `usb`/`keyfile` mode, stays locked in `passphrase` mode; locks at shutdown) and the tools in `/usr/local/lib/aibox`, `/usr/local/sbin/aibox-{vault,backup,timesync}`. |
| `status [--json]`, `check` | State, key roles, links, containers; `check` also fails on plaintext swap and an unguarded mount point, and warns about core dumps and containers with a writable layer but no `/tmp` tmpfs. |

**Fail closed.** While the volume is closed its mount point is empty and
immutable (`chattr +i`) and the links dangle. Docker starts the containers at
boot before the volume is open; a container whose bind source lies there fails
to start (docker cannot create the missing directory), so no application writes
to the plaintext disk. The unit, or `aibox-vault unlock`, starts them once the
volume is mounted. Keys reach cryptsetup on stdin or through an inherited pipe,
never on a command line, and are never logged.

**Unlock mode (customer decision: who holds the key).** `passphrase` (default):
nothing is stored; after every reboot an administrator runs `sudo aibox-vault
unlock`. `usb`: a random key file on a stick labelled `AIBOX-KEY`; the box boots
unattended while the stick is in, so the stick must be kept apart from the box
otherwise. `keyfile`: the key is on the system disk, so it only protects a data
disk removed on its own: development only. The recovery key is always there and
opens the volume and the backup disks when the other key is lost; without it a
lost key means the data is gone (that is the point). Unknown values mean
`passphrase`.

**Layouts.** Jetson: the NVMe partition at `/data` also holds docker and the
models, so the volume is a file there (`/data/aibox-vault.img`), mounted at
`/data/vault`, and the data directories are linked. DGX Spark (4 TB SSD): the
factory OS uses the whole disk; a file (`/var/lib/aibox/vault.img`, e.g.
`AIBOX_VAULT_SIZE=3000G`) mounted at `/data` before the application is
installed needs no links.

### Backup (`backup.py`, `aibox-backup`)

| Command | What it does |
| --- | --- |
| `init-target PATH [--yes-erase DEV \| --size 500G]` | Prepares a removable disk (or a file off the volume) as LUKS2 labelled `AIBOX-BACKUP` with the backup key (created on the volume, `.aibox/backup.key`) and the box's recovery key, which is checked against the volume first. |
| `run [--if-present]` | New generation `<host>/generations/<stamp>/`: pass 1 rsync live, pass 2 with the volume's containers paused (about a second), so databases are consistent; unchanged files are hard links to the previous generation; plus the volume's LUKS header, the container settings, `AIBOX_BACKUP_EXTRA` and a manifest with every file's SHA-256 (unchanged files reuse the previous hash). Keeps `AIBOX_BACKUP_KEEP`. |
| `verify [GEN] [--quick]` | Re-hashes a generation on the disk against its manifest. |
| `rekey [--recovery-stdin]` | After `aibox-vault rotate recovery`: the attached disk gets the volume's new recovery key (checked against the volume) and the old one is destroyed. Every disk records which recovery key it holds; `run` refuses a disk that still opens with a previous one. |
| `drill [GEN]` | The measured restore test (RTO): restores into a scratch directory on the volume, verifies every file, records the times, removes the copy. |
| `restore [GEN] --confirm HOSTNAME [--recovery] [--from-host H]` | Stops the containers, replaces the volume's content with the generation (`.aibox/` kept), checks sizes, starts them. On a new box: `vault.py init`, `migrate`, then `restore --recovery --from-host <old name>`. |
| `list`, `status`, `install-timer` | Generations; last run, drill and restore (`status` fails beyond `AIBOX_BACKUP_MAX_AGE_HOURS`); the daily `aibox-backup.timer` at `AIBOX_BACKUP_TIME` (RPO one day). A missing disk is skipped and shows in `status`. |

### Time (`timesync.py`, `aibox-timesync`)

`configure` probes `AIBOX_NTP_SERVERS` over SNTP (unsynchronised, unreachable or
spoofed answers are refused), then points chrony (when installed) or
systemd-timesyncd at those servers only: no internet pool or fallback (on a
Jetson the vendor's `nv-fallback-ntp.conf` is overridden), and the box serves no
time. Which source the customer has is an open question; the domain controllers
usually serve time; `none` leaves the clock to the operator and `check` fails as
a reminder. `check` fails when the client is not synchronised or the clock is
more than `AIBOX_TIME_MAX_OFFSET` (5 s) off; the administration page shows the
same (S7).

### Installation order

On a fresh box, after the base installation and an application release:

```bash
sudo install -d -m 0700 /etc/aibox
sudo install -m 0600 standalone.conf.example /etc/aibox/standalone.conf   # then edit it
sudo python3 standalone_install.py --check
sudo python3 standalone_install.py          # preflight time vault tls clamav auth backup audit
```

`--resume` continues after a failure, `--only PHASE` repeats one. The vault phase
asks for the recovery key to be written down; after it, write a header backup
next to it (`aibox-vault header-backup`). The backup phase installs the timer; a
backup disk is prepared once with `aibox-backup init-target /dev/sdX --yes-erase
/dev/sdX`, then `aibox-backup run` and `aibox-backup drill`.

**Not covered:** the plaintext docker disk still holds the images, the
container logs (`json-file`; they carry user names and paths, not document
text) and the environment variables of the containers (internal service keys,
e.g. `RAG_API_KEY`); the models are not sensitive. Core dumps (apport) must be
switched off on a customer box (`vault.py check` warns). TPM-sealed keys are
not used (the Jetson has no discrete TPM).
