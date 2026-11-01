"""
assistant/gmail_provider.py
===========================
Official Gmail API integration for creating and managing email drafts in Jarvis.

Security & Safety:
- Strict Draft-Only: Exposes draft creation and updates only. No send capability.
- Scope: https://www.googleapis.com/auth/gmail.compose
- Desktop OAuth flow: Loads credentials from %LOCALAPPDATA%\\Jarvis\\oauth\\google_credentials.json.
- DPAPI token cache: Tokens encrypted with Windows DPAPI at %LOCALAPPDATA%\\Jarvis\\oauth\\gmail_token.dpapi.
- Tokens, refresh tokens, and secrets are strictly redacted and never logged.
"""

from __future__ import annotations

import base64
import email.message
import json
import logging
from pathlib import Path
from typing import Any, Optional

from config import GMAIL_CREDENTIALS_FILE, GMAIL_TOKEN_FILE, GMAIL_SCOPES
from assistant.email_security import (
    save_secure_token,
    load_secure_token,
    delete_secure_token,
    redact_secrets,
)

logger = logging.getLogger(__name__)

# Lazy imports for google libraries so module imports cleanly even if dependencies are missing
try:
    import google.auth
    import google.auth.transport.requests
    from google.auth.transport.requests import Request
    import google.oauth2.credentials
    from google.oauth2.credentials import Credentials
    import google_auth_oauthlib.flow
    from google_auth_oauthlib.flow import InstalledAppFlow
    import googleapiclient.discovery
    from googleapiclient.discovery import build
    _GOOGLE_API_AVAILABLE = True
except ImportError:
    Credentials = None
    InstalledAppFlow = None
    Request = None
    build = None
    _GOOGLE_API_AVAILABLE = False


class GmailProvider:
    """Manages Google OAuth 2.0 authentication and Gmail draft operations."""

    def __init__(
        self,
        credentials_file: Optional[Path | str] = None,
        token_file: Optional[Path | str] = None,
        scopes: Optional[list[str]] = None,
    ):
        self.credentials_file = Path(credentials_file or GMAIL_CREDENTIALS_FILE)
        self.token_file = Path(token_file or GMAIL_TOKEN_FILE)
        self.scopes = list(scopes or GMAIL_SCOPES)
        self._service = None
        self._cached_creds = None

    @property
    def is_library_available(self) -> bool:
        """Return True if Google client libraries are installed."""
        try:
            import google.oauth2.credentials
            import googleapiclient.discovery
            return True
        except ImportError:
            return _GOOGLE_API_AVAILABLE

    def get_status(self) -> dict[str, Any]:
        """Check current Gmail connection status."""
        creds_exist = False
        try:
            creds_exist = self.credentials_file.is_file() and self.credentials_file.stat().st_size > 0
        except Exception:
            creds_exist = False

        if not creds_exist:
            return {
                "provider": "gmail",
                "status": "missing_credentials",
                "detail": f"Desktop OAuth file missing: {self.credentials_file.name}",
                "credentials_present": False,
                "connected": False,
            }

        creds = self._get_valid_credentials()
        if creds is not None:
            email_addr = self._get_user_email(creds)
            return {
                "provider": "gmail",
                "status": "connected",
                "detail": f"Connected as {email_addr}" if email_addr else "Connected",
                "email": email_addr,
                "credentials_present": True,
                "connected": True,
            }

        return {
            "provider": "gmail",
            "status": "disconnected",
            "detail": "Credentials present. Authentication required.",
            "credentials_present": True,
            "connected": False,
        }

    def _get_user_email(self, creds) -> str:
        """Attempt to fetch authenticated user email address."""
        try:
            service = self._get_service(creds)
            if service:
                profile = service.users().getProfile(userId="me").execute()
                return profile.get("emailAddress", "")
        except Exception:
            pass
        return ""

    def _get_valid_credentials(self):
        """Retrieve and validate credentials from DPAPI storage, refreshing if expired."""
        if self._cached_creds and getattr(self._cached_creds, "valid", False):
            return self._cached_creds

        token_json_str = load_secure_token(self.token_file)
        if not token_json_str:
            return None

        try:
            token_data = json.loads(token_json_str)

            # Resolve Credentials class dynamically to support patching and modular testing
            creds_cls = Credentials
            if creds_cls is None:
                try:
                    import google.oauth2.credentials
                    creds_cls = google.oauth2.credentials.Credentials
                except Exception:
                    creds_cls = None

            if creds_cls is None:
                return None

            creds = creds_cls.from_authorized_user_info(token_data, self.scopes)

            if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                try:
                    logger.info("[Gmail] Refreshing expired OAuth token...")
                    req_obj = None
                    if Request is not None:
                        req_obj = Request()
                    else:
                        from google.auth.transport.requests import Request as GReq
                        req_obj = GReq()

                    creds.refresh(req_obj)
                    if hasattr(creds, "to_json"):
                        save_secure_token(self.token_file, creds.to_json())
                except Exception as refresh_err:
                    logger.warning("[Gmail] Token refresh failed: %s", redact_secrets(str(refresh_err)))
                    delete_secure_token(self.token_file)
                    return None

            if getattr(creds, "valid", False):
                self._cached_creds = creds
                return creds
        except Exception as e:
            logger.warning("[Gmail] Error loading saved credentials: %s", redact_secrets(str(e)))

        return None

    def connect(self) -> tuple[bool, str]:
        """Launch browser sign-in flow and store DPAPI-encrypted token."""
        if not self.is_library_available:
            return False, "Google API libraries not installed. Run: pip install google-api-python-client google-auth-oauthlib"

        if not self.credentials_file.is_file():
            return False, (
                f"Missing Google OAuth client secrets file.\n"
                f"Please place your downloaded 'google_credentials.json' at:\n"
                f"  {self.credentials_file}"
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_file),
                scopes=self.scopes,
            )
            creds = flow.run_local_server(port=0, prompt="consent")
            if not creds:
                return False, "Authorization was cancelled or returned empty credentials."

            save_secure_token(self.token_file, creds.to_json())
            self._cached_creds = creds
            self._service = None
            email_addr = self._get_user_email(creds)
            msg = f"Gmail connected successfully! Authorized as: {email_addr or 'user'}"
            logger.info("[Gmail] %s", msg)
            return True, msg
        except Exception as exc:
            msg = f"Gmail authorization failed: {redact_secrets(str(exc))}"
            logger.error("[Gmail] %s", msg)
            return False, msg

    def disconnect(self) -> tuple[bool, str]:
        """Disconnect Gmail and delete local encrypted token."""
        self._cached_creds = None
        self._service = None
        deleted = delete_secure_token(self.token_file)
        if deleted:
            return True, "Gmail has been disconnected and stored credentials removed."
        return True, "Gmail was not connected."

    def _get_service(self, creds=None):
        """Build or return cached Gmail Resource service."""
        if not self.is_library_available:
            return None
        if self._service is not None:
            return self._service

        active_creds = creds or self._get_valid_credentials()
        if not active_creds:
            return None

        try:
            self._service = build("gmail", "v1", credentials=active_creds, cache_discovery=False)
            return self._service
        except Exception as exc:
            logger.error("[Gmail] Failed to build Gmail service: %s", redact_secrets(str(exc)))
            return None

    def create_draft(self, to_addresses: list[str], subject: str, body: str) -> dict[str, Any]:
        """Create a new draft in user's Gmail Drafts folder.

        Strict draft-only: Never sends messages.
        """
        if not to_addresses:
            return {"success": False, "error": "No recipient addresses provided."}

        creds = self._get_valid_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Gmail is not authorized. Please connect Gmail first via OAuth.",
                "status": "not_authorized",
            }

        service = self._get_service(creds)
        if not service:
            return {"success": False, "error": "Unable to initialize Gmail service client."}

        try:
            # Build standard MIME email
            msg = email.message.EmailMessage()
            msg["To"] = ", ".join(to_addresses)
            msg["Subject"] = subject
            msg.set_content(body)

            raw_bytes = msg.as_bytes()
            raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

            draft_body = {
                "message": {
                    "raw": raw_b64,
                }
            }

            created_draft = service.users().drafts().create(userId="me", body=draft_body).execute()
            draft_id = created_draft.get("id")

            if not draft_id:
                return {
                    "success": False,
                    "error": "Gmail API responded but did not return a valid draft ID.",
                }

            logger.info("[Gmail] Draft created successfully with ID: %s", draft_id)
            return {
                "success": True,
                "provider": "gmail",
                "draft_id": draft_id,
                "to": to_addresses,
                "subject": subject,
                "message": f"Gmail draft saved successfully. Draft ID: {draft_id}",
            }
        except Exception as exc:
            err_msg = redact_secrets(str(exc))
            logger.error("[Gmail] Error creating draft: %s", err_msg)
            return {
                "success": False,
                "error": f"Gmail draft creation failed: {err_msg}",
            }

    def update_draft(self, draft_id: str, to_addresses: list[str], subject: str, body: str) -> dict[str, Any]:
        """Update an existing draft in Gmail Drafts folder."""
        if not draft_id:
            return {"success": False, "error": "Missing required draft ID for update."}
        if not to_addresses:
            return {"success": False, "error": "No recipient addresses provided."}

        creds = self._get_valid_credentials()
        if not creds:
            return {"success": False, "error": "Gmail is not authorized."}

        service = self._get_service(creds)
        if not service:
            return {"success": False, "error": "Unable to initialize Gmail service client."}

        try:
            msg = email.message.EmailMessage()
            msg["To"] = ", ".join(to_addresses)
            msg["Subject"] = subject
            msg.set_content(body)

            raw_bytes = msg.as_bytes()
            raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

            draft_body = {
                "id": draft_id,
                "message": {
                    "raw": raw_b64,
                },
            }

            updated = service.users().drafts().update(userId="me", id=draft_id, body=draft_body).execute()
            res_id = updated.get("id", draft_id)

            return {
                "success": True,
                "provider": "gmail",
                "draft_id": res_id,
                "to": to_addresses,
                "subject": subject,
                "message": f"Gmail draft {res_id} updated successfully.",
            }
        except Exception as exc:
            err_msg = redact_secrets(str(exc))
            logger.error("[Gmail] Error updating draft %s: %s", draft_id, err_msg)
            return {"success": False, "error": f"Failed to update Gmail draft: {err_msg}"}

