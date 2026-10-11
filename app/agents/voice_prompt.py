from app.agents.state import AgentState

VOICE_PROMPT = """

This answer will be spoken aloud. Reply in one or two short sentences
using plain language. Do not use lists, markdown, symbols, or emoji.
Make dates, numbers, marks, and FA or SA labels easy to say aloud.
Answer only in {language}; use the same language as the parent's question.
All existing safety, privacy, age-tier, and data-grounding rules still apply."""


def voice_prompt(state: AgentState) -> str:
    if state.get("channel") != "voice":
        return ""
    language = "Hindi" if state.get("reply_language") == "hi" else "English"
    return VOICE_PROMPT.format(language=language)
