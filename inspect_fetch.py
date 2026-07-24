#!/usr/bin/env python3
"""查看各订阅源抓取到的原始新闻。

只做 fetch（标题/链接/摘要/日期/分类），不调用 AI、不发钉钉、不读取正文，
方便确认每个源当前能抓到哪些条目。

用法:
    python inspect_fetch.py                  # 按生产窗口抓取（最近 24h，周一 72h）
    python inspect_fetch.py --hours 168      # 抓最近 7 天，便于看清各源都有什么
    python inspect_fetch.py --source 证监会  # 只抓名称含“证监会”的源（可多次指定）
    python inspect_fetch.py --json           # 输出 JSON，便于管道处理
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# 让中文在 Windows 控制台正常输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 确保能 import app（脚本放在项目根目录直接运行）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import Settings
from app.models import ContentItem
from app.rules import DEFAULT_WINDOW_HOURS, WEEKEND_WINDOW_HOURS
from app.sources import Source, build_sources


def _load_env_file(path: str) -> None:
    """简单加载 .env（KEY=VALUE），不覆盖已存在的环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _resolve_window(hours: int | None, now: datetime, timezone_name: str) -> tuple[datetime, str]:
    """返回 (since, 窗口描述)。未指定 hours 时按生产逻辑：周一 72h，其余 24h。"""
    if hours is not None:
        return now - timedelta(hours=hours), f"最近 {hours} 小时（手动指定）"
    local_weekday = now.astimezone(ZoneInfo(timezone_name)).weekday()
    window_hours = WEEKEND_WINDOW_HOURS if local_weekday == 0 else DEFAULT_WINDOW_HOURS
    return now - timedelta(hours=window_hours), f"最近 {window_hours} 小时（生产窗口）"


def _format_error(exc: Exception) -> str:
    """把异常及其 __cause__ 都展示出来，避免 'RSS fetch failed:' 后面空白。"""
    detail = str(exc).strip()
    cause = exc.__cause__
    if cause is not None:
        cause_repr = f"{type(cause).__name__}: {cause!r}".strip()
        if cause_repr and cause_repr not in detail:
            detail = f"{detail} | 底层: {cause_repr}" if detail else cause_repr
    if not detail:
        detail = repr(exc)
    return f"{type(exc).__name__}: {detail}"


async def _fetch_one(source: Source, since: datetime, attempts: int = 3) -> tuple[str, list[ContentItem], str | None]:
    """抓取单个源，失败重试。返回 (源名称, 条目列表, 错误信息)。"""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            items = await source.fetch(since)
            return source.name, items, None
        except Exception as exc:  # 单源失败不影响其他源
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(0.5 * attempt)
    assert last_error is not None
    return source.name, [], _format_error(last_error)


def _print_report(
    window_desc: str,
    since: datetime,
    fetched_at: datetime,
    sources: list[Source],
    results: list[tuple[str, list[ContentItem], str | None]],
) -> None:
    total = ok = fail = 0
    bar = "=" * 78
    print(bar)
    print(f"抓取原始新闻   窗口: {window_desc}")
    print(f"since = {since.isoformat()}   fetched_at = {fetched_at.isoformat()}")
    print(f"信息源: {len(sources)} 个")
    print(bar)

    for idx, (source, (name, items, error)) in enumerate(zip(sources, results), 1):
        if error is not None:
            fail += 1
            print(f"\n[{idx}/{len(sources)}] {name}   [FAIL]   {error}")
            continue
        ok += 1
        total += len(items)
        print(f"\n[{idx}/{len(sources)}] {name}   [OK]   {len(items)} 条")
        for item in items:
            pub = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
            print(f"  - {item.title}")
            print(f"    {pub} | {item.category} | {item.url}")
            if item.summary:
                summary = " ".join(item.summary.split())
                preview = summary[:120] + ("…" if len(summary) > 120 else "")
                print(f"    摘要: {preview}")

    print("\n" + bar)
    print(f"汇总: {len(sources)} 个源, 成功 {ok}, 失败 {fail}, 共 {total} 条原始新闻")
    print(bar)


def _dump_json(
    window_desc: str,
    since: datetime,
    fetched_at: datetime,
    sources: list[Source],
    results: list[tuple[str, list[ContentItem], str | None]],
) -> None:
    payload: dict = {
        "window": window_desc,
        "since": since.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "sources": [],
    }
    for (name, items, error) in results:
        entry: dict = {"name": name}
        if error is not None:
            entry["status"] = "error"
            entry["error"] = error
        else:
            entry["status"] = "ok"
            entry["count"] = len(items)
            entry["items"] = [
                {
                    "title": it.title,
                    "url": it.url,
                    "category": str(it.category),
                    "published_at": it.published_at.isoformat(),
                    "summary": it.summary,
                }
                for it in items
            ]
        payload["sources"].append(entry)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def amain(args: argparse.Namespace) -> int:
    _load_env_file(args.env)
    settings = Settings.from_env()

    fetched_at = datetime.now(timezone.utc)
    since, window_desc = _resolve_window(args.hours, fetched_at, settings.timezone)

    sources = build_sources(settings)
    if args.source:
        keys = [k.lower() for k in args.source]
        sources = [s for s in sources if any(k in s.name.lower() for k in keys)]
    if not sources:
        print("没有匹配的信息源。", file=sys.stderr)
        return 1

    results = await asyncio.gather(*(_fetch_one(s, since) for s in sources))

    if args.json:
        _dump_json(window_desc, since, fetched_at, sources, results)
    else:
        _print_report(window_desc, since, fetched_at, sources, results)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查看各信息源抓取到的原始新闻")
    parser.add_argument("--hours", type=int, help="覆盖抓取窗口（小时）；不传则用生产窗口")
    parser.add_argument(
        "--source",
        action="append",
        help="只抓名称包含该子串的源（可多次指定，如 --source 证监会 --source SEC）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于管道处理")
    parser.add_argument("--env", default=".env", help="环境变量文件路径（默认 .env）")
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
