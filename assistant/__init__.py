"""Jarvis Personal AI Assistant package."""

from assistant.jarvis import Jarvis
from assistant.agent import Agent, Brain, run_agent
from assistant.speech import (
    Speaker,
    speak,
    prepare_spoken_answer,
    is_interruption_phrase,
    normalize_phrase,
    STOP_PHRASES,
)
from assistant.listen import Listener, listen
from assistant.memory import Memory, remember_fact, recall_facts, list_memories
from assistant.notes import save_research_note, read_research_note, list_research_notes
from assistant.commands import CommandRouter, execute_fast_command, ActionResult
from assistant.router import route_request
from assistant.wake_word import WakeWordDetector
from assistant.audio_coordinator import AudioCoordinator, AssistantState
from assistant.desktop_ui import run_desktop_app
from assistant.system_control import (
    set_volume,
    adjust_volume,
    mute_volume,
    get_volume,
    set_brightness,
    adjust_brightness,
    get_brightness,
    get_battery_status,
    get_disk_usage,
    get_network_status,
    open_settings,
    lock_computer,
    power_action,
)
from assistant.file_manager import (
    search_files,
    list_directory,
    read_file_content,
    create_folder,
    create_text_file,
    copy_file,
    move_file,
    rename_file,
    delete_to_recycle,
    find_duplicates,
    open_file,
)
from assistant.email_assistant import (
    EmailAssistant,
    compose_email_draft,
    show_email_draft,
    list_email_drafts,
    send_email_draft,
    get_email_setup_instructions,
)
from assistant.desktop_control import (
    list_open_windows,
    focus_window,
    minimize_window,
    maximize_window,
    close_window,
    take_screenshot,
    type_text,
    open_folder,
    get_active_window,
)
from assistant.approvals import (
    ActionHistory,
    ActionRecord,
    RiskLevel,
    get_risk_level,
    format_confirmation_prompt,
    get_recent_history,
)

__all__ = [
    "Jarvis",
    "Agent",
    "Brain",
    "run_agent",
    "Speaker",
    "speak",
    "prepare_spoken_answer",
    "is_interruption_phrase",
    "normalize_phrase",
    "STOP_PHRASES",
    "Listener",
    "listen",
    "Memory",
    "remember_fact",
    "recall_facts",
    "list_memories",
    "save_research_note",
    "read_research_note",
    "list_research_notes",
    "CommandRouter",
    "execute_fast_command",
    "ActionResult",
    "route_request",
    # System Control
    "set_volume",
    "adjust_volume",
    "mute_volume",
    "get_volume",
    "set_brightness",
    "adjust_brightness",
    "get_brightness",
    "get_battery_status",
    "get_disk_usage",
    "get_network_status",
    "open_settings",
    "lock_computer",
    "power_action",
    # File Manager
    "search_files",
    "list_directory",
    "read_file_content",
    "create_folder",
    "create_text_file",
    "copy_file",
    "move_file",
    "rename_file",
    "delete_to_recycle",
    "find_duplicates",
    "open_file",
    # Email Assistant
    "EmailAssistant",
    "compose_email_draft",
    "show_email_draft",
    "list_email_drafts",
    "send_email_draft",
    "get_email_setup_instructions",
    # Desktop Control
    "list_open_windows",
    "focus_window",
    "minimize_window",
    "maximize_window",
    "close_window",
    "take_screenshot",
    "type_text",
    "open_folder",
    "get_active_window",
    # Approvals & History
    "ActionHistory",
    "ActionRecord",
    "RiskLevel",
    # Desktop AI Companion
    "WakeWordDetector",
    "AudioCoordinator",
    "AssistantState",
    "run_desktop_app",
    "get_risk_level",
    "format_confirmation_prompt",
    "get_recent_history",
]
