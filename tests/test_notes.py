import pytest
from pathlib import Path
from unittest.mock import patch
from assistant import notes


@pytest.fixture(autouse=True)
def temp_notes_dir(tmp_path):
    with patch("assistant.notes.NOTES_DIR", tmp_path):
        yield tmp_path


def test_save_and_read_note():
    save_res = notes.save_research_note(
        title="Quantum Computing Summary",
        content="Quantum computers utilize qubits in superposition.",
        tags="quantum, computing, science",
        sources="https://example.com/quantum",
    )
    assert "saved successfully" in save_res

    read_res = notes.read_research_note("Quantum Computing")
    assert "qubits in superposition" in read_res
    assert "https://example.com/quantum" in read_res


def test_list_notes():
    notes.save_research_note("Note A", "Content A")
    notes.save_research_note("Note B", "Content B")

    listing = notes.list_research_notes()
    assert "Note_A.md" in listing
    assert "Note_B.md" in listing


def test_path_traversal_protection(temp_notes_dir):
    # Attempting to escape notes directory using ..
    res = notes.save_research_note(
        title="../escaped_note",
        content="Malicious payload",
    )
    # The filename should either be sanitized or rejected
    escaped_file = temp_notes_dir.parent / "escaped_note.md"
    assert not escaped_file.exists()


def test_delete_note_confirmation():
    notes.save_research_note("Note to delete", "Temporary content")

    # Without confirmation
    unconfirmed = notes.delete_research_note("Note to delete", confirmed=False)
    assert "Action requires confirmation" in unconfirmed

    # With confirmation
    confirmed = notes.delete_research_note("Note to delete", confirmed=True)
    assert "deleted successfully" in confirmed
