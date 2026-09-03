"""Does the extractor already see PDF page boundaries?

rag_service/extract.py collapses every document into one logical page
(page_number=1), so every citation we emit says page 1. markitdown[all] pulls
pdfminer.six, so the information may already be there -- this checks whether
the extracted text carries form-feed page separators, and what pdfminer reports
directly.

Run inside the vivesec-rag container, which has the real dependencies.
"""
from __future__ import annotations

import io
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/sample.pdf"
    raw = open(path, "rb").read()
    print(f"fajl: {path}  ({len(raw)} bajt)\n")

    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(raw))
        pages = text.split("\f")
        non_empty = [p for p in pages if p.strip()]
        print(f"pdfminer.extract_text : {len(text)} kar, "
              f"\\f darabok: {len(pages)}, nem-ures: {len(non_empty)}")
        for i, page in enumerate(non_empty[:3], start=1):
            print(f"   oldal {i}: {len(page)} kar | {page.strip()[:70]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"pdfminer nem hasznalhato: {exc}")

    try:
        sys.path.insert(0, "/app/rag_service")
        import extract as rag_extract
        pages = rag_extract.extract_pages(raw, path)
        print(f"\nrag_service.extract_pages: {len(pages)} oldal, "
              f"oldalszamok={[p[0] for p in pages]}")
        md_text = pages[0][2] if pages else ""
        print(f"   szoveg: {len(md_text)} kar, tartalmaz \\f-et: {chr(12) in md_text}")
        print(f"   \\f darabok a markitdown szovegben: {len(md_text.split(chr(12)))}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nrag_service.extract nem toltheto be: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
