# Matrix AI on a DGX Spark (E07 M1)

Installs the Matrix AI (this repository's standalone stack) on a DGX Spark that
already runs the Dokumentum AI, and connects it to the ViVeSecBox proxy tunnel.
Plan and open questions: `docs/hu/ViVeSec_AIBox_Matrix_Telepito_Frissites_Terv_20261120.md`
(local only).

```text
ViVeSecBox ──mTLS──▶ :7476 tunnel front (nginx) ──▶ 127.0.0.1:18476 tunnel gateway ──VVS-Identity──▶ adapter :8088
                       client cert vs. ViVeSecBox CA      (matrix_tunnel/gateway.py)                    standalone
                                                               │
                                                               └──▶ auth :8097 /auth/tunnel/token (mapped users only)
                                                                         /auth/admin/* (mapped administrators only)
adapter ──▶ rag :8090 ──▶ shared Ollama 127.0.0.1:11434 (Dokumentum AI, read-only)
adapter ──▶ clamav :3310          ui :8080 (loopback: admin over an SSH tunnel)
```

## Sharing the box

| The Dokumentum AI owns | The Matrix uses |
| --- | --- |
| :80, :443, UDP 1900 (its ViVeSecBox adapter), :8443, loopback 8098, 11000, 18083–18086, 18200 | loopback 8080, 8088, 8090, 8097, 3310, 18476; :7476 for the tunnel front |
| Ollama on 127.0.0.1:11434 with its models | that Ollama, read-only: no pull, no delete, the same `num_ctx` (65536) |
| `table inet vivesec_prep_v2` (ViVeSec prep firewall, rewritten every 30 s) | nothing, unless `MATRIX_TUNNEL_FIREWALL=dropin-dev` |

The installer refuses a setting that would use one of their ports. The
embedding model's digest is recorded at installation; when the Dokumentum AI
changes it, the installer stops until the index is rebuilt
(`MATRIX_ACCEPT_EMBED_CHANGE=yes` once afterwards), so vectors of two models
never mix in one index. The Ollama serves one request at a time
(`OLLAMA_NUM_PARALLEL=1` on the Dokumentum AI side): Matrix answers wait while
the other product generates, and the other way round.

## Tunnel trust

- The front accepts a connection only with a client certificate signed by the
  ViVeSecBox CA (`ssl_verify_client on`) and relays only `/api/v1/matrix/*`.
- The gateway accepts a request only with the front's forward key and
  `X-Matrix-Client-Verify: SUCCESS`; only then does it read `VVS-User`.
- The auth service maps that `VVS-User` to a local user an administrator set up
  (`manage.py tunnel-map`) and mints the same identity token as the web login.
  An unmapped or suspended identity gets nothing; `/api/v1/matrix/init`
  answers 400 for it.
- The adapter stays standalone with `ADAPTER_IDENTITY_MODE=require`: roles,
  groups and rooms come from the box's own user record, never from the tunnel.
- The ViVeSecBox `VVS-Drive` is ignored (it names a ViVeSecBox drive). The
  active room is the user's first room. Choosing a room through the tunnel
  needs a UI change (the tunnel carries no extra headers): open item.

Certificate of the front (`MATRIX_TUNNEL_PKI`):

| Mode | Use |
| --- | --- |
| `off` (default) | no front |
| `files` | `/etc/vivesec-matrix/tunnel-pki/{server.crt,server.key,ca.crt}`, e.g. a certificate the ViVeSecBox CA signed for the Matrix |
| `partner-dev` | **development Spark only**: copies the Dokumentum AI pairing certificate every 15 s and stops the front when it disappears (fail closed). It reads another product's private key, so it never goes to a customer box. |

The ViVeSec prep firewall opens only 22, 80, 443, 1900, 5353 and 8443.
`MATRIX_TUNNEL_FIREWALL=dropin-dev` re-adds an accept rule for the tunnel port
after each refresh (LAN only), on the development Spark. A customer box needs
the port in the prep firewall itself (`allow_matrix`, to be agreed).

## Bootloader (the UI inside the ViVeSecBox)

The ViVeSec hosts a small static app, `matrix_boot/` (`index.html` +
`matrix-boot.js`); their frame socket script defines `frameSocketFetcher` in it.
The frame socket carries only the page's own requests, so the bootloader fetches
the whole UI from the box in one piece and puts it into the page:

1. `GET /api/v1/matrix/app/manifest` (label, sha256 of the bundle);
2. the bundle from IndexedDB when the digest matches, otherwise
   `GET /api/v1/matrix/app/bundle/<sha256>`, checked against the manifest;
3. the prerendered shell goes into the page (inline scripts run, the module
   entry stays inert, every asset is a `data:` URL), then the client script runs
   as an inline module (a `blob:` URL if the page's CSP refuses inline scripts)
   and hydrates it;
4. `matrixApiPrefix = "/api/v1/matrix/"` makes the UI call
   `/api/v1/matrix/<x>` instead of `/api/v1/ui/<x>`.

The bundle therefore changes with every release of the box, the bootloader only
when its loading contract changes. The document store (uploads) is off inside
the ViVeSecBox embedding, as before: uploads go through the box's own UI.

```powershell
npm run build:matrix   # dist/matrix/app/{manifest,bundle}.json + dist/matrix/boot/ + matrix-ai-boot-<label>.zip
python scripts/spark/pack_kit.py matrix-kit.tgz --ui ui-output.tgz --app dist/matrix/app
```

The zip goes to the ViVeSec; the kit's `app/` is installed by the `tunnel`
phase to `/opt/vivesec-matrix/app` (bundle first, then the manifest), where the
gateway serves it (`MATRIX_TUNNEL_APP_DIR`).

Trying it without the ViVeSecBox (development): `sim/matrix_frame_sim.py` plays
the frame socket and the tunnel front on the box.

```bash
sudo python3 matrix_frame_sim.py --boot <dist/matrix/boot> --vvs-user <mapped VVS-User>
# from the workstation: ssh -L 8199:127.0.0.1:8199 vvsaibadmin@<spark>, open http://localhost:8199/
```

## Install

On the build machine, from a commit of this branch:

```powershell
npm ci
python scripts/pack_ui_bundle.py ui-output.tgz --build
npm run build:matrix
python scripts/spark/pack_kit.py matrix-kit.tgz --ui ui-output.tgz --app dist/matrix/app
scp matrix-kit.tgz vvsaibadmin@<spark>:/tmp/
```

On the Spark:

```bash
sudo mkdir -p /opt/vivesec-matrix-kit && sudo tar -xzf /tmp/matrix-kit.tgz -C /opt/vivesec-matrix-kit
sudo install -D -m 600 /opt/vivesec-matrix-kit/src/scripts/spark/matrix.conf.example /etc/vivesec-matrix/matrix.conf
sudo nano /etc/vivesec-matrix/matrix.conf
cd /opt/vivesec-matrix-kit/src/scripts/spark
sudo python3 spark_install.py --check
sudo python3 spark_install.py            # --resume after a failure, --only PHASE for one phase
sudo python3 spark_install.py status
```

Phases: `preflight dirs images clamav auth graph core tunnel verify` (`--list`). The
image phase builds on the Spark and needs Docker Hub and the package mirrors;
offline images come with the signed kit (M2). A failed container start brings
the previous container back.

### Relationship analysis and product behaviour

`MATRIX_GRAPH=on` adds the `graph` phase: Neo4j (`vivesec-neo4j`, bolt on
loopback) and the graph service (`vivesec-graph`, built from the kit, its tests
run inside the image first), and wires the adapter to it with the GPU broker.
Secrets are generated once under `/var/lib/vivesec-matrix/graph-secrets`; the
person-key salt stays with the Neo4j data. `MATRIX_GRAPH_PROFILE` picks the
domain profile (e.g. `bv`), `MATRIX_GRAPH_ADMINS` the users who manage it.

`ADAPTER_*` lines of `matrix.conf` (not of the environment) go to the adapter
as they are: the deployment's behaviour, such as `ADAPTER_APP_MODE=analyst`,
`ADAPTER_PURPOSE`, the languages. What the installer wires (listening address,
TLS, identity, integration, data paths, ClamAV, broker, models) is refused.
The kit carries no data; documents and records are loaded after the install.

## Users

A ViVeSecBox user's first visit through the tunnel creates its own user
(`vvs-<hash>`, role analyst, no rooms: it sees nothing until an administrator
gives it rooms; `MATRIX_TUNNEL_AUTOCREATE=off` turns this off). The owner of the
ViVeSecBox (its requests carry `VVS-Permissions: write,owner`) becomes the
administrator on its own visit, and gets the role back on every visit
(`MATRIX_TUNNEL_OWNER_ADMIN=on`, the default). A former owner stays an
administrator until another administrator removes the role. With the switch off,
or for a further administrator, one ViVeSecBox user is promoted from the
command line:

```bash
docker exec -it vivesec-auth python /app/auth_service/manage.py tunnel-map promote <VVS-User>
```

A local administrator for the web page, and mapping an identity by hand:

```bash
docker exec -it vivesec-auth python /app/auth_service/manage.py create-user admin --role admin --temporary
docker exec -it vivesec-auth python /app/auth_service/manage.py create-user anna --drive /storage/drives/kozos/
docker exec -it vivesec-auth python /app/auth_service/manage.py tunnel-map set <VVS-User> anna
docker exec -it vivesec-auth python /app/auth_service/manage.py tunnel-map show
```

The ViVeSecBox user an administrator promoted (or mapped to a local user with
the admin role) finds **Administration** in the account menu of the Matrix app.
The page opens over the app, and its calls go through the tunnel like every
other call: `/api/v1/matrix/admin/*` to the adapter, and
`/api/v1/matrix/auth/admin/<action>` to the auth service as that user (users,
AD groups, clock, sign-in events). The client certificate card of the box's own
HTTPS front is not shown there. Password change and sign-out stay with the
ViVeSecBox.

The web UI listens on loopback only. The local administrator reaches it over SSH:
`ssh -L 8080:127.0.0.1:8080 vvsaibadmin@<spark>`, then `http://localhost:8080`.
A ViVeSecBox user the tunnel refused shows up in the gateway's journal
(`journalctl -u vivesec-matrix-tunnel | grep 'identity not accepted'`), with the
`VVS-User` to map.

## Install from a USB stick (release kit, signed channel)

A Spark with internet installs a published release from a small stick:
`scripts/spark/usb/matrix-usb.sh`, `matrix.conf` (from `usb/matrix.conf.bv-pilot`
or `matrix.conf.example`, with `MATRIX_UPDATE=on` and the channel URL) and the
release kit `matrix-kit-<version>.tgz` (`matrix-kit.tgz` of the pack_release.py
output, renamed). On the Spark:

```bash
sudo bash /media/<user>/<stick>/matrix-usb.sh install   # nginx if missing, matrix.conf, bootstrap
sudo bash /media/<user>/<stick>/matrix-usb.sh admin     # first administrator, temporary password
sudo bash /media/<user>/<stick>/matrix-usb.sh ids       # ViVeSecBox users the tunnel refused
sudo bash /media/<user>/<stick>/matrix-usb.sh map <VVS-User> <user>
```

`install` runs `updater.py bootstrap` from a scratch copy of the kit: it trusts
the root in the kit, checks that the stick's kit is byte for byte the signed
release kit, downloads that release's images from the channel (TUF), loads
them, and only then unpacks the kit and runs every installer phase. Later
versions come through the administration page.

## Updates (signed release channel)

Releases travel as static files (TUF metadata + targets) on any HTTPS host; the
Spark trusts only the root metadata its kit brought. An administrator approves
every installation on the administration page (Updates tab); nothing installs by
itself.

```text
workstation  pack_kit.py --wheels --trusted-root  ->  build kit
build Spark  pack_release.py                        ->  release dir (images, release kit, release.json)
key holder   matrix_repo.py publish / refresh       ->  repository (metadata/, targets/)
host         upload: targets/ and metadata/<n>.*.json first, timestamp.json last
box          timer: updater.py check (6 h)  ->  admin approves  ->  updater.py request
```

Wheels of the update client for the Spark (aarch64, Python 3.12), once per
`matrix_updater/requirements.txt` change:

```powershell
python -m pip download -r matrix_updater/requirements.txt --platform manylinux_2_28_aarch64 `
  --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 --python-version 3.12 `
  --implementation cp --only-binary=:all: -d dist/wheels-aarch64
python scripts/spark/pack_kit.py matrix-kit.tgz --ui ui-output.tgz --app dist/matrix/app `
  --wheels dist/wheels-aarch64 --trusted-root <repo>/trusted_root.json
```

The repository and its keys (`scripts/release/matrix_repo.py`; needs
`pip install -r matrix_updater/requirements.txt`):

```powershell
python scripts/release/matrix_repo.py init --repo <repo> --keys <keys>        # development keys: labor only
python scripts/release/matrix_repo.py publish --repo <repo> --keys <keys> --channel labor <release dir>
python scripts/release/matrix_repo.py refresh --repo <repo> --keys <keys>     # at least every 14 days
python scripts/release/matrix_repo.py status --repo <repo>
```

Until the hardware keys come, the pilot and stable channels use software keys
encrypted with a passphrase (`init --encrypt`, `MATRIX_REPO_PASSPHRASE`, at
least 16 characters; development keys publish to labor only). On the
publishing workstation the passphrase is kept with Windows DPAPI, readable by
that Windows user only; a written copy goes to the safe:

```powershell
$h = "$env:USERPROFILE\.vivesec-matrix"
# once: a random passphrase, stored for this Windows user
$p = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | % { [char]$_ })
ConvertTo-SecureString $p -AsPlainText -Force | ConvertFrom-SecureString | Set-Content "$h\passphrase-main.dpapi"
# every session that signs
$env:MATRIX_REPO_PASSPHRASE = [Net.NetworkCredential]::new('', (Get-Content "$h\passphrase-main.dpapi" | ConvertTo-SecureString)).Password
python scripts/release/matrix_repo.py init --encrypt --repo "$h\repo-main" --keys "$h\keys-main"
Remove-Item Env:MATRIX_REPO_PASSPHRASE
```

Uploading (any static host; for the Google Cloud Storage test channel): the
targets first, then the versioned metadata, `timestamp.json` last and uncached:

```powershell
gcloud storage rsync <repo>\targets gs://<bucket>/<prefix>/targets --recursive
gcloud storage cp <repo>\metadata\[0-9]*.json gs://<bucket>/<prefix>/metadata/ --cache-control=no-cache
gcloud storage cp <repo>\metadata\timestamp.json gs://<bucket>/<prefix>/metadata/ --cache-control="no-cache, max-age=0"
```

On the build Spark, from the unpacked build kit (builds and runs the tests in the
images, retags `:latest` of adapter, rag and ui there):

```bash
sudo python3 pack_release.py --version 26.11.1.1 --out /var/tmp/matrix-26.11.1.1 \
  --notes-hu "..." --notes-en "..." [--min-from 26.10.1] [--security] [--data-copy] [--reindex]
```

`YY.MM.N` is a release (stable channel) and needs a kit built at the release
tag; `YY.MM.N.B` is a pilot or labor build. A release never goes back to an
older version, and `--min-from` names the oldest version it may be installed
over. `--data-copy` makes the updater copy the data aside before installing
(services stopped meanwhile); a rollback may put it back.

On the box, `matrix.conf`: `MATRIX_UPDATE=on`, `MATRIX_UPDATE_URL=https://...`,
`MATRIX_UPDATE_CHANNEL=stable|pilot|labor`; the `updater` phase unpacks the
wheels into `/opt/vivesec-matrix/updater/lib` (nothing goes into the host's
Python) and enables `vivesec-matrix-updater-check.timer` and
`vivesec-matrix-updater-request.path`. State: `/var/lib/vivesec-matrix/updater/`
(`state.json`, logs of every request under `logs/`); `python3 updater.py status`.

Installing: the updater hashes the downloaded files again, loads the images,
swaps the kit (the previous one stays as `/opt/vivesec-matrix-kit.prev`) and
runs its installer from the prebuilt images. When that fails, the previous kit
and image tags come back and the previous installer runs. Rolling back to a kit
that builds on the box (a development kit) builds again and needs internet.

## Tests

```powershell
Push-Location scripts/spark; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location matrix_tunnel; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location matrix_updater; python -m unittest discover -p "*_test.py"; Pop-Location   # needs python-tuf
Push-Location auth_service; python -m unittest discover -p "*_test.py"; Pop-Location
```
