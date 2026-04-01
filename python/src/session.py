"""Session persistence utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


@dataclass
class SessionMetadata:
    """
    会话元数据结构。

    Parameters:
        id (str): 会话 ID。
        model (str): 当前会话使用的模型名。
        cwd (str): 会话启动目录。
        startTime (str): 会话开始时间（ISO 字符串）。
        messageCount (int): 当前消息数量。

    Returns:
        SessionMetadata: 数据类实例。

    Raises:
        None

    Examples:
        >>> SessionMetadata("abcd1234", "claude-opus-4-6", "/tmp", "2026-01-01T00:00:00", 10)
    """

    id: str
    model: str
    cwd: str
    startTime: str
    messageCount: int


def ensure_dir() -> None:
    """
    确保会话目录存在。

    Parameters:
        None

    Returns:
        None: 创建目录副作用。

    Raises:
        OSError: 当目录创建失败时抛出。

    Examples:
        >>> ensure_dir()
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    """
    将会话保存到 JSON 文件。

    Parameters:
        session_id (str): 会话 ID。
        data (Dict[str, Any]): 会话完整数据。

    Returns:
        None: 文件写入副作用。

    Raises:
        OSError: 文件写入失败时抛出。

    Examples:
        >>> save_session("abcd1234", {"metadata": {}})
    """
    ensure_dir()
    file_path = SESSION_DIR / f"{session_id}.json"
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    读取指定会话。

    Parameters:
        session_id (str): 会话 ID。

    Returns:
        Optional[Dict[str, Any]]: 成功返回会话字典，失败返回 None。

    Raises:
        None

    Examples:
        >>> load_session("abcd1234")
    """
    file_path = SESSION_DIR / f"{session_id}.json"
    if not file_path.exists():
        return None

    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> List[SessionMetadata]:
    """
    列出所有会话的元数据。

    Parameters:
        None

    Returns:
        List[SessionMetadata]: 会话元数据列表。

    Raises:
        None

    Examples:
        >>> sessions = list_sessions()
    """
    ensure_dir()
    result: List[SessionMetadata] = []

    # 1) 逐个读取 JSON 文件，解析 metadata。
    for file_path in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            meta = data.get("metadata", {})
            result.append(
                SessionMetadata(
                    id=str(meta.get("id", "")),
                    model=str(meta.get("model", "")),
                    cwd=str(meta.get("cwd", "")),
                    startTime=str(meta.get("startTime", datetime.now().isoformat())),
                    messageCount=int(meta.get("messageCount", 0)),
                )
            )
        except Exception:
            continue

    return result


def get_latest_session_id() -> Optional[str]:
    """
    获取最近一次会话 ID。

    Parameters:
        None

    Returns:
        Optional[str]: 最近会话 ID，不存在时返回 None。

    Raises:
        None

    Examples:
        >>> get_latest_session_id()
    """
    sessions = list_sessions()
    if not sessions:
        return None

    # 1) 使用时间字段排序，保持与 TS 版本一致的策略。
    sessions.sort(key=lambda item: item.startTime, reverse=True)
    return sessions[0].id
