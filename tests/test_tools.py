import pytest
from assistant import tools


def test_time_and_date_format():
    t = tools.get_current_time()
    assert ("AM" in t or "PM" in t) and ":" in t

    d = tools.get_current_date()
    # Check that day, month, and year are present
    assert any(month in d for month in [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ])


def test_open_application_unknown():
    res = tools.open_application("nonexistent_random_app_xyz")
    assert "I don't know how to open" in res


def test_system_status():
    status = tools.get_system_status()
    assert "CPU usage is" in status
    assert "RAM usage is" in status


def test_tools_schema_validity():
    assert len(tools.TOOLS_SCHEMA) >= 8
    for tool_def in tools.TOOLS_SCHEMA:
        assert tool_def["type"] == "function"
        fn = tool_def["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        assert fn["name"] in tools.AVAILABLE_TOOLS
