# User manual

The end-user manual of the Matrix AI web UI. Hungarian first; other languages follow the same
structure (`manual/<lang>/`).

```text
manual/
  hu/index.md          table of contents
  hu/01-…08-*.md       one chapter per file
  hu/img/*.png         screenshots, referenced as img/<name>.png
  tools/               screenshot capture (dev only, not shipped)
```

## Writing rules

- Quote UI labels exactly as the UI shows them, in **bold**. Look them up in
  `src/components/vivesec/i18n-data.ts` (English source → `[hu, da, de]`) or `i18n.tsx`.
- Plain CommonMark, relative links and images only (`img/x.png`, `02-belepes.md`), no HTML:
  the chapters are meant to be rendered later as in-app help.
- Screenshots show synthetic data only. Mask secrets (QR codes, keys) with the `mask` option.
- When the UI changes, update the affected chapter and recapture its screenshots.

## Recapturing the screenshots

The tools sign in to a running Matrix AI and capture 1440×900 screenshots in Hungarian, light
theme. They need two dedicated accounts: an `analyst` with rooms and an `admin`. Create them on
the **Felhasználók** admin tab with a temporary password, and delete them afterwards.

Sessions and TOTP secrets are written to `MANUAL_STATE_DIR`, which must be **outside the
repository**.

```powershell
cd manual\tools
npm install
npx playwright install chromium

# Dev Spark UI over SSH: ssh -N -L 18080:127.0.0.1:8080 vvsaibadmin@<spark>
$env:MANUAL_BASE_URL = "http://localhost:18080"
$env:MANUAL_STATE_DIR = "$env:LOCALAPPDATA\vivesec-manual"
$env:MANUAL_USER = "kezikonyv";           $env:MANUAL_PASS = "<temporary password>"
$env:MANUAL_NEW_PASS = "<new password>"   # used if the box forces a change
$env:MANUAL_ADMIN_USER = "kezikonyv-admin"; $env:MANUAL_ADMIN_PASS = "<temporary password>"
$env:MANUAL_ADMIN_NEW_PASS = "<new password>"
$env:MANUAL_SHOTS = "1"                    # also capture the sign-in screens

node login.mjs                             # first run registers TOTP, sets the passwords
node capture.mjs                           # all screenshots
node capture.mjs valasz admin-09           # only names containing a filter
```

Run `login.mjs` right before `capture.mjs`: admin sessions expire quickly. On later runs
`login.mjs` reuses the passwords and TOTP secrets kept in `MANUAL_STATE_DIR`.

Optional settings: `MANUAL_CASE_REF` (case reference in the box's format), `MANUAL_PERSON`,
`MANUAL_PERSON_ID` and `MANUAL_QUESTION` (the example question; it must match the corpus),
`MANUAL_ANSWER_TIMEOUT_MS`.

The answer screenshots ask a real question and rate the answer as bad (to show the feedback
reasons), so they add one conversation and one feedback record per run. A failed step leaves
`img/_failed-<name>.png`; delete it before committing.

## Rendering to DOCX and PDF

`tools/build_pdf.py` joins the chapters into one document and renders it with the ViVeSec
Word template: cover, table of contents, every chapter on a new page, the template's header
logo, footer and styles. It needs pandoc and, for the PDF, Microsoft Word.

```powershell
cd manual\tools
python build_pdf.py              # manual/build/Matrix_AI_Felhasznaloi_utmutato.{docx,pdf}
python build_pdf.py --no-pdf     # DOCX only (no Word needed)
```

`tools/reference.docx` is the cleaned template: the styles, page setup, header and footer of
the ViVeSec template without its body text, plus the styles pandoc needs (table, caption, note)
in the template's colours. To rebuild it from a new template version:
`python build_pdf.py --template <ViVeSec template>.docx`.
