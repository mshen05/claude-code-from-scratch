"""Terminal UI helpers for the mini coding agent."""

from __future__ import annotations

from typing import Any, Dict


def print_welcome() -> None:
    """Print the welcome banner and initial instructions."""
    print("\n  Mini Claude Code")
    print("  A minimal coding agent\n")
    print("  Type your request, or 'exit' to quit.")
    print("  Commands: /clear /cost /compact\n")


def print_user_prompt() -> None:
    """Print the input prompt (e.g. `> `)."""
    print("\n> ", end="", flush=True)


def print_assistant_text(text: str) -> None:
    """Print the assistant's streaming text output."""
    print(text, end="", flush=True)


def print_tool_call(name: str, tool_input: Dict[str, Any]) -> None:
    """Print a summary of an invoked tool."""
    summary = _get_tool_summary(name, tool_input)
    print(f"\n  [tool] {name} {summary}")


def print_tool_result(name: str, result: str) -> None:
    max_len = 500
    truncated = result
    if len(result) > max_len:
        truncated = result[:max_len] + f"\n  ... ({len(result)} chars total)"

    lines = ["  " + line for line in truncated.splitlines()]
    print("\n".join(lines) if lines else "  (no output)")


def print_error(message: str) -> None:
    print(f"\n  Error: {message}")


def print_confirmation(command: str) -> None:
    print(f"\n  Warning dangerous action: {command}")


def print_divider() -> None:
    print("\n  " + "-" * 50)


def print_cost(input_tokens: int, output_tokens: int) -> None:
    cost_in = (input_tokens / 1_000_000) * 3
    cost_out = (output_tokens / 1_000_000) * 15
    total = cost_in + cost_out
    print(f"\n  Tokens: {input_tokens} in / {output_tokens} out (~${total:.4f})")


def print_retry(attempt: int, max_retry: int, reason: str) -> None:
    print(f"\n  Retry {attempt}/{max_retry}: {reason}")


def print_info(message: str) -> None:
    print(f"\n  Info: {message}")


def _get_tool_summary(name: str, tool_input: Dict[str, Any]) -> str:
    if name in {"read_file", "write_file", "edit_file"}:
        return str(tool_input.get("file_path", ""))
    if name == "list_files":
        return str(tool_input.get("pattern", ""))
    if name == "grep_search":
        pattern = str(tool_input.get("pattern", ""))
        path = str(tool_input.get("path", "."))
        return f'"{pattern}" in {path}'
    if name == "run_shell":
        command = str(tool_input.get("command", ""))
        return command[:60] + "..." if len(command) > 60 else command
    return ""


