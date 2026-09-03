#!/usr/bin/env bash
# Deploy előtti állapotfelmérés a boxon.
echo "=== arch / kernel ==="
uname -m; uname -r
echo
echo "=== futó konténerek ==="
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}'
echo
echo "=== adapter env (voice + port) ==="
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter 2>/dev/null \
  | grep -E '^ADAPTER_(PORT|HOST|STT|TTS|GEN_MODEL|THINK|NUM_PREDICT|TENANT)' || echo "(nincs vivesec-adapter)"
echo
echo "=== adapter bindek ==="
docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter 2>/dev/null || true
echo
echo "=== adapter forrás a boxon ==="
ls -la "$HOME/adapter-src" 2>/dev/null | head -5 || echo "(nincs ~/adapter-src)"
echo "voice.py: $( [ -f "$HOME/adapter-src/voice.py" ] && echo VAN || echo NINCS )"
echo
echo "=== UI bundle ==="
ls -d "$HOME/ui-app/.output" 2>/dev/null || echo "(nincs ~/ui-app/.output)"
docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-ui 2>/dev/null || true
echo
echo "=== hely ==="
df -h / /data 2>/dev/null | tail -3
echo
echo "=== ollama ==="
curl -s -m 5 http://127.0.0.1:11434/api/version || echo "(nem válaszol)"
echo
echo "=== foglalt portok (8080/8088/8090/8095/8096) ==="
ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | grep -E ':(8080|8088|8090|8095|8096)$' || echo "(egyik sem foglalt a felsoroltak közül)"
echo
echo "=== python / pip a hoston ==="
python3 -V 2>/dev/null; pip3 --version 2>/dev/null | head -1 || echo "(nincs pip3)"
echo
echo "=== van-e internet a boxon (image-húzáshoz) ==="
curl -s -o /dev/null -w 'registry-1.docker.io HTTP %{http_code}\n' -m 10 https://registry-1.docker.io/v2/ || echo "(nincs kifelé út)"
