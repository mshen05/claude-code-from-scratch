"""Terminal UI helpers for the mini coding agent."""

from __future__ import annotations

from typing import Any, Dict


def print_welcome() -> None:
    """
    打印欢迎信息。

    Parameters:
        None

    Returns:
        None: 仅向终端输出文本。

    Raises:
        None

    Examples:
        >>> print_welcome()
    """
    print("\n  Mini Claude Code")
    print("  A minimal coding agent\n")
    print("  Type your request, or 'exit' to quit.")
    print("  Commands: /clear /cost /compact\n")


def print_user_prompt() -> None:
    """
    打印用户输入提示符。

    Parameters:
        None

    Returns:
        None: 向终端打印提示符。

    Raises:
        None

    Examples:
        >>> print_user_prompt()
    """
    print("\n> ", end="", flush=True)


def print_assistant_text(text: str) -> None:
    """
    流式打印助手文本。

    Parameters:
        text (str): 需要输出的文本片段。

    Returns:
        None: 向终端连续输出文本。

    Raises:
        None

    Examples:
        >>> print_assistant_text("hello")
    """
    print(text, end="", flush=True)


def print_tool_call(name: str, tool_input: Dict[str, Any]) -> None:
    """
    打印工具调用摘要。

    Parameters:
        name (str): 工具名。
        tool_input (Dict[str, Any]): 工具输入。

    Returns:
        None: 向终端打印一行工具调用信息。

    Raises:
        None

    Examples:
        >>> print_tool_call("read_file", {"file_path": "src/a.py"})
    """
    summary = _get_tool_summary(name, tool_input)
    print(f"\n  [tool] {name} {summary}")


def print_tool_result(name: str, result: str) -> None:
    """
    打印工具执行结果（带截断）。

    Parameters:
        name (str): 工具名。
        result (str): 原始结果文本。

    Returns:
        None: 将工具输出写入终端。

    Raises:
        None

    Examples:
        >>> print_tool_result("read_file", "line1")
    """
    max_len = 500
    # 1) 先做 UI 级截断，避免终端被超长输出刷屏。
    truncated = result
    if len(result) > max_len:
        truncated = result[:max_len] + f"\n  ... ({len(result)} chars total)"

    # 2) 再统一给每一行加缩进，让工具输出和普通回答视觉分离。
    lines = ["  " + line for line in truncated.splitlines()]
    print("\n".join(lines) if lines else "  (no output)")


def print_error(message: str) -> None:
    """
    打印错误信息。

    Parameters:
        message (str): 错误描述。

    Returns:
        None: 向终端输出错误文本。

    Raises:
        None

    Examples:
        >>> print_error("API key missing")
    """
    print(f"\n  Error: {message}")


def print_confirmation(command: str) -> None:
    """
    打印危险操作确认提示。

    Parameters:
        command (str): 待确认的命令或动作描述。

    Returns:
        None: 仅向终端打印确认文本。

    Raises:
        None

    Examples:
        >>> print_confirmation("rm -rf /")
    """
    print(f"\n  Warning dangerous action: {command}")


def print_divider() -> None:
    """
    打印分隔线。

    Parameters:
        None

    Returns:
        None: 向终端输出分隔线。

    Raises:
        None

    Examples:
        >>> print_divider()
    """
    print("\n  " + "-" * 50)


def print_cost(input_tokens: int, output_tokens: int) -> None:
    """
    打印 Token 统计与费用估算。

    Parameters:
        input_tokens (int): 输入 token 累计数量。
        output_tokens (int): 输出 token 累计数量。

    Returns:
        None: 向终端输出统计文本。

    Raises:
        None

    Examples:
        >>> print_cost(1000, 500)
    """
    cost_in = (input_tokens / 1_000_000) * 3
    cost_out = (output_tokens / 1_000_000) * 15
    total = cost_in + cost_out
    print(f"\n  Tokens: {input_tokens} in / {output_tokens} out (~${total:.4f})")


def print_retry(attempt: int, max_retry: int, reason: str) -> None:
    """
    打印重试提示。

    Parameters:
        attempt (int): 当前重试序号（从 1 开始）。
        max_retry (int): 最大重试次数。
        reason (str): 重试原因。

    Returns:
        None: 向终端输出重试状态。

    Raises:
        None

    Examples:
        >>> print_retry(1, 3, "HTTP 429")
    """
    print(f"\n  Retry {attempt}/{max_retry}: {reason}")


def print_info(message: str) -> None:
    """
    打印普通信息。

    Parameters:
        message (str): 信息内容。

    Returns:
        None: 向终端输出提示信息。

    Raises:
        None

    Examples:
        >>> print_info("Conversation cleared")
    """
    print(f"\n  Info: {message}")


def _get_tool_summary(name: str, tool_input: Dict[str, Any]) -> str:
    """
    生成工具调用摘要字符串。

    Parameters:
        name (str): 工具名。
        tool_input (Dict[str, Any]): 工具参数。

    Returns:
        str: 紧凑的摘要文本。

    Raises:
        None

    Examples:
        >>> _get_tool_summary("list_files", {"pattern": "**/*.py"})
        '**/*.py'
    """
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
