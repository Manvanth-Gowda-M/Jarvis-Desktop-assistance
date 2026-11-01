"""
tests/test_email_integration.py
================================
Comprehensive test suite for Jarvis Gmail + Outlook email integration.

Validates:
1. Gmail OAuth connection states.
2. Outlook OAuth connection states.
3. Expired token handling and refresh.
4. Gmail draft payload generation and MIME encoding.
5. Outlook draft payload generation (Graph API format).
6. Missing and invalid recipient handling (no invented emails).
7. Provider resolution logic.
8. Successful draft creation with verified remote IDs.
9. Failed draft creation error handling.
10. Confirmation requirement before draft replacement.
11. Strict Draft-Only: Ensuring NO email-sending tool is registered or exposed.
"""

import base64
import email
import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from assistant.email_security import dpapi_encrypt, dpapi_decrypt, redact_secrets
from assistant.gmail_provider import GmailProvider
from assistant.outlook_provider import OutlookProvider
from assistant.email_assistant import (
    EmailAssistant,
    DraftStore,
    EmailDraft,
    EMAIL_TOOLS_SCHEMA,
    EMAIL_AVAILABLE_TOOLS,
)
from assistant.tools import TOOLS_SCHEMA, AVAILABLE_TOOLS


# ---------------------------------------------------------------------------
# 1. DPAPI & Security Tests
# ---------------------------------------------------------------------------

class TestEmailSecurity:
    def test_dpapi_roundtrip(self):
        secret = b"my_secret_oauth_token_12345"
        encrypted = dpapi_encrypt(secret)
        assert encrypted != secret
        decrypted = dpapi_decrypt(encrypted)
        assert decrypted == secret

    def test_redact_secrets(self):
        log = "Token: Bearer ya29.a0AfH6SMD_very_secret_token_123456789 and client_secret: my_super_secret_value"
        redacted = redact_secrets(log)
        assert "ya29" not in redacted
        assert "my_super_secret_value" not in redacted
        assert "[REDACTED]" in redacted


# ---------------------------------------------------------------------------
# 2. Gmail Provider Tests
# ---------------------------------------------------------------------------

class TestGmailProvider:
    def test_status_missing_credentials(self, tmp_path):
        missing_file = tmp_path / "nonexistent.json"
        token_file = tmp_path / "token.dpapi"
        provider = GmailProvider(credentials_file=missing_file, token_file=token_file)

        status = provider.get_status()
        assert status["status"] == "missing_credentials"
        assert status["connected"] is False

    def test_status_disconnected_when_token_absent(self, tmp_path):
        creds_file = tmp_path / "google_credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        token_file = tmp_path / "nonexistent_token.dpapi"

        provider = GmailProvider(credentials_file=creds_file, token_file=token_file)
        status = provider.get_status()
        assert status["status"] == "disconnected"
        assert status["connected"] is False

    def test_status_connected_with_valid_token(self, tmp_path):
        creds_file = tmp_path / "google_credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        token_file = tmp_path / "token.dpapi"

        provider = GmailProvider(credentials_file=creds_file, token_file=token_file)

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False

        with patch.object(provider, "_get_valid_credentials", return_value=mock_creds), \
             patch.object(provider, "_get_user_email", return_value="user@gmail.com"):
            status = provider.get_status()
            assert status["status"] == "connected"
            assert status["connected"] is True
            assert status["email"] == "user@gmail.com"

    def test_expired_token_triggers_refresh(self, tmp_path):
        creds_file = tmp_path / "google_credentials.json"
        token_file = tmp_path / "token.dpapi"

        token_data = {
            "token": "expired_access_token",
            "refresh_token": "valid_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid",
            "client_secret": "csec",
        }
        from assistant.email_security import save_secure_token
        save_secure_token(token_file, json.dumps(token_data))

        provider = GmailProvider(credentials_file=creds_file, token_file=token_file)

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = True
        mock_creds.refresh_token = "valid_refresh_token"
        mock_creds.to_json.return_value = json.dumps(token_data)

        with patch("google.oauth2.credentials.Credentials.from_authorized_user_info", return_value=mock_creds):
            valid_creds = provider._get_valid_credentials()
            assert mock_creds.refresh.called
            assert valid_creds is not None

    def test_create_draft_payload_and_success(self, tmp_path):
        provider = GmailProvider()

        mock_service = MagicMock()
        mock_drafts = MagicMock()
        mock_service.users.return_value.drafts.return_value = mock_drafts
        mock_drafts.create.return_value.execute.return_value = {
            "id": "draft_gmail_12345",
            "message": {"id": "msg_gmail_12345"},
        }

        with patch.object(provider, "_get_valid_credentials", return_value=MagicMock()), \
             patch.object(provider, "_get_service", return_value=mock_service):
            result = provider.create_draft(
                to_addresses=["prof@univ.edu"],
                subject="Appointment Request",
                body="Dear Professor, I would like to schedule an appointment.",
            )

            assert result["success"] is True
            assert result["draft_id"] == "draft_gmail_12345"
            assert result["provider"] == "gmail"

            # Verify arguments sent to Gmail API
            call_kwargs = mock_drafts.create.call_args[1]
            body_arg = call_kwargs["body"]
            raw_b64 = body_arg["message"]["raw"]
            raw_bytes = base64.urlsafe_b64decode(raw_b64)
            parsed_msg = email.message_from_bytes(raw_bytes)

            assert parsed_msg["To"] == "prof@univ.edu"
            assert parsed_msg["Subject"] == "Appointment Request"
            assert "schedule an appointment" in str(parsed_msg.get_payload())

    def test_create_draft_failure(self):
        provider = GmailProvider()
        mock_service = MagicMock()
        mock_service.users.return_value.drafts.return_value.create.return_value.execute.side_effect = (
            RuntimeError("Quota exceeded")
        )

        with patch.object(provider, "_get_valid_credentials", return_value=MagicMock()), \
             patch.object(provider, "_get_service", return_value=mock_service):
            result = provider.create_draft(
                to_addresses=["prof@univ.edu"],
                subject="Appointment",
                body="Body",
            )
            assert result["success"] is False
            assert "failed" in result["error"].lower()


# ---------------------------------------------------------------------------
# 3. Outlook Provider Tests
# ---------------------------------------------------------------------------

class TestOutlookProvider:
    def test_status_missing_client_id(self):
        provider = OutlookProvider(client_id="")
        status = provider.get_status()
        assert status["status"] == "missing_client_id"
        assert status["connected"] is False

    def test_status_disconnected(self):
        provider = OutlookProvider(client_id="test-client-guid")
        with patch.object(provider, "_get_valid_access_token", return_value=None):
            status = provider.get_status()
            assert status["status"] == "disconnected"
            assert status["connected"] is False

    def test_status_connected(self):
        provider = OutlookProvider(client_id="test-client-guid")
        with patch.object(provider, "_get_valid_access_token", return_value="fake_access_token"), \
             patch.object(provider, "_get_user_email", return_value="user@outlook.com"):
            status = provider.get_status()
            assert status["status"] == "connected"
            assert status["connected"] is True
            assert status["email"] == "user@outlook.com"

    def test_create_draft_payload_and_success(self):
        provider = OutlookProvider(client_id="test-client-guid")

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "id": "outlook_msg_98765",
            "subject": "Follow-up",
            "isDraft": True,
        }

        with patch.object(provider, "_get_valid_access_token", return_value="fake_access_token"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            result = provider.create_draft(
                to_addresses=["recruiter@company.com"],
                subject="Interview Follow-up",
                body="Thank you for taking the time to speak with me.",
            )

            assert result["success"] is True
            assert result["draft_id"] == "outlook_msg_98765"
            assert result["provider"] == "outlook"

            # Verify request payload
            url, kwargs = mock_post.call_args
            assert url[0].endswith("/me/messages")
            payload = kwargs["json"]
            assert payload["isDraft"] is True
            assert payload["subject"] == "Interview Follow-up"
            assert payload["toRecipients"][0]["emailAddress"]["address"] == "recruiter@company.com"
            assert "Thank you" in payload["body"]["content"]
            assert kwargs["headers"]["Authorization"] == "Bearer fake_access_token"

    def test_create_draft_failure(self):
        provider = OutlookProvider(client_id="test-client-guid")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Invalid recipient format"

        with patch.object(provider, "_get_valid_access_token", return_value="fake_access_token"), \
             patch("requests.post", return_value=mock_resp):
            result = provider.create_draft(
                to_addresses=["bad@format"],
                subject="Test",
                body="Body",
            )
            assert result["success"] is False
            assert "400" in result["error"]


# ---------------------------------------------------------------------------
# 4. Email Assistant Unified Coordination Tests
# ---------------------------------------------------------------------------

class TestEmailAssistantCoordination:
    def test_missing_recipient_rejected(self):
        assistant = EmailAssistant()
        res = assistant.compose_draft(
            to_addresses="",
            subject="Test",
            body="Body",
        )
        assert "recipient missing" in res.lower()

    def test_invalid_email_format_rejected(self):
        assistant = EmailAssistant()
        res = assistant.compose_draft(
            to_addresses="not-an-email-address",
            subject="Test",
            body="Body",
        )
        assert "invalid email" in res.lower()

    def test_provider_auto_resolution(self):
        gmail_mock = MagicMock()
        gmail_mock.get_status.return_value = {"connected": True}
        outlook_mock = MagicMock()
        outlook_mock.get_status.return_value = {"connected": False}

        assistant = EmailAssistant(gmail_provider=gmail_mock, outlook_provider=outlook_mock)
        prov, _ = assistant.resolve_provider("auto")
        assert prov == "gmail"

        # Reverse
        gmail_mock.get_status.return_value = {"connected": False}
        outlook_mock.get_status.return_value = {"connected": True}
        prov, _ = assistant.resolve_provider("auto")
        assert prov == "outlook"

    def test_compose_saves_to_gmail_when_connected(self):
        gmail_mock = MagicMock()
        gmail_mock.get_status.return_value = {"connected": True}
        gmail_mock.create_draft.return_value = {
            "success": True,
            "draft_id": "gmail_draft_abc",
        }
        outlook_mock = MagicMock()
        outlook_mock.get_status.return_value = {"connected": False}

        assistant = EmailAssistant(gmail_provider=gmail_mock, outlook_provider=outlook_mock)
        res = assistant.compose_draft(
            to_addresses="prof@university.edu",
            subject="Appointment",
            body="Meeting request",
            provider="gmail",
        )

        assert "verified: draft successfully saved to your gmail drafts folder" in res.lower()
        assert "gmail_draft_abc" in res
        latest = assistant.store.get_latest()
        assert latest.status == "saved_to_provider"
        assert latest.provider_draft_id == "gmail_draft_abc"

    def test_compose_saves_to_outlook_when_connected(self):
        gmail_mock = MagicMock()
        gmail_mock.get_status.return_value = {"connected": False}
        outlook_mock = MagicMock()
        outlook_mock.get_status.return_value = {"connected": True}
        outlook_mock.create_draft.return_value = {
            "success": True,
            "draft_id": "outlook_msg_xyz",
        }

        assistant = EmailAssistant(gmail_provider=gmail_mock, outlook_provider=outlook_mock)
        res = assistant.compose_draft(
            to_addresses="manager@corp.com",
            subject="Extension Request",
            body="Requesting deadline extension",
            provider="outlook",
        )

        assert "verified: draft successfully saved to your outlook drafts folder" in res.lower()
        assert "outlook_msg_xyz" in res

    def test_confirmation_required_before_draft_replacement(self):
        assistant = EmailAssistant()
        # Create initial draft
        assistant.compose_draft(
            to_addresses="user@example.com",
            subject="Original Subject",
            body="Original Body",
            save_to_provider=False,
        )
        draft = assistant.store.get_latest()

        # Update without confirmation
        res_unconfirmed = assistant.update_draft(
            draft_id=draft.draft_id,
            subject="New Subject",
            confirmed=False,
        )
        assert "confirmation required" in res_unconfirmed.lower()
        assert draft.subject == "Original Subject"

        # Update with confirmation
        res_confirmed = assistant.update_draft(
            draft_id=draft.draft_id,
            subject="New Subject",
            confirmed=True,
        )
        assert "updated successfully" in res_confirmed.lower()
        assert draft.subject == "New Subject"


# ---------------------------------------------------------------------------
# 5. Strict Draft-Only Safety & Tool Registration Tests
# ---------------------------------------------------------------------------

class TestStrictDraftOnlySafety:
    def test_no_send_email_tool_registered_in_email_schema(self):
        """Verify that EMAIL_TOOLS_SCHEMA contains zero send tools."""
        tool_names = [t["function"]["name"] for t in EMAIL_TOOLS_SCHEMA]
        for prohibited in ("send_email", "send_draft", "send_email_draft", "transmit_email"):
            assert prohibited not in tool_names, f"Forbidden tool '{prohibited}' found in EMAIL_TOOLS_SCHEMA!"

    def test_no_send_email_tool_registered_in_available_tools(self):
        """Verify that EMAIL_AVAILABLE_TOOLS maps zero send tools."""
        for prohibited in ("send_email", "send_draft", "send_email_draft"):
            assert prohibited not in EMAIL_AVAILABLE_TOOLS, f"Forbidden tool '{prohibited}' in EMAIL_AVAILABLE_TOOLS!"

    def test_no_send_email_tool_exposed_in_global_tools(self):
        """Verify that the global TOOLS_SCHEMA given to the OpenRouter LLM has NO send tool."""
        global_tool_names = [t["function"]["name"] for t in TOOLS_SCHEMA]
        for prohibited in ("send_email", "send_draft", "send_email_draft"):
            assert prohibited not in global_tool_names, f"Forbidden tool '{prohibited}' in global TOOLS_SCHEMA!"

    def test_draft_only_tools_are_registered(self):
        """Verify that all safe draft tools are registered."""
        tool_names = [t["function"]["name"] for t in EMAIL_TOOLS_SCHEMA]
        assert "compose_email_draft" in tool_names
        assert "show_email_draft" in tool_names
        assert "list_email_drafts" in tool_names
        assert "save_draft_to_provider" in tool_names
        assert "get_email_provider_status" in tool_names
        assert "get_email_setup_instructions" in tool_names
