"""ChatGPT-subscription (Codex) OAuth for ``chatgpt/*`` model slugs (§9).

When a model slug is ``chatgpt/<model>`` (:func:`jobcrawler.lm.is_chatgpt`), litellm's
native ChatGPT provider calls the Codex *Responses* backend billed against a ChatGPT
subscription instead of per-token OpenRouter credits. That backend authenticates with an
OAuth token, not an API key. Rather than run our own device-code login, we REUSE the one
the official ``codex`` CLI already performs: ``codex login`` writes ``~/.codex/auth.json``;
:func:`login` imports that token into the store litellm reads, and litellm refreshes it
from there on its own.

The load-bearing field is the ``refresh_token``: Codex's stored ``access_token`` is a
short-lived JWT (expired within the hour), so on first use litellm sees it expired and mints
a fresh one from the refresh_token — which is why importing a days-old ``~/.codex/auth.json``
still works. All litellm coupling (its ``Authenticator`` and the exact on-disk schema) is
confined to this module; the token dir is :func:`jobcrawler.config.auth_dir`.

Everything here is deliberately **non-interactive**: :func:`verify_token` refreshes without
ever falling through to litellm's blocking 15-minute device-code prompt, so a daemon whose
token has died fails fast (``AuthError``) instead of hanging at the first match.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jobcrawler import config
from jobcrawler.lm import is_chatgpt


class AuthError(Exception):
    """A ChatGPT-subscription credential is missing or unusable (§9).

    Carries an operator-actionable message (run ``codex login`` then ``jobcrawl login``)
    so a ``chatgpt/*`` deployment with no token fails fast at boot rather than blocking the
    daemon on litellm's interactive device-code prompt.
    """


def codex_auth_path() -> Path:
    """Where the official ``codex`` CLI stores its OAuth token (honors ``CODEX_HOME``)."""
    home = os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")
    return Path(home) / "auth.json"


def apply_auth_env() -> None:
    """Pin ``CHATGPT_TOKEN_DIR`` to :func:`config.auth_dir` for this process (§9).

    litellm's ChatGPT provider builds its own ``Authenticator`` at call time, reading
    ``CHATGPT_TOKEN_DIR`` from the environment; :func:`login` and :func:`_authenticator`
    resolve the same dir. Pinning it here — before any ``chatgpt/*`` LM is built — guarantees
    the provider reads the exact store ``jobcrawl login`` wrote to, whatever
    ``JOBCRAWLER_AUTH_DIR`` resolved to.
    """
    os.environ["CHATGPT_TOKEN_DIR"] = config.auth_dir()


def _authenticator() -> Any:
    """litellm's ChatGPT ``Authenticator``, bound to our token dir (lazy import).

    Imported here rather than at module load so importing :mod:`jobcrawler.auth` (which the
    preflight guard does for every ``run``/``crawl``) stays cheap until a ``chatgpt/*`` slug
    is actually configured.
    """
    apply_auth_env()
    from litellm.llms.chatgpt.authenticator import Authenticator

    return Authenticator()


def _read_codex_tokens(codex_path: Path | None = None) -> dict[str, str]:
    """The ``tokens`` block from ``~/.codex/auth.json`` (access/refresh/id/account_id).

    Raises :class:`AuthError` when Codex has never logged in (no file), the file is
    unreadable, or it carries no ``refresh_token`` — the one field a session cannot be
    minted without.
    """
    path = codex_path or codex_auth_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise AuthError(
            f"no Codex login found at {path} — run `codex login` first, then `jobcrawl login`"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"Codex auth file {path} is unreadable: {exc}") from exc
    tokens = raw.get("tokens") if isinstance(raw, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("refresh_token"):
        raise AuthError(
            f"Codex auth file {path} has no refresh_token — run `codex login` again"
        )
    return tokens


def import_codex_login(*, codex_path: Path | None = None) -> dict[str, Any]:
    """Seed jobcrawler's ChatGPT token store from the Codex CLI login; return the record (§9).

    Reads ``~/.codex/auth.json`` and writes litellm's flat ``auth.json`` schema
    (``{access_token, refresh_token, id_token, expires_at, account_id}``) into
    :func:`config.auth_dir`, reusing litellm's own ``_build_auth_record`` so the derived
    ``expires_at``/``account_id`` match exactly what its provider expects to read back. From
    here litellm owns refresh. Raises :class:`AuthError` if Codex hasn't logged in.
    """
    tokens = _read_codex_tokens(codex_path)
    auth = _authenticator()
    record: dict[str, Any] = auth._build_auth_record(
        {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
        }
    )
    # Prefer Codex's explicit account_id if litellm couldn't derive one from the JWT claim.
    if not record.get("account_id") and tokens.get("account_id"):
        record["account_id"] = tokens["account_id"]
    auth._write_auth_file(record)
    return record


def verify_token() -> str:
    """Return a fresh access token, refreshing if needed — WITHOUT device login (§9).

    The daemon-safe validity check: use the cached access_token if unexpired, else refresh
    from the refresh_token. Deliberately bypasses litellm's ``get_access_token`` (which, on a
    dead refresh, blocks up to 15 minutes on an interactive device-code prompt — fatal in a
    daemon). Raises :class:`AuthError` with a re-login hint on any failure.
    """
    auth = _authenticator()
    data = auth._read_auth_file()
    if not data:
        raise AuthError(
            "no ChatGPT credentials — run `codex login`, then `jobcrawl login`"
        )
    access = data.get("access_token")
    if access and not auth._is_token_expired(data, access):
        return access
    refresh = data.get("refresh_token")
    if not refresh:
        raise AuthError(
            "ChatGPT credentials have no refresh token — run `codex login` again, "
            "then `jobcrawl login`"
        )
    from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

    try:
        return auth._refresh_tokens(refresh)["access_token"]
    except RefreshAccessTokenError as exc:
        raise AuthError(
            f"ChatGPT token refresh failed ({exc}) — run `codex login` again, "
            "then `jobcrawl login`"
        ) from exc


def ensure_logged_in(model: str) -> None:
    """Preflight guard for a ``chatgpt/*`` model: a usable token must already exist (§9).

    A no-op for a non-``chatgpt`` slug. For a ``chatgpt/*`` slug it validates the token
    non-interactively (:func:`verify_token`), first importing from ``~/.codex`` if
    jobcrawler's own store is empty — so a freshly-provisioned host that has only run ``codex
    login`` still boots. It NEVER triggers device login; on failure it raises
    :class:`AuthError` so ``cmd_run``/``cmd_crawl`` fail fast with a clear message instead of
    the daemon blocking on litellm's interactive prompt at the first match.
    """
    if not is_chatgpt(model):
        return
    auth = _authenticator()
    if not auth._read_auth_file():
        import_codex_login()  # seed from codex; raises AuthError if it never logged in
    verify_token()


def login(*, codex_path: Path | None = None) -> dict[str, Any]:
    """Import the Codex CLI token and confirm it mints a session (the ``jobcrawl login`` core).

    Imports ``~/.codex/auth.json`` into jobcrawler's store, then does a live
    :func:`verify_token` so the operator learns *now* — not at the first match — whether the
    credential actually works. Returns ``{"account_id", "auth_dir"}`` for the caller to print.
    Raises :class:`AuthError` when Codex hasn't logged in or its refresh token is dead.
    """
    record = import_codex_login(codex_path=codex_path)
    verify_token()  # live refresh check — surface a dead token here, not in the daemon
    return {"account_id": record.get("account_id"), "auth_dir": config.auth_dir()}
