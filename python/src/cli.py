"""CLI entrypoint for Mini Claude Code (Python version)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import dataclass
from typing import List, Optional

from agent import Agent, AgentOptions
from session import get_latest_session_id, load_session
from ui import print_error, print_info, print_user_prompt, print_welcome


@dataclass
class ParsedArgs:
    """
    命令行参数结构。

    Parameters:
        yolo (bool): 是否跳过确认。
        model (str): 模型名。
        api_base (Optional[str]): OpenAI 兼容地址。
        api_key (Optional[str]): API Key。
        prompt (Optional[str]): 单次模式 prompt。
        resume (bool): 是否恢复上次会话。
        thinking (bool): 是否启用 extended thinking。

    Returns:
        ParsedArgs: 参数对象。

    Raises:
        None

    Examples:
        >>> ParsedArgs(False, "claude-opus-4-6")
    """

    yolo: bool
    model: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    prompt: Optional[str] = None
    resume: bool = False
    thinking: bool = False


def parse_args(argv: List[str]) -> ParsedArgs:
    """
    解析命令行参数（手写解析，保持参考实现风格）。

    Parameters:
        argv (List[str]): 命令行参数列表（不含程序名）。

    Returns:
        ParsedArgs: 解析结果。

    Raises:
        SystemExit: --help 时退出。

    Examples:
        >>> parse_args(["--yolo", "hello"]).yolo
        True
    """
    yolo = False
    thinking = False
    model = os.environ.get("MINI_CLAUDE_MODEL", "gpt-4o-mini")
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    resume = False
    positional: List[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--yolo", "-y"}:
            yolo = True
        elif arg == "--thinking":
            thinking = True
        elif arg in {"--model", "-m"}:
            if i + 1 < len(argv):
                model = argv[i + 1]
                i += 1
        elif arg == "--api-base":
            if i + 1 < len(argv):
                api_base = argv[i + 1]
                i += 1
        elif arg == "--api-key":
            if i + 1 < len(argv):
                api_key = argv[i + 1]
                i += 1
        elif arg == "--resume":
            resume = True
        elif arg in {"--help", "-h"}:
            print(
                """
Usage: mini-claude-py [options] [prompt]

Options:
  --yolo, -y       Skip all confirmation prompts
  --thinking       Enable extended thinking (Anthropic only)
  --model, -m      Model to use (default: claude-opus-4-6, or MINI_CLAUDE_MODEL env)
  --api-base URL   Use OpenAI-compatible API endpoint
  --api-key KEY    API key for the specified endpoint
  --resume         Resume the last session
  --help, -h       Show this help

REPL commands:
  /clear           Clear conversation history
  /cost            Show token usage and cost
  /compact         Manually compact conversation
"""
            )
            raise SystemExit(0)
        else:
            positional.append(arg)
        i += 1

    return ParsedArgs(
        yolo=yolo,
        model=model,
        api_base=api_base,
        api_key=api_key,
        resume=resume,
        thinking=thinking,
        prompt=" ".join(positional) if positional else None,
    )


async def run_repl(agent: Agent) -> None:
    """
    运行交互式 REPL。

    Parameters:
        agent (Agent): Agent 实例。

    Returns:
        None: 循环处理用户输入直到退出。

    Raises:
        KeyboardInterrupt: 连续中断时退出进程。

    Examples:
        >>> # run_repl(agent)
    """
    sigint_count = 0

    def _sigint_handler(signum, frame):
        """
        SIGINT 信号处理器。

        Parameters:
            signum (int): 信号值。
            frame (object): 当前栈帧。

        Returns:
            None: 通过终端提示反馈状态。

        Raises:
            SystemExit: 连续两次 Ctrl+C 时退出。

        Examples:
            >>> # _sigint_handler(signal.SIGINT, None)
        """
        nonlocal sigint_count
        del signum, frame

        if agent.is_processing:
            agent.abort()
            print("\n  (interrupted)")
            sigint_count = 0
            print_user_prompt()
            return

        sigint_count += 1
        if sigint_count >= 2:
            print("\nBye!\n")
            raise SystemExit(0)

        print("\n  Press Ctrl+C again to exit.")
        print_user_prompt()

    signal.signal(signal.SIGINT, _sigint_handler)
    print_welcome()

    while True:
        try:
            print_user_prompt()
            line = input()
        except EOFError:
            print("\nBye!\n")
            return

        user_input = line.strip()
        sigint_count = 0

        if not user_input:
            continue
        if user_input in {"exit", "quit"}:
            print("\nBye!\n")
            return

        # 1) 先处理内建 REPL 命令。
        if user_input == "/clear":
            agent.clear_history()
            continue
        if user_input == "/cost":
            agent.show_cost()
            continue
        if user_input == "/compact":
            try:
                await agent.compact()
            except Exception as error:
                print_error(str(error))
            continue

        # 2) 普通文本进入 Agent 主循环。
        try:
            await agent.chat(user_input)
        except Exception as error:
            text = str(error)
            if "aborted" in text.lower():
                pass
            else:
                print_error(text)


def resolve_api_config(parsed: ParsedArgs):
    """
    按优先级解析 API 配置（CLI 参数优先）。

    Parameters:
        parsed (ParsedArgs): 已解析参数。

    Returns:
        tuple[Optional[str], Optional[str], bool]: api_base, api_key, use_openai。

    Raises:
        None

    Examples:
        >>> resolve_api_config(ParsedArgs(False, "m"))
    """
    resolved_api_base = parsed.api_base
    resolved_api_key = parsed.api_key
    resolved_use_openai = bool(parsed.api_base)

    if not resolved_api_key:
        if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
            resolved_api_key = os.environ.get("OPENAI_API_KEY")
            resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
            resolved_use_openai = True
        elif os.environ.get("ANTHROPIC_API_KEY"):
            resolved_api_key = os.environ.get("ANTHROPIC_API_KEY")
            resolved_api_base = resolved_api_base or os.environ.get("ANTHROPIC_BASE_URL")
            resolved_use_openai = False
        elif os.environ.get("OPENAI_API_KEY"):
            resolved_api_key = os.environ.get("OPENAI_API_KEY")
            resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
            resolved_use_openai = True

    return resolved_api_base, resolved_api_key, resolved_use_openai


async def main() -> None:
    """
    CLI 主入口。

    Parameters:
        None

    Returns:
        None: 根据模式执行 one-shot 或 REPL。

    Raises:
        SystemExit: 缺少 API key 或发生致命异常时退出。

    Examples:
        >>> # main()
    """
    parsed = parse_args(sys.argv[1:])
    resolved_api_base, resolved_api_key, resolved_use_openai = resolve_api_config(parsed)

    if not resolved_api_key:
        print_error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY (+ optional ANTHROPIC_BASE_URL) for Anthropic format,\n"
            "  or OPENAI_API_KEY + OPENAI_BASE_URL for OpenAI-compatible format,\n"
            "  or use --api-key / --api-base flags."
        )
        raise SystemExit(1)

    agent = Agent(
        AgentOptions(
            yolo=parsed.yolo,
            model=parsed.model,
            thinking=parsed.thinking,
            api_base=resolved_api_base if resolved_use_openai else None,
            anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
            api_key=resolved_api_key,
        )
    )

    # 1) 如果指定 --resume，恢复最近会话。
    if parsed.resume:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session(
                    {
                        "anthropicMessages": session.get("anthropicMessages"),
                        "openaiMessages": session.get("openaiMessages"),
                    }
                )
            else:
                print_info("No session found to resume.")
        else:
            print_info("No previous sessions found.")

    # 2) one-shot 与 REPL 两种模式。
    if parsed.prompt:
        try:
            await agent.chat(parsed.prompt)
        except Exception as error:
            print_error(str(error))
            raise SystemExit(1)
    else:
        await run_repl(agent)


if __name__ == "__main__":
    asyncio.run(main())
