"""Cross-lingual control experiment.

Hypothesis: the wrong Hungarian numbers came from MIXING languages (English
corpus + Hungarian question/answer), not from model size. To test it we build a
HUNGARIAN index from data_hu/ and ask the SAME questions in Hungarian — a pure
same-language setup. If qwen2.5:3b now reports 4,82 millió correctly, the
hypothesis holds.

Writes test_hu_out.md (UTF-8). Uses a separate index so the running bridge's
main index is untouched.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config  # noqa: E402

# Point the PoC at the Hungarian corpus + a separate index.
config.DATA_DIR = os.path.join(HERE, "data_hu")
config.INDEX_PATH = os.path.join(HERE, "index_hu.json")
config.ANSWER_LANG = "Hungarian"
# GEN_MODEL already defaults to qwen2.5:3b.

import rag  # noqa: E402  (imports after config tweak)

OUT = os.path.join(HERE, "test_hu_out.md")

QUESTIONS = [
    ("Q4 bevétel (VAN a HU doksiban)", "/finance mennyi volt a Q4 bevétel és mennyivel nőtt?"),
    ("Q1 forgalom (NINCS a doksiban)", "/finance mennyi volt a Q1 forgalom?"),
]


def main():
    meta, files = rag.ingest()
    lines = ["# Cross-lingual control: Hungarian corpus + Hungarian question\n",
             "Index: %s | chunks: %s | embed: %s\n" % (
                 ", ".join(os.path.basename(f) for f in files),
                 meta.get("n_chunks"), meta.get("embed_backend"))]
    for label, q in QUESTIONS:
        print("RUN:", label, flush=True)
        res = rag.answer(q)
        lines.append("\n## %s\n**Q:** %s\n" % (label, q))
        lines.append("**backend:** %s\n" % res.get("llm_backend", ""))
        lines.append("**answer:**\n\n%s\n" % res.get("answer"))
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    print("DONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
