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
sudo bash 00-install-all.sh            # full installation
sudo bash 00-install-all.sh --resume   # continue after a failure
sudo bash 00-install-all.sh --list     # list the phases
```

## Files

| File | Purpose |
| --- | --- |
| `install.conf.example` | Parameter template - copy to `install.conf` and edit |
| `00-install-all.sh` | Phase orchestrator, logging and resume handling |
| `10-build-images.sh` | Builds the three application images, keeps rollback tags |
| `20-pull-models.sh` | Downloads the embedding and generation models |
| `90-acceptance-audit.sh` | Read-only handover audit, one PASS/FAIL line per check |
| `95-manifest.sh` | Writes `/data/app/MANIFEST.txt`, the delivery record |

The orchestrator drives the existing phase scripts in `scripts/jetson/`
(`10b-base-jp6.sh`, `20-ollama-jp6.sh`, `30-app-storage-jp6.sh`,
`40-app-deploy-jp6.sh`, the `verify-*` scripts) and `scripts/provision_jetson_nvme.sh`.
Those scripts remain the authoritative definition of what gets installed and
with which settings.

`90-acceptance-audit.sh` performs no writes and can be run at any time on a
live box for a health check. Exit code 0 means every check passed.
