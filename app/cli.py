"""One-shot commands used by GitHub Actions and local automation."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from .config import Settings
from .service import BriefService


async def _run_once() -> int:
    settings = Settings.from_env()
    outcome = await BriefService(settings).run_once()
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0 if outcome.status == "success" else 1


def _run_simulate(extra_args: list[str]) -> int:
    """运行项目根目录下的 simulate_run.py，原样透传参数。

    simulate_run.py 是带详细打印的本地调试脚本（默认不推送钉钉），其参数
    （--hours / --no-ai / --env）仍由它自行解析；这里只提供与 run-once 一致
    的统一入口，避免重复实现一遍抓取/筛选/读正文/AI 生成的可视化逻辑。
    """
    project_root = Path(__file__).resolve().parent.parent
    script = project_root / "simulate_run.py"
    if not script.exists():
        print(f"找不到模拟脚本: {script}", file=sys.stderr)
        return 1
    completed = subprocess.run(
        [sys.executable, str(script), *extra_args],
        cwd=str(project_root),
    )
    return completed.returncode


def main() -> int:
    argv = sys.argv[1:]
    # simulate 的参数（--hours / --no-ai / --env …）原样透传给 simulate_run.py，
    # 不经 argparse 解析：argparse.REMAINDER 无法可靠捕获以 -- 开头的透传参数，
    # 手动拦截可保证任意参数都能传到子脚本，且 simulate --help 直接展示其真实用法。
    if argv and argv[0] == "simulate":
        return _run_simulate(argv[1:])

    parser = argparse.ArgumentParser(
        description="Commodity and risk intelligence bot automation commands"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once", help="执行一次完整生产任务（抓取->AI->钉钉）")
    sub.add_parser("simulate", help="模拟一次完整任务并展示每一步结果（不真正推送钉钉）")

    args = parser.parse_args(argv)
    if args.command == "run-once":
        return asyncio.run(_run_once())
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
