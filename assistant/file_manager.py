"""
file_manager.py — Safe File Management Module for Jarvis Assistant
====================================================================
Provides read, search, copy, move, rename, delete, and inspect
operations constrained strictly to a configurable set of approved
root directories.  No file is ever executed; deletions go to the
system Recycle Bin via send2trash; bulk/destructive actions require
explicit confirmation.

Security model
--------------
Every path argument is resolved to its absolute canonical form and
checked against APPROVED_ROOTS before any I/O is performed.  Symlinks
are resolved so that a link pointing outside the approved tree is
rejected even if the link itself sits inside an approved directory.

Dependencies
------------
- send2trash >= 1.8.3   (pip install Send2Trash)  — required for deletion
- All other imports are from the Python 3.12 standard library.

Usage
-----
Import the individual functions or drive them via the tool-schema at
the bottom of this file (FILE_MANAGER_TOOLS_SCHEMA / FILE_MANAGER_AVAILABLE_TOOLS).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import shutil
import stat
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import send2trash
except ImportError:
    send2trash = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approved roots — configurable via environment variables
# ---------------------------------------------------------------------------
_HOME: Path = Path.home()

#: Directories that Jarvis is permitted to touch.
DEFAULT_APPROVED_ROOTS: list[Path] = [
    _HOME / "Desktop",
    _HOME / "Documents",
    _HOME / "Downloads",
    _HOME / "Pictures",
    _HOME / "Music",
    _HOME / "Videos",
    Path(os.getenv("JARVIS_NOTES_DIR", str(_HOME / "Desktop" / "Jarvis" / "notes"))),
    # Jarvis project directory itself
    Path(os.getenv("JARVIS_DIR", str(_HOME / "Desktop" / "Jarvis"))),
]

# Extra roots may be injected via a colon-separated env-var at startup.
_extra_env: str = os.getenv("JARVIS_EXTRA_ROOTS", "")
if _extra_env:
    DEFAULT_APPROVED_ROOTS.extend(
        Path(p.strip()) for p in _extra_env.split(os.pathsep) if p.strip()
    )

#: Live list — callers may append to this list at runtime.
APPROVED_ROOTS: list[Path] = DEFAULT_APPROVED_ROOTS.copy()

# ---------------------------------------------------------------------------
# File-type constants
# ---------------------------------------------------------------------------
#: Extensions that may be read as plain text.
READABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt", ".md", ".py", ".json", ".csv", ".log",
        ".ini", ".cfg", ".yaml", ".yml", ".toml",
        ".html", ".htm", ".xml",
    }
)

#: Extensions that must never be opened/executed via open_file.
BLOCKED_EXECUTE_EXTENSIONS: frozenset[str] = frozenset(
    {".exe", ".bat", ".ps1", ".cmd", ".vbs", ".js"}
)

# Maximum results returned by search_files.
_MAX_SEARCH_RESULTS: int = 50

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fmt_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0 or unit == "TB":
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"  # pragma: no cover


def _fmt_mtime(mtime: float) -> str:
    """Return a human-readable modification timestamp."""
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _file_md5(path: Path, chunk_size: int = 65536) -> str:
    """Compute the MD5 hex-digest of *path* without loading it all into RAM."""
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_path(path: str) -> Path:
    """
    Resolve *path* to an absolute ``pathlib.Path`` and verify it sits
    inside at least one of the ``APPROVED_ROOTS``.

    Parameters
    ----------
    path:
        Raw path string provided by the caller (may be relative or contain
        ``..`` components).

    Returns
    -------
    Path
        The resolved absolute path.

    Raises
    ------
    ValueError
        If the resolved path is outside every approved root, or if the
        string is empty.
    """
    if not path or not path.strip():
        raise ValueError("Path must not be empty.")

    resolved: Path = Path(path).resolve()

    for root in APPROVED_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return resolved  # ✓ inside an approved root
        except ValueError:
            continue

    approved_list: str = "\n  ".join(str(r) for r in APPROVED_ROOTS)
    raise ValueError(
        f"Access denied: '{resolved}' is not in an approved directory. Outside all approved roots.\n"
        f"Approved roots:\n  {approved_list}"
    )


def _stat_line(entry: Path) -> str:
    """Return a single formatted line describing *entry*."""
    try:
        s = entry.stat()
        size_str: str = _fmt_size(s.st_size) if entry.is_file() else "<dir>"
        kind: str = "DIR " if entry.is_dir() else "FILE"
        return f"  [{kind}]  {_fmt_mtime(s.st_mtime)}  {size_str:>10}  {entry.name}"
    except OSError as exc:
        return f"  [ERR ]  {entry.name}  ({exc})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_files(
    query: str,
    search_root: str = "",
    extension: str = "",
) -> str:
    """
    Search for files whose names match *query* (glob-style, case-insensitive)
    within the approved roots (or a specific *search_root*).

    Parameters
    ----------
    query:
        Filename pattern, e.g. ``"report*"`` or ``"*.pdf"``.
        An empty string matches every file (combined with *extension* this
        is useful for listing all files of a given type).
    search_root:
        Optional path to restrict the search.  Must be inside an approved
        root.  Defaults to all approved roots.
    extension:
        Optional extension filter, e.g. ``".pdf"`` or ``"pdf"``.  Applied
        in addition to *query*.

    Returns
    -------
    str
        Formatted list of up to 50 matches, each with size and date.
    """
    # Normalise extension
    ext_filter: str = ""
    if extension:
        ext_filter = extension if extension.startswith(".") else f".{extension}"
        ext_filter = ext_filter.lower()

    # Determine search roots
    if search_root.strip():
        try:
            root_path = _validate_path(search_root)
        except ValueError as exc:
            return f"❌ {exc}"
        if not root_path.is_dir():
            return f"❌ '{root_path}' is not a directory."
        roots: list[Path] = [root_path]
    else:
        roots = [r for r in APPROVED_ROOTS if r.is_dir()]

    # Build glob pattern
    pattern: str = f"*{query}*" if query and "*" not in query and "?" not in query else (query or "*")

    matches: list[Path] = []
    for root in roots:
        try:
            for item in root.rglob(pattern):
                if item.is_file():
                    if ext_filter and item.suffix.lower() != ext_filter:
                        continue
                    matches.append(item)
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break
        except PermissionError as exc:
            logger.warning("Permission denied scanning '%s': %s", root, exc)
        if len(matches) >= _MAX_SEARCH_RESULTS:
            break

    if not matches:
        return "🔍 No files found matching the search criteria."

    lines: list[str] = [
        f"🔍 Search results for '{query}'"
        + (f" (.{ext_filter.lstrip('.')})" if ext_filter else "")
        + f" — {len(matches)} result(s)"
        + (" (max shown)" if len(matches) == _MAX_SEARCH_RESULTS else "")
        + ":"
    ]
    for p in matches:
        try:
            s = p.stat()
            lines.append(
                f"  {_fmt_mtime(s.st_mtime)}  {_fmt_size(s.st_size):>10}  {p}"
            )
        except OSError:
            lines.append(f"  [unreadable]  {p}")
    return "\n".join(lines)


def list_directory(path: str) -> str:
    """
    List the contents of an approved directory.

    Parameters
    ----------
    path:
        Absolute or relative path to the directory to list.

    Returns
    -------
    str
        Formatted directory listing with type, modification date, size,
        and name for each entry, followed by a summary.
    """
    try:
        dir_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if not dir_path.exists():
        return f"❌ Directory not found: '{dir_path}'"
    if not dir_path.is_dir():
        return f"❌ '{dir_path}' is a file, not a directory. Use read_file_content to read it."

    try:
        entries: list[Path] = sorted(
            dir_path.iterdir(),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except PermissionError as exc:
        return f"❌ Permission denied listing '{dir_path}': {exc}"

    if not entries:
        return f"📂 '{dir_path}' is empty."

    lines: list[str] = [f"📂 {dir_path}  ({len(entries)} items)\n"]
    file_count = dir_count = 0
    total_size = 0
    for entry in entries:
        lines.append(_stat_line(entry))
        if entry.is_dir():
            dir_count += 1
        else:
            file_count += 1
            try:
                total_size += entry.stat().st_size
            except OSError:
                pass

    lines.append(
        f"\n  {file_count} file(s), {dir_count} folder(s)"
        f" — total size: {_fmt_size(total_size)}"
    )
    return "\n".join(lines)


def read_file_content(path: str, max_chars: int = 3000) -> str:
    """
    Read and return the text content of a file.

    Only files with a recognised text extension are permitted.  Binary
    or executable files are refused.  Content is truncated at *max_chars*
    characters with a trailing notice.

    Parameters
    ----------
    path:
        Path to the file to read.
    max_chars:
        Maximum number of characters to return (default 3 000).
        Pass a larger value if the caller needs more context.

    Returns
    -------
    str
        File content (possibly truncated) or an error message.
    """
    try:
        file_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if not file_path.exists():
        return f"❌ File not found: '{file_path}'"
    if not file_path.is_file():
        return f"❌ '{file_path}' is a directory. Use list_directory instead."

    suffix: str = file_path.suffix.lower()
    if suffix not in READABLE_EXTENSIONS:
        return (
            f"❌ Cannot read '{file_path.name}': extension '{suffix}' is not in the "
            f"allowed list ({', '.join(sorted(READABLE_EXTENSIONS))}).\n"
            "Only plain-text files may be read."
        )

    try:
        raw: str = file_path.read_text(encoding="utf-8", errors="replace")
    except PermissionError as exc:
        return f"❌ Permission denied reading '{file_path}': {exc}"
    except OSError as exc:
        return f"❌ Could not read '{file_path}': {exc}"

    truncated: bool = len(raw) > max_chars
    content: str = raw[:max_chars] if truncated else raw

    header: str = (
        f"📄 {file_path.name}  ({_fmt_size(file_path.stat().st_size)})\n"
        f"{'─' * 60}\n"
    )
    footer: str = (
        f"\n{'─' * 60}\n"
        f"⚠️  Output truncated at {max_chars:,} chars "
        f"(file is {len(raw):,} chars total). "
        "Pass a larger max_chars to see more."
        if truncated
        else ""
    )
    return header + content + footer


def create_folder(path: str) -> str:
    """
    Create a directory (including any missing parents) inside an approved root.

    Parameters
    ----------
    path:
        Desired path for the new directory.

    Returns
    -------
    str
        Success or error message.
    """
    try:
        dir_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if dir_path.exists():
        if dir_path.is_dir():
            return f"ℹ️  Directory already exists: '{dir_path}'"
        return f"❌ A file with that name already exists: '{dir_path}'"

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        return f"✅ Folder created: '{dir_path}'"
    except PermissionError as exc:
        return f"❌ Permission denied creating '{dir_path}': {exc}"
    except OSError as exc:
        return f"❌ Could not create folder '{dir_path}': {exc}"


def create_text_file(path: str, content: str, overwrite: bool = False) -> str:
    """
    Create a plain-text file inside an approved root.

    Parameters
    ----------
    path:
        Desired file path.
    content:
        Text content to write.
    overwrite:
        If ``False`` (default) and the file already exists, the operation
        is refused.  Pass ``True`` to replace an existing file.

    Returns
    -------
    str
        Success or error message including the file path and byte count.
    """
    try:
        file_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if file_path.exists() and not overwrite:
        return (
            f"❌ File already exists: '{file_path}'.\n"
            "Pass overwrite=True to replace it."
        )

    existed: bool = file_path.exists()
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        action_word: str = "overwritten" if existed else "created"
        return (
            f"✅ File {action_word}: '{file_path}'"
            f"  ({_fmt_size(file_path.stat().st_size)})"
        )
    except PermissionError as exc:
        return f"❌ Permission denied writing '{file_path}': {exc}"
    except OSError as exc:
        return f"❌ Could not write '{file_path}': {exc}"


def copy_file(source: str, destination: str, overwrite: bool = False) -> str:
    """
    Copy a file from *source* to *destination*, both inside approved roots.

    Parameters
    ----------
    source:
        Path to the file to copy.
    destination:
        Target path (may be a directory, in which case the original name
        is kept).
    overwrite:
        If ``False`` (default) and *destination* already exists, the
        operation is refused.

    Returns
    -------
    str
        Success or error message.
    """
    try:
        src_path: Path = _validate_path(source)
        dst_path: Path = _validate_path(destination)
    except ValueError as exc:
        return f"❌ {exc}"

    if not src_path.exists():
        return f"❌ Source file not found: '{src_path}'"
    if not src_path.is_file():
        return f"❌ Source '{src_path}' is not a file."

    # If destination is a directory, copy into it preserving the filename.
    if dst_path.is_dir():
        dst_path = dst_path / src_path.name

    if dst_path.exists() and not overwrite:
        return (
            f"❌ Destination already exists: '{dst_path}'.\n"
            "Pass overwrite=True to replace it."
        )

    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        return f"✅ Copied '{src_path.name}' → '{dst_path}'  ({_fmt_size(dst_path.stat().st_size)})"
    except PermissionError as exc:
        return f"❌ Permission denied: {exc}"
    except OSError as exc:
        return f"❌ Copy failed: {exc}"


def move_file(source: str, destination: str, confirmed: bool = False) -> str:
    """
    Move a file from *source* to *destination* (both inside approved roots).

    If *confirmed* is ``False``, a dry-run preview is shown instead of
    actually moving the file.

    Parameters
    ----------
    source:
        Path to the file to move.
    destination:
        Target path or directory.
    confirmed:
        Must be ``True`` to perform the actual move.

    Returns
    -------
    str
        Preview message (if unconfirmed) or success / error message.
    """
    try:
        src_path: Path = _validate_path(source)
        dst_path: Path = _validate_path(destination)
    except ValueError as exc:
        return f"❌ {exc}"

    if not src_path.exists():
        return f"❌ Source not found: '{src_path}'"

    # If destination is an existing directory, place the file inside it.
    if dst_path.is_dir():
        dst_path = dst_path / src_path.name

    if not confirmed:
        exists_note: str = "  ⚠️  Destination already exists — it WILL be overwritten." if dst_path.exists() else ""
        return (
            f"🔍 Dry-run preview (not moved yet):\n"
            f"  FROM: {src_path}\n"
            f"  TO:   {dst_path}{exists_note}\n"
            "Pass confirmed=True to execute the move."
        )

    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
        return f"✅ Moved '{src_path}' → '{dst_path}'"
    except PermissionError as exc:
        return f"❌ Permission denied: {exc}"
    except OSError as exc:
        return f"❌ Move failed: {exc}"


def rename_file(path: str, new_name: str, confirmed: bool = False) -> str:
    """
    Rename a file or directory in-place (same parent folder).

    Parameters
    ----------
    path:
        Current path of the file or directory.
    new_name:
        New bare name (no directory component), e.g. ``"report_v2.pdf"``.
    confirmed:
        Must be ``True`` to perform the actual rename.

    Returns
    -------
    str
        Preview or success / error message.
    """
    # Reject path separators in the new name to prevent directory traversal.
    if any(ch in new_name for ch in ("/", "\\", ":")):
        return "❌ new_name must be a bare filename with no path separators."

    try:
        old_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if not old_path.exists():
        return f"❌ Path not found: '{old_path}'"

    new_path: Path = old_path.parent / new_name

    # The new path must also pass validation (same parent → same root).
    try:
        _validate_path(str(new_path))
    except ValueError as exc:
        return f"❌ Rename destination is outside approved roots: {exc}"

    if not confirmed:
        exists_note: str = "  ⚠️  Target name already exists — it WILL be overwritten." if new_path.exists() else ""
        return (
            f"🔍 Dry-run preview (not renamed yet):\n"
            f"  OLD: {old_path.name}\n"
            f"  NEW: {new_name}{exists_note}\n"
            "Pass confirmed=True to execute the rename."
        )

    try:
        old_path.rename(new_path)
        return f"✅ Renamed '{old_path.name}' → '{new_path.name}'  (in '{old_path.parent}')"
    except PermissionError as exc:
        return f"❌ Permission denied: {exc}"
    except OSError as exc:
        return f"❌ Rename failed: {exc}"


def delete_to_recycle(path: str, confirmed: bool = False) -> str:
    """
    Send a file or folder to the system Recycle Bin using ``send2trash``.

    The operation is **always** a soft delete — nothing is permanently
    removed.  *confirmed* must be ``True`` for the operation to proceed.

    Parameters
    ----------
    path:
        Path of the file or directory to recycle.
    confirmed:
        Must be ``True`` to perform the deletion.

    Returns
    -------
    str
        Confirmation or error message.
    """
    try:
        target: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if not target.exists():
        return f"❌ Path not found: '{target}'"

    kind: str = "folder" if target.is_dir() else "file"

    if not confirmed:
        try:
            s = target.stat()
            size_hint: str = _fmt_size(s.st_size) if target.is_file() else ""
        except OSError:
            size_hint = ""
        return (
            f"🗑️  Recycle-bin preview (NOT deleted yet):\n"
            f"  {kind.upper()}: {target}  {size_hint}\n"
            "The item will be moved to the Recycle Bin (recoverable).\n"
            "Pass confirmed=True to proceed."
        )

    if send2trash is None:
        return (
            "❌ 'send2trash' is not installed. Run: pip install Send2Trash\n"
            "Deletion was NOT performed."
        )

    try:
        send2trash.send2trash(str(target))
        return f"🗑️  '{target}' has been moved to the Recycle Bin."
    except Exception as exc:  # noqa: BLE001
        return f"❌ Recycle-bin operation failed for '{target}': {exc}"


def find_duplicates(search_root: str = "", confirmed: bool = False) -> str:
    """
    Scan for duplicate files by size and then MD5 hash.

    Duplicate candidates are **listed only** — nothing is deleted.

    Parameters
    ----------
    search_root:
        Directory to scan.  Defaults to all approved roots.
    confirmed:
        Unused for safety (no deletion ever occurs here), but kept for API
        consistency.  A value of ``False`` shows a brief explanation;
        ``True`` runs the full scan.

    Returns
    -------
    str
        Groups of duplicate files with paths and sizes, or a message if
        none are found.
    """
    if not confirmed:
        return (
            "ℹ️  Duplicate scan — dry-run mode.\n"
            "This will compare files by size and MD5 hash and LIST candidates.\n"
            "No files will be deleted by this function.\n"
            "Pass confirmed=True to run the scan."
        )

    if search_root.strip():
        try:
            root_path: Path = _validate_path(search_root)
        except ValueError as exc:
            return f"❌ {exc}"
        if not root_path.is_dir():
            return f"❌ '{root_path}' is not a directory."
        roots: list[Path] = [root_path]
    else:
        roots = [r for r in APPROVED_ROOTS if r.is_dir()]

    # Phase 1: group by size
    size_map: dict[int, list[Path]] = defaultdict(list)
    for root in roots:
        try:
            for item in root.rglob("*"):
                if item.is_file():
                    try:
                        size_map[item.stat().st_size].append(item)
                    except OSError:
                        pass
        except PermissionError as exc:
            logger.warning("Permission denied scanning '%s': %s", root, exc)

    candidates: list[list[Path]] = [
        paths for paths in size_map.values() if len(paths) > 1
    ]

    if not candidates:
        return "✅ No duplicate files found (by size)."

    # Phase 2: group by MD5 within size groups
    hash_map: dict[str, list[Path]] = defaultdict(list)
    for group in candidates:
        for file_path in group:
            try:
                digest: str = _file_md5(file_path)
                hash_map[digest].append(file_path)
            except OSError as exc:
                logger.warning("Could not hash '%s': %s", file_path, exc)

    dup_groups: list[list[Path]] = [
        paths for paths in hash_map.values() if len(paths) > 1
    ]

    if not dup_groups:
        return "✅ No duplicate files found (files with the same size have different content)."

    lines: list[str] = [
        f"🔁 Found {len(dup_groups)} group(s) of duplicate files "
        f"(listing only — nothing has been deleted):\n"
    ]
    for idx, group in enumerate(dup_groups, start=1):
        try:
            size_str: str = _fmt_size(group[0].stat().st_size)
        except OSError:
            size_str = "?"
        lines.append(f"  Group {idx}  ({size_str} each):")
        for p in group:
            lines.append(f"    • {p}")
    lines.append(
        "\nTo free space, manually delete unwanted copies using delete_to_recycle."
    )
    return "\n".join(lines)


def open_file(path: str) -> str:
    """
    Open a file with its default Windows application via ``os.startfile()``.

    Executable file types (.exe, .bat, .ps1, .cmd, .vbs, .js) are always
    blocked regardless of the approved-roots check.

    Parameters
    ----------
    path:
        Path to the file to open.

    Returns
    -------
    str
        Success or error message.
    """
    try:
        file_path: Path = _validate_path(path)
    except ValueError as exc:
        return f"❌ {exc}"

    if not file_path.exists():
        return f"❌ File not found: '{file_path}'"
    if not file_path.is_file():
        return f"❌ '{file_path}' is a directory, not a file."

    suffix: str = file_path.suffix.lower()
    if suffix in BLOCKED_EXECUTE_EXTENSIONS:
        return (
            f"❌ Cannot open executable file: opening '{suffix}' files is not allowed and refused for security reasons.\n"
            f"Blocked types: {', '.join(sorted(BLOCKED_EXECUTE_EXTENSIONS))}"
        )

    try:
        os.startfile(str(file_path))  # type: ignore[attr-defined]
        return f"✅ Opened '{file_path}' with its default application."
    except AttributeError:
        return "❌ os.startfile() is only available on Windows."
    except OSError as exc:
        return f"❌ Could not open '{file_path}': {exc}"


# ---------------------------------------------------------------------------
# Tool schema — for LLM function-calling / Jarvis dispatcher
# ---------------------------------------------------------------------------

FILE_MANAGER_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for files by name pattern and/or extension within approved "
                "directories.  Returns up to 50 results with sizes and dates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Glob-style filename pattern (e.g. 'report*', '*.pdf'). Empty matches all.",
                    },
                    "search_root": {
                        "type": "string",
                        "description": "Optional directory to restrict the search (must be inside an approved root).",
                    },
                    "extension": {
                        "type": "string",
                        "description": "Optional extension filter, e.g. '.pdf' or 'pdf'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory (must be inside an approved root).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the directory.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_content",
            "description": (
                "Read the text content of a file.  Only plain-text extensions are permitted. "
                "Content is truncated at max_chars characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the text file."},
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 3000).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Create a new directory (including parents) inside an approved root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the new directory."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_text_file",
            "description": (
                "Create a plain-text file inside an approved root.  "
                "Set overwrite=true to replace an existing file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Destination file path."},
                    "content": {"type": "string", "description": "Text content to write."},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Replace the file if it already exists (default false).",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": (
                "Copy a file from source to destination (both inside approved roots).  "
                "Set overwrite=true to replace an existing destination."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Source file path."},
                    "destination": {"type": "string", "description": "Destination path or directory."},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Replace destination if it already exists (default false).",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": (
                "Move a file from source to destination (both inside approved roots).  "
                "Pass confirmed=false first to preview; confirmed=true to execute."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Source file path."},
                    "destination": {"type": "string", "description": "Destination path or directory."},
                    "confirmed": {
                        "type": "boolean",
                        "description": "true to execute the move; false (default) shows a dry-run preview.",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": (
                "Rename a file or directory in its current location.  "
                "new_name must be a bare filename with no path separators.  "
                "Pass confirmed=false to preview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Current path of the file or directory."},
                    "new_name": {"type": "string", "description": "New bare filename, e.g. 'notes_v2.txt'."},
                    "confirmed": {
                        "type": "boolean",
                        "description": "true to execute; false (default) shows a dry-run preview.",
                    },
                },
                "required": ["path", "new_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_to_recycle",
            "description": (
                "Move a file or folder to the system Recycle Bin (recoverable).  "
                "confirmed must be true for the operation to proceed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file or folder to recycle."},
                    "confirmed": {
                        "type": "boolean",
                        "description": "true to proceed with recycling; false (default) shows a preview.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_duplicates",
            "description": (
                "Scan for duplicate files by size then MD5 hash and list the candidates.  "
                "No files are deleted.  Pass confirmed=true to run the scan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_root": {
                        "type": "string",
                        "description": "Directory to scan (defaults to all approved roots).",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "true to run the full scan; false shows a description of what will happen.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": (
                "Open a file with its default Windows application.  "
                "Executable types (.exe, .bat, .ps1, .cmd, .vbs, .js) are always blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to open."},
                },
                "required": ["path"],
            },
        },
    },
]

#: Maps function-call names to the actual Python callables.
FILE_MANAGER_AVAILABLE_TOOLS: dict[str, Any] = {
    "search_files": search_files,
    "list_directory": list_directory,
    "read_file_content": read_file_content,
    "create_folder": create_folder,
    "create_text_file": create_text_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "rename_file": rename_file,
    "delete_to_recycle": delete_to_recycle,
    "find_duplicates": find_duplicates,
    "open_file": open_file,
}
