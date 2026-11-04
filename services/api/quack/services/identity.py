"""Who a request is from. Nothing is believed about a user unless it comes through here.

Supabase is the real path: it holds the passwords, and this only checks the tokens it signs. The
local provider is a stand-in for working without a project, only allowed in development, and the app
labels its accounts as such. See docs/AUTH_AND_PROFILES.md.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import jwt

from quack.db.database import Database

LOCAL_ISSUER = "quack-local"
LOCAL_TOKEN_LIFETIME_S = 30 * 24 * 3600
MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_CHARS = 200
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
# OWASP's recommended minimum for scrypt.
_SCRYPT = {"n": 2**17, "r": 8, "p": 1, "maxmem": 256 * 1024 * 1024}
_ASYMMETRIC = ["ES256", "RS256", "EdDSA"]


@dataclass(frozen=True)
class Identity:
    user_id: str


class NotAuthenticated(Exception):
    """The token is missing, malformed, expired, or wasn't signed by who it should have been."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Identity: ...


class SupabaseVerifier:
    """Checks tokens signed by a Supabase project.

    The algorithm comes from a fixed list that depends on the key in use, never from what the token
    claims, or a token could choose to be checked with a public key as if it were a shared secret.
    """

    def __init__(self, url: str, jwt_secret: str = "", jwks_client: jwt.PyJWKClient | None = None) -> None:
        self._issuer = f"{url.rstrip('/')}/auth/v1"
        self._secret = jwt_secret
        self._jwks = jwks_client or jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)

    def verify(self, token: str) -> Identity:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm == "HS256" and self._secret:
                key: object = self._secret
                allowed = ["HS256"]
            elif algorithm in _ASYMMETRIC:
                key = self._jwks.get_signing_key_from_jwt(token).key
                allowed = _ASYMMETRIC
            else:
                raise NotAuthenticated("unexpected signing algorithm")
            claims = jwt.decode(
                token,
                key,  # type: ignore[arg-type]
                algorithms=allowed,
                audience="authenticated",
                issuer=self._issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise NotAuthenticated("invalid token") from exc
        return _identity_of(claims)


def _identity_of(claims: dict) -> Identity:
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 64:
        raise NotAuthenticated("token names no user")
    return Identity(subject)


class AccountRejected(ValueError):
    """Details a person can act on, such as an email that's already registered."""


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{b64encode(salt).decode()}${b64encode(derived).decode()}"


def _password_matches(password: str, stored: str) -> bool:
    salt = b64decode(stored.split("$", 1)[0])
    return hmac.compare_digest(_hash_password(password, salt), stored)


def _load_or_create_secret(path: Path) -> str:
    """Kept in a file, so that signing in survives a restart of the server."""

    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    path.touch(mode=0o600)
    path.write_text(secret)
    return secret


class LocalAccounts:
    """Development accounts on this machine. Not a secure registered account, and never in production."""

    def __init__(self, database: Database, secret_path: Path) -> None:
        self._database = database
        self._secret = _load_or_create_secret(secret_path)
        database.enable_local_accounts()
        # So that an unknown email takes as long as a wrong password, and can't be told apart by timing.
        self._decoy = _hash_password(secrets.token_urlsafe(16))

    def _token_for(self, user_id: str) -> str:
        now = int(time.time())
        claims = {"sub": user_id, "iss": LOCAL_ISSUER, "iat": now, "exp": now + LOCAL_TOKEN_LIFETIME_S}
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def register(self, email: str, password: str) -> tuple[str, str]:
        address = email.strip().lower()
        if not _EMAIL.match(address):
            raise AccountRejected("That doesn't look like an email address.")
        if not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
            raise AccountRejected(f"Use a password of at least {MIN_PASSWORD_CHARS} characters.")
        user_id = str(uuid.uuid4())
        added = self._database.execute(
            "INSERT INTO local_accounts (email, user_id, password_hash, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (email) DO NOTHING",
            (address, user_id, _hash_password(password), time.time()),
        )
        if not added:
            raise AccountRejected("There's already an account with that email.")
        return user_id, self._token_for(user_id)

    def sign_in(self, email: str, password: str) -> tuple[str, str]:
        row = self._database.one(
            "SELECT user_id, password_hash FROM local_accounts WHERE email = ?", (email.strip().lower(),)
        )
        matches = _password_matches(password[:MAX_PASSWORD_CHARS], row["password_hash"] if row else self._decoy)
        if row is None or not matches:
            raise AccountRejected("That email and password don't match.")
        return row["user_id"], self._token_for(row["user_id"])

    def delete(self, user_id: str) -> None:
        self._database.execute("DELETE FROM local_accounts WHERE user_id = ?", (user_id,))

    def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token, self._secret, algorithms=["HS256"], issuer=LOCAL_ISSUER, options={"require": ["exp", "sub", "iss"]}
            )
        except jwt.PyJWTError as exc:
            raise NotAuthenticated("invalid token") from exc
        identity = _identity_of(claims)
        # A deleted account's token stops working at once.
        if self._database.one("SELECT 1 AS found FROM local_accounts WHERE user_id = ?", (identity.user_id,)) is None:
            raise NotAuthenticated("no such account")
        return identity
