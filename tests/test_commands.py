import pytest
from assistant import commands


def test_fast_exit():
    for phrase in ["exit", "quit", "goodbye", "shutdown jarvis"]:
        res = commands.execute_fast_command(phrase)
        assert res.handled is True
        assert res.should_exit is True


def test_mode_switches():
    text_res = commands.execute_fast_command("text mode")
    assert text_res.handled is True
    assert text_res.mode_change == "text"

    voice_res = commands.execute_fast_command("voice mode")
    assert voice_res.handled is True
    assert voice_res.mode_change == "voice"


def test_fast_time_and_date():
    t_res = commands.execute_fast_command("what time is it")
    assert t_res.handled is True
    assert "It is currently" in t_res.detailed_text

    d_res = commands.execute_fast_command("what is today's date")
    assert d_res.handled is True
    assert "Today is" in d_res.detailed_text


def test_fast_system_status():
    s_res = commands.execute_fast_command("system status")
    assert s_res.handled is True
    assert "CPU usage is" in s_res.detailed_text


def test_unhandled_query():
    unhandled = commands.execute_fast_command("explain quantum entanglement in depth")
    assert unhandled.handled is False
