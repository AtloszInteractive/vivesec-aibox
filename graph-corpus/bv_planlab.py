"""Planner lab (dev box only): the analyst planner's first plan for each eval question.

Runs adapter/orchestrator.plan() with the box's model and the graph's live domain
profile, without the rest of the chat, so a prompt change can be tried in seconds
per question instead of a release. Run next to orchestrator.py, llm.py,
gpu_gate.py, dossier.py and bv_eval.json:

    OLLAMA_URL=http://127.0.0.1:11434 ADAPTER_GEN_MODEL=qwen3.6:35b \
        python3 planlab.py bv_eval.json [question-id ...]
"""
import json
import os
import sys
import time
import urllib.request

import orchestrator


class Graph:
    def __init__(self):
        with open(os.path.expanduser("~/graph-secrets/graph_api_key")) as fh:
            self.key = fh.read().strip()

    def post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:8095" + path, data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "X-API-Key": self.key})
        return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main():
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    only = set(sys.argv[2:])
    vocab = orchestrator.vocabulary(orchestrator.profile(Graph()), "Hungarian")
    for q in data["questions"]:
        if (only and q["id"] not in only) or not q.get("reference_plan"):
            continue
        started = time.time()
        plan = orchestrator.plan(q["question"], [], "Hungarian", vocab, deep=True)
        print("== %s (%.0fs) more=%s" % (q["id"], time.time() - started, (plan or {}).get("more")))
        for call in (plan or {}).get("calls") or []:
            print("   %s %s" % (call["tool"], json.dumps(call["args"], ensure_ascii=False)))
        if plan and plan["action"] != "tools":
            print("   %s %s" % (plan["action"], plan.get("question") or ""))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
