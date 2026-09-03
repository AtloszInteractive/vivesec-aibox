"""Post-deploy check on the demo box: status, voice capability, feedback route."""
import json
import urllib.request

req = urllib.request.Request("http://127.0.0.1:80/api/v1/status",
                             data=b"{}", method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=20) as r:
    s = json.loads(r.read().decode("utf-8"))

print("ui_ready :", s.get("ui_ready"), "| fs_ready:", s.get("fs_ready"),
      "| locked:", s.get("storage_locked"))
print("voice    :", s.get("voice"), "(nincs STT/TTS motor a demo boxon -> off)")
print("features :", s.get("features"))
print("index    :", s.get("index"))
