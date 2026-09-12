import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request


QUESTIONS = [
    ("employer", "Szeredy Csaba melyik c\u00e9gn\u00e9l dolgozik?"),
    ("title", "Szeredy Csaba a ViVeTech Zrt. vez\u00e9rigazgat\u00f3ja?"),
    ("file", "Az IFUA_VVT_adatelemzes_szerzodes_20200615.docx dokumentumban milyen tiszts\u00e9ggel szerepel Szeredy Csaba?"),
]
FOLLOWUP = "Kor\u00e1bban m\u00e1st mondt\u00e1l. Melyik dokumentum t\u00e1masztja al\u00e1 Szeredy Csaba tiszts\u00e9g\u00e9t?"


def post(port, path, body, headers=None):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def main():
    stage = Path(sys.argv[1])
    mode = sys.argv[2]
    manifest = json.loads((stage / "manifest.json").read_text())
    drive = manifest["drive"].rstrip("/")
    assert drive == "/storage/drives/aiboxdev"
    corpus_id = "aiboxdev-" + hashlib.sha1(drive.encode()).hexdigest()[:8]
    output = stage / (mode + "-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    with output.open("w", encoding="utf-8") as report:
        versions = (("baseline", 18092), ("candidate", 18093)) if mode == "search" else (("baseline", 18082), ("candidate", 18083))
        for version, port in versions:
            cases = list(QUESTIONS)
            if mode == "chat":
                cases += [("followup", FOLLOWUP), ("general", "Mi\u00e9rt k\u00e9k az \u00e9g?")]
            for name, question in cases:
                started = time.monotonic()
                if mode == "search":
                    body = {"corpus_ids": [corpus_id], "question": question, "top_k": 5,
                            "max_context_tokens": 4000, "include_debug": True}
                    if version == "candidate" and name == "file":
                        body["source_paths"] = ["IFUA_VVT_adatelemzes_szerzodes_20200615.docx"]
                    result = post(port, "/rag/search_context", body)
                    summary = {"debug": result.get("debug"), "hits": [
                        {"path": context["source_path"], "chunk_id": context["chunk_id"],
                         "named_person": "szeredy" in context["text"].lower() and "csaba" in context["text"].lower(),
                         "snippet": context["text"][:650]} for context in result.get("contexts", [])]}
                else:
                    headers = {"VVS-Drive": base64.urlsafe_b64encode(drive.encode()).decode(),
                               "VVS-User": manifest["user"] + "-evaluation-" + version}
                    result = post(port, "/api/v1/ui/query", {"query": question, "top_k": 5, "profile": "hybrid"}, headers)
                    summary = {key: result.get(key) for key in ("answer", "backend", "citations", "retrieval_query",
                                                               "retrieval_debug", "evidence_chunk_ids", "source_paths")}
                elapsed = round(time.monotonic() - started, 2)
                record = {"version": version, "case": name, "question": question, "seconds": elapsed, "result": result}
                report.write(json.dumps(record, ensure_ascii=False) + "\n")
                report.flush()
                print(json.dumps({"version": version, "case": name, "seconds": elapsed, **summary}, ensure_ascii=False), flush=True)
    print("REPORT=" + str(output), flush=True)


if __name__ == "__main__":
    main()