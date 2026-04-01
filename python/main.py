"""Root launcher for mini-claude Python project."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 1) 将 src 加入 sys.path，保证在根目录运行也能导入内部模块。
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.cli import main  # noqa: E402


if __name__ == "__main__":
    asyncio.run(main())
