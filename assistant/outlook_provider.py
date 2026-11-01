"""
assistant/outlook_provider.py
=============================
Microsoft Graph API & MSAL integration for creating and managing Outlook email drafts.

Security & Safety:
- Strict Draft-Only: Exposes draft creation and updates only. No send capability.
- Delegated Scope: https://graph.microsoft.com/Mail.ReadWrite (Strictly NO Mail.Send).
- MSAL PublicClientApplication: Public client OAuth without embedded client secrets.
- DPAPI token cache: Token cache encrypted with Windows DPAPI via msal-extensions.
- Cache location: %LOCALAPPDATA%\\Jarvis\\oauth\\outlook_token_cache.bin (outside Git).
- Authority: https://login.microsoftonline.com/common (supports personal and work/school accounts).
- Tokens and credentials are strictly redacted and never logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional
import requests

from config import (
    JARVIS_OUTLOOK_CLIENT_ID,
    JARVIS_OUTLOOK_AUTHORITY,
    OUTLOOK_TOKEN_CACHE_FILE,
    OUTLOOK_SCOPES,
)
from assistant.email_security import redact_secrets, is_windows

logger = logging.getLogger(__name__)

# Lazy imports for msal so module imports cleanly even if dependencies are missing
try:
    import msal
    from msal_extensions import FilePersistenceWithDataProtection, PersistedTokenCache
    _MSAL_AVAILABLE = True
except ImportError:
    msal = None
    FilePersistenceWithDataProtection = None
    PersistedTokenCache = None
    _MSAL_AVAILABLE = False


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookProvider:
    """Manages Microsoft Graph API authentication and Outlook draft operations."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        authority: Optional[str] = None,
        cache_file: Optional[Path | str] = None,
        scopes: Optional[list[str]] = None,
    ):
        self.client_id = (client_id or JARVIS_OUTLOOK_CLIENT_ID or "").strip()
        self.authority = authority or JARVIS_OUTLOOK_AUTHORITY
        self.cache_file = Path(cache_file or OUTLOOK_TOKEN_CACHE_FILE)
        self.scopes = list(scopes or OUTLOOK_SCOPES)
        self._app = None
        self._cache = None

    @property
    def is_library_available(self) -> bool:
        """Return True if msal and msal-extensions are installed."""
        try:
            import msal
            return True
        except ImportError:
            return _MSAL_AVAILABLE

    def _init_app(self):
        """Initialize MSAL PublicClientApplication with DPAPI token cache."""
        if not self.is_library_available:
            return None
        if self._app is not None:
            return self._app
        if not self.client_id:
            return None

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        token_cache = None
        try:
            if is_windows() and FilePersistenceWithDataProtection and PersistedTokenCache:
                persistence = FilePersistenceWithDataProtection(str(self.cache_file))
                token_cache = PersistedTokenCache(persistence)
            else:
                token_cache = msal.SerializableTokenCache()
        except Exception as exc:
            logger.warning("[Outlook] Token cache initialization note: %s", redact_secrets(str(exc)))
            token_cache = msal.SerializableTokenCache()

        self._cache = token_cache
        self._app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            token_cache=token_cache,
        )
        return self._app

    def get_status(self) -> dict[str, Any]:
        """Check current Outlook / Microsoft Graph connection status."""
        if not self.client_id:
            return {
                "provider": "outlook",
                "status": "missing_client_id",
                "detail": "JARVIS_OUTLOOK_CLIENT_ID is not configured.",
                "client_id_configured": False,
                "connected": False,
            }

        token = self._get_valid_access_token()
        if token:
            email_addr = self._get_user_email(token)
            return {
                "provider": "outlook",
                "status": "connected",
                "detail": f"Connected as {email_addr}" if email_addr else "Connected",
                "email": email_addr,
                "client_id_configured": True,
                "connected": True,
            }

        return {
            "provider": "outlook",
            "status": "disconnected",
            "detail": "Client ID configured. Authorization required.",
            "client_id_configured": True,
            "connected": False,
        }

    def _get_user_email(self, access_token: str) -> str:
        """Fetch user email or principal name from Graph /me."""
        try:
            resp = requests.get(
                f"{GRAPH_BASE_URL}/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("mail") or data.get("userPrincipalName", "")
        except Exception:
            pass
        return ""

    def _get_valid_access_token(self) -> Optional[str]:
        """Silently acquire a valid access token from cache or refresh token."""
        app = self._init_app()
        if not app:
            return None

        accounts = app.get_accounts()
        if not accounts:
            return None

        result = app.acquire_token_silent(scopes=self.scopes, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

        return None

    def connect(self) -> tuple[bool, str]:
        """Open interactive browser sign-in flow for Microsoft Graph."""
        if not self.is_library_available:
            return False, "MSAL libraries not installed. Run: pip install msal msal-extensions"

        if not self.client_id:
            return False, (
                "Missing Outlook Application Client ID.\n"
                "Please register an App in Azure Portal (Personal + Work accounts, Public client)\n"
                "and set JARVIS_OUTLOOK_CLIENT_ID in your .env or environment."
            )

        app = self._init_app()
        if not app:
            return False, "Failed to initialize MSAL application."

        try:
            result = app.acquire_token_interactive(
                scopes=self.scopes,
                prompt="select_account",
            )
            if "access_token" in result:
                email_addr = self._get_user_email(result["access_token"])
                msg = f"Outlook connected successfully! Authorized as: {email_addr or 'user'}"
                logger.info("[Outlook] %s", msg)
                return True, msg

            err_desc = result.get("error_description") or result.get("error") or "Unknown authorization failure"
            return False, f"Outlook authorization failed: {redact_secrets(str(err_desc))}"
        except Exception as exc:
            msg = f"Outlook authorization failed: {redact_secrets(str(exc))}"
            logger.error("[Outlook] %s", msg)
            return False, msg

    def disconnect(self) -> tuple[bool, str]:
        """Disconnect Outlook, remove accounts from token cache, and delete cache file."""
        app = self._init_app()
        if app:
            try:
                for account in app.get_accounts():
                    app.remove_account(account)
            except Exception:
                pass

        self._app = None
        self._cache = None

        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
                return True, "Outlook has been disconnected and cached token removed."
            except Exception as e:
                return True, f"Outlook disconnected (cache file removal note: {e})"

        return True, "Outlook was not connected."

    def create_draft(self, to_addresses: list[str], subject: str, body: str) -> dict[str, Any]:
        """Create a new draft in user's Outlook Drafts folder via POST /me/messages.

        Strict draft-only: Sets isDraft=True and never sends.
        """
        if not to_addresses:
            return {"success": False, "error": "No recipient addresses provided."}

        token = self._get_valid_access_token()
        if not token:
            return {
                "success": False,
                "error": "Outlook is not authorized. Please connect Outlook first via OAuth.",
                "status": "not_authorized",
            }

        try:
            recipients = [
                {"emailAddress": {"address": addr.strip()}}
                for addr in to_addresses
                if addr.strip()
            ]

            payload = {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": recipients,
                "isDraft": True,
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            resp = requests.post(
                f"{GRAPH_BASE_URL}/me/messages",
                json=payload,
                headers=headers,
                timeout=15,
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                draft_id = data.get("id")
                if not draft_id:
                    return {"success": False, "error": "Microsoft Graph created draft but returned no message ID."}

                logger.info("[Outlook] Draft created successfully with ID: %s", draft_id)
                return {
                    "success": True,
                    "provider": "outlook",
                    "draft_id": draft_id,
                    "to": to_addresses,
                    "subject": subject,
                    "message": f"Outlook draft saved successfully. Draft ID: {draft_id}",
                }

            error_body = redact_secrets(resp.text[:300])
            logger.error("[Outlook] Graph API error (%d): %s", resp.status_code, error_body)
            return {
                "success": False,
                "error": f"Microsoft Graph API returned HTTP {resp.status_code}: {error_body}",
            }
        except Exception as exc:
            err_msg = redact_secrets(str(exc))
            logger.error("[Outlook] Error creating draft: %s", err_msg)
            return {"success": False, "error": f"Outlook draft creation failed: {err_msg}"}

    def update_draft(self, draft_id: str, to_addresses: list[str], subject: str, body: str) -> dict[str, Any]:
        """Update an existing draft in Outlook via PATCH /me/messages/{id}."""
        if not draft_id:
            return {"success": False, "error": "Missing required draft ID for update."}
        if not to_addresses:
            return {"success": False, "error": "No recipient addresses provided."}

        token = self._get_valid_access_token()
        if not token:
            return {"success": False, "error": "Outlook is not authorized."}

        try:
            recipients = [
                {"emailAddress": {"address": addr.strip()}}
                for addr in to_addresses
                if addr.strip()
            ]

            payload = {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": recipients,
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            resp = requests.patch(
                f"{GRAPH_BASE_URL}/me/messages/{draft_id}",
                json=payload,
                headers=headers,
                timeout=15,
            )

            if resp.status_code in (200, 201):
                return {
                    "success": True,
                    "provider": "outlook",
                    "draft_id": draft_id,
                    "to": to_addresses,
                    "subject": subject,
                    "message": f"Outlook draft {draft_id} updated successfully.",
                }

            error_body = redact_secrets(resp.text[:300])
            return {
                "success": False,
                "error": f"Microsoft Graph API update returned HTTP {resp.status_code}: {error_body}",
            }
        except Exception as exc:
            err_msg = redact_secrets(str(exc))
            return {"success": False, "error": f"Failed to update Outlook draft: {err_msg}"}

