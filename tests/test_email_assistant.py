"""Tests for email assistant — draft safety, no real sending."""

import pytest
from unittest.mock import patch, MagicMock


class TestEmailDraftCompose:
    """Composing drafts must never send automatically."""

    def test_compose_creates_draft(self):
        from assistant.email_assistant import compose_email_draft
        result = compose_email_draft(
            to_addresses="professor@university.edu",
            subject="Appointment Request",
            body="Dear Professor, I would like to schedule an appointment.",
        )
        assert isinstance(result, str)
        assert "draft" in result.lower()
        # Must show the draft content
        assert "professor@university.edu" in result or "Appointment" in result

    def test_compose_returns_draft_id(self):
        from assistant.email_assistant import compose_email_draft
        result = compose_email_draft(
            to_addresses="test@example.com",
            subject="Test Subject",
            body="Test body",
        )
        assert "draft_id" in result.lower() or "id:" in result.lower() or "draft id" in result.lower()

    def test_compose_shows_not_sent_warning(self):
        from assistant.email_assistant import compose_email_draft
        result = compose_email_draft(
            to_addresses="someone@example.com",
            subject="Subject",
            body="Body text",
        )
        # Must clearly state it has NOT been sent
        assert "not been sent" in result.lower() or "not sent" in result.lower() or "draft only" in result.lower() or "has not" in result.lower()

    def test_compose_empty_recipient_rejected(self):
        from assistant.email_assistant import compose_email_draft
        result = compose_email_draft(
            to_addresses="",
            subject="Test",
            body="Body",
        )
        assert "recipient" in result.lower() or "address" in result.lower() or "error" in result.lower()

    def test_compose_empty_subject_still_works(self):
        from assistant.email_assistant import compose_email_draft
        # Subject can be empty or auto-generated
        result = compose_email_draft(
            to_addresses="test@example.com",
            subject="",
            body="Just a quick note.",
        )
        assert isinstance(result, str)


class TestEmailSendSafety:
    """Send must require confirmed=True and match the exact draft."""

    def test_send_without_confirmation_returns_preview(self):
        from assistant.email_assistant import compose_email_draft, send_email_draft
        compose_result = compose_email_draft(
            to_addresses="test@example.com",
            subject="Test",
            body="Hello",
        )
        # Extract draft_id from result
        import re
        match = re.search(r"([a-f0-9\-]{8,})", compose_result)
        if match:
            draft_id = match.group(1)
            result = send_email_draft(draft_id, confirmed=False)
            assert "confirm" in result.lower() or "review" in result.lower() or "send" in result.lower()

    def test_send_nonexistent_draft(self):
        from assistant.email_assistant import send_email_draft
        result = send_email_draft("nonexistent-draft-id-12345", confirmed=True)
        assert "not found" in result.lower() or "does not exist" in result.lower() or "invalid" in result.lower()

    def test_oauth_not_configured_reports_draft_only(self):
        from assistant.email_assistant import compose_email_draft, send_email_draft
        compose_result = compose_email_draft(
            to_addresses="test@example.com",
            subject="OAuth Test",
            body="Testing OAuth not configured behavior",
        )
        import re
        match = re.search(r"([a-f0-9\-]{8,})", compose_result)
        if match:
            draft_id = match.group(1)
            with patch.dict("os.environ", {"EMAIL_PROVIDER": ""}):
                result = send_email_draft(draft_id, confirmed=True)
                # Must report that OAuth is not configured
                assert (
                    "oauth" in result.lower()
                    or "not configured" in result.lower()
                    or "draft" in result.lower()
                    or "setup" in result.lower()
                )


class TestEmailListDrafts:
    """Draft management."""

    def test_list_drafts_empty(self):
        from assistant.email_assistant import EmailAssistant
        assistant = EmailAssistant()  # fresh instance
        result = assistant.list_drafts()
        assert isinstance(result, str)

    def test_list_drafts_after_compose(self):
        from assistant.email_assistant import EmailAssistant
        assistant = EmailAssistant()
        assistant.compose_draft(
            to_addresses="a@example.com",
            subject="First Draft",
            body="First draft body",
        )
        result = assistant.list_drafts()
        assert "First Draft" in result or "draft" in result.lower()


class TestEmailSetupInstructions:
    """OAuth setup documentation."""

    def test_gmail_setup_instructions(self):
        from assistant.email_assistant import get_email_setup_instructions
        result = get_email_setup_instructions("gmail")
        assert "gmail" in result.lower() or "google" in result.lower()
        assert "oauth" in result.lower() or "api" in result.lower()
        assert isinstance(result, str)
        assert len(result) > 100  # must be substantive

    def test_outlook_setup_instructions(self):
        from assistant.email_assistant import get_email_setup_instructions
        result = get_email_setup_instructions("outlook")
        assert "outlook" in result.lower() or "microsoft" in result.lower()
        assert isinstance(result, str)


class TestEmailToolsSchema:
    def test_schema_valid(self):
        from assistant.email_assistant import EMAIL_TOOLS_SCHEMA, EMAIL_AVAILABLE_TOOLS
        assert len(EMAIL_TOOLS_SCHEMA) >= 3
        for tool in EMAIL_TOOLS_SCHEMA:
            assert tool["type"] == "function"
            assert tool["function"]["name"] in EMAIL_AVAILABLE_TOOLS

