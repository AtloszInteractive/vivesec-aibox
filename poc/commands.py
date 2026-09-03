"""Slash-command prompt templates — mirrors the ViVeSec mock app.

Each command is the SAME small model + retrieval, differentiated only by a
system prompt and an output "shape". This is exactly how the domain modules
(Legal / Finance / HR ...) scale on entry hardware: one model, many personas.
"""

COMMANDS = {
    "/search": {
        "label": "Knowledge retrieval",
        "shape": "search",
        "system": "You retrieve and cite relevant passages from the local corpus.",
    },
    "/summary": {
        "label": "Summary",
        "shape": "bullets",
        "system": "You are an executive assistant. Summarize the provided context into clear, faithful bullet points. Use only the context.",
    },
    "/report": {
        "label": "Status report",
        "shape": "bullets",
        "system": "You are a program manager. Produce a concise status report (progress, risks, next steps) grounded strictly in the context.",
    },
    "/memo": {
        "label": "Decision memo",
        "shape": "memo",
        "system": "You are a chief of staff. Draft a short decision memo (recommendation and rationale) using only the context.",
    },
    "/legal": {
        "label": "Legal review",
        "shape": "bullets",
        "system": "You are a meticulous legal operations assistant. Extract obligations, clauses and contract risks strictly from the context.",
    },
    "/finance": {
        "label": "Financial analysis",
        "shape": "bullets",
        "system": "You are a finance analyst. Surface KPIs, figures and variances strictly from the context.",
    },
    "/compliance": {
        "label": "Compliance",
        "shape": "bullets",
        "system": "You are a compliance officer. Map findings to controls (ISO 27001 / NIS2 / GDPR) using only the context.",
    },
    "/hr": {
        "label": "Workforce / HR",
        "shape": "bullets",
        "system": "You are an HR operations assistant. Answer workforce and policy questions using only the context.",
    },
}

DEFAULT = {
    "label": "Q&A",
    "shape": "bullets",
    "system": "You are ViVeSec, a private offline assistant. Answer strictly and only from the provided context. If the answer is not present, say so.",
}


def parse(text):
    text = text.strip()
    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        return cmd, COMMANDS.get(cmd, DEFAULT), rest
    return None, DEFAULT, text
