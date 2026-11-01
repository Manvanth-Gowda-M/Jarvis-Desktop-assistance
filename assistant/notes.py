import os
import re
from datetime import datetime
from pathlib import Path

from config import NOTES_DIR


def sanitize_filename(name: str) -> str:
    """Sanitize title into a safe filesystem filename."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name).strip(" ._")
    return name or "untitled_note"


def get_safe_note_path(title: str) -> Path:
    """Resolve note path and strictly verify it resides inside NOTES_DIR."""
    base_name = sanitize_filename(title)
    if not base_name.endswith(".md"):
        base_name += ".md"

    safe_path = (NOTES_DIR / base_name).resolve()

    # Prevent directory traversal attacks
    if not str(safe_path).startswith(str(NOTES_DIR.resolve())):
        raise ValueError("Security violation: Path traversal detected.")

    return safe_path


def save_research_note(
    title: str,
    content: str,
    tags: str = "",
    sources: str = "",
    overwrite: bool = True,
) -> str:
    """Save structured research findings as a Markdown file."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    title = str(title).strip()
    content = str(content).strip()

    if not title:
        return "Please provide a title for the research note."
    if not content:
        return "Please provide content for the research note."

    try:
        note_path = get_safe_note_path(title)
    except ValueError as e:
        return str(e)

    if note_path.exists() and not overwrite:
        return f"Note '{note_path.name}' already exists. Overwrite was not confirmed."

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    md_content = [
        f"# {title}",
        f"- **Date:** {timestamp}",
    ]
    if tags:
        md_content.append(f"- **Tags:** {tags}")
    md_content.append("")
    md_content.append("## Summary & Analysis")
    md_content.append(content)

    if sources:
        md_content.append("")
        md_content.append("## Sources")
        md_content.append(sources)

    md_text = "\n".join(md_content) + "\n"

    try:
        note_path.write_text(md_text, encoding="utf-8")
        return f"Research note saved successfully as '{note_path.name}' in notes directory."
    except Exception as e:
        return f"Error saving research note: {e}"


def read_research_note(title: str) -> str:
    """Read an existing research note by title or filename."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    clean_title = sanitize_filename(title).lower()

    # Look for exact or partial matches in NOTES_DIR
    matching_files = [
        p for p in NOTES_DIR.glob("*.md")
        if clean_title in p.stem.lower() or clean_title == p.name.lower()
    ]

    if not matching_files:
        return f"No research note found matching '{title}'."

    target_file = matching_files[0]
    try:
        content = target_file.read_text(encoding="utf-8")
        return f"=== Note: {target_file.name} ===\n\n{content}"
    except Exception as e:
        return f"Error reading note '{target_file.name}': {e}"


def list_research_notes() -> str:
    """List all saved research notes."""
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes = sorted(NOTES_DIR.glob("*.md"), key=os.path.getmtime, reverse=True)

    if not notes:
        return "No research notes saved yet."

    lines = ["Saved Research Notes:"]
    for note in notes:
        mtime = datetime.fromtimestamp(os.path.getmtime(note)).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- {note.name} (modified {mtime})")

    return "\n".join(lines)


def delete_research_note(title: str, confirmed: bool = False) -> str:
    """Delete a research note, requiring explicit confirmation."""
    if not confirmed:
        return f"Action requires confirmation: Please confirm deletion of research note '{title}'."

    try:
        note_path = get_safe_note_path(title)
        if not note_path.exists():
            return f"Note '{note_path.name}' does not exist."
        note_path.unlink()
        return f"Note '{note_path.name}' deleted successfully."
    except Exception as e:
        return f"Error deleting note: {e}"
