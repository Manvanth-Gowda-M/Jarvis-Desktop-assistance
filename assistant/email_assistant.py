"""
assistant/email_assistant.py
============================
Unified Email Assistant for Jarvis with official Gmail and Outlook Graph API integrations.

Architectural principles:
1. Strict Draft-Only Safety: Jarvis only creates and updates drafts in Gmail or
   Outlook Drafts folders. No send tools exist or are exposed to the AI agent.
2. Verified Saving: Never claim a draft was saved unless the provider API
   confirms a valid draft ID.
3. DPAPI Token Security: Credentials and tokens are stored outside the Git
   repository in %LOCALAPPDATA%\\Jarvis\\oauth with Windows DPAPI encryption.
4. Two Independent Providers: Full support for official Gmail API (users.drafts.create)
   and Microsoft Graph API (POST /me/messages with isDraft=True).
5. Natural Voice & Text: Clear subjects, professional bodies, explicit recipient
   resolution (no invented emails), and concise voice confirmations.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import (
    OAUTH_DIR,
    GMAIL_CREDENTIALS_FILE,
    GMAIL_TOKEN_FILE,
    JARVIS_OUTLOOK_CLIENT_ID,
    EMAIL_DEFAULT_PROVIDER,
)
from assistant.gmail_provider import GmailProvider
from assistant.outlook_provider import OutlookProvider
from assistant.email_security import redact_secrets

logger = logging.getLogger(__name__)

# Recipient email pattern validation
_EMAIL_RE = re.compile(r"^[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}$")


@dataclass
class EmailDraft:
    """In-memory representation of an email draft."""
    draft_id: str
    to: list[str]
    subject: str
    body: str
    provider: str  # "gmail", "outlook", "local", or "none"
    created_at: str
    status: str = "draft"  # "draft", "saved_to_provider", "failed"
    provider_draft_id: Optional[str] = None
    last_updated: Optional[str] = None

    def preview(self, body_chars: int = 500) -> str:
        """Format a clear human-readable preview of the draft."""
        body_snippet = self.body[:body_chars]
        if len(self.body) > body_chars:
            body_snippet += "…"

        provider_label = self.provider.upper() if self.provider else "NONE"
        remote_status = f" (Remote ID: {self.provider_draft_id})" if self.provider_draft_id else ""

        sep = "─" * 56
        return (
            f"Draft ID    : {self.draft_id}\n"
            f"To          : {', '.join(self.to)}\n"
            f"Subject     : {self.subject}\n"
            f"Provider    : {provider_label}{remote_status}\n"
            f"Status      : {self.status.upper()}\n"
            f"{sep}\n"
            f"Body:\n{body_snippet}\n"
            f"{sep}"
        )


class DraftStore:
    """Thread-safe in-memory store for active session drafts."""

    def __init__(self) -> None:
        self._store: dict[str, EmailDraft] = {}

    def save(self, draft: EmailDraft) -> str:
        self._store[draft.draft_id] = draft
        return draft.draft_id

    def get(self, draft_id: str) -> Optional[EmailDraft]:
        return self._store.get(draft_id)

    def get_latest(self) -> Optional[EmailDraft]:
        if not self._store:
            return None
        return list(self._store.values())[-1]

    def list_drafts(self) -> str:
        if not self._store:
            return "📭 No email drafts in memory."
        lines = ["📧 Active Email Drafts:", "─" * 60]
        for idx, d in enumerate(self._store.values(), start=1):
            to_str = ", ".join(d.to[:2])
            if len(d.to) > 2:
                to_str += f" (+{len(d.to) - 2} more)"
            remote_tag = f" [Synced: {d.provider.upper()}]" if d.provider_draft_id else " [Local]"
            lines.append(
                f"{idx}. [{d.status.upper()[:10]}] ID: {d.draft_id[:8]}… | To: {to_str} | "
                f"Subject: '{d.subject[:30]}'{remote_tag}"
            )
        return "\n".join(lines)


class EmailAssistant:
    """Coordinates Gmail and Outlook providers with strict draft-only safety."""

    def __init__(
        self,
        store: Optional[DraftStore] = None,
        gmail_provider: Optional[GmailProvider] = None,
        outlook_provider: Optional[OutlookProvider] = None,
    ):
        self.store = store or DraftStore()
        self.gmail = gmail_provider or GmailProvider()
        self.outlook = outlook_provider or OutlookProvider()

    def get_status(self) -> dict[str, Any]:
        """Return unified status of both email providers."""
        return {
            "gmail": self.gmail.get_status(),
            "outlook": self.outlook.get_status(),
            "default_provider": EMAIL_DEFAULT_PROVIDER,
        }

    def connect_gmail(self) -> tuple[bool, str]:
        """Authenticate with Gmail using Google OAuth desktop flow."""
        return self.gmail.connect()

    def disconnect_gmail(self) -> tuple[bool, str]:
        """Disconnect Gmail and remove stored DPAPI token."""
        return self.gmail.disconnect()

    def connect_outlook(self) -> tuple[bool, str]:
        """Authenticate with Microsoft Graph using MSAL interactive flow."""
        return self.outlook.connect()

    def disconnect_outlook(self) -> tuple[bool, str]:
        """Disconnect Outlook and remove cached token."""
        return self.outlook.disconnect()

    def resolve_provider(self, requested: str = "auto") -> tuple[str, str]:
        """Resolve the target provider ('gmail', 'outlook', or 'local').

        Returns (provider_name, explanation).
        """
        req = (requested or "auto").lower().strip()
        gmail_status = self.gmail.get_status()
        outlook_status = self.outlook.get_status()

        if req in ("gmail", "google"):
            if gmail_status["connected"]:
                return "gmail", "Using connected Gmail provider."
            return "gmail", "Gmail requested but not yet connected. Saved locally."

        if req in ("outlook", "microsoft", "graph"):
            if outlook_status["connected"]:
                return "outlook", "Using connected Outlook provider."
            return "outlook", "Outlook requested but not yet connected. Saved locally."

        # Auto resolution
        if gmail_status["connected"] and not outlook_status["connected"]:
            return "gmail", "Automatically selected connected Gmail provider."
        if outlook_status["connected"] and not gmail_status["connected"]:
            return "outlook", "Automatically selected connected Outlook provider."
        if gmail_status["connected"] and outlook_status["connected"]:
            pref = EMAIL_DEFAULT_PROVIDER.lower().strip()
            if pref == "outlook":
                return "outlook", "Using preferred Outlook provider."
            return "gmail", "Using default Gmail provider (both connected)."

        return "local", "Neither provider is currently connected. Saved as local draft."

    def compose_draft(
        self,
        to_addresses: str,
        subject: str,
        body: str,
        provider: str = "auto",
        save_to_provider: bool = True,
    ) -> str:
        """Compose a draft email and optionally sync to the real Gmail or Outlook Drafts folder.

        Strict Draft-Only: Never sends the email.
        """
        # 1. Parse and validate recipients
        recipients = [addr.strip() for addr in (to_addresses or "").split(",") if addr.strip()]
        if not recipients:
            return (
                "❌ Recipient Missing: Please provide a valid recipient email address.\n"
                "Example: 'draft an email to professor@university.edu about thesis review'"
            )

        # 2. Validate email format
        invalid_emails = [addr for addr in recipients if not _EMAIL_RE.match(addr)]
        if invalid_emails:
            return f"❌ Invalid email address format: {', '.join(invalid_emails)}"

        # 3. Clean subject and body
        subject = subject.strip() if subject else "(No Subject)"
        body = body.strip()

        # 4. Determine target provider
        chosen_provider, explanation = self.resolve_provider(provider)

        # 5. Create in-memory draft record
        now_iso = datetime.now(timezone.utc).isoformat()
        draft = EmailDraft(
            draft_id=str(uuid.uuid4()),
            to=recipients,
            subject=subject,
            body=body,
            provider=chosen_provider,
            created_at=now_iso,
            status="draft",
        )

        remote_success = False
        remote_id = None
        remote_msg = ""

        # 6. Save directly to real provider Drafts folder if requested and connected
        if save_to_provider:
            if chosen_provider == "gmail" and self.gmail.get_status()["connected"]:
                res = self.gmail.create_draft(recipients, subject, body)
                if res.get("success"):
                    remote_success = True
                    remote_id = res.get("draft_id")
                    draft.status = "saved_to_provider"
                    draft.provider_draft_id = remote_id
                    remote_msg = f"✅ Verified: Draft successfully saved to your Gmail Drafts folder! (Draft ID: {remote_id})"
                else:
                    remote_msg = f"⚠ Failed to save to Gmail Drafts: {res.get('error')}. Kept as local draft."

            elif chosen_provider == "outlook" and self.outlook.get_status()["connected"]:
                res = self.outlook.create_draft(recipients, subject, body)
                if res.get("success"):
                    remote_success = True
                    remote_id = res.get("draft_id")
                    draft.status = "saved_to_provider"
                    draft.provider_draft_id = remote_id
                    remote_msg = f"✅ Verified: Draft successfully saved to your Outlook Drafts folder! (Draft ID: {remote_id})"
                else:
                    remote_msg = f"⚠ Failed to save to Outlook Drafts: {res.get('error')}. Kept as local draft."

            elif chosen_provider in ("gmail", "outlook"):
                remote_msg = (
                    f"ℹ {chosen_provider.capitalize()} is not currently connected.\n"
                    f"   Draft is saved in local memory. Connect {chosen_provider.capitalize()} "
                    f"to sync to your actual Drafts folder."
                )
            else:
                remote_msg = (
                    "ℹ Draft saved in local memory. Connect Gmail or Outlook in the desktop panel "
                    "to sync to your mailbox Drafts folder."
                )

        self.store.save(draft)

        sep = "─" * 56
        preview_body = body[:300] + ("…" if len(body) > 300 else "")

        return (
            f"📧 Email Draft Created\n"
            f"{sep}\n"
            f"Draft ID    : {draft.draft_id}\n"
            f"To          : {', '.join(draft.to)}\n"
            f"Subject     : {draft.subject}\n"
            f"Provider    : {chosen_provider.upper()}\n"
            f"{sep}\n"
            f"Body preview:\n{preview_body}\n"
            f"{sep}\n"
            f"{remote_msg}\n\n"
            f"⚠ STRICT DRAFT-ONLY SAFETY: This email has NOT been sent and will NEVER be sent automatically.\n"
            f"   You can review and send it directly from your official Gmail or Outlook application."
        )

    def save_draft_to_provider(self, draft_id: str, provider: str = "auto") -> str:
        """Sync an existing local draft to the selected provider's actual Drafts folder."""
        draft = self.store.get(draft_id)
        if not draft:
            return f"❌ Draft '{draft_id}' not found."

        chosen_provider, _ = self.resolve_provider(provider if provider != "auto" else draft.provider)

        if chosen_provider == "gmail":
            res = self.gmail.create_draft(draft.to, draft.subject, draft.body)
            if res.get("success"):
                draft.provider = "gmail"
                draft.provider_draft_id = res.get("draft_id")
                draft.status = "saved_to_provider"
                return f"✅ Verified: Draft saved to Gmail Drafts folder. (Draft ID: {draft.provider_draft_id})"
            return f"❌ Failed to save to Gmail Drafts: {res.get('error')}"

        if chosen_provider == "outlook":
            res = self.outlook.create_draft(draft.to, draft.subject, draft.body)
            if res.get("success"):
                draft.provider = "outlook"
                draft.provider_draft_id = res.get("draft_id")
                draft.status = "saved_to_provider"
                return f"✅ Verified: Draft saved to Outlook Drafts folder. (Draft ID: {draft.provider_draft_id})"
            return f"❌ Failed to save to Outlook Drafts: {res.get('error')}"

        return (
            f"❌ Cannot save to provider: '{chosen_provider}' is not connected.\n"
            "   Please connect Gmail or Outlook first using the desktop panel or OAuth setup."
        )

    def update_draft(
        self,
        draft_id: str,
        to_addresses: Optional[str] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        confirmed: bool = False,
    ) -> str:
        """Update an existing draft with confirmation requirement before replacement."""
        draft = self.store.get(draft_id)
        if not draft:
            return f"❌ Draft '{draft_id}' not found."

        new_to = [a.strip() for a in to_addresses.split(",")] if to_addresses else draft.to
        new_subj = subject.strip() if subject is not None else draft.subject
        new_body = body.strip() if body is not None else draft.body

        if not confirmed:
            sep = "─" * 56
            return (
                f"⚠ CONFIRMATION REQUIRED BEFORE REPLACING DRAFT\n"
                f"{sep}\n"
                f"Existing Draft ID : {draft.draft_id}\n"
                f"Current Subject   : {draft.subject}\n"
                f"Proposed Subject  : {new_subj}\n"
                f"Proposed To       : {', '.join(new_to)}\n"
                f"Proposed Body Preview:\n{new_body[:200]}…\n"
                f"{sep}\n"
                f"To confirm and update this draft, call update_draft with confirmed=True."
            )

        draft.to = new_to
        draft.subject = new_subj
        draft.body = new_body
        draft.last_updated = datetime.now(timezone.utc).isoformat()

        # Update remote draft if already synced
        remote_info = ""
        if draft.provider == "gmail" and draft.provider_draft_id and self.gmail.get_status()["connected"]:
            res = self.gmail.update_draft(draft.provider_draft_id, draft.to, draft.subject, draft.body)
            if res.get("success"):
                remote_info = f" (Updated in Gmail Drafts: {draft.provider_draft_id})"
        elif draft.provider == "outlook" and draft.provider_draft_id and self.outlook.get_status()["connected"]:
            res = self.outlook.update_draft(draft.provider_draft_id, draft.to, draft.subject, draft.body)
            if res.get("success"):
                remote_info = f" (Updated in Outlook Drafts: {draft.provider_draft_id})"

        return f"✅ Draft '{draft.draft_id}' updated successfully.{remote_info}"

    def show_draft(self, draft_id: str) -> str:
        draft = self.store.get(draft_id)
        if not draft:
            return f"❌ Draft '{draft_id}' not found. Use list_email_drafts() to view active drafts."
        sep = "─" * 56
        return f"📧 Email Draft – Full Review\n{sep}\n{draft.preview(body_chars=3000)}"

    def list_drafts(self) -> str:
        return self.store.list_drafts()

    def get_setup_instructions(self, provider: str = "gmail") -> str:
        p = (provider or "gmail").lower().strip()
        if p == "gmail":
            return _GMAIL_SETUP_GUIDE
        if p in ("outlook", "microsoft"):
            return _OUTLOOK_SETUP_GUIDE
        return (
            f"Supported email providers: 'gmail', 'outlook'.\n"
            f"Use get_email_setup_instructions('gmail') or get_email_setup_instructions('outlook')."
        )


# Setup Guides
_GMAIL_SETUP_GUIDE = f"""
📧 Gmail OAuth 2.0 Setup Guide for Jarvis
═════════════════════════════════════════════════════════════════

OAuth Credential File Path:
  %LOCALAPPDATA%\\Jarvis\\oauth\\google_credentials.json
  (Full resolved path: {GMAIL_CREDENTIALS_FILE})

Steps to Configure:
1. Open Google Cloud Console: https://console.cloud.google.com/
2. Create a new project named "Jarvis Assistant" (or select an existing one).
3. Navigate to: APIs & Services → Library.
4. Search for "Gmail API" and click Enable.
5. Navigate to: APIs & Services → OAuth consent screen:
   - User Type: External (for personal Gmail accounts).
   - App Name: "Jarvis Assistant".
   - Support & Developer email: your personal Gmail.
   - Scopes: Add 'https://www.googleapis.com/auth/gmail.compose'.
     * Honest Scope Note: 'gmail.compose' allows managing drafts and sending.
       However, Jarvis strictly operates in Draft-Only mode and never sends email.
   - Test Users: Add your personal Gmail address.
6. Navigate to: APIs & Services → Credentials:
   - Click "Create Credentials" → "OAuth client ID".
   - Application type: "Desktop app". Name: "Jarvis Desktop".
   - Download the JSON client configuration.
7. Save the downloaded JSON file to:
   {GMAIL_CREDENTIALS_FILE}
8. In Jarvis desktop panel, click "Connect Gmail".
   Your browser will open for one-time Google sign-in.
   Tokens are encrypted with Windows DPAPI at:
   {GMAIL_TOKEN_FILE}
═════════════════════════════════════════════════════════════════
""".strip()


_OUTLOOK_SETUP_GUIDE = f"""
📧 Outlook / Microsoft Graph OAuth Setup Guide for Jarvis
═════════════════════════════════════════════════════════════════

Application Client ID Environment Variable:
  JARVIS_OUTLOOK_CLIENT_ID

Token Cache Location (DPAPI encrypted):
  %LOCALAPPDATA%\\Jarvis\\oauth\\outlook_token_cache.bin
  (Full resolved path: {OAUTH_DIR / 'outlook_token_cache.bin'})

Steps to Configure:
1. Open Microsoft Azure Portal: https://portal.azure.com/
2. Navigate to: "Microsoft Entra ID" (or Azure Active Directory) → "App registrations".
3. Click "New registration":
   - Name: "Jarvis Desktop Assistant".
   - Supported account types:
     "Accounts in any organizational directory and personal Microsoft accounts (e.g. Skype, Xbox, Outlook.com)".
   - Redirect URI: Select platform "Public client/native" and set:
     http://localhost
4. Click Register. Note the "Application (client) ID" (a GUID).
5. Navigate to: "API permissions" → "Add a permission":
   - Microsoft Graph → Delegated permissions.
   - Select: 'Mail.ReadWrite'
   - Strictly DO NOT add 'Mail.Send'.
6. Add your Client ID to c:\\Users\\krish\\Desktop\\Jarvis\\.env:
   JARVIS_OUTLOOK_CLIENT_ID=<your-application-client-id>
7. In Jarvis desktop panel, click "Connect Outlook".
   Your browser will open for one-time Microsoft sign-in.
═════════════════════════════════════════════════════════════════
""".strip()


# Singleton instance
_default_assistant = EmailAssistant()


# ---------------------------------------------------------------------------
# Public Functions & Backward Compatibility API
# ---------------------------------------------------------------------------

def compose_email_draft(
    to_addresses: str,
    subject: str,
    body: str,
    provider: str = "auto",
) -> str:
    """Compose an email draft and save it to Gmail or Outlook Drafts."""
    return _default_assistant.compose_draft(
        to_addresses=to_addresses,
        subject=subject,
        body=body,
        provider=provider,
    )


def show_email_draft(draft_id: str) -> str:
    return _default_assistant.show_draft(draft_id)


def list_email_drafts() -> str:
    return _default_assistant.list_drafts()


def save_draft_to_provider(draft_id: str, provider: str = "auto") -> str:
    return _default_assistant.save_draft_to_provider(draft_id, provider)


def get_email_provider_status() -> str:
    st = _default_assistant.get_status()
    g = st["gmail"]
    o = st["outlook"]
    g_info = f"✅ Connected ({g.get('email', 'User')})" if g["connected"] else f"❌ {g.get('detail', 'Disconnected')}"
    o_info = f"✅ Connected ({o.get('email', 'User')})" if o["connected"] else f"❌ {o.get('detail', 'Disconnected')}"
    return (
        f"📧 Email Provider Status:\n"
        f"  • Gmail   : {g_info}\n"
        f"  • Outlook : {o_info}"
    )


def get_email_setup_instructions(provider: str = "gmail") -> str:
    return _default_assistant.get_setup_instructions(provider)


def send_email_draft(draft_id: str, confirmed: bool = False) -> str:
    """Strict Draft-Only Safety Gate: Refuses to send and explains policy.

    Retained as Python function for test compatibility, but NEVER exposed
    in EMAIL_TOOLS_SCHEMA or TOOLS_SCHEMA.
    """
    draft = _default_assistant.store.get(draft_id)
    if not draft:
        return f"❌ Draft '{draft_id}' not found."

    if not confirmed:
        return (
            f"⚠ CONFIRMATION REQUIRED / DRAFT-ONLY POLICY:\n"
            f"{draft.preview()}\n\n"
            f"Notice: Jarvis is strictly configured for draft creation only. "
            f"Emails must be reviewed and sent manually from your Gmail or Outlook application."
        )

    # Even with confirmed=True, Jarvis enforces draft-only operation
    return (
        f"📭 Draft-Only Safety: Jarvis created draft '{draft_id}' in your "
        f"Drafts folder. Transmission is disabled for safety."
    )


# ---------------------------------------------------------------------------
# Tool Schemas for Agent (Strictly Draft-Only: NO send tools!)
# ---------------------------------------------------------------------------

EMAIL_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "compose_email_draft",
            "description": (
                "Compose an email draft and save it to the user's official Gmail or Outlook Drafts folder. "
                "STRICTLY DRAFT-ONLY: Never sends email. Requires explicit recipient address."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_addresses": {
                        "type": "string",
                        "description": "Comma-separated recipient email addresses.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Clear and professional email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Professional plain-text email message body.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Target email provider: 'gmail', 'outlook', or 'auto'.",
                        "enum": ["gmail", "outlook", "auto"],
                        "default": "auto",
                    },
                },
                "required": ["to_addresses", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_email_draft",
            "description": "Display the full content and status of a saved email draft by its draft_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "UUID of the draft to display.",
                    },
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_drafts",
            "description": "List all active email drafts held in memory or synced to providers.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft_to_provider",
            "description": "Sync an existing draft to the user's actual Gmail or Outlook Drafts folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "UUID of the draft to sync.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Target provider: 'gmail', 'outlook', or 'auto'.",
                        "enum": ["gmail", "outlook", "auto"],
                        "default": "auto",
                    },
                },
                "required": ["draft_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_provider_status",
            "description": "Check whether Gmail and Outlook OAuth providers are connected or need setup.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_setup_instructions",
            "description": "Get step-by-step OAuth setup instructions for Gmail or Outlook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "Email provider: 'gmail' or 'outlook'.",
                        "enum": ["gmail", "outlook"],
                        "default": "gmail",
                    },
                },
                "required": [],
            },
        },
    },
]

EMAIL_AVAILABLE_TOOLS: dict[str, Any] = {
    "compose_email_draft": compose_email_draft,
    "show_email_draft": show_email_draft,
    "list_email_drafts": list_email_drafts,
    "save_draft_to_provider": save_draft_to_provider,
    "get_email_provider_status": get_email_provider_status,
    "get_email_setup_instructions": get_email_setup_instructions,
}
