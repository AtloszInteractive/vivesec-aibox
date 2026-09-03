# ViVeSec-box szimulátor + AIBox `/index/*` fogadó (FA.1)

Mock-first fejlesztéshez: a ViVeSec-platform push-alapú szinkronjának és az AIBox
sync/query interfészének szimulációja, hogy az **FA.2 determinisztikus ingest**
(kritikus út) a valós box nélkül is fejleszthető és tesztelhető legyen.

Stdlib-only (Python 3.8+), újrahasználja a `poc/` embedding+chunking láncot
(bge-m3 a Jetsonon, hashing-fallback dev-gépen Ollama nélkül).

## Komponensek

| Fájl | Szerep |
|------|--------|
| `aibox_index.py` | AIBox-oldali index-store: path-metaadat, két-fázisú `check`→`content`, path-szegmens-érzékeny `drop/tree`, thread-safe token-map, ACL pre-filter |
| `aibox_server.py` | AIBox HTTP-szerver: `/api/v1/index/*` + `/api/v1/ui/query` (drive-prefix hard-filter) + `/api/v1/status` (watchdog) |
| `vivesecbox_sim.py` | ViVeSec-box mock: minta-drive-ok, diff-szinkron push (check→content + reconcile drop/tree), VVS-User/VVS-Drive injekció, soros/párhuzamos kapcsoló |
| `wsfs_box.py` | ViVeSec-box **fogadó** oldala (`/api/v1/ws/fs`): a generált dokumentumokat leírja a `drives/<drive>/` mappába, így a UI „Mentés a drive-ra" gombja dev-gépen is valódi fájlt eredményez |
| `drives/` | Minta-drive-ok, köztük szóközös nevű (`beta dev 2`) és a szegmens-teszthez `beta2` |

## Futtatás

Két terminál. Dev-gépen `VIVESEC_BACKEND=fallback` (nem kell Ollama); Jetsonon
hagyd el → bge-m3-at használ.

```powershell
# 1) AIBox szerver
$env:VIVESEC_BACKEND="fallback"; python sim/aibox_server.py

# 2) Szimulátor (másik terminál)
python sim/vivesecbox_sim.py drives                       # drive-ok listája
python sim/vivesecbox_sim.py sync                         # teljes diff-szinkron
python sim/vivesecbox_sim.py sync --drive "beta dev 2" --parallel 4
python sim/vivesecbox_sim.py query --drive finance "Q4 revenue?"
python sim/vivesecbox_sim.py delete --drive "beta dev 2"  # teljes-drive törlés
```

A Jetson AIBox ellen: `--aibox http://192.168.0.181:8088` (vagy `AIBOX_URL` env).

### Fájl-mentés fogadása (ws-fs)

Harmadik terminál; amíg fut, a UI mentései a `sim/drives/<drive>/` alá kerülnek:

```powershell
python sim/wsfs_box.py                      # 127.0.0.1:8088
python sim/wsfs_box.py --subdir generated   # aldrive-mappába
python sim/wsfs_box.py --reject permission  # letöltés-fallback tesztje
python sim/wsfs_box.py --reject temporary   # újrapróbálkozás tesztje
```

A fogadó a valódi box szerepét játssza: keepalive-t küld, a `put-file`-t nyugtázza
és visszaadja a drive-beli útvonalat, amit a UI a visszaigazolásban mutat.

## Mit bizonyít (igazolt füstteszt)

- **Diff-szinkron**: első sync push-ol, újra-sync mind skip (mtime+size diff).
- **Két-fázis**: `check`(path,size,mtime,head 256B) → token vagy `null` → `content/{token}`.
- **ACL hard-filter**: a `VVS-Drive` prefix kötelező; a query csak az adott drive path-jaiból ad találatot.
- **Szóköz a drive-névben**: `beta dev 2` egy literál, sosem tokenizálva.
- **Szegmens-izoláció**: a `beta dev 2` lekérdezés/törlés SOSEM érinti a `beta2`-t.
- **Teljes-drive törlés**: egy `drop/tree` a drive-gyökérre.
- **Párhuzamosság**: `--parallel N` egyidejű feltöltés, thread-safe index-írás.
- **Encoding nyitott kérdés**: `--space raw|pct` mutatja a `%20` hatását (ezt a ViVeSec-kel tisztázni kell).

## Kapcsoló / env

| Kapcsoló / env | Jelentés |
|----------------|----------|
| `--parallel N` | soros (1) vs N-párhuzamos feltöltés |
| `--space raw\|pct` | VVS-Drive szóköz nyersen vagy `%20` (nyitott kérdés) |
| `AIBOX_URL` / `--aibox` | AIBox cím (default `http://127.0.0.1:8088`) |
| `AIBOX_PORT`, `AIBOX_HOST` | szerver kötés |
| `AIBOX_INDEX_PATH` | JSON-perzisztencia (default in-memory) |
| `AIBOX_MAX_FILE_BYTES` | méret-küszöb a `check`-ben (felette metadata-only) |
| `VIVESEC_BACKEND=fallback` | hashing-embedding Ollama nélkül (dev) |

## Még nem valós (mock-egyszerűsítés)

- Az extractor csak szöveget bont (txt/md); PDF/DOCX → MarkItDown/Docling később (FA.2).
- Nincs mTLS/`init/commit`/LUKS — a köztes tárolás itt RAM, a valós boxon
  titkosított index + cleartext sosem titkosítatlan lemezen.
- A `/status` watchdog csak jelez, nem zár (a valós box lezárja a LUKS-kötetet).
