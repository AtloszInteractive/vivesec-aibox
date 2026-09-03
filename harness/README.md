# harness — paritás- / eval-harness (átvételi kapu)

Ez a **dev/CI eszköz**, amivel eldöntjük, hogy egy RAG-motor elég jó-e. **Nem
kerül** a készülékre. Pontosan a CoLearn-modell kapuja:

> Mi szállítjuk a **baseline** motort. A CoLearn motorját **csak akkor** cseréljük
> be, ha a harness bizonyítja, hogy **legalább olyan jó** ÉS **semmit nem szivárogtat**.

Ugyanazt a **v1 retrieval-kontraktust** hajtja, amit a híd (adapter) használ a
`rag_service` felé (`ViVeSec_AIBox_RAG_Interfesz_Spec.md` /
`drive_sync_api_spec.txt`): `/health`, `/ingest`, `/rag/search_context`,
`/index/drop/tree` — így pontosan úgy mér, ahogy az éles hívás.

Az izoláció **`corpus_id` szerinti** (egy drive = egy korpusz); ez a korpusz-határ
**maga az ACL elő-szűrő**. A harness ezt közvetlenül teszteli: minden fixture-dok a
saját korpuszába kerül, és egy gold-eset `allowed_file_ids` mezője dönti el, **mely
korpuszokat** érintheti a kérés. Tiltott dokumentum más korpuszban van, így a
korpusz-szkópú keresés **strukturálisan** nem adhatja vissza.

## Mit mér

**Minőség:** recall@k, precision@k, MRR, citation-rate (megjelent-e releváns passzus).
**Biztonság:** ACL-leak — hány találat esik az engedélyezett halmazon kívülre (vagy a
tiltott halmazba). **Ennek 0-nak kell lennie**; egyetlen szivárgás megbuktatja a futást.
**Teljesítmény:** latency p50 / p95.

## Átvételi kapu (`compare`)

A candidate (CoLearn) akkor cserélheti le a baseline-t, ha a **holdout** spliten:

1. **biztonság:** ACL-leak = 0 (kemény, nem alku tárgya)
2. **recall@k** ≥ baseline − eps
3. **citation-rate** ≥ baseline − eps
4. **latency p95** ≤ budget (alap: 1500 ms)

## Felépítés

| Fájl | Szerep |
|---|---|
| `client.py` | stdlib v1-kliens (`/health`, `/ingest`, `/rag/search_context`, `/index/drop/tree`) |
| `corpus.py` | fixture-korpusz betöltés + ingest; SMOKE: fájlonként egy korpusz, FULL: **drive-onként egy korpusz, sok fájllal** (path-alapú hit-feloldás) |
| `metrics.py` | recall@k / precision@k / MRR / citation / **ACL-leak** / latency |
| `runner.py` | egy motor futtatása egy gold-spliten → eset- és aggregált eredmény |
| `compare.py` | paritás-tábla + átvételi kapu (baseline vs candidate) |
| `cli.py` | belépési pont (`run`, `compare`, `system`), `--profile smoke|full` |
| `gen_corpus.py` | a **FULL profil generátora**: ~44 dok / 3 drive / ~110 kérdés, seed-determinisztikus (ültetett tények → gold-kérdések), mtime-szórás, edge-case fájlok |
| `adapter_client.py` | **2. szintű kliens**: az adapter ÉLES felülete (VVS-Drive header, sync-push check→content, `/ui/ask`+`/ui/poll` long-poll) |
| `system_eval.py` | **2. szintű rendszer-eval**: válasz-szintű metrikák — refusal-helyesség, answer-hit, citáció-validítás, **szám-grounding**, **answer-leak=0**, e2e latency |
| `fixtures/` | SMOKE: kézzel írt mini-korpusz (3 dok) — gyors regresszió |
| `fixtures_full/` | FULL: generált több-fájlos drive-korpusz (finance/hr/board) — a kapu szettje |
| `gold_set/` | `dev.jsonl`, `holdout.jsonl` (smoke) + `dev_full.jsonl`, `holdout_full.jsonl` (generált), governance README |

## Futtatás

```powershell
# 1) indítsd a rag_service-t (másik mappából), pl. fallback-embeddinggel devhez:
#    cd ../rag_service ; $env:VIVESEC_BACKEND="fallback"; python service.py   # :8090

# 2) egy motor kiértékelése a dev spliten (smoke = alapértelmezett profil):
python cli.py --split dev run --engine-url http://127.0.0.1:8090

# 2b) a TELJES (generált) kapu-szett futtatása:
python cli.py --profile full --split holdout run --engine-url http://127.0.0.1:8090

# 2c) a FULL szett újragenerálása (determinisztikus, seed=42):
python gen_corpus.py

# 3) paritás-kapu: candidate (CoLearn, 8091) vs baseline (ours, 8090), holdouton:
python cli.py --profile full --split holdout compare `
    --baseline-url http://127.0.0.1:8090 `
    --candidate-url http://127.0.0.1:8091 `
    --latency-budget-ms 1500

# 4) 2. SZINTŰ rendszer-eval a VALÓS production-úton (adapter /api/v1/ui/*):
#    sync-push ingest -> retrieval -> generálás -> válasz-szintű pontozás.
#    A --cleanup a futás után eldobja az eval-drive-okat a boxról.
python cli.py --profile full --split holdout system `
    --adapter-url http://192.168.0.181:8088 --cleanup
#    gyors minta:  --limit 12   ·   generálás nélküli környezetben (nincs
#    Ollama) a válasz-metrikák kihagyódnak, a transport/leak/citáció élő.
```

A kilépési kód nem-nulla, ha van ACL-leak vagy a kapu blokkolja a candidate-et —
így a CI elbukik. Token (opcionális): `--token` vagy `RAG_API_KEY` env (`X-API-Key`).

> Csak stdlib — nincs külső függőség. A `fixtures/` korpusz szintetikus
> (nem valós adat), hogy a kapu reprodukálható legyen.
