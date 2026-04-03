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
    id: str
    model: str
    cwd: str
    startTime: str
    messageCount: int


def ensure_dir() -> None:
    """Ensure the session directory exists."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    """Persist session data as JSON."""
    ensure_dir()
    file_path = SESSION_DIR / f"{session_id}.json"
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Load a session file from disk by its ID."""
    file_path = SESSION_DIR / f"{session_id}.json"
    if not file_path.exists():
        return None

    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> List[SessionMetadata]:
    """List all available saved sessions and parse their metadata."""
    ensure_dir()
    result: List[SessionMetadata] = []

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
    """Return the ID of the most recently modified session."""
    sessions = list_sessions()
    if not sessions:
        return None

    sessions.sort(key=lambda item: item.startTime, reverse=True)
    return sessions[0].id
