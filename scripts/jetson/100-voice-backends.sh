#!/usr/bin/env bash
# Voice mode élesítése: az adapter STT/TTS végpontjainak bekötése a boxon futó
# beszédmotorokhoz. A hangadat SOHA nem hagyja el a dobozt.
#
# A 80/84-es szkriptek mintája: nem épít image-et, csak a FUTÓ adapter-konténert
# hozza újra létre, az env-et és a bindeket örökölve, a voice-kulcsokat pedig
# EXPLICITEN felülírva (öröklésnél a régi/hiányzó érték menne tovább).
#
#   bash 100-voice-backends.sh --check          csak megméri a két motort
#   bash 100-voice-backends.sh                  mér + bekötteti az adapterbe
#   bash 100-voice-backends.sh --off            voice kikapcsolása (env törlés)
#
# Env:
#   STT_URL   OpenAI-kompatibilis átirat-végpont (faster-whisper / speaches)
#   TTS_URL   Piper HTTP szerver
#   STT_MODEL a whisper modell neve a szervernél
#   STATUS_URL http://127.0.0.1:80/api/v1/status  a DEMO boxon (adapter a :80-on)
#
# ⚠ A beszédmotorokat magukat NEM ez a szkript telepíti — a Jetsonon arm64
#   image-et kell választani, és a tag-et a boxon kell ellenőrizni. A whisper
#   oldalnak a böngésző WebM/Opus felvételét kell fogadnia (ffmpeg-es build);
#   a sima whisper.cpp szerver 16 kHz WAV-ot vár, ahhoz konvertáló kell elé.
set -eu

STT_URL=${STT_URL:-http://127.0.0.1:8095/v1/audio/transcriptions}
TTS_URL=${TTS_URL:-http://127.0.0.1:8096/}
STT_MODEL=${STT_MODEL:-Systran/faster-whisper-small}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
MODE=${1:-}
TS=$(date +%Y%m%d-%H%M)

probe() {
  echo "=== 1. beszédmotorok elérhetősége ==="
  echo -n "  STT $STT_URL -> "
  # 400/422 is JÓ: a végpont él, csak a payload üres — a 000 a halott.
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "$STT_URL" || true)
  echo "HTTP $code"
  [ "$code" = "000" ] && echo "    FIGYELEM: nem válaszol"

  echo -n "  TTS $TTS_URL -> "
  out=$(mktemp)
  code=$(curl -s -o "$out" -w '%{http_code}' -m 30 -X POST "$TTS_URL" \
         -H 'Content-Type: text/plain; charset=utf-8' \
         --data-binary 'Voltara Energy Group.' || true)
  echo "HTTP $code, $(wc -c <"$out") bájt"
  if [ "$code" = "200" ] && [ "$(head -c4 "$out")" = "RIFF" ]; then
    echo "    OK: valódi WAV jött vissza"
  elif [ "$code" != "000" ]; then
    echo "    FIGYELEM: 200/WAV-ot vártunk"
  fi
  rm -f "$out"
}

probe
[ "$MODE" = "--check" ] && exit 0

echo
echo "=== 2. adapter újralétrehozása (env/bind örökölve, voice-kulcsok felülírva) ==="
docker inspect vivesec-adapter >/dev/null 2>&1 || { echo "  ABORT: nincs vivesec-adapter konténer"; exit 1; }

envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -vE '^(ADAPTER_STT_URL|ADAPTER_TTS_URL|ADAPTER_STT_MODEL)=')
if [ "$MODE" != "--off" ]; then
  envargs+=( -e "ADAPTER_STT_URL=$STT_URL" -e "ADAPTER_TTS_URL=$TTS_URL" \
             -e "ADAPTER_STT_MODEL=$STT_MODEL" )
fi

bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)

docker rename vivesec-adapter "vivesec-adapter-prevcfg-$TS"
docker stop "vivesec-adapter-prevcfg-$TS" >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 8

echo
echo "=== 3. mit mond magáról az adapter ==="
if curl -fsS -m 20 -X POST "$STATUS_URL" -H 'Content-Type: application/json' -d '{}' \
   | tr ',' '\n' | grep -A2 -i '"voice"' | sed 's/^/  /'; then :; else
  echo "  ABORT: az adapter nem válaszol -> visszaállítás"
  docker rm -f vivesec-adapter >/dev/null 2>&1 || true
  docker rename "vivesec-adapter-prevcfg-$TS" vivesec-adapter
  docker start vivesec-adapter >/dev/null
  exit 1
fi

echo
echo "KÉSZ. Visszaállás:  docker rm -f vivesec-adapter && docker rename vivesec-adapter-prevcfg-$TS vivesec-adapter && docker start vivesec-adapter"
echo "Takarítás a demó után:  docker rm vivesec-adapter-prevcfg-$TS"
