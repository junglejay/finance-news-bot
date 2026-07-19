"""One-shot commands used by GitHub Actions and local automation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import Settings
from .service import BriefService


async def _run_once() -> int:
    settings = Settings.from_env()
    outcome = await BriefService(settings).run_once()
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0 if outcome.status == "success" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Commodity and risk intelligence bot automation commands")
    parser.add_argument("command", choices=("run-once",))
    command = parser.parse_args().command
    if command == "run-once":
        return asyncio.run(_run_once())
    raise AssertionError(f"unhandled command: {command}")


if __name__ == "__main__":
    sys.exit(main())
