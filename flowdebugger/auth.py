# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import threading
from typing import Optional

import msal

from .settings import app_data_dir

# Same public client used by the XrmToolBox version (supports http://localhost redirect).
DEFAULT_CLIENT_ID = "51f81489-12ee-4a9e-aaae-a2591f45987d"
FLOW_AUDIENCE = "https://service.flow.microsoft.com"
FLOW_SCOPE = FLOW_AUDIENCE + "/.default"

CACHE_FILE = app_data_dir() / "msal_token_cache.bin"


class AuthError(Exception):
    pass


def _build_cache():
    """Token cache encrypted with DPAPI (Windows). Falls back to memory-only, never plain text."""
    try:
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        return PersistedTokenCache(build_encrypted_persistence(str(CACHE_FILE))), True
    except Exception:
        return msal.SerializableTokenCache(), False


class TokenProvider:
    """
    Interactive sign-in in the system browser (supports MFA / Conditional Access),
    then silent renewal through the MSAL cache for every resource (Flow API, Dataverse).
    """

    def __init__(self, tenant: Optional[str] = None, client_id: Optional[str] = None):
        self.tenant = (tenant or "").strip() or "organizations"
        self.client_id = (client_id or "").strip() or DEFAULT_CLIENT_ID
        cache, self.cache_persisted = _build_cache()
        self._app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant}",
            token_cache=cache,
        )
        self._account = None
        self._lock = threading.Lock()

    @property
    def username(self) -> Optional[str]:
        return self._account.get("username") if self._account else None

    def _current_account(self):
        if self._account:
            return self._account
        accounts = self._app.get_accounts()
        return accounts[0] if accounts else None

    def get_token(self, scope: str, force_interactive: bool = False) -> str:
        with self._lock:
            account = self._current_account()

            if account and not force_interactive:
                result = self._app.acquire_token_silent([scope], account=account)
                if result and "access_token" in result:
                    self._account = account
                    return result["access_token"]

            result = self._app.acquire_token_interactive(
                [scope],
                login_hint=account.get("username") if account else None,
                prompt=None if account else "select_account",
                timeout=300,
            )
            if not result or "access_token" not in result:
                err = (result or {}).get("error_description") or (result or {}).get("error") or "unknown error"
                raise AuthError(f"Sign-in failed: {err}")

            claims = result.get("id_token_claims") or {}
            username = claims.get("preferred_username")
            matches = self._app.get_accounts(username=username) if username else []
            self._account = matches[0] if matches else self._current_account()
            return result["access_token"]

    def flow_token(self) -> str:
        return self.get_token(FLOW_SCOPE)

    def dataverse_token(self, instance_url: str) -> str:
        return self.get_token(instance_url.rstrip("/") + "/.default")

    def sign_out(self) -> None:
        with self._lock:
            for acc in self._app.get_accounts():
                self._app.remove_account(acc)
            self._account = None
