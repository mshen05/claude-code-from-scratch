"""CLI entrypoint for the Python mini agent."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .agent import Agent, AgentOptions
from .session import get_latest_session_id, load_session
from .ui import print_error, print_info, print_user_prompt, print_welcome


@dataclass
class ParsedArgs:
    yolo: bool
    model: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    prompt: Optional[str] = None
    resume: bool = False
    thinking: bool = False


def parse_args(argv: List[str]) -> ParsedArgs:
    """Parse command line arguments and environment variables."""
    yolo = False
    thinking = False
    model = os.environ.get("MINI_CLAUDE_MODEL", "claude-opus-4-6")
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
                """Usage: mini-claude-py [options] [prompt]

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


def resolve_api_config(parsed: ParsedArgs) -> Tuple[Optional[str], Optional[str], bool]:
    """Resolve API config: CLI flags > env vars
    Priority: --api-base/--api-key flags first, then env vars
    """
    api_base = parsed.api_base
    api_key = parsed.api_key
    use_openai = bool(parsed.api_base)

    if not api_key:
        if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
            api_key = os.environ.get("OPENAI_API_KEY")
            api_base = api_base or os.environ.get("OPENAI_BASE_URL")
            use_openai = True
        elif os.environ.get("ANTHROPIC_API_KEY"):
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            api_base = api_base or os.environ.get("ANTHROPIC_BASE_URL")
            use_openai = False
        elif os.environ.get("OPENAI_API_KEY"):
            api_key = os.environ.get("OPENAI_API_KEY")
            api_base = api_base or os.environ.get("OPENAI_BASE_URL")
            use_openai = True

    return api_base, api_key, use_openai


async def run_repl(agent: Agent) -> None:
    """Interactive REPL loop for the assistant."""
    # Ctrl+C handling
    sigint_count = 0

    def _sigint_handler(signum, frame):
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

        try:
            await agent.chat(user_input)
        except Exception as error:
            if "aborted" not in str(error).lower():
                print_error(str(error))


async def main() -> None:
    parsed = parse_args(sys.argv[1:])
    api_base, api_key, use_openai = resolve_api_config(parsed)

    if not api_key:
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
            api_base=api_base if use_openai else None,
            anthropic_base_url=api_base if not use_openai else None,
            api_key=api_key,
        )
    )

    if parsed.resume:
        # Resume session if requested
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

    if parsed.prompt:
        # One-shot mode
        try:
            await agent.chat(parsed.prompt)
        except Exception as error:
            print_error(str(error))
            raise SystemExit(1)
    else:
        # Interactive REPL mode
        await run_repl(agent)


if __name__ == "__main__":
    asyncio.run(main())


