import argparse
import sys
from assistant.jarvis import Jarvis
from assistant.diagnostics import run_diagnostics


def main():
    parser = argparse.ArgumentParser(description="Jarvis Personal AI Assistant")
    parser.add_argument(
        "mode",
        nargs="?",
        default=None,
        choices=["desktop", "text", "voice", "diagnostics", None],
        help="Optional mode: desktop, text, voice, or diagnostics.",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Run as an animated Windows desktop AI companion with orb and system tray.",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Run in terminal text mode without voice input.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Run in voice mode with microphone input and speech output.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Run system diagnostic and hardware health checks.",
    )

    args = parser.parse_args()

    if args.diagnostics or args.mode == "diagnostics":
        run_diagnostics()
        sys.exit(0)

    if args.desktop or args.mode == "desktop":
        from assistant.desktop_ui import run_desktop_app
        sys.exit(run_desktop_app())

    # If --text is specified, voice is disabled; otherwise default is voice if hardware allows
    voice_enabled = not (args.text or args.mode == "text")
    if args.voice or args.mode == "voice":
        voice_enabled = True

    assistant = Jarvis(voice_enabled=voice_enabled)
    assistant.run()


if __name__ == "__main__":
    main()
