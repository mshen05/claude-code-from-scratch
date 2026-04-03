"""System prompt builder."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# ─── CLAUDE.md loader ────────────────────────────────────────

def load_claude_md() -> str:
    parts: list[str] = []
    current_dir = Path.cwd().resolve()
    while True:
        target = current_dir / "CLAUDE.md"
        if target.exists():
            try:
                parts.insert(0, target.read_text(encoding="utf-8"))
            except Exception:
                pass
        if current_dir.parent == current_dir:
            break
        current_dir = current_dir.parent

    if not parts:
        return ""
    return "\n\n# Project Instructions (CLAUDE.md)\n" + "\n\n---\n\n".join(parts)


# ─── Git context ─────────────────────────────────────────────

def get_git_context() -> str:
    def _run_git(command: str) -> str:
        output = subprocess.check_output(
            f"git {command}",
            shell=True,
            stderr=subprocess.STDOUT,
            timeout=3,
            text=True,
        )
        return output.strip()

    try:
        branch = _run_git("rev-parse --abbrev-ref HEAD")
        log = _run_git("log --oneline -5")
        status = _run_git("status --short")
    except Exception:
        return ""

    result = f"\nGit branch: {branch}"
    if log:
        result += f"\nRecent commits:\n{log}"
    if status:
        result += f"\nGit status:\n{status}"
    return result


# ─── System prompt builder ───────────────────────────────────

def build_system_prompt() -> str:
    base_dir = Path(__file__).resolve().parent
    template = (base_dir / "system-prompt.md").read_text(encoding="utf-8")

    date_text = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    platform_text = f"{platform.system().lower()} {platform.machine().lower()}"
    shell_text = os.environ.get("SHELL", "unknown")
    git_context = get_git_context()
    claude_md = load_claude_md()

    return (
        template.replace("{{cwd}}", str(Path.cwd()))
        .replace("{{date}}", date_text)
        .replace("{{platform}}", platform_text)
        .replace("{{shell}}", shell_text)
        .replace("{{git_context}}", git_context)
        .replace("{{claude_md}}", claude_md)
    )
