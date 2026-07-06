"""Tests for ChatGPT-subscription (Codex) OAuth handling (§9).

Two layers:

  * The *import* bridge (:func:`~jobcrawler.auth.import_codex_login`) is tested against the
    REAL litellm ``Authenticator`` writing to a temp store, with synthetic JWTs shaped exactly
    as its parser expects — so a schema drift in the pinned litellm would fail here.
  * The *validity* path (:func:`~jobcrawler.auth.verify_token`,
    :func:`~jobcrawler.auth.ensure_logged_in`, :func:`~jobcrawler.auth.login`) is tested with
    ``_authenticator`` monkeypatched to a fake, because the real one would hit the network.
    The invariant these lock in: **no code path ever reaches litellm's blocking device-code
    login** — a dead credential must raise :class:`~jobcrawler.auth.AuthError`, not hang.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from jobcrawler import auth, config
from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

# A far-future Unix expiry (year 2100) so an access token reads as valid without a clock.
_FAR_FUTURE = 4102444800


def _jwt(payload: dict[str, Any]) -> str:
    """A syntactically valid unsigned JWT: litellm only base64-decodes the claims segment."""

    def seg(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg(payload)}.sig"


def _codex_file(tmp_path: Path, tokens: dict[str, Any]) -> Path:
    path = tmp_path / "codex" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"OPENAI_API_KEY": None, "tokens": tokens}))
    return path


class FakeAuth:
    """Stand-in for litellm's ``Authenticator`` exposing only the methods auth.py calls."""

    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        self.stored = stored
        self.written: dict[str, Any] | None = None
        self.expired = False
        self.refreshed = 0
        self.refresh_error: Exception | None = None

    def _read_auth_file(self) -> dict[str, Any] | None:
        return self.stored

    def _write_auth_file(self, data: dict[str, Any]) -> None:
        self.written = data
        self.stored = data

    def _is_token_expired(self, auth_data: dict[str, Any], access_token: str) -> bool:
        return self.expired

    def _refresh_tokens(self, refresh_token: str) -> dict[str, str]:
        self.refreshed += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        self.stored = {**(self.stored or {}), "access_token": "fresh-access"}
        return {"access_token": "fresh-access"}

    def _build_auth_record(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
            "expires_at": _FAR_FUTURE,
            "account_id": "acct-from-record",
        }


@pytest.fixture
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> FakeAuth:
    """Route every ``_authenticator()`` call to one shared fake, so state persists across the
    several ``_authenticator()`` constructions a single auth.py call makes."""
    fake = FakeAuth()
    monkeypatch.setattr(auth, "_authenticator", lambda: fake)
    return fake


# --- import bridge against the real litellm Authenticator ------------------


def test_import_codex_login_writes_the_litellm_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", str(store))
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    codex = _codex_file(
        tmp_path,
        {
            "access_token": _jwt({"exp": _FAR_FUTURE}),
            "refresh_token": "r" * 90,
            "id_token": _jwt(
                {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-jwt"}}
            ),
            "account_id": "acct-explicit",
        },
    )

    record = auth.import_codex_login(codex_path=codex)

    written = json.loads((store / "auth.json").read_text())
    assert written == record
    assert written["refresh_token"] == "r" * 90
    assert written["expires_at"] == _FAR_FUTURE
    # account_id is litellm-derived from the id_token claim, not the codex explicit field.
    assert written["account_id"] == "acct-jwt"


def test_import_codex_login_falls_back_to_explicit_account_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When neither JWT carries the account claim, the codex file's explicit account_id fills
    # the record rather than leaving it null.
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    codex = _codex_file(
        tmp_path,
        {
            "access_token": _jwt({"exp": _FAR_FUTURE}),
            "refresh_token": "r" * 90,
            "id_token": _jwt({"exp": _FAR_FUTURE}),
            "account_id": "acct-explicit",
        },
    )

    record = auth.import_codex_login(codex_path=codex)
    assert record["account_id"] == "acct-explicit"


def test_read_codex_tokens_raises_when_login_missing(tmp_path: Path) -> None:
    with pytest.raises(auth.AuthError, match="no Codex login"):
        auth._read_codex_tokens(tmp_path / "nonexistent" / "auth.json")


def test_read_codex_tokens_requires_a_refresh_token(tmp_path: Path) -> None:
    codex = _codex_file(tmp_path, {"access_token": _jwt({"exp": _FAR_FUTURE})})
    with pytest.raises(auth.AuthError, match="no refresh_token"):
        auth._read_codex_tokens(codex)


# --- non-interactive validity (fake authenticator) -------------------------


def test_verify_token_returns_cached_when_unexpired(fake_auth: FakeAuth) -> None:
    fake_auth.stored = {"access_token": "cached", "refresh_token": "r"}
    fake_auth.expired = False
    assert auth.verify_token() == "cached"
    assert fake_auth.refreshed == 0  # no network refresh when the cached token is live


def test_verify_token_refreshes_when_expired(fake_auth: FakeAuth) -> None:
    fake_auth.stored = {"access_token": "stale", "refresh_token": "r"}
    fake_auth.expired = True
    assert auth.verify_token() == "fresh-access"
    assert fake_auth.refreshed == 1


def test_verify_token_raises_when_no_store(fake_auth: FakeAuth) -> None:
    fake_auth.stored = None
    with pytest.raises(auth.AuthError, match="no ChatGPT credentials"):
        auth.verify_token()


def test_verify_token_raises_when_expired_without_refresh_token(
    fake_auth: FakeAuth,
) -> None:
    fake_auth.stored = {"access_token": "stale"}  # no refresh_token
    fake_auth.expired = True
    with pytest.raises(auth.AuthError, match="no refresh token"):
        auth.verify_token()


def test_verify_token_raises_on_refresh_failure_never_device_login(
    fake_auth: FakeAuth,
) -> None:
    # The daemon-critical invariant: a dead refresh token surfaces as AuthError, it does NOT
    # fall through to litellm's blocking device-code prompt.
    fake_auth.stored = {"access_token": "stale", "refresh_token": "r"}
    fake_auth.expired = True
    fake_auth.refresh_error = RefreshAccessTokenError(401, "boom")
    with pytest.raises(auth.AuthError, match="refresh failed"):
        auth.verify_token()


def test_ensure_logged_in_is_a_noop_for_non_chatgpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plain OpenRouter deployment must not touch the authenticator at all.
    monkeypatch.setattr(
        auth,
        "_authenticator",
        lambda: pytest.fail("authenticator built for a non-chatgpt slug"),
    )
    auth.ensure_logged_in("openrouter/test/model")


def test_ensure_logged_in_imports_from_codex_when_store_empty(
    fake_auth: FakeAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fresh host: jobcrawler's store is empty but `codex login` has run — ensure_logged_in
    # seeds from codex, then the (now-populated) token verifies without a refresh.
    fake_auth.stored = None
    fake_auth.expired = False
    monkeypatch.setattr(
        auth,
        "_read_codex_tokens",
        lambda codex_path=None: {
            "access_token": _jwt({"exp": _FAR_FUTURE}),
            "refresh_token": "r" * 90,
            "id_token": None,
        },
    )
    auth.ensure_logged_in("chatgpt/gpt-5-codex")
    assert fake_auth.written is not None  # a record was imported into the store
    assert fake_auth.refreshed == 0


def test_ensure_logged_in_raises_when_no_codex_and_empty_store(
    fake_auth: FakeAuth, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_auth.stored = None
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    with pytest.raises(auth.AuthError, match="no Codex login"):
        auth.ensure_logged_in("chatgpt/gpt-5-codex")


def test_login_returns_account_and_dir(
    fake_auth: FakeAuth, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    monkeypatch.setattr(
        auth,
        "_read_codex_tokens",
        lambda codex_path=None: {
            "access_token": _jwt({"exp": _FAR_FUTURE}),
            "refresh_token": "r" * 90,
            "id_token": None,
        },
    )
    info = auth.login()
    assert info["account_id"] == "acct-from-record"
    assert info["auth_dir"] == str(tmp_path / "store")
