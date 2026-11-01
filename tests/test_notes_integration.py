"""Tests for note save/read/list round-trip and edge cases."""

import pytest
from pathlib import Path
from unittest.mock import patch
from assistant import notes


@pytest.fixture(autouse=True)
def temp_notes_dir(tmp_path):
    with patch("assistant.notes.NOTES_DIR", tmp_path):
        yield tmp_path


class TestNoteRoundTrip:
    """End-to-end save then read-back tests."""

    def test_save_and_read_back_content(self, temp_notes_dir):
        notes.save_research_note(
            title="AI Overview",
            content="Artificial intelligence is transforming industries.",
            tags="AI, technology",
            sources="https://example.com/ai",
        )
        read_result = notes.read_research_note("AI Overview")
        assert "Artificial intelligence is transforming industries" in read_result
        assert "https://example.com/ai" in read_result

    def test_read_by_partial_title(self, temp_notes_dir):
        notes.save_research_note(
            title="Machine Learning Basics",
            content="ML uses algorithms to learn from data.",
        )
        # Partial match should work
        read_result = notes.read_research_note("Machine Learning")
        assert "ML uses algorithms" in read_result

    def test_read_nonexistent_note(self):
        result = notes.read_research_note("this note does not exist at all")
        assert "No research note found" in result

    def test_save_without_title_rejected(self):
        result = notes.save_research_note(title="", content="Some content")
        assert "title" in result.lower()

    def test_save_without_content_rejected(self):
        result = notes.save_research_note(title="Test Note", content="")
        assert "content" in result.lower()


class TestNoteFilesystem:
    """Verify actual file existence and content on disk."""

    def test_file_created_on_disk(self, temp_notes_dir):
        notes.save_research_note("Disk Check Note", "This is a disk check.")
        expected = temp_notes_dir / "Disk_Check_Note.md"
        assert expected.exists()
        content = expected.read_text(encoding="utf-8")
        assert "# Disk Check Note" in content
        assert "This is a disk check." in content

    def test_list_shows_saved_notes(self, temp_notes_dir):
        notes.save_research_note("Alpha Note", "Content alpha.")
        notes.save_research_note("Beta Note", "Content beta.")
        listing = notes.list_research_notes()
        assert "Alpha_Note.md" in listing
        assert "Beta_Note.md" in listing

    def test_list_empty_directory(self, temp_notes_dir):
        listing = notes.list_research_notes()
        assert "No research notes" in listing


class TestNoteOverwrite:
    """Verify overwrite behavior."""

    def test_overwrite_allowed_by_default(self, temp_notes_dir):
        notes.save_research_note("Overwrite Test", "Version 1")
        result = notes.save_research_note("Overwrite Test", "Version 2")
        assert "saved successfully" in result
        content = (temp_notes_dir / "Overwrite_Test.md").read_text(encoding="utf-8")
        assert "Version 2" in content

    def test_overwrite_rejected_when_disabled(self, temp_notes_dir):
        notes.save_research_note("Protected Note", "Original content")
        result = notes.save_research_note(
            "Protected Note", "New content", overwrite=False
        )
        assert "already exists" in result
        # Original content preserved
        content = (temp_notes_dir / "Protected_Note.md").read_text(encoding="utf-8")
        assert "Original content" in content

