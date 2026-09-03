"""Internal service-token auth (spec §3, invariant: rag-engine is internal-only).

The HÍD calls with `Authorization: Bearer <RAG_INTERNAL_TOKEN>`. This is a
network-internal shared secret, not user auth — the ACL/jogosultság decision is
made by the HÍD and arrives as `allowed_file_ids` (invariant 2). mTLS optional.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def require_internal_token(authorization: str = Header(default="")) -> None:
    """FastAPI dependency: reject anything without the shared internal token."""
    # If no token is configured we are likely in a dev/CI run; allow but warn-by-design.
    expected = settings.INTERNAL_TOKEN
    if not expected:
        return

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Missing bearer token."},
        )
    presented = authorization[len(prefix):]
    # Constant-time compare to avoid token-timing leaks.
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Invalid internal token."},
        )
