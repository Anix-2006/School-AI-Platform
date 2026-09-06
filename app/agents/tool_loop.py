"""Run a bound-tools LLM until it produces a final text reply.

Agents bind tools but previously only did a single invoke, so the API
often returned an empty assistant message that still had tool_calls.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage


def run_tool_loop(llm, messages: list, tools: list, max_rounds: int = 5) -> dict:
    tool_map = {t.name: t for t in tools}
    produced: list = []
    working = list(messages)

    for _ in range(max_rounds):
        response = llm.invoke(working)
        produced.append(response)
        working.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break

        for call in tool_calls:
            tool = tool_map.get(call["name"])
            if tool is None:
                result: object = {"error": f"unknown tool {call['name']}"}
            else:
                try:
                    result = tool.invoke(call["args"])
                except Exception as exc:  # noqa: BLE001 — surface tool errors to the model
                    result = {"error": str(exc)}
            tool_msg = ToolMessage(content=str(result), tool_call_id=call["id"])
            produced.append(tool_msg)
            working.append(tool_msg)

    return {"messages": produced}


def extract_text_reply(messages: list) -> str:
    """Return the last non-empty assistant text content."""
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            text = "".join(parts).strip()
            if text:
                return text
    return ""
