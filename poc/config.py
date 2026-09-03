"""Central configuration for the ViVeSec AI Box PoC.

Everything is overridable via environment variables so the exact same code
runs unchanged on a laptop and on a Jetson Orin Nano.
"""
import os

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Generative model. Default = qwen2.5:3b. In PoC A/B testing it stayed far more
# faithful to the retrieved context than EuroLLM-1.7B: with the grounding-guard
# prompt it correctly REFUSES questions whose answer is absent from the corpus
# (no hallucinated figures), where EuroLLM invented numbers even with the guard.
# Trade-off: qwen's Hungarian prose is weaker than EuroLLM's, so for the most
# fluent Hungarian generation set VIVESEC_GEN_MODEL=cas/eurollm-1.7b-instruct-q8.
# qwen2.5:7b (fits a Jetson Orin Nano 8GB) is the next step toward reliable
# numeric extraction. EuroLLM is EU-funded and covers all EU languages incl. HU.
GEN_MODEL = os.environ.get("VIVESEC_GEN_MODEL", "qwen2.5:3b")

# Embedding model. Default = bge-m3: strong MULTILINGUAL retrieval incl.
# Hungarian. Use "nomic-embed-text" for English-only corpora.
EMBED_MODEL = os.environ.get("VIVESEC_EMBED_MODEL", "bge-m3")

# Backend selection: "auto" (detect Ollama) | "ollama" (force) | "fallback" (force).
BACKEND = os.environ.get("VIVESEC_BACKEND", "auto")

# Optional: force the generated answer language regardless of source/query
# language. Empty = model decides (usually mirrors the question).
# Example:  set VIVESEC_ANSWER_LANG=Hungarian
ANSWER_LANG = os.environ.get("VIVESEC_ANSWER_LANG", "")

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VIVESEC_DATA", os.path.join(_HERE, "data"))
INDEX_PATH = os.environ.get("VIVESEC_INDEX", os.path.join(_HERE, "index.json"))

# Retrieval / chunking parameters.
CHUNK_WORDS = 110
CHUNK_OVERLAP = 25
TOP_K = 4

# Cap the number of generated tokens to bound answer latency and prevent a small
# model from running away (generating until the context window fills). Set to 0
# (or empty) to let the model decide on its own.
NUM_PREDICT = int(os.environ.get("VIVESEC_NUM_PREDICT", "512") or 0)

# Penalize token repetition. Small models tend to loop on longer answers; 1.15
# tames the repetition seen in testing without hurting short factual replies.
# Set to 1.0 (or empty) to disable.
REPEAT_PENALTY = float(os.environ.get("VIVESEC_REPEAT_PENALTY", "1.15") or 0)

# Dimensionality of the dependency-free fallback embedding.
FALLBACK_EMBED_DIM = 384
