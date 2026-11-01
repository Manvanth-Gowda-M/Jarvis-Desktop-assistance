import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from config import DB_PATH


def initialize_database():
    """Create the memory table if it doesn't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


# Common stop words for memory search
STOP_WORDS = {
    "what", "do", "you", "remember", "about", "my", "me",
    "the", "is", "are", "did", "i", "to", "tell", "have",
    "know", "please", "can", "jarvis", "anything", "that",
    "this", "for", "of", "in", "on", "a", "an", "and", "or",
}

SENSITIVE_PHRASES = (
    "my password is",
    "password:",
    "my api key is",
    "api_key",
    "apikey",
    "my secret key is",
    "my access token is",
    "bearer ",
    "private key",
)


def is_sensitive(text: str) -> bool:
    """Check if the text contains sensitive credentials."""
    lower_text = text.lower()
    return any(p in lower_text for p in SENSITIVE_PHRASES)


def remember_fact(fact: str) -> str:
    """Save a fact permanently in SQLite long-term memory."""
    fact = str(fact).strip()
    if not fact:
        return "I need some information to remember."

    if len(fact) > 1000:
        return "That memory is too long. Please shorten it."

    if is_sensitive(fact):
        return "Security restriction: I will not save passwords, API keys, or access tokens."

    initialize_database()

    with closing(sqlite3.connect(DB_PATH)) as conn:
        existing = conn.execute(
            "SELECT id FROM memories WHERE lower(content) = lower(?)",
            (fact,),
        ).fetchone()

        if existing:
            return "I already have that information in memory."

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "INSERT INTO memories (content, created_at) VALUES (?, ?)",
            (fact, created_at),
        )
        conn.commit()

    return f"Saved to long-term memory: '{fact}'"


def list_memories(limit: int = 10) -> str:
    """Return the most recently stored facts."""
    initialize_database()

    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("""
            SELECT content, created_at
            FROM memories
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    if not rows:
        return "Your long-term memory is empty."

    return "\n".join(
        f"- {content} (saved {created_at})"
        for content, created_at in rows
    )


def recall_facts(query: str) -> str:
    """Find stored facts relevant to a question using keyword matching."""
    initialize_database()

    query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    keywords = {
        word for word in query_words
        if len(word) > 2 and word not in STOP_WORDS
    }

    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("""
            SELECT id, content, created_at
            FROM memories
            ORDER BY id DESC
            LIMIT 200
        """).fetchall()

    if not rows:
        return "I don't have any saved memories yet."

    if not keywords:
        return list_memories()

    matches = []
    for memory_id, content, created_at in rows:
        content_words = set(re.findall(r"[a-z0-9]+", content.lower()))
        score = len(keywords & content_words)
        if score > 0:
            matches.append((score, memory_id, content, created_at))

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)

    if not matches:
        return "I couldn't find a matching saved memory."

    return "\n".join(
        f"- {content} (saved {created_at})"
        for _, _, content, created_at in matches[:5]
    )


class Memory:
    """Object-oriented wrapper providing backward compatibility for class-based imports."""

    def __init__(self):
        initialize_database()

    def remember(self, fact: str) -> str:
        return remember_fact(fact)

    def search(self, query: str) -> list[tuple]:
        """Return list of (content, created_at) rows matching query."""
        initialize_database()
        query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
        keywords = {w for w in query_words if len(w) > 2 and w not in STOP_WORDS}

        with closing(sqlite3.connect(DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT content, created_at FROM memories ORDER BY id DESC"
            ).fetchall()

        if not keywords:
            return rows[:5]

        scored = []
        for content, created_at in rows:
            c_words = set(re.findall(r"[a-z0-9]+", content.lower()))
            score = len(keywords & c_words)
            if score > 0:
                scored.append((score, content, created_at))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [(c, d) for _, c, d in scored[:5]]

    def list_all(self, limit: int = 10) -> str:
        return list_memories(limit)


# Initialize database upon import
initialize_database()
