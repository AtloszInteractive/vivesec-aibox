# ViVeSec AI Box - installation kit

Scripted installation of a Jetson AGX Orin AI Box on JetPack 6 (L4T R36.4.4).

**The full procedure, including flashing, parameters, troubleshooting and the
handover checklist, is described in the installation manual:**
`docs/ViVeSec_AIBox_Telepitesi_Kezikonyv.md` (Hungarian).

## Quick reference

```bash
cp install.conf.example install.conf   # fill in the box-specific values
nano install.conf

sudo bash 00-install-all.sh --check    # preflight, changes nothing
sudo TS_AUTH_KEY=tskey-auth-... bash 00-install-all.sh   # full installation
sudo bash 00-install-all.sh --resume   # continue after a failure
sudo bash 00-install-all.sh --list     # list the phases
```

The `harden` phase disables password SSH login and joins the box to the
Tailscale tailnet for remote administration. It **blocks** until a new
key-based login is confirmed from a second terminal:

```bash
ssh -o PasswordAuthentication=no aibox@<box-ip>
sudo bash ~/vivesec_iabox_app/scripts/jetson/install/05-harden-host.sh --confirm-ssh
```

Without confirmation within the grace window (15 min) the hardening reverts
itself and the phase fails. `--rollback-ssh` removes it manually.

## Files

| File | Purpose |
| --- | --- |
| `install.conf.example` | Parameter template - copy to `install.conf` and edit |
| `00-install-all.sh` | Phase orchestrator, logging and resume handling |
| `05-harden-host.sh` | Key-only SSH (root-owned admin keys) + Tailscale remote access, pairing-safe |
| `10-build-images.sh` | Builds the three application images, keeps rollback tags, stamps and labels the release version and refuses a UI bundle of another release |
| `20-pull-models.sh` | Downloads the embedding and generation models |
| `90-acceptance-audit.sh` | Read-only handover audit, one PASS/FAIL line per check |
| `95-manifest.sh` | Writes `/data/app/MANIFEST.txt`, the delivery record, and `/data/app/MANIFEST.json`, the machine-readable release manifest of the box (`scripts/release/box_manifest.py`) |

The orchestrator drives the existing phase scripts in `scripts/jetson/`
(`10b-base-jp6.sh`, `20-ollama-jp6.sh`, `30-app-storage-jp6.sh`,
`40-app-deploy-jp6.sh`, the `verify-*` scripts) and `scripts/provision_jetson_nvme.sh`.
Those scripts remain the authoritative definition of what gets installed and
with which settings.

`90-acceptance-audit.sh` performs no writes and can be run at any time on a
live box for a health check. Exit code 0 means every check passed.

## Preparing the delivery package

The installer needs four things on the box. Three of them come straight from
this repository; the fourth has to be built first.

| Item | Where it comes from |
| --- | --- |
| Application source tree | This repository (`adapter/`, `rag_service/`, `poc/`, `scripts/`) |
| Installation kit | This directory |
| Installation manual | `docs/ViVeSec_AIBox_Telepitesi_Kezikonyv.md` (+ `.docx`) |
| **Web UI bundle (`ui-output.tgz`)** | **Built on a workstation - see below** |

### Building the web UI bundle

The Jetson deliberately does not run the web toolchain, so the interface is
compiled on a workstation and shipped as a tarball. From the repository root:

```bash
npm install
python scripts/pack_ui_bundle.py ui-output.tgz --build
```

`--build` runs `npm run build` first; drop it to pack an existing `.output`.
The script verifies the archive after writing it and fails if the layout is
wrong, so a broken bundle never reaches the box.

**The archive layout is fixed:** `.output` must sit at the root of the archive,
because `10-build-images.sh` extracts the tarball and then expects
`.output/server/index.mjs`. Packing by hand from inside `.output` produces an
archive that fails the build phase - this is why the script exists.

Manual equivalent, if the script is not available:

```bash
npm run build
tar -czf ui-output.tgz .output      # from the repository root, not from inside .output
tar -tzf ui-output.tgz | head -3    # expected: .output/ , .output/server/... 
```

Rebuild the bundle whenever the interface changes - the installation kit has no
way to detect a stale one.

### Release version

Every delivery is one release with one calendar version (`YY.MM.N`, the
repository `VERSION` file) on the adapter, the RAG service and the UI; the
release procedure is in `scripts/release/README.md`. Build the UI bundle from
the same release as the source tree: `10-build-images.sh` reads
`.output/public/version.json` from the bundle and stops if its version differs
from the source tree's. When the source tree is delivered without `.git`, stamp
it on the build machine first (`python scripts/release/build_info.py stamp`) so
the commit travels with it. All parameters the box accepts are listed in
`CONFIGURATION.md` in the repository root.

