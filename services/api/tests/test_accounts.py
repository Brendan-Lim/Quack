"""Who a request is from, and that nobody gets at anyone else's data."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from quack.config import Settings
from quack.main import build_services, create_app
from quack.services.identity import NotAuthenticated, SupabaseVerifier
from tests.accounts_support import account_settings, calibrate, register
from tests.test_pipeline import png

URL = "https://project.supabase.co"
ISSUER = f"{URL}/auth/v1"
SECRET = "a-shared-secret-of-a-decent-length-for-hs256"


def claims(**overrides: Any) -> dict[str, Any]:
    return {"sub": "user-1", "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600} | overrides


class Keys:
    """Stands in for the project's published keys."""

    def __init__(self, public_key: Any) -> None:
        self.key = public_key

    def get_signing_key_from_jwt(self, _: str) -> Any:
        return self


@pytest.fixture
def ec_key() -> Any:
    return ec.generate_private_key(ec.SECP256R1())


def verifier(public_key: Any = None, secret: str = SECRET) -> SupabaseVerifier:
    return SupabaseVerifier(URL, secret, jwks_client=Keys(public_key))  # type: ignore[arg-type]


class TestSupabaseTokens:
    def test_accepts_a_token_signed_with_the_projects_key(self, ec_key: Any) -> None:
        token = jwt.encode(claims(), ec_key, algorithm="ES256")
        assert verifier(ec_key.public_key()).verify(token).user_id == "user-1"

    def test_accepts_a_shared_secret_when_configured(self) -> None:
        assert verifier().verify(jwt.encode(claims(), SECRET, algorithm="HS256")).user_id == "user-1"

    def test_refuses_a_shared_secret_token_with_none_configured(self, ec_key: Any) -> None:
        token = jwt.encode(claims(), "whatever-secret-someone-picked-that-is-long", algorithm="HS256")
        with pytest.raises(NotAuthenticated):
            verifier(ec_key.public_key(), secret="").verify(token)

    def test_refuses_the_wrong_signature(self, ec_key: Any) -> None:
        other = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(NotAuthenticated):
            verifier(ec_key.public_key()).verify(jwt.encode(claims(), other, algorithm="ES256"))

    def test_refuses_an_unsigned_token(self, ec_key: Any) -> None:
        token = jwt.encode(claims(), key="", algorithm="none")
        with pytest.raises(NotAuthenticated):
            verifier(ec_key.public_key()).verify(token)

    def test_a_public_key_cant_be_used_as_a_shared_secret(self) -> None:
        # The classic confusion: sign HS256 with the published key, and hope it's checked as a secret.
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(NotAuthenticated):
            verifier(rsa_key.public_key(), secret="").verify(
                jwt.encode(claims(), "not-the-key-used-for-anything-really-long", algorithm="HS256")
            )

    @pytest.mark.parametrize(
        "changed",
        [
            {"exp": int(time.time()) - 10},
            {"aud": "anon"},
            {"iss": "https://elsewhere.supabase.co/auth/v1"},
            {"sub": ""},
        ],
        ids=["expired", "audience", "issuer", "no user"],
    )
    def test_refuses_bad_claims(self, ec_key: Any, changed: dict[str, Any]) -> None:
        with pytest.raises(NotAuthenticated):
            verifier(ec_key.public_key()).verify(jwt.encode(claims(**changed), ec_key, algorithm="ES256"))


class TestModes:
    def test_off_has_no_accounts_and_the_notebook_needs_none(self, settings: Settings) -> None:
        client = TestClient(create_app(build_services(settings)))
        assert client.get("/api/auth/config").json()["mode"] == "off"
        assert client.get("/api/profile").status_code == 404

    def test_local_is_refused_outside_development(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="development"):
            build_services(account_settings(tmp_path, QUACK_ENV="production"))

    def test_mode_follows_what_is_configured(self) -> None:
        assert Settings(_env_file=None).auth_mode == "off"
        assert Settings(QUACK_ENV="development", _env_file=None).auth_mode == "local"
        assert Settings(SUPABASE_URL=URL, _env_file=None).auth_mode == "supabase"

    def test_supabase_mode_only_gives_the_browser_public_values(self, tmp_path: Path) -> None:
        client = TestClient(
            create_app(
                build_services(
                    account_settings(
                        tmp_path,
                        QUACK_AUTH_MODE="supabase",
                        SUPABASE_URL=URL,
                        SUPABASE_ANON_KEY="public-anon",
                        SUPABASE_JWT_SECRET=SECRET,
                        SUPABASE_SERVICE_ROLE_KEY="very-secret",
                    )
                )
            )
        )
        body = client.get("/api/auth/config").text
        assert "public-anon" in body and SECRET not in body and "very-secret" not in body
        token = jwt.encode(claims(), SECRET, algorithm="HS256")
        assert client.get("/api/profile", headers={"authorization": f"Bearer {token}"}).json()["userId"] == "user-1"
        assert client.post("/api/auth/local/login", json={"email": "a@b.co", "password": "x"}).status_code == 404


class TestLocalAccounts:
    def test_register_sign_in_and_wrong_password(self, accounts_client: TestClient) -> None:
        register(accounts_client)
        good = accounts_client.post("/api/auth/local/login", json={"email": "A@example.com ", "password": "correct horse"})
        assert good.status_code == 200
        bad = accounts_client.post("/api/auth/local/login", json={"email": "a@example.com", "password": "wrong"})
        unknown = accounts_client.post("/api/auth/local/login", json={"email": "no@example.com", "password": "wrong"})
        assert bad.status_code == unknown.status_code == 401
        assert bad.json() == unknown.json()

    def test_no_plaintext_password_is_stored(self, tmp_path: Path) -> None:
        client = TestClient(create_app(build_services(account_settings(tmp_path, QUACK_DATABASE_URL=""))))
        register(client, password="findme-if-you-can")
        assert b"findme-if-you-can" not in (tmp_path / "quack.db").read_bytes()
        wal = tmp_path / "quack.db-wal"
        assert not wal.exists() or b"findme-if-you-can" not in wal.read_bytes()

    @pytest.mark.parametrize(
        ("email", "password"), [("not-an-email", "long enough pass"), ("a@b.co", "short"), ("a@example.com", "again and again")]
    )
    def test_rejections_can_be_acted_on(self, accounts_client: TestClient, email: str, password: str) -> None:
        register(accounts_client)
        response = accounts_client.post("/api/auth/local/register", json={"email": email, "password": password})
        assert response.status_code == 400
        assert response.json()["detail"]

    def test_signing_in_survives_a_server_restart(self, tmp_path: Path) -> None:
        settings = account_settings(tmp_path)
        headers = register(TestClient(create_app(build_services(settings))))
        again = TestClient(create_app(build_services(settings)))
        assert again.get("/api/profile", headers=headers).status_code == 200

    def test_expired_token_is_refused(self, tmp_path: Path, accounts_client: TestClient) -> None:
        register(accounts_client)
        secret = (tmp_path / "local_auth_secret").read_text()
        now = int(time.time())
        token = jwt.encode({"sub": "x", "iss": "quack-local", "iat": now - 100, "exp": now - 10}, secret, algorithm="HS256")
        assert accounts_client.get("/api/profile", headers={"authorization": f"Bearer {token}"}).status_code == 401


class TestAccessControl:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/profile"),
            ("get", "/api/calibration"),
            ("get", "/api/calibration/export"),
            ("delete", "/api/calibration"),
            ("post", "/api/calibration/restart"),
            ("delete", "/api/account"),
            ("delete", "/api/account/learning"),
        ],
    )
    def test_needs_a_valid_token(self, accounts_client: TestClient, method: str, path: str) -> None:
        assert getattr(accounts_client, method)(path).status_code == 401
        bad = {"authorization": "Bearer nonsense"}
        assert getattr(accounts_client, method)(path, headers=bad).status_code == 401

    def test_checks_cant_spend_credit_without_signing_in(self, accounts_client: TestClient) -> None:
        body = {"sessionId": "s", "snapshotVersion": 1, "image": png()}
        assert accounts_client.post("/api/analyze", json=body).status_code == 401

    def test_cross_site_writes_are_refused(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client) | {"origin": "https://evil.example"}
        assert accounts_client.delete("/api/account", headers=headers).status_code == 403

    def test_each_account_sees_only_its_own_samples(self, accounts_client: TestClient) -> None:
        one, other = register(accounts_client), register(accounts_client, "b@example.com")
        calibrate(accounts_client, one)
        assert len(accounts_client.get("/api/calibration/export", headers=one).json()["samples"]) == 6
        assert accounts_client.get("/api/calibration/export", headers=other).json()["samples"] == []
        assert accounts_client.get("/api/profile", headers=other).json()["onboardingStatus"] == "account_created"

    def test_a_client_supplied_user_id_means_nothing(self, accounts_client: TestClient) -> None:
        one, other = register(accounts_client), register(accounts_client, "b@example.com")
        them = accounts_client.get("/api/profile", headers=other).json()["userId"]
        asked = accounts_client.get(f"/api/profile?userId={them}&user_id={them}", headers=one).json()
        assert asked["userId"] != them

    def test_one_accounts_mistakes_cant_be_reached_from_another(self, accounts_client: TestClient) -> None:
        one, other = register(accounts_client), register(accounts_client, "b@example.com")
        services = accounts_client.app.state.services
        user = accounts_client.get("/api/profile", headers=one).json()["userId"]
        from quack.services.mistake_manager import TrackedMistake

        tracked = TrackedMistake("m_1", "arithmetic", "arithmetic_error", "arithmetic", "2*3", "5", "6", "")
        services.mistakes.record(f"{user}:shared", 1, new=[tracked])
        body = {"sessionId": "shared", "mistakeId": "m_1", "level": 1}
        assert accounts_client.post("/api/hint", json=body, headers=other).status_code == 404
        assert accounts_client.post("/api/hint", json=body, headers=one).status_code == 200


class TestDeleting:
    def test_deleting_an_account_removes_it_and_stops_its_token(self, accounts_client: TestClient) -> None:
        headers = register(accounts_client)
        calibrate(accounts_client, headers)
        assert accounts_client.delete("/api/account", headers=headers).json()["signInDeleted"] is True
        assert accounts_client.get("/api/profile", headers=headers).status_code == 401
        login = accounts_client.post("/api/auth/local/login", json={"email": "a@example.com", "password": "correct horse"})
        assert login.status_code == 401
