"""Shared-secret bearer auth for the PRIMCS HTTP server.

run_code executes arbitrary Python, so every HTTP caller must present
``Authorization: Bearer <PRIMCS_AUTH_TOKEN>``. The token comes from the server's
environment and the server refuses to start without one (fail closed).
"""
from __future__ import annotations

import hmac
import os

from fastmcp.server.auth import AccessToken, TokenVerifier

MIN_TOKEN_LENGTH = 32


def load_auth_token() -> str:
    token = os.getenv("PRIMCS_AUTH_TOKEN", "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"PRIMCS_AUTH_TOKEN must be set to a random secret of at least {MIN_TOKEN_LENGTH} characters "
            "(e.g. `openssl rand -hex 32`); PRIMCS will not serve run_code unauthenticated."
        )
    return token


def token_matches(presented: str | None, expected: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented.encode(), expected.encode())


def bearer_matches(authorization_header: str | None, expected: str) -> bool:
    scheme, _, value = (authorization_header or "").partition(" ")
    return scheme.lower() == "bearer" and token_matches(value.strip(), expected)


class SharedSecretVerifier(TokenVerifier):
    """Accepts exactly one bearer token, compared in constant time."""

    def __init__(self, token: str):
        super().__init__()
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token_matches(token, self._token):
            return None
        return AccessToken(token=token, client_id="hive-backend", scopes=[])
