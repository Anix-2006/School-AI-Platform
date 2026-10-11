from app.agents.state import AgentState

VOICE_PROMPT = """

This answer will be spoken aloud. Reply in one to three short sentences
using plain language. Do not use lists, markdown, symbols, or emoji.
Make dates, numbers, marks, and FA or SA labels easy to say aloud.
All existing safety, privacy, age-tier, and data-grounding rules still apply."""


def voice_prompt(state: AgentState) -> str:
    return VOICE_PROMPT if state.get("channel") == "voice" else ""
