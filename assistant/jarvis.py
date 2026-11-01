import sys
from config import JARVIS_NAME
from assistant.speech import Speaker, prepare_spoken_answer
from assistant.listen import Listener
from assistant.memory import Memory
from assistant.commands import CommandRouter
from assistant.agent import Agent, Brain
from assistant.router import route_request


class Jarvis:
    """Unified coordinator for the Jarvis personal assistant."""

    def __init__(self, voice_enabled: bool = True):
        self.speaker = Speaker()
        self.memory = Memory()
        self.commands = CommandRouter(self.speaker, self.memory)
        self.brain = Brain()
        self.listener = Listener()

        # Check microphone hardware availability
        self.voice_hardware_available = self.listener.is_available()
        self.voice_mode = voice_enabled and self.voice_hardware_available

        if voice_enabled and not self.voice_hardware_available:
            print("[Notice]: Microphone input unavailable or not detected; falling back to text mode.")

    def get_input(self) -> str:
        """Get input from microphone if in voice mode, otherwise from console."""
        if self.voice_mode:
            text = self.listener.listen()
            if text:
                print(f"\nYou (Voice): {text}")
                return text
            return ""

        try:
            return input("\nYou (Text): ").strip()
        except (EOFError, KeyboardInterrupt):
            return "exit"

    def handle(self, user_input: str) -> bool:
        """Process a single user input, route the request, display full text, and speak concise answer.

        Returns False if the assistant should exit.
        """
        user_input = str(user_input).strip()
        if not user_input:
            return True

        result = route_request(user_input)

        # Handle mode changes
        if result.mode_change == "text":
            self.voice_mode = False
        elif result.mode_change == "voice":
            if self.voice_hardware_available:
                self.voice_mode = True
            else:
                print("[Notice]: Cannot enable voice mode; microphone is unavailable.")

        # 1. Output detailed text in terminal
        if result.detailed_text:
            print(f"\n{JARVIS_NAME}:\n{result.detailed_text}")

        # 2. Output concise spoken text via TTS if voice mode is active
        # Notice: Handlers NEVER speak directly; speech occurs strictly here to prevent duplicate speech.
        if self.voice_mode and result.spoken_text:
            self.speaker.speak(result.spoken_text)

        # Exit signal
        if result.should_exit:
            return False

        return True

    def run(self):
        """Main execution loop for Jarvis."""
        from assistant.llm_providers import configured_provider_names
        from assistant import reminders

        reminders.start_scheduler()
        providers = configured_provider_names()
        welcome_detail = (
            f"{JARVIS_NAME} online.\n"
            f"AI providers (free, in failover order): {', '.join(providers) if providers else 'NONE - add a key to .env'}\n"
            "Fast commands execute instantly on your system; everything else goes to the AI agent.\n"
            f"Mode: {'Voice + Terminal' if self.voice_mode else 'Terminal Text Mode'}. "
            "Say 'help' for capabilities, 'exit' to quit."
        )
        from config import USER_TITLE
        welcome_spoken = f"{JARVIS_NAME} online. How can I help, {USER_TITLE}?"

        print("=" * 60)
        print(welcome_detail)
        print("=" * 60)

        if self.voice_mode:
            self.speaker.speak(welcome_spoken)

        while True:
            try:
                user_input = self.get_input()
                if not user_input:
                    continue

                should_continue = self.handle(user_input)
                if not should_continue:
                    break

            except KeyboardInterrupt:
                print("\nShutdown requested.")
                break
            except Exception as e:
                print(f"\n[Unexpected Error]: {e}")

        print(f"\n{JARVIS_NAME} has shut down.")
