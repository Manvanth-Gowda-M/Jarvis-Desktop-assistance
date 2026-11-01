import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from assistant import memory


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    test_db = tmp_path / "test_memory.db"
    with patch("assistant.memory.DB_PATH", test_db):
        memory.initialize_database()
        yield test_db


def test_remember_fact_and_recall():
    res1 = memory.remember_fact("The server port is 8080.")
    assert "Saved to long-term memory" in res1

    # Recalling the fact
    recalled = memory.recall_facts("server port")
    assert "8080" in recalled


def test_remember_duplicate():
    memory.remember_fact("My favorite language is Python.")
    res_dup = memory.remember_fact("My favorite language is Python.")
    assert "already have that information" in res_dup


def test_reject_sensitive_information():
    res_pw = memory.remember_fact("My password is supersecret123")
    assert "Security restriction" in res_pw

    res_key = memory.remember_fact("My api key is sk-123456789")
    assert "Security restriction" in res_key

    # Verify not stored
    list_res = memory.list_memories()
    assert "supersecret123" not in list_res


def test_list_memories():
    memory.remember_fact("Meeting at 3pm on Tuesday.")
    memory.remember_fact("Buy milk and coffee.")

    res = memory.list_memories()
    assert "Meeting at 3pm" in res
    assert "Buy milk" in res


def test_memory_class_compatibility():
    mem = memory.Memory()
    mem.remember("Backup takes place every night at midnight.")
    results = mem.search("backup midnight")
    assert len(results) > 0
    assert "midnight" in results[0][0]
