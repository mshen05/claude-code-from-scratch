"""Tool definitions and tool execution runtime."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Tool definition type for Claude API
ToolDef = Dict[str, Any]

# All tool definitions
tool_definitions: List[ToolDef] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file content with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to find and replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement string",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files matching a glob pattern. Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")',
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search from. Defaults to current directory.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a pattern in files. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file path to search in",
                },
                "include": {
                    "type": "string",
                    "description": 'Include glob (e.g., "*.ts", "*.py")',
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in milliseconds (default: 30000)",
                },
            },
            "required": ["command"],
        },
    },
]

MAX_RESULT_CHARS = 50_000

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
]


def read_file(input_data: Dict[str, Any]) -> str:
    try:
        file_path = Path(str(input_data["file_path"]))
        content = file_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        numbered = [f"{str(i + 1).rjust(4)} | {line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)
    except Exception as error:
        return f"Error reading file: {error}"


def write_file(input_data: Dict[str, Any]) -> str:
    try:
        file_path = Path(str(input_data["file_path"]))
        content = str(input_data["content"])
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote to {file_path}"
    except Exception as error:
        return f"Error writing file: {error}"


def edit_file(input_data: Dict[str, Any]) -> str:
    try:
        file_path = Path(str(input_data["file_path"]))
        old_string = str(input_data["old_string"])
        new_string = str(input_data["new_string"])
        content = file_path.read_text(encoding="utf-8")

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return f"Error: old_string found {count} times. Must be unique."

        updated = content.replace(old_string, new_string, 1)
        file_path.write_text(updated, encoding="utf-8")
        return f"Successfully edited {file_path}"
    except Exception as error:
        return f"Error editing file: {error}"


def list_files(input_data: Dict[str, Any]) -> str:
    try:
        pattern = str(input_data["pattern"])
        base_path = Path(str(input_data.get("path", os.getcwd())))

        files = []
        for path in base_path.glob(pattern):
            if not path.is_file():
                continue
            normalized = path.as_posix()
            if "/node_modules/" in normalized or "/.git/" in normalized:
                continue
            files.append(str(path.relative_to(base_path)).replace("\\", "/"))

        if not files:
            return "No files found matching the pattern."

        files.sort()
        display = files[:200]
        suffix = f"\n... and {len(files) - 200} more" if len(files) > 200 else ""
        return "\n".join(display) + suffix
    except Exception as error:
        return f"Error listing files: {error}"


def grep_search(input_data: Dict[str, Any]) -> str:
    try:
        pattern = str(input_data["pattern"])
        search_path = Path(str(input_data.get("path", ".")))
        include = input_data.get("include")
        include_pattern = str(include) if include else None
        regex = re.compile(pattern)

        files: List[Path] = []
        if search_path.is_file():
            files = [search_path]
        else:
            for file_path in search_path.rglob("*"):
                if file_path.is_file():
                    files.append(file_path)

        matches: List[str] = []
        for file_path in files:
            path_text = file_path.as_posix()
            if "/.git/" in path_text or "/node_modules/" in path_text:
                continue
            if include_pattern and not fnmatch.fnmatch(file_path.name, include_pattern):
                continue

            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            for index, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    rel = str(file_path).replace("\\", "/")
                    matches.append(f"{rel}:{index}:{line}")

        if not matches:
            return "No matches found."

        display = matches[:100]
        suffix = f"\n... and {len(matches) - 100} more matches" if len(matches) > 100 else ""
        return "\n".join(display) + suffix
    except re.error as error:
        return f"Error: invalid regex pattern ({error})"
    except Exception as error:
        return f"Error: {error}"


def run_shell(input_data: Dict[str, Any]) -> str:
    command = str(input_data.get("command", ""))
    timeout_ms = int(input_data.get("timeout", 30_000))

    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(timeout_ms / 1000.0, 0.001),
        )

        if completed.returncode == 0:
            return completed.stdout if completed.stdout else "(no output)"

        stdout = f"\nStdout: {completed.stdout}" if completed.stdout else ""
        stderr = f"\nStderr: {completed.stderr}" if completed.stderr else ""
        return f"Command failed (exit code {completed.returncode}){stdout}{stderr}"
    except subprocess.TimeoutExpired:
        return f"Command failed (timeout after {timeout_ms} ms)"
    except Exception as error:
        return f"Command failed: {error}"


def is_dangerous(command: str) -> bool:
    return any(pattern.search(command) for pattern in DANGEROUS_PATTERNS)


def needs_confirmation(tool_name: str, input_data: Dict[str, Any]) -> Optional[str]:
    if tool_name == "run_shell" and is_dangerous(str(input_data.get("command", ""))):
        return str(input_data.get("command", ""))

    if tool_name == "write_file" and not Path(str(input_data.get("file_path", ""))).exists():
        return f"write new file: {input_data.get('file_path', '')}"

    if tool_name == "edit_file" and not Path(str(input_data.get("file_path", ""))).exists():
        return f"edit non-existent file: {input_data.get('file_path', '')}"

    return None


def truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result

    keep_each = (MAX_RESULT_CHARS - 60) // 2
    omitted = len(result) - keep_each * 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {omitted} chars ...]\n\n"
        + result[-keep_each:]
    )


async def execute_tool(name: str, input_data: Dict[str, Any]) -> str:
    handlers = {
        "read_file": read_file,
        "write_file": write_file,
        "edit_file": edit_file,
        "list_files": list_files,
        "grep_search": grep_search,
        "run_shell": run_shell,
    }

    handler = handlers.get(name)
    if handler is None:
        return f"Unknown tool: {name}"

    result = await asyncio.to_thread(handler, input_data)
    return truncate_result(result)


