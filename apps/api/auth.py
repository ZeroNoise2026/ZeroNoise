"""
auth.py — Supabase JWT verification for FastAPI.

Supports BOTH:
  * Legacy HS256 (shared JWT secret) — older Supabase projects.
  * New asymmetric signing keys (ES256/RS256) — projects with "JWT Signing
    Keys" enabled. Public keys are fetched from the project's JWKS endpoint
    at <SUPABASE_URL>/auth/v1/.well-known/jwks.json and cached.

The mode is auto-detected from the token header's `alg` field, so the same
code works whether you flip the dashboard toggle or not.

Usage:
    from auth import get_current_user

    @app.get("/api/something")
    def handler(user_id: str = Depends(get_current_user)):
        ...
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException, status

from config import SUPABASE_JWT_SECRET, SUPABASE_URL

logger = logging.getLogger(__name__)

# Supabase access tokens always carry aud="authenticated" for logged-in users.
_AUDIENCE = "authenticated"
_ASYMMETRIC_ALGS = ["RS256", "ES256"]

# Lazily-built JWKS client (one HTTP round-trip on first call, cached after).
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not SUPABASE_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="SUPABASE_URL not configured on server.",
            )
        jwks_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        # cache_keys=True memoises keys by kid for the process lifetime.
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _decode(token: str) -> dict:
    # Peek at the header to pick the right verification path.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Malformed token: {e}")

    alg = header.get("alg")
    try:
        if alg in _ASYMMETRIC_ALGS:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
            return jwt.decode(
                token,
                signing_key,
                algorithms=_ASYMMETRIC_ALGS,
                audience=_AUDIENCE,
                options={"require": ["exp", "sub"]},
            )
        elif alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="SUPABASE_JWT_SECRET not configured on server.",
                )
            return jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience=_AUDIENCE,
                options={"require": ["exp", "sub"]},
            )
        else:
            raise HTTPException(status_code=401, detail=f"Unsupported token alg: {alg}")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Invalid token audience.")
    except jwt.PyJWKClientError as e:
        raise HTTPException(status_code=401, detail=f"JWKS lookup failed: {e}")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """FastAPI dependency. Returns the user's UUID (string) from a Bearer JWT.

    Raises 401 if the header is missing/malformed or the token is invalid.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header (expected 'Bearer <jwt>').",
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = _decode(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject (sub) claim.")
    return user_id


def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[str]:
    """Like get_current_user, but returns None instead of raising if no/invalid token.
    Useful for endpoints that work both authenticated and anonymously."""
    if not authorization:
        return None
    try:
        return get_current_user(authorization)
    except HTTPException:
        return None
