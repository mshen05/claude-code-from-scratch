"""Core agent loop implementation."""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from prompt import build_system_prompt
from session import save_session
from tools import execute_tool, needs_confirmation, tool_definitions
from ui import (
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
except ImportError:  # pragma: no cover - runtime dependency
    Anthropic = None  # type: ignore

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime dependency
    OpenAI = None  # type: ignore


MODEL_CONTEXT: Dict[str, int] = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-opus-4-20250514": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-5.4-nano": 128000,
}


@dataclass
class AgentOptions:
    """
    Agent 初始化参数。

    Parameters:
        yolo (bool): 是否跳过权限确认。
        model (str): 模型名称。
        api_base (Optional[str]): OpenAI 兼容后端地址。
        anthropic_base_url (Optional[str]): Anthropic 代理地址。
        api_key (Optional[str]): API 密钥。
        thinking (bool): 是否启用 extended thinking。

    Returns:
        AgentOptions: 配置实例。

    Raises:
        None

    Examples:
        >>> AgentOptions(model="claude-opus-4-6")
    """

    yolo: bool = False
    model: str = "claude-opus-4-6"
    api_base: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    api_key: Optional[str] = None
    thinking: bool = False


def is_retryable(error: Exception) -> bool:
    """
    判断错误是否可重试。

    Parameters:
        error (Exception): 捕获的异常对象。

    Returns:
        bool: 可重试返回 True。

    Raises:
        None

    Examples:
        >>> is_retryable(Exception("overloaded"))
        True
    """
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    message = str(error)
    code = getattr(error, "code", None)

    if status in {429, 503, 529}:
        return True
    if code in {"ECONNRESET", "ETIMEDOUT"}:
        return True
    if "overloaded" in message:
        return True
    return False


async def with_retry(
    fn: Callable[[], Awaitable[Any]],
    abort_event: Optional[threading.Event] = None,
    max_retries: int = 3,
):
    """
    用指数退避执行函数。

    Parameters:
        fn (callable): 目标函数。
        abort_event (Optional[threading.Event]): 中断事件。
        max_retries (int): 最大重试次数。

    Returns:
        Any: 目标函数返回值。

    Raises:
        Exception: 超过重试上限或不可重试时抛出原始异常。

    Examples:
        >>> # await with_retry(lambda: some_async_fn())
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as error:
            if abort_event and abort_event.is_set():
                raise
            if attempt >= max_retries or not is_retryable(error):
                raise

            delay = min(1000 * (2**attempt), 30000) + random.random() * 1000
            reason = getattr(error, "status", None)
            if reason is not None:
                reason_text = f"HTTP {reason}"
            else:
                reason_text = str(getattr(error, "code", "network error"))

            print_retry(attempt + 1, max_retries, reason_text)
            await asyncio.sleep(delay / 1000.0)
            attempt += 1


def get_context_window(model: str) -> int:
    """
    获取模型上下文窗口大小。

    Parameters:
        model (str): 模型名。

    Returns:
        int: 上下文窗口 token 容量。

    Raises:
        None

    Examples:
        >>> get_context_window("gpt-4o")
        128000
    """
    return MODEL_CONTEXT.get(model, 200000)


def to_openai_tools() -> List[Dict[str, Any]]:
    """
    将内部工具定义转为 OpenAI tools 格式。

    Parameters:
        None

    Returns:
        List[Dict[str, Any]]: OpenAI tool 列表。

    Raises:
        None

    Examples:
        >>> isinstance(to_openai_tools(), list)
        True
    """
    result = []
    for item in tool_definitions:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["input_schema"],
                },
            }
        )
    return result


def get_openai_token_limit_kwargs(model: str, token_limit: int) -> Dict[str, int]:
    """
    根据模型返回 OpenAI token 上限参数。

    Parameters:
        model (str): 模型名称。
        token_limit (int): 期望的输出 token 上限。

    Returns:
        Dict[str, int]: 对应的参数字典。

    Raises:
        None

    Examples:
        >>> get_openai_token_limit_kwargs("gpt-5.4-nano", 1024)
        {'max_completion_tokens': 1024}
    """
    normalized = model.lower()

    # 1) 新模型（如 gpt-5 / o 系列）要求使用 max_completion_tokens。
    if normalized.startswith("gpt-5") or normalized.startswith("o1") or normalized.startswith("o3") or normalized.startswith("o4"):
        return {"max_completion_tokens": token_limit}

    # 2) 其余保持旧参数，兼容传统 chat.completions 模型。
    return {"max_tokens": token_limit}


class Agent:
    """
    Mini Claude Code 的核心 Agent 类。

    Parameters:
        options (Optional[AgentOptions]): Agent 配置。

    Returns:
        Agent: 可执行对话循环的实例。

    Raises:
        RuntimeError: 缺少后端 SDK 依赖时抛出。

    Examples:
        >>> agent = Agent(AgentOptions(api_key="sk-xxx"))
    """

    def __init__(self, options: Optional[AgentOptions] = None) -> None:
        """
        初始化 Agent 状态与后端客户端。

        Parameters:
            options (Optional[AgentOptions]): 初始化配置。

        Returns:
            None: 通过成员变量保存初始化结果。

        Raises:
            RuntimeError: 指定后端 SDK 未安装时抛出。

        Examples:
            >>> _ = Agent(AgentOptions(api_key="test"))
        """
        opts = options or AgentOptions()

        self.yolo = opts.yolo
        self.thinking = opts.thinking
        self.model = opts.model
        self.use_openai = bool(opts.api_base)
        self.system_prompt = build_system_prompt()
        self.effective_window = get_context_window(self.model) - 20_000
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = datetime.utcnow().isoformat()

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0

        self.abort_event: Optional[threading.Event] = None
        self.confirmed_paths: set[str] = set()
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
        """
        判断当前是否处于请求处理中。

        Parameters:
            None

        Returns:
            bool: 正在处理返回 True。

        Raises:
            None

        Examples:
            >>> _ = Agent().is_processing
        """
        return self.abort_event is not None

    def abort(self) -> None:
        """
        请求中断当前任务。

        Parameters:
            None

        Returns:
            None: 通过事件标记触发中断。

        Raises:
            None

        Examples:
            >>> agent = Agent()
            >>> agent.abort()
        """
        if self.abort_event is not None:
            self.abort_event.set()

    def get_token_usage(self) -> Dict[str, int]:
        """
        获取累计 token 使用量。

        Parameters:
            None

        Returns:
            Dict[str, int]: 包含 input 与 output 两个字段。

        Raises:
            None

        Examples:
            >>> Agent().get_token_usage()
        """
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    async def chat(self, user_message: str) -> None:
        """
        执行一次用户消息的完整处理流程。

        Parameters:
            user_message (str): 用户输入内容。

        Returns:
            None: 结果通过终端输出与会话持久化体现。

        Raises:
            Exception: 后端请求异常会向上抛出。

        Examples:
            >>> # await agent.chat("hello")
        """
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

    def clear_history(self) -> None:
        """
        清空当前会话历史和 token 计数。

        Parameters:
            None

        Returns:
            None: 清空内部状态。

        Raises:
            None

        Examples:
            >>> agent = Agent()
            >>> agent.clear_history()
        """
        self.anthropic_messages = []
        self.openai_messages = []

        if self.use_openai:
            self.openai_messages.append({"role": "system", "content": self.system_prompt})

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        """
        打印累计 token 与费用估算。

        Parameters:
            None

        Returns:
            None: 仅终端输出。

        Raises:
            None

        Examples:
            >>> agent = Agent()
            >>> agent.show_cost()
        """
        cost_in = (self.total_input_tokens / 1_000_000) * 3
        cost_out = (self.total_output_tokens / 1_000_000) * 15
        total = cost_in + cost_out
        print_info(
            f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n"
            f"  Estimated cost: ${total:.4f}"
        )

    async def compact(self) -> None:
        """
        手动触发会话压缩。

        Parameters:
            None

        Returns:
            None: 修改内部消息历史。

        Raises:
            Exception: 压缩失败时抛出。

        Examples:
            >>> # await agent.compact()
        """
        await self.compact_conversation()

    def restore_session(self, data: Dict[str, Any]) -> None:
        """
        恢复历史会话消息。

        Parameters:
            data (Dict[str, Any]): 会话数据，包含 anthropicMessages/openaiMessages。

        Returns:
            None: 覆盖当前消息历史。

        Raises:
            None

        Examples:
            >>> # agent.restore_session({"openaiMessages": []})
        """
        anthropic_messages = data.get("anthropicMessages")
        openai_messages = data.get("openaiMessages")

        if isinstance(anthropic_messages, list):
            self.anthropic_messages = anthropic_messages
        if isinstance(openai_messages, list):
            self.openai_messages = openai_messages

        print_info(f"Session restored ({self.get_message_count()} messages).")

    def get_message_count(self) -> int:
        """
        获取当前后端对应的消息数。

        Parameters:
            None

        Returns:
            int: 消息数量。

        Raises:
            None

        Examples:
            >>> Agent().get_message_count()
        """
        return len(self.openai_messages) if self.use_openai else len(self.anthropic_messages)

    def auto_save(self) -> None:
        """
        自动持久化当前会话。

        Parameters:
            None

        Returns:
            None: 写入会话 JSON 文件。

        Raises:
            None

        Examples:
            >>> # agent.auto_save()
        """
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

    async def check_and_compact(self) -> None:
        """
        当输入 token 接近窗口上限时自动压缩会话。

        Parameters:
            None

        Returns:
            None: 可能触发消息压缩。

        Raises:
            Exception: 压缩失败时抛出。

        Examples:
            >>> # await agent.check_and_compact()
        """
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self.compact_conversation()

    async def compact_conversation(self) -> None:
        """
        按当前后端选择压缩策略。

        Parameters:
            None

        Returns:
            None: 修改消息数组。

        Raises:
            Exception: 压缩失败时抛出。

        Examples:
            >>> # await agent.compact_conversation()
        """
        if self.use_openai:
            await self.compact_openai()
        else:
            await self.compact_anthropic()
        print_info("Conversation compacted.")

    async def compact_anthropic(self) -> None:
        """
        使用 Anthropic 模型对历史会话做摘要压缩。

        Parameters:
            None

        Returns:
            None: 重写 anthropic_messages。

        Raises:
            RuntimeError: Anthropic 客户端不可用时抛出。

        Examples:
            >>> # agent.compact_anthropic()
        """
        if len(self.anthropic_messages) < 4:
            return
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client is not initialized")

        last_user_msg = self.anthropic_messages[-1]
        summary_req = [
            {
                "role": "user",
                "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work.",
            }
        ]

        summary_resp = await asyncio.to_thread(
            self.anthropic_client.messages.create,
            model=self.model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[*self.anthropic_messages[:-1], *summary_req],
        )

        content_blocks = self._model_to_dict(summary_resp).get("content", [])
        summary_text = "No summary available."
        if content_blocks and content_blocks[0].get("type") == "text":
            summary_text = content_blocks[0].get("text", summary_text)

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
        """
        使用 OpenAI 兼容后端进行摘要压缩。

        Parameters:
            None

        Returns:
            None: 重写 openai_messages。

        Raises:
            RuntimeError: OpenAI 客户端不可用时抛出。

        Examples:
            >>> # agent.compact_openai()
        """
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
                    "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work.",
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

    async def chat_anthropic(self, user_message: str) -> None:
        """
        运行 Anthropic 后端的主循环。

        Parameters:
            user_message (str): 用户输入。

        Returns:
            None: 通过工具调用和输出完成任务。

        Raises:
            Exception: API 或工具异常会向上抛出。

        Examples:
            >>> # await agent.chat_anthropic("fix bug")
        """
        self.anthropic_messages.append({"role": "user", "content": user_message})

        while True:
            if self.abort_event and self.abort_event.is_set():
                break

            response = await self.call_anthropic_stream()
            usage = response.get("usage", {})
            self.total_input_tokens += int(usage.get("input_tokens", 0))
            self.total_output_tokens += int(usage.get("output_tokens", 0))
            self.last_input_token_count = int(usage.get("input_tokens", 0))

            # 1) 提取本轮所有 tool_use block。
            tool_uses = []
            for block in response.get("content", []):
                if block.get("type") == "tool_use":
                    tool_uses.append(block)

            self.anthropic_messages.append(
                {
                    "role": "assistant",
                    "content": response.get("content", []),
                }
            )

            if not tool_uses:
                print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            tool_results = []
            for tool_use in tool_uses:
                if self.abort_event and self.abort_event.is_set():
                    break

                input_data = tool_use.get("input", {})
                print_tool_call(tool_use.get("name", ""), input_data)

                # 2) 权限检查和会话白名单逻辑与 TS 版本一致。
                if not self.yolo:
                    confirm_msg = needs_confirmation(str(tool_use.get("name", "")), input_data)
                    if confirm_msg and confirm_msg not in self.confirmed_paths:
                        confirmed = self.confirm_dangerous(confirm_msg)
                        if not confirmed:
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_use.get("id", ""),
                                    "content": "User denied this action.",
                                }
                            )
                            continue
                        self.confirmed_paths.add(confirm_msg)

                result = await execute_tool(str(tool_use.get("name", "")), input_data)
                print_tool_result(str(tool_use.get("name", "")), result)
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
        """
        调用 Anthropic 流式接口并返回完整消息。

        Parameters:
            None

        Returns:
            Dict[str, Any]: 标准化后的消息对象。

        Raises:
            RuntimeError: Anthropic 客户端不可用时抛出。
            Exception: API 调用失败时抛出。

        Examples:
            >>> # response = await agent.call_anthropic_stream()
        """
        if self.anthropic_client is None:
            raise RuntimeError("Anthropic client is not initialized")

        def _sync_call() -> Dict[str, Any]:
            """
            执行一次 Anthropic 请求并规范化返回结构。

            Parameters:
                None

            Returns:
                Dict[str, Any]: 统一后的消息字典。

            Raises:
                Exception: 请求失败时抛出。

            Examples:
                >>> # data = _sync_call()
            """
            create_params: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": 16_000 if self.thinking else 8096,
                "system": self.system_prompt,
                "tools": tool_definitions,
                "messages": self.anthropic_messages,
            }
            if self.thinking:
                create_params["thinking"] = {"type": "enabled", "budget_tokens": 10000}

            # 1) 优先使用 stream API；如果 SDK 版本不支持则降级到 create。
            try:
                with self.anthropic_client.messages.stream(**create_params) as stream:
                    first_text = True
                    for text in stream.text_stream:
                        if first_text:
                            print_assistant_text("\n")
                            first_text = False
                        print_assistant_text(text)
                    final_message = stream.get_final_message()
                    final_dict = self._model_to_dict(final_message)
            except Exception:
                fallback = self.anthropic_client.messages.create(**create_params)
                final_dict = self._model_to_dict(fallback)
                text_content = self._extract_first_text(final_dict.get("content", []))
                if text_content:
                    print_assistant_text("\n")
                    print_assistant_text(text_content)

            # 2) thinking 模式下过滤 thinking block，避免污染后续上下文。
            if self.thinking:
                content = final_dict.get("content", [])
                final_dict["content"] = [block for block in content if block.get("type") != "thinking"]

            return final_dict

        async def _do_call() -> Dict[str, Any]:
            """
            在线程中执行阻塞 SDK 调用，避免阻塞事件循环。

            Parameters:
                None

            Returns:
                Dict[str, Any]: 标准化后的消息字典。

            Raises:
                Exception: SDK 调用失败时抛出。

            Examples:
                >>> # data = await _do_call()
            """
            return await asyncio.to_thread(_sync_call)

        return await with_retry(_do_call, self.abort_event)

    async def chat_openai(self, user_message: str) -> None:
        """
        运行 OpenAI 兼容后端的主循环。

        Parameters:
            user_message (str): 用户输入。

        Returns:
            None: 循环处理直到无工具调用。

        Raises:
            Exception: API 或工具异常会向上抛出。

        Examples:
            >>> # await agent.chat_openai("hello")
        """
        self.openai_messages.append({"role": "user", "content": user_message})

        while True:
            if self.abort_event and self.abort_event.is_set():
                break

            response = await self.call_openai_stream()
            usage = response.get("usage", {})
            self.total_input_tokens += int(usage.get("prompt_tokens", 0))
            self.total_output_tokens += int(usage.get("completion_tokens", 0))
            self.last_input_token_count = int(usage.get("prompt_tokens", 0))

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            self.openai_messages.append(message)

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                print_cost(self.total_input_tokens, self.total_output_tokens)
                break

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

                if not self.yolo:
                    confirm_msg = needs_confirmation(fn_name, input_data)
                    if confirm_msg and confirm_msg not in self.confirmed_paths:
                        confirmed = self.confirm_dangerous(confirm_msg)
                        if not confirmed:
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
        """
        调用 OpenAI 流式接口并重组最终响应。

        Parameters:
            None

        Returns:
            Dict[str, Any]: 兼容 chat.completion 的结构。

        Raises:
            RuntimeError: OpenAI 客户端不可用时抛出。
            Exception: API 调用失败时抛出。

        Examples:
            >>> # response = await agent.call_openai_stream()
        """
        if self.openai_client is None:
            raise RuntimeError("OpenAI client is not initialized")

        def _sync_call() -> Dict[str, Any]:
            """
            执行一次 OpenAI 流式请求并重建完整响应。

            Parameters:
                None

            Returns:
                Dict[str, Any]: 类 chat.completion 结构。

            Raises:
                Exception: 请求失败时抛出。

            Examples:
                >>> # data = _sync_call()
            """
            stream = self.openai_client.chat.completions.create(
                model=self.model,
                **get_openai_token_limit_kwargs(self.model, 8096),
                tools=to_openai_tools(),
                messages=self.openai_messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            content = ""
            first_text = True
            tool_calls: Dict[int, Dict[str, str]] = {}
            finish_reason = ""
            usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            # 1) 逐 chunk 累积文本与 tool_calls 参数片段。
            for chunk in stream:
                chunk_dict = self._model_to_dict(chunk)
                if chunk_dict.get("usage"):
                    usage = {
                        "prompt_tokens": int(chunk_dict["usage"].get("prompt_tokens", 0)),
                        "completion_tokens": int(chunk_dict["usage"].get("completion_tokens", 0)),
                        "total_tokens": int(chunk_dict["usage"].get("total_tokens", 0)),
                    }

                choices = chunk_dict.get("choices", [])
                if not choices:
                    continue
                choice0 = choices[0]
                delta = choice0.get("delta", {})

                if delta.get("content"):
                    if first_text:
                        print_assistant_text("\n")
                        first_text = False
                    print_assistant_text(delta["content"])
                    content += delta["content"]

                for tc in delta.get("tool_calls", []) or []:
                    index = int(tc.get("index", 0))
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

            # 2) 按 index 重建 tool_calls，保持原始顺序。
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
                            "tool_calls": assembled_tool_calls if assembled_tool_calls else None,
                            "refusal": None,
                        },
                        "finish_reason": finish_reason or "stop",
                        "logprobs": None,
                    }
                ],
                "usage": usage,
            }

        async def _do_call() -> Dict[str, Any]:
            """
            在线程中执行阻塞 OpenAI SDK 流式调用。

            Parameters:
                None

            Returns:
                Dict[str, Any]: 重组后的完整 completion 字典。

            Raises:
                Exception: SDK 调用失败时抛出。

            Examples:
                >>> # data = await _do_call()
            """
            return await asyncio.to_thread(_sync_call)

        return await with_retry(_do_call, self.abort_event)

    def confirm_dangerous(self, command: str) -> bool:
        """
        与用户交互确认危险操作。

        Parameters:
            command (str): 待确认内容。

        Returns:
            bool: 用户允许返回 True。

        Raises:
            None

        Examples:
            >>> # agent.confirm_dangerous("rm -rf /")
        """
        print_confirmation(command)
        answer = input("  Allow? (y/n): ").strip().lower()
        return answer.startswith("y")

    def _extract_first_text(self, content_blocks: List[Dict[str, Any]]) -> str:
        """
        从 content blocks 中提取首段 text。

        Parameters:
            content_blocks (List[Dict[str, Any]]): 模型返回 content 数组。

        Returns:
            str: 首段文本，找不到时返回空字符串。

        Raises:
            None

        Examples:
            >>> Agent()._extract_first_text([{"type": "text", "text": "hi"}])
            'hi'
        """
        for block in content_blocks:
            if block.get("type") == "text":
                return str(block.get("text", ""))
        return ""

    def _model_to_dict(self, obj: Any) -> Dict[str, Any]:
        """
        将 SDK 返回对象安全转换为字典。

        Parameters:
            obj (Any): SDK 返回对象。

        Returns:
            Dict[str, Any]: 普通字典结构。

        Raises:
            None

        Examples:
            >>> Agent()._model_to_dict({"a": 1})
            {'a': 1}
        """
        if isinstance(obj, dict):
            return obj

        # 1) Pydantic v2 模型。
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass

        # 2) 兼容少量 SDK 的 to_dict 接口。
        if hasattr(obj, "to_dict"):
            try:
                return obj.to_dict()
            except Exception:
                pass

        # 3) 最后兜底 __dict__，确保尽量可用。
        if hasattr(obj, "__dict__"):
            try:
                return dict(obj.__dict__)
            except Exception:
                pass

        return {}
