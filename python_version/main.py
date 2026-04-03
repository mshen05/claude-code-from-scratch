"""Compatibility launcher for running from repository root."""

from __future__ import annotations

import asyncio

from src.cli import main


if __name__ == "__main__":
    asyncio.run(main())
