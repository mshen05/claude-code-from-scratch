"""Core agent loop implementation."""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .prompt import build_system_prompt
from .session import save_session
from .tools import execute_tool, needs_confirmation, tool_definitions
from .ui import (
    print_assistant_text,
    print_confirmation,
    print_cost,
    print_divider,
    print_info,
    print_retry,
    print_tool_call,
    print_tool_result,
)

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


MODEL_CONTEXT: Dict[str, int] = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-opus-4-20250514": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}


@dataclass
class AgentOptions:
    yolo: bool = False
    model: str = "claude-opus-4-6"
    api_base: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    api_key: Optional[str] = None
    thinking: bool = False


# ─── Retry with exponential backoff ──────────────────────────

def is_retryable(error: Exception) -> bool:
    """Check if an API error is safe to retry."""
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    text = str(error).lower()
    return bool(
        status in {429, 503, 529}
        or code in {"ECONNRESET", "ETIMEDOUT"}
        or "overloaded" in text
        or "rate limit" in text
        or "temporarily unavailable" in text
    )


async def with_retry(
    fn: Callable[[], Awaitable[Any]],
    abort_event: Optional[threading.Event] = None,
    max_retries: int = 3,
) -> Any:
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as error:
            if abort_event and abort_event.is_set():
                raise
            if attempt >= max_retries or not is_retryable(error):
                raise

            delay_ms = min(1000 * (2**attempt), 30000) + random.random() * 1000
            status = getattr(error, "status", None) or getattr(error, "status_code", None)
            reason = f"HTTP {status}" if status else str(getattr(error, "code", "network error"))
            print_retry(attempt + 1, max_retries, reason)
            await asyncio.sleep(delay_ms / 1000.0)
            attempt += 1


# ─── Model context windows ──────────────────────────────────

def get_context_window(model: str) -> int:
    return MODEL_CONTEXT.get(model, 200000)


# ─── Convert tools to OpenAI format ─────────────────────────

def to_openai_tools() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": item["name"],
                "description": item["description"],
                "parameters": item["input_schema"],
            },
        }
        for item in tool_definitions
    ]


def get_openai_token_limit_kwargs(model: str, token_limit: int) -> Dict[str, int]:
    lowered = model.lower()
    if lowered.startswith("gpt-5") or lowered.startswith("o1") or lowered.startswith("o3") or lowered.startswith("o4"):
        return {"max_completion_tokens": token_limit}
    return {"max_tokens": token_limit}


# ─── Agent ───────────────────────────────────────────────────

class Agent:
    def __init__(self, options: Optional[AgentOptions] = None) -> None:
        opts = options or AgentOptions()

        self.yolo = opts.yolo
        self.model = opts.model
        self.thinking = opts.thinking
        self.use_openai = bool(opts.api_base)

        self.system_prompt = build_system_prompt()
        self.effective_window = get_context_window(self.model) - 20000
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = datetime.now(timezone.utc).isoformat()

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0

        # Abort support
        self.abort_event: Optional[threading.Event] = None
        
        # Permission whitelist: paths confirmed in this session
        self.confirmed_paths: set[str] = set()
        
        # Separate message histories for each backend
        self.anthropic_messages: List[Dict[str, Any]] = []
        self.openai_messages: List[Dict[str, Any]] = []

        self.anthropic_client = None
        self.openai_client = None

        if self.use_openai:
            if OpenAI is None:
                raise RuntimeError("openai package is required for OpenAI-compatible backend")
            self.openai_client = OpenAI(api_key=opts.api_key, base_url=opts.api_base)
            self.openai_messages.append({"role": "system", "content": self.system_prompt})
        else:
            if Anthropic is None:
                raise RuntimeError("anthropic package is required for Anthropic backend")
            kwargs: Dict[str, Any] = {"api_key": opts.api_key}
            if opts.anthropic_base_url:
                kwargs["base_url"] = opts.anthropic_base_url
            self.anthropic_client = Anthropic(**kwargs)

    @property
    def is_processing(self) -> bool:
        return self.abort_event is not None

    def abort(self) -> None:
        if self.abort_event is not None:
            self.abort_event.set()

    def get_token_usage(self) -> Dict[str, int]:
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    def get_message_count(self) -> int:
        return len(self.openai_messages) if self.use_openai else len(self.anthropic_messages)

    # ─── REPL commands ──────────────────────────────────────────

    def clear_history(self) -> None:
        self.anthropic_messages = []
        self.openai_messages = []
        if self.use_openai:
            self.openai_messages.append({"role": "system", "content": self.system_prompt})
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        cost_in = (self.total_input_tokens / 1_000_000) * 3
        cost_out = (self.total_output_tokens / 1_000_000) * 15
        total = cost_in + cost_out
        print_info(
            f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n"
            f"  Estimated cost: ${total:.4f}"
        )

    def auto_save(self) -> None:
        try:
            save_session(
                self.session_id,
                {
                    "metadata": {
                        "id": self.session_id,
                        "model": self.model,
                        "cwd": str(Path.cwd()),
                        "startTime": self.session_start_time,
                        "messageCount": self.get_message_count(),
                    },
                    "anthropicMessages": None if self.use_openai else self.anthropic_messages,
                    "openaiMessages": self.openai_messages if self.use_openai else None,
                },
            )
        except Exception:
            pass

    # ─── Session restore ───────────────────────────────────────

    def restore_session(self, data: Dict[str, Any]) -> None:
        anthropic_messages = data.get("anthropicMessages")
        openai_messages = data.get("openaiMessages")
        if isinstance(anthropic_messages, list):
            self.anthropic_messages = anthropic_messages
        if isinstance(openai_messages, list):
            self.openai_messages = openai_messages
        print_info(f"Session restored ({self.get_message_count()} messages).")

    async def chat(self, user_message: str) -> None:
        self.abort_event = threading.Event()
        try:
            if self.use_openai:
                await self.chat_openai(user_message)
            else:
                await self.chat_anthropic(user_message)
        finally:
            self.abort_event = None

        print_divider()
        self.auto_save()

    # ─── Autocompact ───────────────────────────────────────────

    async def compact(self) -> None:
        await self.compact_conversation()

    async def check_and_compact(self) -> None:
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self.compact_conversation()

    async def compact_conversation(self) -> None:
        if self.use_openai:
            await self.compact_openai()
        else:
            await self.compact_anthropic()
        print_info("Conversation compacted.")

    async def compact_anthropic(self) -> None:
        if len(self.anthropic_messages) < 4:
            return
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client is not initialized")

        last_user_msg = self.anthropic_messages[-1]
        summary_req = {
            "role": "user",
            "content": (
                "Summarize the conversation so far in a concise paragraph, preserving key decisions, "
                "file paths, and context needed to continue the work."
            ),
        }

        summary_resp = await asyncio.to_thread(
            self.anthropic_client.messages.create,
            model=self.model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[*self.anthropic_messages[:-1], summary_req],
        )

        content = self._model_to_dict(summary_resp).get("content", [])
        summary_text = self._extract_first_text(content) or "No summary available."

        self.anthropic_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {
                "role": "assistant",
                "content": "Understood. I have the context from our previous conversation. How can I continue helping?",
            },
        ]

        if last_user_msg.get("role") == "user":
            self.anthropic_messages.append(last_user_msg)
        self.last_input_token_count = 0

    async def compact_openai(self) -> None:
        if len(self.openai_messages) < 5:
            return
        if self.openai_client is None:
            raise RuntimeError("OpenAI client is not initialized")

        system_msg = self.openai_messages[0]
        last_user_msg = self.openai_messages[-1]
        summary_resp = await asyncio.to_thread(
            self.openai_client.chat.completions.create,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a conversation summarizer. Be concise but preserve important details.",
                },
                *self.openai_messages[1:-1],
                {
                    "role": "user",
                    "content": (
                        "Summarize the conversation so far in a concise paragraph, preserving key decisions, "
                        "file paths, and context needed to continue the work."
                    ),
                },
            ],
            **get_openai_token_limit_kwargs(self.model, 2048),
        )

        summary_dict = self._model_to_dict(summary_resp)
        summary_text = (
            summary_dict.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "No summary available.")
        )

        self.openai_messages = [
            system_msg,
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {
                "role": "assistant",
                "content": "Understood. I have the context from our previous conversation. How can I continue helping?",
            },
        ]

        if last_user_msg.get("role") == "user":
            self.openai_messages.append(last_user_msg)
        self.last_input_token_count = 0

    # ─── Anthropic backend ───────────────────────────────────────

    async def chat_anthropic(self, user_message: str) -> None:
        self.anthropic_messages.append({"role": "user", "content": user_message})

        while True:
            if self.abort_event and self.abort_event.is_set():
                break

            response = await self.call_anthropic_stream()
            
            # Track tokens
            usage = response.get("usage") or {}
            self.total_input_tokens += int(usage.get("input_tokens") or 0)
            self.total_output_tokens += int(usage.get("output_tokens") or 0)
            self.last_input_token_count = int(usage.get("input_tokens") or 0)

            content_blocks = response.get("content", [])
            tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]

            # Add assistant message to history
            self.anthropic_messages.append({"role": "assistant", "content": content_blocks})

            # If no tool calls, we're done
            if not tool_uses:
                print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            # Execute tool calls
            tool_results: List[Dict[str, Any]] = []
            for tool_use in tool_uses:
                if self.abort_event and self.abort_event.is_set():
                    break

                input_data = tool_use.get("input", {})
                tool_name = str(tool_use.get("name", ""))
                print_tool_call(tool_name, input_data)

                # Permission check
                if not self.yolo:
                    confirm_msg = needs_confirmation(tool_name, input_data)
                    if confirm_msg and confirm_msg not in self.confirmed_paths:
                        if not self.confirm_dangerous(confirm_msg):
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_use.get("id", ""),
                                    "content": "User denied this action.",
                                }
                            )
                            continue
                        self.confirmed_paths.add(confirm_msg)

                result = await execute_tool(tool_name, input_data)
                print_tool_result(tool_name, result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id", ""),
                        "content": result,
                    }
                )

            self.anthropic_messages.append({"role": "user", "content": tool_results})
            await self.check_and_compact()

    async def call_anthropic_stream(self) -> Dict[str, Any]:
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client is not initialized")

        def _sync_call() -> Dict[str, Any]:
            create_params: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": 16000 if self.thinking else 8096,
                "system": self.system_prompt,
                "tools": tool_definitions,
                "messages": self.anthropic_messages,
            }
            
            # Extended thinking support (Anthropic only)
            if self.thinking:
                create_params["thinking"] = {"type": "enabled", "budget_tokens": 10000}

            messages_api = self.anthropic_client.messages
            stream_method = getattr(messages_api, "stream", None)

            if callable(stream_method):
                try:
                    with stream_method(**create_params) as stream:
                        first_text = True
                        for text in stream.text_stream:
                            if first_text:
                                print_assistant_text("\n")
                                first_text = False
                            print_assistant_text(text)
                        final_message = stream.get_final_message()
                        final_dict = self._model_to_dict(final_message)
                except (AttributeError, TypeError, NotImplementedError):
                    fallback = messages_api.create(**create_params)
                    final_dict = self._model_to_dict(fallback)
                    text_content = self._extract_first_text(final_dict.get("content", []))
                    if text_content:
                        print_assistant_text("\n")
                        print_assistant_text(text_content)
            else:
                fallback = messages_api.create(**create_params)
                final_dict = self._model_to_dict(fallback)
                text_content = self._extract_first_text(final_dict.get("content", []))
                if text_content:
                    print_assistant_text("\n")
                    print_assistant_text(text_content)

            # Filter out thinking blocks from content (don't store in history)
            if self.thinking:
                final_dict["content"] = [
                    block for block in final_dict.get("content", []) if block.get("type") != "thinking"
                ]

            return final_dict

        async def _do_call() -> Dict[str, Any]:
            return await asyncio.to_thread(_sync_call)

        return await with_retry(_do_call, self.abort_event)

    # ─── OpenAI-compatible backend ───────────────────────────────

    async def chat_openai(self, user_message: str) -> None:
        self.openai_messages.append({"role": "user", "content": user_message})

        while True:
            if self.abort_event and self.abort_event.is_set():
                break

            response = await self.call_openai_stream()
            
            # Track tokens
            usage = response.get("usage") or {}
            self.total_input_tokens += int(usage.get("prompt_tokens") or 0)
            self.total_output_tokens += int(usage.get("completion_tokens") or 0)
            self.last_input_token_count = int(usage.get("prompt_tokens") or 0)

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            
            # Add assistant message to history
            self.openai_messages.append(message)

            # If no tool calls, we're done
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            # Execute tool calls
            for tool_call in tool_calls:
                if self.abort_event and self.abort_event.is_set():
                    break
                if tool_call.get("type") != "function":
                    continue

                fn_name = tool_call.get("function", {}).get("name", "")
                arguments_text = tool_call.get("function", {}).get("arguments", "{}")
                try:
                    input_data = json.loads(arguments_text)
                except Exception:
                    input_data = {}

                print_tool_call(fn_name, input_data)

                # Permission check
                if not self.yolo:
                    confirm_msg = needs_confirmation(fn_name, input_data)
                    if confirm_msg and confirm_msg not in self.confirmed_paths:
                        if not self.confirm_dangerous(confirm_msg):
                            self.openai_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_call.get("id", ""),
                                    "content": "User denied this action.",
                                }
                            )
                            continue
                        self.confirmed_paths.add(confirm_msg)

                result = await execute_tool(fn_name, input_data)
                print_tool_result(fn_name, result)
                self.openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": result,
                    }
                )

            await self.check_and_compact()

    async def call_openai_stream(self) -> Dict[str, Any]:
        if self.openai_client is None:
            raise RuntimeError("OpenAI client is not initialized")

        def _sync_call() -> Dict[str, Any]:
            stream = self.openai_client.chat.completions.create(
                model=self.model,
                **get_openai_token_limit_kwargs(self.model, 8096),
                tools=to_openai_tools(),
                messages=self.openai_messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            # Accumulate the streamed response
            content = ""
            first_text = True
            tool_calls: Dict[int, Dict[str, str]] = {}
            finish_reason = ""
            usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            for chunk in stream:
                chunk_dict = self._model_to_dict(chunk)
                
                # Usage comes in the final chunk (no delta)
                if chunk_dict.get("usage"):
                    usage_dict = chunk_dict["usage"]
                    usage = {
                        "prompt_tokens": int(usage_dict.get("prompt_tokens") or 0),
                        "completion_tokens": int(usage_dict.get("completion_tokens") or 0),
                        "total_tokens": int(usage_dict.get("total_tokens") or 0),
                    }

                choices = chunk_dict.get("choices", [])
                if not choices:
                    continue
                choice0 = choices[0]
                delta = choice0.get("delta", {})

                # Stream text content
                if delta.get("content"):
                    if first_text:
                        print_assistant_text("\n")
                        first_text = False
                    print_assistant_text(delta["content"])
                    content += delta["content"]

                # Accumulate tool calls (arguments arrive in chunks)
                for tc in delta.get("tool_calls", []) or []:
                    index = int(tc.get("index") or 0)
                    existing = tool_calls.get(index)
                    if existing is None:
                        tool_calls[index] = {
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", ""),
                        }
                    else:
                        args_piece = tc.get("function", {}).get("arguments", "")
                        if args_piece:
                            existing["arguments"] += args_piece

                if choice0.get("finish_reason"):
                    finish_reason = choice0["finish_reason"]

            # Reconstruct ChatCompletion from streamed chunks
            assembled_tool_calls = []
            for index in sorted(tool_calls.keys()):
                item = tool_calls[index]
                assembled_tool_calls.append(
                    {
                        "id": item["id"] or f"tool_{index}",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    }
                )

            return {
                "id": "stream",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": assembled_tool_calls or None,
                            "refusal": None,
                        },
                        "finish_reason": finish_reason or "stop",
                        "logprobs": None,
                    }
                ],
                "usage": usage,
            }

        async def _do_call() -> Dict[str, Any]:
            return await asyncio.to_thread(_sync_call)

        return await with_retry(_do_call, self.abort_event)

    # ─── Shared ──────────────────────────────────────────────────

    def confirm_dangerous(self, command: str) -> bool:
        print_confirmation(command)
        answer = input("  Allow? (y/n): ").strip().lower()
        return answer.startswith("y")

    def _extract_first_text(self, content_blocks: List[Dict[str, Any]]) -> str:
        for block in content_blocks:
            if block.get("type") == "text":
                return str(block.get("text", ""))
        return ""

    def _model_to_dict(self, obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if hasattr(obj, "to_dict"):
            try:
                return obj.to_dict()
            except Exception:
                pass
        if hasattr(obj, "__dict__"):
            try:
                return dict(obj.__dict__)
            except Exception:
                pass
        return {}
