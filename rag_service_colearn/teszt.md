# Teszteles teljes konyvtarbol

Ha a deploy csomagbol inditod a szolgaltatast, akkor a tesztfajlokat a deploy konyvtar alatti `data/corpora/<corpus-nev>/` konyvtarba tedd.

Pelda struktura:

```text
pageindexes-deploy/
  docker-compose.yml
  .env
  data/
    corpora/
      varkonyi-blog/
        elektromos-autok.md
        masik-cikk.md
        dokumentum.docx
```

A host oldali:

```text
pageindexes-deploy/data
```

be van mountolva a kontenerbe ide:

```text
/app/data
```

Ezert az API-ban ezt a path-t kell megadni:

```json
"path": "data/corpora/varkonyi-blog"
```

## Pelda futtatas

```bash
cd pageindexes-deploy

mkdir -p data/corpora/varkonyi-blog
cp /ahonnan/a/fajlok/vannak/* data/corpora/varkonyi-blog/

curl -X POST http://localhost:8080/index/rebuild \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: change-me' \
  -d '{
    "corpus_id": "varkonyi-blog",
    "tenant_id": "default",
    "path": "data/corpora/varkonyi-blog",
    "clear": true
  }'
```

## Tobb corpus

Ha tobb kulon indexet/corpust akarsz tesztelni:

```text
data/
  corpora/
    varkonyi-blog/
      ...
    ceges-dokuk/
      ...
    termekleirasok/
      ...
```

Kulon rebuild pelda:

```json
{
  "corpus_id": "ceges-dokuk",
  "path": "data/corpora/ceges-dokuk",
  "clear": true
}
```

A `path` konyvtar lehet almappas is. A rebuild rekurzivan beolvassa a tamogatott fajlokat.
