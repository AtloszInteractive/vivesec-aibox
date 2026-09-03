# Gold-set — kérdés-készlet az átvételi kapuhoz

A gold-set a **mérce**: ezen mérjük, hogy egy RAG-motor (baseline vagy CoLearn)
mennyire jó és **biztonságos**. Két split:

- **`dev.jsonl`** — ezen szabad **hangolni** (fejlesztés, paraméterek).
- **`holdout.jsonl`** — ezen csak **jelentünk**, sosem hangolunk rajta.

## Miért két split (gold-set governance)

A CoLearn fejlesztők társtulajdonosok a GapHopperben, így a **bizalmi** kérdés nem
éles — a valódi technikai ok az **overfitting**: ha ugyanazon a kérdés-készleten
hangolunk és mérünk is, a motor „bevágja" a választ, és a szám szebb lesz, mint a
valóság. Ezért a holdout-split a fejlesztés alatt **nem megy ki** a motor
fejlesztőjéhez; az átvételi kaput (`compare`) a holdouton futtatjuk.

> A fizikai „kinél van a gold-set" kérdés tehát másodlagos; a lényeg a
> **dev/holdout elválasztás**.

## Rekord-séma (egy JSON / sor)

| Mező | Köt. | Leírás |
|---|---|---|
| `id` | ✔ | Egyedi eset-azonosító |
| `query` | ✔ | A felhasználó kérdése |
| `lang` | – | `en`/`hu`/`da`/`de` |
| `allowed_file_ids` | ✔ | A híd ACL pre-filter eredménye — **kemény** szűrő |
| `relevant_file_ids` | ✔ | A helyes találat(ok) fájl-szinten (recall@k ezen mér) |
| `relevant_chunk_ids` | – | Chunk-szintű helyes találat (`--use-chunks`) |
| `forbidden_file_ids` | – | Sosem jelenhet meg (ACL-leak teszt) |
| `expected_tier` | – | Várt T1–T4 besorolás (dokumentáció; a routing külön mérhető) |
| `notes` | – | Megjegyzés |

## ACL-guard esetek

A `*-aclguard` esetek **szándékosan** olyan kérdést tesznek fel, ami tartalmilag
illik egy tiltott dokumentumra, de a `allowed_file_ids` nem engedi. A motornak
**0 találatot** kell adnia a tiltott fájlokból — **egyetlen** szivárgás megbuktatja
az egész futást (spec §2, 2. invariáns).

## Bővítés

Éles gold-set: ~30–60 eset / nyelv, valós (de anonimizált) kérdésekből, T1–T4 és
sokféle `doc_type` lefedésével. A jelenlegi fixture-ök szintetikusak és kicsik,
hogy a kapu reprodukálható és függőségmentes legyen.
