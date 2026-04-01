"""System prompt builder."""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path


def load_claude_md() -> str:
    """
    自底向上加载当前目录及父目录中的 CLAUDE.md 内容。

    Parameters:
        None

    Returns:
        str: 拼接后的项目指令文本，不存在时返回空字符串。

    Raises:
        None

    Examples:
        >>> isinstance(load_claude_md(), str)
        True
    """
    parts = []
    current_dir = Path.cwd().resolve()

    # 1) 从当前目录逐层向上查找，保持与参考实现相同的搜索顺序。
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


def get_git_context() -> str:
    """
    获取 Git 分支、最近提交与工作区状态。

    Parameters:
        None

    Returns:
        str: 格式化后的 Git 上下文，不在 Git 仓库时返回空字符串。

    Raises:
        None

    Examples:
        >>> isinstance(get_git_context(), str)
        True
    """

    def _run_git(command: str) -> str:
        """
        运行单条 git 命令并返回去空白后的结果。

        Parameters:
            command (str): git 子命令。

        Returns:
            str: 命令输出。

        Raises:
            subprocess.SubprocessError: 命令失败时抛出。

        Examples:
            >>> _ = _run_git("rev-parse --is-inside-work-tree")
        """
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

        result = f"\nGit branch: {branch}"
        if log:
            result += f"\nRecent commits:\n{log}"
        if status:
            result += f"\nGit status:\n{status}"
        return result
    except Exception:
        return ""


def build_system_prompt() -> str:
    """
    构造最终 System Prompt。

    Parameters:
        None

    Returns:
        str: 注入环境变量后的完整系统提示词。

    Raises:
        FileNotFoundError: system-prompt.md 缺失时抛出。

    Examples:
        >>> isinstance(build_system_prompt(), str)
        True
    """
    base_dir = Path(__file__).resolve().parent
    template = (base_dir / "system-prompt.md").read_text(encoding="utf-8")

    date_text = datetime.utcnow().strftime("%Y-%m-%d")
    platform_text = f"{platform.system().lower()} {platform.machine().lower()}"
    shell_text = os.environ.get("SHELL", "unknown")
    git_context = get_git_context()
    claude_md = load_claude_md()

    # 1) 用链式 replace 保持和参考实现一致的简单模板替换策略。
    return (
        template.replace("{{cwd}}", str(Path.cwd()))
        .replace("{{date}}", date_text)
        .replace("{{platform}}", platform_text)
        .replace("{{shell}}", shell_text)
        .replace("{{git_context}}", git_context)
        .replace("{{claude_md}}", claude_md)
    )
