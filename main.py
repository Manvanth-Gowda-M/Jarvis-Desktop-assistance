import argparse
import os
import sys
from pathlib import Path


def _redirect_output_if_windowless():
    """pythonw has no console; send prints and errors to logs/jarvis.log instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "jarvis.log"
    try:
        if log_file.exists() and log_file.stat().st_size > 2_000_000:
            log_file.replace(log_dir / "jarvis.old.log")
    except OSError:
        pass
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def main():
    _redirect_output_if_windowless()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # song titles, emoji, Hindi text
        except Exception:
            pass
    os.chdir(Path(__file__).resolve().parent)

    parser = argparse.ArgumentParser(description="Jarvis Personal AI Assistant")
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=["app", "desktop", "legacy-orb", "text", "voice", "diagnostics", "install", None],
        help="Optional mode: app (default), desktop (same as app), legacy-orb, text, voice, diagnostics, install.",
    )
    parser.add_argument("--app", action="store_true", help="Run the full Jarvis app with Dynamic Island (default).")
    parser.add_argument("--minimized", action="store_true", help="Start the app hidden in the system tray.")
    parser.add_argument("--dashboard", action="store_true", help="Open the dashboard window on start.")
    parser.add_argument("--desktop", action="store_true", help="Alias for --app (kept for old commands).")
    parser.add_argument("--legacy-orb", action="store_true", help="Run the old floating-orb companion only.")
    parser.add_argument("--text", action="store_true", help="Run in terminal text mode without voice input.")
    parser.add_argument("--voice", action="store_true", help="Run in terminal voice mode.")
    parser.add_argument("--diagnostics", action="store_true", help="Run system diagnostic and hardware health checks.")
    parser.add_argument("--install", action="store_true", help="Create Desktop and Start Menu shortcuts.")
    parser.add_argument("--uninstall", action="store_true", help="Remove shortcuts and launch-at-login entry.")

    args = parser.parse_args()

    if args.install or args.mode == "install":
        from assistant.installer import install_shortcuts
        for path in install_shortcuts():
            print(f"Created shortcut: {path}")
        print("Done. Launch Jarvis from the Desktop or Start Menu.")
        return

    if args.uninstall:
        from assistant.installer import uninstall_shortcuts
        from assistant.app import set_app_startup
        for path in uninstall_shortcuts():
            print(f"Removed: {path}")
        set_app_startup(False)
        print("Shortcuts and launch-at-login entry removed.")
        return

    if args.diagnostics or args.mode == "diagnostics":
        from assistant.diagnostics import run_diagnostics
        run_diagnostics()
        sys.exit(0)

    if args.legacy_orb or args.mode == "legacy-orb":
        from assistant.desktop_ui import run_desktop_app
        sys.exit(run_desktop_app())

    console_mode = args.text or args.voice or args.mode in ("text", "voice")
    if not console_mode:
        from assistant.app import run_app
        sys.exit(run_app(start_minimized=args.minimized, force_dashboard=args.dashboard))

    from assistant.jarvis import Jarvis
    voice_enabled = not (args.text or args.mode == "text")
    assistant = Jarvis(voice_enabled=voice_enabled)
    assistant.run()


if __name__ == "__main__":
    main()
