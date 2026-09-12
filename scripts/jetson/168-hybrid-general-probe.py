import importlib.util
import json
import sys
import time

sys.path.insert(0, "/app/adapter")
import llm


def load_candidate(path):
    spec = importlib.util.spec_from_file_location("candidate_llm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module, label, name, question, contexts, history=None, task=None):
    started = time.monotonic()
    answer, backend = module.generate(question, contexts, lang="Hungarian",
                                     history=history, profile="hybrid", task=task)
    print(json.dumps({"version": label, "case": name, "question": question,
                      "seconds": round(time.monotonic() - started, 1),
                      "backend": backend, "answer": answer}, ensure_ascii=False), flush=True)
    assert answer, (name, backend)
    assert "hybrid" in backend if not task else backend == "no-context", backend


def main():
    candidate = load_candidate(sys.argv[1])
    sky = "Mi\u00e9rt k\u00e9k az \u00e9g?"
    irrelevant = [{"text": "Expense claims require manager approval.", "source_path": "policy.txt"}]
    refused = [
        {"role": "user", "content": sky},
        {"role": "assistant", "content": "A dokumentumokban nincs erre vonatkoz\u00f3 inform\u00e1ci\u00f3."},
    ]
    for module, label in ((llm, "baseline"), (candidate, "candidate")):
        run(module, label, "bare-empty", sky, [])
        run(module, label, "bare-after-refusal", sky, irrelevant, refused)
    run(candidate, "candidate", "other-general", "Mi\u00e9rt \u00faszik a j\u00e9g a v\u00edzen?", irrelevant, refused)
    run(candidate, "candidate", "company-missing", "Mennyi a c\u00e9g\u00fcnk 2031. novemberi nett\u00f3 profitja?", [])
    run(candidate, "candidate", "mixed", "Mennyi a c\u00e9g\u00fcnk 2025-\u00f6s bev\u00e9tele, \u00e9s mi a k\u00fcl\u00f6nbs\u00e9g a bev\u00e9tel \u00e9s a profit k\u00f6z\u00f6tt?",
        [{"text": "Company revenue in 2025 was 12 million HUF.", "source_path": "annual.txt"}])
    run(candidate, "candidate", "grounded-action", "Write a company report", [], task="report")


if __name__ == "__main__":
    main()