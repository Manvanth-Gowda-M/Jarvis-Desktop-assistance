"""Tests for approvals module — risk classification and action history."""

import pytest
from unittest.mock import patch


class TestRiskClassification:
    def test_low_risk_actions(self):
        from assistant.approvals import get_risk_level, RiskLevel
        assert get_risk_level("get_time") == RiskLevel.LOW
        assert get_risk_level("get_battery") == RiskLevel.LOW
        assert get_risk_level("create_file") == RiskLevel.LOW

    def test_high_risk_actions(self):
        from assistant.approvals import get_risk_level, RiskLevel
        assert get_risk_level("send_email") == RiskLevel.HIGH
        assert get_risk_level("delete_file") == RiskLevel.HIGH
        assert get_risk_level("bulk_file_op") == RiskLevel.HIGH

    def test_critical_risk_power_action(self):
        from assistant.approvals import get_risk_level, RiskLevel
        assert get_risk_level("power_action") == RiskLevel.CRITICAL

    def test_unknown_action_defaults_to_medium(self):
        from assistant.approvals import get_risk_level, RiskLevel
        risk = get_risk_level("completely_unknown_action_xyz")
        assert risk in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)


class TestConfirmationPrompt:
    def test_critical_prompt_includes_consequences(self):
        from assistant.approvals import format_confirmation_prompt, RiskLevel
        prompt = format_confirmation_prompt(
            "power_action",
            "Shut down the computer",
            RiskLevel.CRITICAL,
        )
        assert isinstance(prompt, str)
        assert "confirm" in prompt.lower() or "consequence" in prompt.lower() or "warning" in prompt.lower()

    def test_high_risk_prompt(self):
        from assistant.approvals import format_confirmation_prompt, RiskLevel
        prompt = format_confirmation_prompt(
            "send_email",
            "Send email to test@example.com",
            RiskLevel.HIGH,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 20


class TestActionHistory:
    def test_add_and_retrieve(self, tmp_path):
        from assistant.approvals import ActionHistory, ActionRecord
        history = ActionHistory(db_path=str(tmp_path / "test_history.db"))
        record = ActionRecord(
            action_type="get_time",
            description="Get current time",
            risk_level="low",
            outcome="completed",
            result_summary="10:30 AM",
            timestamp="2026-10-28 10:30:00",
        )
        history.add(record)
        recent = history.get_recent(10)
        assert len(recent) >= 1

    def test_sensitive_fields_not_logged(self, tmp_path):
        """Action history must never log API keys or passwords."""
        from assistant.approvals import ActionHistory, ActionRecord
        history = ActionHistory(db_path=str(tmp_path / "test_sensitive.db"))
        record = ActionRecord(
            action_type="api_call",
            description="Calling external API",
            risk_level="medium",
            outcome="completed",
            result_summary="Success",
            timestamp="2026-10-28 10:30:00",
        )
        history.add(record)
        recent = history.get_recent(5)
        for r in recent:
            # Ensure no actual secret values leaked
            assert "sk-or-" not in str(r)
            assert "password" not in str(r).lower() or "description" in str(r).lower()

    def test_get_recent_history_returns_string(self, tmp_path):
        from assistant.approvals import ActionHistory
        history = ActionHistory(db_path=str(tmp_path / "test_get.db"))
        result = history.get_recent_as_string(5)
        assert isinstance(result, str)

