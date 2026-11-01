"""
assistant/approvals.py
======================
Action approval and history module for the Jarvis personal AI assistant.

Responsibilities
----------------
- Define risk levels for all action types (LOW → CRITICAL).
- Persist action records to SQLite so the user can audit what Jarvis did.
- Provide helper functions that the orchestration layer calls before
  executing any action:
    * get_risk_level()            – look up the action's risk level
    * format_confirmation_prompt() – produce a human-readable prompt
    * get_recent_history()        – formatted audit-log summary

Security notes
--------------
- Fields whose names contain "key", "password", "token", or "secret" are
  automatically redacted before being written to the database.
- Email bodies and file contents are never stored here.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk level enumeration
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    """
    Represents how dangerous an action is and what confirmation policy applies.

    LOW      – Execute immediately; no user confirmation needed.
    MEDIUM   – Warn the user but proceed unless they opt out.
    HIGH     – Require ``confirmed=True`` in the tool call.
    CRITICAL – Require ``confirmed=True`` **and** explain full consequences.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Risk classification registry
# ---------------------------------------------------------------------------

RISK_REGISTRY: dict[str, RiskLevel] = {
    # Informational / read-only
    "get_time":           RiskLevel.LOW,
    "get_battery":        RiskLevel.LOW,
    "take_screenshot":    RiskLevel.LOW,
    "open_file":          RiskLevel.LOW,
    "create_file":        RiskLevel.LOW,

    # Application control
    "open_application":   RiskLevel.LOW,
    "set_volume":         RiskLevel.LOW,

    # System state changes (reversible)
    "lock_computer":      RiskLevel.MEDIUM,
    "move_file":          RiskLevel.MEDIUM,
    "desktop_click":      RiskLevel.MEDIUM,
    "desktop_type":       RiskLevel.MEDIUM,

    # Destructive / communication
    "send_email":         RiskLevel.HIGH,
    "delete_file":        RiskLevel.HIGH,
    "bulk_file_op":       RiskLevel.HIGH,

    # Irreversible system actions
    "power_action":       RiskLevel.CRITICAL,
}

# Human-readable policy strings shown in confirmation prompts
_POLICY_DESCRIPTIONS: dict[RiskLevel, str] = {
    RiskLevel.LOW: (
        "This action is low-risk and will execute immediately."
    ),
    RiskLevel.MEDIUM: (
        "This action has moderate impact. It will proceed unless you cancel."
    ),
    RiskLevel.HIGH: (
        "⚠  HIGH RISK – This action requires your explicit approval.\n"
        "   Pass confirmed=True to proceed."
    ),
    RiskLevel.CRITICAL: (
        "🚨 CRITICAL – This action is potentially irreversible and may have\n"
        "   serious consequences. Pass confirmed=True only after reviewing\n"
        "   the description below carefully."
    ),
}


# ---------------------------------------------------------------------------
# ActionRecord dataclass
# ---------------------------------------------------------------------------

# Fields that must never be stored in the database
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(key|password|token|secret)", re.IGNORECASE
)

def _is_sensitive_field(field_name: str) -> bool:
    """Return True if *field_name* looks like it might hold a secret."""
    return bool(_SENSITIVE_FIELD_PATTERN.search(field_name))


@dataclass
class ActionRecord:
    """
    A single auditable record of an action Jarvis attempted or completed.

    Fields
    ------
    action_type     : Machine-readable identifier (matches RISK_REGISTRY keys).
    description     : Human-readable summary.  NEVER include secrets here.
    risk_level      : One of the RiskLevel enum values (stored as its string value).
    outcome         : One of "pending", "approved", "denied", "completed", "failed".
    result_summary  : Short summary of what happened.  NEVER include secrets.
    timestamp       : ISO-8601 UTC timestamp string.
    """
    action_type:    str
    description:    str
    risk_level:     str
    outcome:        str = "pending"
    result_summary: str = ""
    timestamp:      str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def sanitised_description(self) -> str:
        """Return *description* with any accidental secret-looking tokens redacted."""
        # Simple heuristic: redact anything that looks like a bearer token or API key
        cleaned = re.sub(
            r"(?i)(key|token|secret|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            self.description,
        )
        return cleaned

    def sanitised_result(self) -> str:
        """Return *result_summary* with accidental secrets redacted."""
        cleaned = re.sub(
            r"(?i)(key|token|secret|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            self.result_summary,
        )
        return cleaned


# ---------------------------------------------------------------------------
# ActionHistory – SQLite-backed persistence
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent.parent / "database" / "action_history.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS action_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type    TEXT    NOT NULL,
    description    TEXT    NOT NULL,
    risk_level     TEXT    NOT NULL,
    outcome        TEXT    NOT NULL DEFAULT 'pending',
    result_summary TEXT    NOT NULL DEFAULT '',
    timestamp      TEXT    NOT NULL
);
"""


class ActionHistory:
    """
    Persists :class:`ActionRecord` instances in a local SQLite database.

    The database file is created automatically at
    ``<project_root>/database/action_history.db``.

    Usage
    -----
    .. code-block:: python

        history = ActionHistory()
        record = ActionRecord(
            action_type="open_application",
            description="Open Notepad",
            risk_level=RiskLevel.LOW.value,
            outcome="completed",
            result_summary="Notepad launched successfully.",
        )
        history.add(record)
        print(history.get_recent(10))
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path: Path = Path(db_path) if db_path else _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with row factory enabled."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create the action_history table if it does not already exist."""
        try:
            with self._connect() as conn:
                conn.execute(_CREATE_TABLE_SQL)
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("ActionHistory: failed to initialise DB – %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, record: ActionRecord) -> int | None:
        """
        Persist *record* to the database.

        Sensitive data is redacted before writing.  Returns the new row ID,
        or *None* if the insert failed.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO action_history
                        (action_type, description, risk_level,
                         outcome, result_summary, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.action_type,
                        record.sanitised_description(),
                        record.risk_level,
                        record.outcome,
                        record.sanitised_result(),
                        record.timestamp,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except sqlite3.Error as exc:
            logger.error("ActionHistory.add: DB error – %s", exc)
            return None

    def get_recent(self, n: int = 20) -> list[ActionRecord]:
        """
        Return the *n* most recent :class:`ActionRecord` objects (newest first).
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT action_type, description, risk_level,
                           outcome, result_summary, timestamp
                    FROM   action_history
                    ORDER  BY id DESC
                    LIMIT  ?
                    """,
                    (n,),
                ).fetchall()
            return [
                ActionRecord(
                    action_type=row["action_type"],
                    description=row["description"],
                    risk_level=row["risk_level"],
                    outcome=row["outcome"],
                    result_summary=row["result_summary"],
                    timestamp=row["timestamp"],
                )
                for row in rows
            ]
        except sqlite3.Error as exc:
            logger.error("ActionHistory.get_recent: DB error – %s", exc)
            return []

    def get_recent_as_string(self, n: int = 20) -> str:
        """Return the n most recent actions formatted as a human-readable table string."""
        records = self.get_recent(n)
        if not records:
            return "No recent action history found."

        header = (
            f"{'#':<4} {'Timestamp':<26} {'Action':<22} "
            f"{'Risk':<10} {'Outcome':<12} Summary"
        )
        separator = "─" * 100
        lines = [header, separator]
        for idx, rec in enumerate(records, start=1):
            ts = rec.timestamp[:19].replace("T", " ")
            summary = rec.result_summary[:40] + ("…" if len(rec.result_summary) > 40 else "")
            lines.append(
                f"{idx:<4} {ts:<26} {rec.action_type:<22} "
                f"{rec.risk_level:<10} {rec.outcome:<12} {summary}"
            )
        return "\n".join(lines)

    def clear_old(self, days: int = 30) -> int:
        """
        Delete records older than *days* days.

        Returns the number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM action_history WHERE timestamp < ?",
                    (cutoff,),
                )
                conn.commit()
                deleted = cursor.rowcount
            logger.info(
                "ActionHistory.clear_old: removed %d records older than %d days.",
                deleted, days,
            )
            return deleted
        except sqlite3.Error as exc:
            logger.error("ActionHistory.clear_old: DB error – %s", exc)
            return 0


# Module-level singleton used by the helper functions below
_history = ActionHistory()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_risk_level(action_type: str) -> RiskLevel:
    """
    Return the :class:`RiskLevel` for *action_type*.

    Unknown action types default to **HIGH** so that unrecognised actions
    are never silently executed without user awareness.

    Parameters
    ----------
    action_type:
        The machine-readable action identifier (e.g. ``"delete_file"``).
    """
    return RISK_REGISTRY.get(action_type, RiskLevel.HIGH)


def format_confirmation_prompt(
    action_type: str,
    description: str,
    risk: RiskLevel,
) -> str:
    """
    Return a human-readable confirmation message for the given action.

    Parameters
    ----------
    action_type:
        Machine-readable action name.
    description:
        What Jarvis intends to do (no secrets).
    risk:
        The action's :class:`RiskLevel`.

    Returns
    -------
    str
        A formatted multi-line string suitable for display to the user.

    Example
    -------
    .. code-block:: text

        ────────────────────────────────────────
        Action  : delete_file  [HIGH RISK]
        ────────────────────────────────────────
        Intent  : Delete C:\\Users\\krish\\report.docx
        Policy  : ⚠  HIGH RISK – This action requires your explicit approval.
                   Pass confirmed=True to proceed.
        ────────────────────────────────────────
    """
    separator = "─" * 44
    policy_text = _POLICY_DESCRIPTIONS.get(risk, "")
    lines = [
        separator,
        f"Action  : {action_type}  [{risk.value.upper()} RISK]",
        separator,
        f"Intent  : {description}",
        f"Policy  : {policy_text}",
        separator,
    ]
    return "\n".join(lines)


def get_recent_history(n: int = 20) -> str:
    """
    Return a formatted audit log of the *n* most recent actions.

    Parameters
    ----------
    n:
        Maximum number of records to include (default 20).

    Returns
    -------
    str
        Human-readable table of recent actions, or a message when the log
        is empty.
    """
    records = _history.get_recent(n)
    if not records:
        return "No recent action history found."

    header = (
        f"{'#':<4} {'Timestamp':<26} {'Action':<22} "
        f"{'Risk':<10} {'Outcome':<12} Summary"
    )
    separator = "─" * 100
    lines = [header, separator]
    for idx, rec in enumerate(records, start=1):
        # Trim timestamp to human-friendly length
        ts = rec.timestamp[:19].replace("T", " ")
        summary = rec.result_summary[:40] + ("…" if len(rec.result_summary) > 40 else "")
        lines.append(
            f"{idx:<4} {ts:<26} {rec.action_type:<22} "
            f"{rec.risk_level:<10} {rec.outcome:<12} {summary}"
        )
    return "\n".join(lines)


def log_action(
    action_type: str,
    description: str,
    outcome: str = "pending",
    result_summary: str = "",
) -> ActionRecord:
    """
    Convenience wrapper: create an :class:`ActionRecord`, persist it, and
    return it so callers can update ``outcome`` later if needed.

    Parameters
    ----------
    action_type:
        Machine-readable action identifier.
    description:
        Human-readable description (no secrets).
    outcome:
        Initial outcome string (default ``"pending"``).
    result_summary:
        Optional short result description.
    """
    risk = get_risk_level(action_type)
    record = ActionRecord(
        action_type=action_type,
        description=description,
        risk_level=risk.value,
        outcome=outcome,
        result_summary=result_summary,
    )
    _history.add(record)
    return record
