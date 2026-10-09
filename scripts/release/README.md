# Release baseline (E01)

Tooling that gives every AI Box release one identity and one record:

| File | Purpose |
| --- | --- |
| `../../VERSION` | The release number, calendar format `YY.MM.N` (e.g. `26.10.1`: the first release of October 2026). Single source of truth. |
| `build_info.py` | Computes the build identity (version + git commit) and stamps it into `adapter/` and `rag_service/`. |
| `config_registry.json` | Configuration register: every deployment parameter with default, image default, allowed range, fail-safe behaviour, impact and what a change requires. |
| `config_registry.py` | `check` (register vs. code, Dockerfiles, `install.conf.example`), `render` (writes `../../CONFIGURATION.md`), `validate` (a running box's environment). |
| `compatibility.json` | Compatibility conditions of the release (contracts, platform, models, data). Maintained by hand. |
| `release_manifest.py` | Release manifest written on the build machine. |
| `box_manifest.py` | Box manifest written on the box (`/data/app/MANIFEST.json`, called by `scripts/jetson/install/95-manifest.sh`). |
| `fleet_status.py` | Read-only fleet overview: version and pairing state of every box. |
| `release_test.py` | Tests for all of the above. |

## Version and source identifier

- Every component carries the same version: the adapter and the RAG service read
  their stamped `build_info.json`; both UI builds get the same record from
  `vite.config.ts` (`__AIBOX_BUILD__`, plus `version.json` next to the client
  assets: `.output/public/version.json`, `dist/client/version.json`).
- The source identifier is the git commit.
- Only a build at tag `v<VERSION>` with no modified tracked file is a release and
  is labelled exactly `<VERSION>`. Anything else is
  `<VERSION>-dev+<commit7>[.dirty]`, so a dev build is never mistaken for a
  release. An unstamped checkout reports `<VERSION>-dev`. Untracked files (such as
  a freshly packed `ui-output.tgz`) do not count as dirty.
- Visible in `/api/v1/status` and `/api/v1/version` (`version` block, see
  `adapter/README.md`), the RAG `/health` and `/stats`, the image labels
  `vivesec.version` / `vivesec.commit`, the embed archive name, and the UI footer
  under the composer (`v<label>`, hover for UI/box versions; a warning icon when
  they differ).

The version is not written into `package.json`: zero-padded months (`26.01.1`)
are not valid semver.

## Making a release

From a clean, up-to-date `main` on the build machine:

```powershell
# 1. set the release number and tag it
Set-Content VERSION "26.10.1" -NoNewline   # YY.MM.N; N restarts at 1 every month
git commit -am "Release 26.10.1"
git tag -a v26.10.1 -m "Release 26.10.1"
python scripts/release/build_info.py check  # must say "release build 26.10.1"

# 2. check the configuration register and stamp the services
python scripts/release/config_registry.py check
python scripts/release/build_info.py stamp

# 3. build and pack both UI forms (each carries version.json)
python scripts/pack_ui_bundle.py ui-output.tgz --build
npm run build:embed; python scripts/pack_embed.py   # -> aibox-ui-embed-26.10.1.zip

# 4. write the release manifest over the delivered artifacts
python scripts/release/release_manifest.py --require-release `
  --artifact ui-output.tgz --artifact aibox-ui-embed-26.10.1.zip
git push origin main v26.10.1
```

If `compatibility.json` changed (contract, model, platform, reindex), commit it
before tagging. The manifest records: code fingerprints per component, artifact
hashes, image build recipes and expected labels, models, image configuration
defaults (no secrets), policy parameters, compatibility conditions and the hash of
the configuration register.

On the box, `10-build-images.sh` stamps the source tree (or keeps the stamp a
delivered tree without `.git` was shipped with), refuses a UI bundle of another
release version, and labels the three images. `95-manifest.sh` then writes
`MANIFEST.txt` and the machine-readable `MANIFEST.json`, which records the
running versions, image ids and labels, Ollama model digests, the non-secret
runtime configuration validated against the register, the pairing state and the
result of every compatibility check.

## Configuration register

`CONFIGURATION.md` in the repository root is generated from
`config_registry.json`; do not edit it by hand. When code reads a new
`ADAPTER_*`, `RAG_*`, `VIVESEC_*` or `OLLAMA_*` variable, or a Dockerfile default
changes, add or update the entry and re-render:

```powershell
python scripts/release/config_registry.py check
python scripts/release/config_registry.py render
```

Check a running box (the env dump contains secrets: keep it on the box):

```bash
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter > /tmp/env.txt
python3 scripts/release/config_registry.py validate /tmp/env.txt; rm /tmp/env.txt
```

It reports invalid numbers, unknown enum values, misspelled parameter names and
dev-only switches left on; secret values are never printed.

## Fleet overview

Copy `fleet.example.json` to `fleet.json` (git-ignored) and list the boxes. A box
with `"ssh"` is reached over the administration channel (Tailscale) and queried
on its own loopback, so no port is exposed; `"url"` queries the adapter directly.

```powershell
python scripts/release/fleet_status.py            # table
python scripts/release/fleet_status.py --json     # machine-readable
python scripts/release/fleet_status.py --strict   # exit 1 on drift, mixed builds or unreachable boxes
```

The overview is read-only: it calls `GET /api/v1/version` and the UI's
`version.json`, never `/api/v1/status` (a status call counts as a ViVeSecBox
presence poll).

## Tests

```powershell
Push-Location scripts/release; python -m unittest discover -p "*_test.py"; Pop-Location
```
