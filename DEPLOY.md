# Deploying ViVeSec to Google Cloud Run

This app is a TanStack Start **SSR** application. It is built into a Node server
bundle (`.output/server/index.mjs`) via Nitro's `node-server` preset and runs in a
container on **Cloud Run**. Cloud Run injects a `PORT` env var (8080) which the
Nitro server honours automatically.

## Prerequisites

- A Google Cloud project with billing enabled.
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and
  authenticated: `gcloud auth login`.
- Set your project and region:

  ```bash
  gcloud config set project YOUR_PROJECT_ID
  gcloud config set run/region europe-west1
  ```

- Enable the required APIs (once per project):

  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
  ```

## Option A — Deploy straight from source (simplest)

Cloud Build picks up the `Dockerfile`, builds the image, and deploys it:

```bash
gcloud run deploy vivesec-iabox \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --port 8080
```

On success the command prints a public URL like
`https://vivesec-iabox-xxxxxxxx-ew.a.run.app` — that's your link.

> Drop `--allow-unauthenticated` if the app should require Google authentication.

## Option B — Build and push the image manually

```bash
# 1. Create an Artifact Registry repo (once)
gcloud artifacts repositories create web \
  --repository-format=docker \
  --location=europe-west1

# 2. Build & push (Cloud Build)
gcloud builds submit \
  --tag europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/web/vivesec-iabox:latest

# 3. Deploy
gcloud run deploy vivesec-iabox \
  --image europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/web/vivesec-iabox:latest \
  --region europe-west1 \
  --allow-unauthenticated \
  --port 8080
```

## Local container test (optional)

```bash
docker build -t vivesec-iabox .
docker run --rm -p 8080:8080 -e PORT=8080 vivesec-iabox
# open http://localhost:8080
```

## Local build test without Docker

```bash
npm install
npm run build
PORT=8080 node .output/server/index.mjs
# open http://localhost:8080
```

## Custom domain: vvs.gaphopper.com

Cloud Run natívan támogatja az egyéni domaineket domain mapping-gel.

### 1. Deploy a szolgáltatás (ha még nem fut)

```bash
gcloud run deploy vivesec-iabox \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --port 8080
```

### 2. Domain mapping létrehozása

```bash
gcloud beta run domain-mappings create \
  --service vivesec-iabox \
  --domain vvs.gaphopper.com \
  --region europe-west1
```

A parancs kilistázza a szükséges DNS rekordokat, pl.:

```
CNAME  vvs  ghs.googlehosted.com.
```

### 3. DNS beállítás (a gaphopper.com domain regisztrátorodnál)

Add hozzá a Google által kiírt rekordot — általában:

| Típus | Hostnév | Érték |
|-------|---------|-------|
| CNAME | `vvs` | `ghs.googlehosted.com.` |

> A pontos értéket a `gcloud beta run domain-mappings describe` parancs adja meg.

### 4. SSL tanúsítvány

A Google automatikusan kiad Let's Encrypt tanúsítványt — a DNS propagáció (5-30 perc) után az app HTTPS-en lesz elérhető a `https://vvs.gaphopper.com` címen.

### Ellenőrzés

```bash
gcloud beta run domain-mappings describe \
  --domain vvs.gaphopper.com \
  --region europe-west1
```

A `certificateMode: AUTOMATIC` és `status: ACTIVE` jelzi, hogy minden rendben.

---

## PWA notes

- The web app manifest is served at `/manifest.webmanifest`, the service worker at
  `/sw.js`, and the offline fallback at `/offline.html`.
- **Icons must be added** to `public/icons/` before installability works:
  - `icon-192.png` (192×192)
  - `icon-512.png` (512×512)
  - `icon-192-maskable.png` (192×192, ~20% padding)
  - `icon-512-maskable.png` (512×512, ~20% padding)
  - `apple-touch-icon.png` (180×180)
- PWA install requires HTTPS — Cloud Run serves HTTPS by default, so installation
  works out of the box on the `run.app` URL.
