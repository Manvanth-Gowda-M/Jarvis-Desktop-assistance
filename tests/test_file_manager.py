"""Tests for the safe file manager module."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture()
def safe_root(tmp_path):
    """Provide a tmp_path approved as a safe root for tests."""
    return tmp_path


@pytest.fixture(autouse=True)
def patch_approved_roots(safe_root):
    """Override approved roots to use tmp_path so tests can't touch real files."""
    with patch("assistant.file_manager.APPROVED_ROOTS", [safe_root]):
        yield safe_root


class TestPathValidation:
    """Path traversal and root protection."""

    def test_valid_path_accepted(self, safe_root):
        from assistant.file_manager import _validate_path
        valid = safe_root / "test.txt"
        result = _validate_path(str(valid))
        assert result == valid.resolve()

    def test_path_traversal_rejected(self, safe_root):
        from assistant.file_manager import _validate_path
        evil = str(safe_root) + r"\..\..\..\Windows\System32\evil.txt"
        with pytest.raises(ValueError, match="not in an approved"):
            _validate_path(evil)

    def test_system_path_rejected(self):
        from assistant.file_manager import _validate_path
        with pytest.raises(ValueError):
            _validate_path(r"C:\Windows\System32\calc.exe")

    def test_user_profile_outside_roots_rejected(self):
        from assistant.file_manager import _validate_path
        with pytest.raises(ValueError):
            _validate_path(r"C:\Windows\System32")


class TestListDirectory:
    """Directory listing."""

    def test_lists_files(self, safe_root):
        (safe_root / "alpha.txt").write_text("hello")
        (safe_root / "beta.md").write_text("world")
        from assistant.file_manager import list_directory
        result = list_directory(str(safe_root))
        assert "alpha.txt" in result
        assert "beta.md" in result

    def test_empty_directory(self, safe_root):
        subdir = safe_root / "empty"
        subdir.mkdir()
        from assistant.file_manager import list_directory
        result = list_directory(str(subdir))
        assert isinstance(result, str)

    def test_nonexistent_directory(self, safe_root):
        from assistant.file_manager import list_directory
        result = list_directory(str(safe_root / "doesnotexist"))
        assert "not found" in result.lower() or "does not exist" in result.lower() or "error" in result.lower()


class TestSearchFiles:
    """File search within approved roots."""

    def test_search_by_name(self, safe_root):
        (safe_root / "java_notes.txt").write_text("Java notes")
        (safe_root / "python_notes.txt").write_text("Python notes")
        from assistant.file_manager import search_files
        result = search_files("java", search_root=str(safe_root))
        assert "java_notes" in result.lower()

    def test_search_by_extension(self, safe_root):
        (safe_root / "file1.md").write_text("markdown")
        (safe_root / "file2.txt").write_text("text")
        from assistant.file_manager import search_files
        result = search_files("file", search_root=str(safe_root), extension=".md")
        assert "file1.md" in result
        assert "file2.txt" not in result

    def test_search_empty_query(self, safe_root):
        from assistant.file_manager import search_files
        result = search_files("", search_root=str(safe_root))
        assert isinstance(result, str)


class TestReadFile:
    """File content reading."""

    def test_read_text_file(self, safe_root):
        f = safe_root / "readme.txt"
        f.write_text("This is the README content.")
        from assistant.file_manager import read_file_content
        result = read_file_content(str(f))
        assert "README content" in result

    def test_read_markdown_file(self, safe_root):
        f = safe_root / "notes.md"
        f.write_text("# My Notes\n\nSome content here.")
        from assistant.file_manager import read_file_content
        result = read_file_content(str(f))
        assert "My Notes" in result

    def test_refuses_to_read_executable(self, safe_root):
        f = safe_root / "malware.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        from assistant.file_manager import read_file_content
        result = read_file_content(str(f))
        assert "not supported" in result.lower() or "refuse" in result.lower() or "binary" in result.lower() or "cannot read" in result.lower()

    def test_read_truncates_large_file(self, safe_root):
        f = safe_root / "large.txt"
        f.write_text("A" * 10000)
        from assistant.file_manager import read_file_content
        result = read_file_content(str(f), max_chars=500)
        assert len(result) < 10000
        assert "truncated" in result.lower() or "..." in result or len(result) <= 600


class TestCreateFolder:
    """Folder creation."""

    def test_create_folder_in_approved_root(self, safe_root):
        from assistant.file_manager import create_folder
        new_dir = safe_root / "InterviewPrep"
        result = create_folder(str(new_dir))
        assert new_dir.exists()
        assert isinstance(result, str)

    def test_create_nested_folder(self, safe_root):
        from assistant.file_manager import create_folder
        nested = safe_root / "a" / "b" / "c"
        result = create_folder(str(nested))
        assert isinstance(result, str)


class TestCreateTextFile:
    """Text file creation."""

    def test_create_new_file(self, safe_root):
        from assistant.file_manager import create_text_file
        path = str(safe_root / "new_file.txt")
        result = create_text_file(path, "Hello world")
        assert (safe_root / "new_file.txt").exists()
        assert "created" in result.lower() or "saved" in result.lower()

    def test_refuses_to_overwrite_without_flag(self, safe_root):
        f = safe_root / "existing.txt"
        f.write_text("original")
        from assistant.file_manager import create_text_file
        result = create_text_file(str(f), "new content", overwrite=False)
        assert "already exists" in result.lower() or "overwrite" in result.lower()
        assert f.read_text() == "original"  # unchanged

    def test_overwrites_with_flag(self, safe_root):
        f = safe_root / "overwrite_me.txt"
        f.write_text("old")
        from assistant.file_manager import create_text_file
        result = create_text_file(str(f), "new content", overwrite=True)
        assert f.read_text() == "new content"


class TestCopyFile:
    """File copy operations."""

    def test_copy_file(self, safe_root):
        src = safe_root / "source.txt"
        src.write_text("copy me")
        dst = str(safe_root / "destination.txt")
        from assistant.file_manager import copy_file
        result = copy_file(str(src), dst)
        assert (safe_root / "destination.txt").exists()
        assert isinstance(result, str)

    def test_copy_refuses_to_overwrite(self, safe_root):
        src = safe_root / "src.txt"
        src.write_text("source")
        dst = safe_root / "dst.txt"
        dst.write_text("original")
        from assistant.file_manager import copy_file
        result = copy_file(str(src), str(dst), overwrite=False)
        assert "already exists" in result.lower() or "overwrite" in result.lower()
        assert dst.read_text() == "original"


class TestMoveFile:
    """File move operations."""

    def test_move_requires_confirmation(self, safe_root):
        src = safe_root / "to_move.txt"
        src.write_text("content")
        dst = str(safe_root / "moved.txt")
        from assistant.file_manager import move_file
        result = move_file(str(src), dst, confirmed=False)
        assert "confirm" in result.lower() or "preview" in result.lower()
        assert src.exists()  # NOT moved yet

    def test_move_with_confirmation(self, safe_root):
        src = safe_root / "moveme.txt"
        src.write_text("content")
        dst = str(safe_root / "moved2.txt")
        from assistant.file_manager import move_file
        result = move_file(str(src), dst, confirmed=True)
        assert (safe_root / "moved2.txt").exists()
        assert not src.exists()


class TestDeleteToRecycle:
    """Recycle bin deletion."""

    def test_delete_requires_confirmation(self, safe_root):
        f = safe_root / "deleteme.txt"
        f.write_text("delete this")
        from assistant.file_manager import delete_to_recycle
        result = delete_to_recycle(str(f), confirmed=False)
        assert "confirm" in result.lower()
        assert f.exists()  # not deleted

    def test_delete_with_confirmation(self, safe_root):
        f = safe_root / "trash_me.txt"
        f.write_text("trash")
        from assistant.file_manager import delete_to_recycle
        with patch("assistant.file_manager.send2trash.send2trash"):
            result = delete_to_recycle(str(f), confirmed=True)
            assert isinstance(result, str)


class TestFindDuplicates:
    """Duplicate detection — no deletion."""

    def test_finds_identical_files(self, safe_root):
        f1 = safe_root / "file_a.txt"
        f2 = safe_root / "file_b.txt"
        f1.write_text("identical content")
        f2.write_text("identical content")
        from assistant.file_manager import find_duplicates
        result = find_duplicates(str(safe_root))
        assert isinstance(result, str)
        # Should mention both files as duplicates
        assert "file_a" in result or "file_b" in result or "duplicate" in result.lower()

    def test_no_duplicates(self, safe_root):
        (safe_root / "unique_a.txt").write_text("unique content A")
        (safe_root / "unique_b.txt").write_text("different content B")
        from assistant.file_manager import find_duplicates
        result = find_duplicates(str(safe_root))
        assert isinstance(result, str)


class TestOpenFile:
    """Safe file opener — must not execute dangerous files."""

    def test_refuses_to_execute_exe(self, safe_root):
        f = safe_root / "dangerous.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        from assistant.file_manager import open_file
        result = open_file(str(f))
        assert "refuse" in result.lower() or "not allowed" in result.lower() or "executable" in result.lower() or "cannot open" in result.lower()

    def test_refuses_bat_file(self, safe_root):
        f = safe_root / "script.bat"
        f.write_text("@echo off\ndel /f /q C:\\Windows")
        from assistant.file_manager import open_file
        result = open_file(str(f))
        assert "refuse" in result.lower() or "not allowed" in result.lower() or "executable" in result.lower() or "cannot open" in result.lower()

    @patch("assistant.file_manager.os.startfile")
    def test_opens_text_file(self, mock_startfile, safe_root):
        f = safe_root / "document.txt"
        f.write_text("hello")
        from assistant.file_manager import open_file
        result = open_file(str(f))
        assert isinstance(result, str)


class TestFileManagerSchema:
    def test_schema_valid(self):
        from assistant.file_manager import FILE_MANAGER_TOOLS_SCHEMA, FILE_MANAGER_AVAILABLE_TOOLS
        assert len(FILE_MANAGER_TOOLS_SCHEMA) >= 6
        for tool in FILE_MANAGER_TOOLS_SCHEMA:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert fn["name"] in FILE_MANAGER_AVAILABLE_TOOLS

