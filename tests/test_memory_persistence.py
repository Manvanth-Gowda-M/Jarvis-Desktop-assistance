"""Tests for memory persistence: data survives across separate Memory instances."""

import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from assistant import memory


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    test_db = tmp_path / "persist_test.db"
    with patch("assistant.memory.DB_PATH", test_db):
        memory.initialize_database()
        yield test_db


class TestMemoryPersistence:
    """Verify data survives across independent Memory and function calls."""

    def test_data_persists_across_function_calls(self, temp_db):
        memory.remember_fact("The meeting is at 3pm on Friday.")
        # Simulate a new session by calling recall directly
        recalled = memory.recall_facts("meeting Friday")
        assert "3pm" in recalled
        assert "Friday" in recalled

    def test_data_persists_across_memory_instances(self, temp_db):
        mem1 = memory.Memory()
        mem1.remember("Favorite color is blue.")

        # New Memory instance — simulates restart
        mem2 = memory.Memory()
        results = mem2.search("favorite color")
        assert len(results) > 0
        assert "blue" in results[0][0]

    def test_list_memories_after_multiple_saves(self, temp_db):
        memory.remember_fact("Fact A: sky is blue.")
        memory.remember_fact("Fact B: grass is green.")
        memory.remember_fact("Fact C: water is clear.")

        listing = memory.list_memories()
        assert "sky is blue" in listing
        assert "grass is green" in listing
        assert "water is clear" in listing

    def test_recall_returns_most_relevant_first(self, temp_db):
        memory.remember_fact("The server runs on port 8080.")
        memory.remember_fact("My cat's name is Whiskers.")
        memory.remember_fact("The production server IP is 10.0.0.5.")

        recalled = memory.recall_facts("server port")
        lines = recalled.strip().split("\n")
        # Server-related facts should appear before cat fact
        assert "server" in lines[0].lower() or "port" in lines[0].lower()

    def test_empty_recall_returns_recent(self, temp_db):
        memory.remember_fact("A stored fact.")
        result = memory.recall_facts("the")
        # With stop words only, should fall back to listing
        assert isinstance(result, str)
        assert len(result) > 0

    def test_length_limit_enforced(self):
        long_text = "x" * 1001
        result = memory.remember_fact(long_text)
        assert "too long" in result.lower()

