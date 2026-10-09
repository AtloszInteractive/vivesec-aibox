"""Fleet overview (E01): version and pairing state of every installed AI Box in one place.

Read-only. Each box is queried for GET /api/v1/version (never /api/v1/status,
which would count as a ViVeSecBox presence poll and could keep the presence
watchdog from locking the storage) and for the UI's version.json.

  python scripts/release/fleet_status.py [--inventory FILE] [--json] [--strict]

Inventory (default scripts/release/fleet.json, git-ignored; see
fleet.example.json): a list of boxes, each reached either
  - over SSH (the administration channel, e.g. Tailscale):  "ssh": "aibox@aibox-01"
    the box itself curls its loopback ports, nothing is exposed;
  - or directly over HTTP:                                  "url": "http://10.0.0.5:8088"
Optional per box: "adapter_port" (80, as the install kit runs it), "ui_port" (8080),
"ui_url".

--strict  exit 1 when a box is unreachable, runs mixed builds, or the fleet
          runs more than one release.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INVENTORY = os.path.join(HERE, "fleet.json")
TIMEOUT = 15


def _http_json(url):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.load(response)


def _ssh_json(target, url):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target,
         "curl -fsS --max-time %d %s" % (TIMEOUT, url)],
        capture_output=True, text=True, timeout=TIMEOUT + 20)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or "ssh exit %d" % result.returncode)[-200:])
    return json.loads(result.stdout)


def fetch(box, http_json=_http_json, ssh_json=_ssh_json):
    """(version payload, ui build) for one box; raises when the adapter is unreachable."""
    # The install kit (40-app-deploy-jp6.sh) runs the adapter on port 80; 8088
    # is only the image default.
    adapter_port = int(box.get("adapter_port", 80))
    ui_port = int(box.get("ui_port", 8080))
    if box.get("ssh"):
        get = lambda url: ssh_json(box["ssh"], url)  # noqa: E731
        adapter_url = "http://127.0.0.1:%d" % adapter_port
        ui_url = box.get("ui_url") or "http://127.0.0.1:%d" % ui_port
    else:
        get = http_json
        adapter_url = box["url"].rstrip("/")
        ui_url = box.get("ui_url")
    payload = get(adapter_url + "/api/v1/version")
    ui = None
    if ui_url:
        try:
            ui = get(ui_url.rstrip("/") + "/version.json")
        except Exception:  # noqa: BLE001
            ui = None
    return payload, ui


def summarize(box, payload=None, ui=None, error=None):
    row = {"box": box.get("name") or box.get("ssh") or box.get("url"), "reachable": error is None,
           "error": error, "release": None, "adapter": None, "rag": None, "ui": None,
           "consistent": False, "paired": None, "ws_fs": None, "storage_locked": None}
    if error is not None:
        return row
    version = payload.get("version") or {}
    components = version.get("components") or {}
    row.update(release=version.get("release"),
               adapter=(components.get("adapter") or {}).get("label"),
               rag=(components.get("rag") or {}).get("label"),
               ui=(ui or {}).get("label"),
               consistent=version.get("consistent") is True,
               paired=payload.get("paired"),
               ws_fs=(payload.get("ws_fs") or {}).get("connected"),
               storage_locked=payload.get("storage_locked"))
    return row


def collect(boxes, fetcher=fetch):
    rows = []
    for box in boxes:
        try:
            payload, ui = fetcher(box)
            rows.append(summarize(box, payload, ui))
        except Exception as error:  # noqa: BLE001
            rows.append(summarize(box, error=str(error) or error.__class__.__name__))
    releases = sorted({row["release"] for row in rows if row["release"]})
    return {"boxes": rows, "releases": releases,
            "drift": len(releases) > 1,
            "unreachable": [row["box"] for row in rows if not row["reachable"]],
            "inconsistent": [row["box"] for row in rows if row["reachable"] and not row["consistent"]]}


def _flag(value):
    return {True: "yes", False: "no", None: "?"}[value]


def render(report):
    headers = ("BOX", "RELEASE", "ADAPTER", "RAG", "UI", "SAME BUILD", "PAIRED", "WS-FS", "LOCKED")
    table = [headers]
    for row in report["boxes"]:
        if row["reachable"]:
            table.append((row["box"], row["release"] or "?", row["adapter"] or "?", row["rag"] or "?",
                          row["ui"] or "?", _flag(row["consistent"]), _flag(row["paired"]),
                          _flag(row["ws_fs"]), _flag(row["storage_locked"])))
    widths = [max(len(str(line[i])) for line in table) for i in range(len(headers))]
    lines = ["  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(line)).rstrip() for line in table]
    for row in report["boxes"]:
        if not row["reachable"]:
            lines.append("%s  UNREACHABLE: %s" % (row["box"], row["error"]))
    lines.append("")
    lines.append("releases in the fleet: %s" % (", ".join(report["releases"]) or "none"))
    if report["drift"]:
        lines.append("WARNING: the fleet runs more than one release")
    for name in report["inconsistent"]:
        lines.append("WARNING: %s runs mixed builds (adapter/RAG differ or RAG unreachable)" % name)
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        with open(args.inventory, encoding="utf-8") as handle:
            boxes = json.load(handle)["boxes"]
    except (OSError, ValueError, KeyError) as error:
        print("cannot read inventory %s: %s (see fleet.example.json)" % (args.inventory, error),
              file=sys.stderr)
        return 2
    report = collect(boxes)
    print(json.dumps(report, indent=2) if args.json else render(report))
    if args.strict and (report["drift"] or report["unreachable"] or report["inconsistent"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
