#!/usr/bin/env python3
"""模拟一次完整任务：抓取 -> 打分筛选 -> 读正文 -> AI 生成报告。

默认【不】真正推送到钉钉（模拟模式），只打印"将会推送"的报告内容。
需要真实推送请用 `python -m app.cli run-once`。

用法:
    python simulate_run.py              # 生产窗口（24h，周一 72h）
    python simulate_run.py --hours 168  # 指定抓取窗口
    python simulate_run.py --no-ai      # 跳过 AI，只看抓取/筛选/读正文
"""

from __future__ import annotations

import argparse
import asyncio
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ai import OpenAICompatibleBriefGenerator
from app.config import Settings
from app.models import ContentItem
from app.rules import DEFAULT_WINDOW_HOURS, MAX_AI_INPUT_CANDIDATES, WEEKEND_WINDOW_HOURS
from app.scoring import score_item, select_candidates
from app.service import BriefService
from app.sources import PublicArticleReader, Source, build_sources


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _format_error(exc: Exception) -> str:
    detail = str(exc).strip()
    cause = exc.__cause__
    if cause is not None:
        cause_repr = f"{type(cause).__name__}: {cause!r}".strip()
        if cause_repr and cause_repr not in detail:
            detail = f"{detail} | 底层: {cause_repr}" if detail else cause_repr
    return detail or repr(exc)


def _bar(char: str = "=") -> str:
    return char * 78


async def _fetch_source(source: Source, since: datetime) -> tuple[str, list[ContentItem], str | None]:
    try:
        items = await BriefService._fetch_source_with_retries(source, since)
        return source.name, items, None
    except Exception as exc:
        return source.name, [], _format_error(exc)


async def amain(args: argparse.Namespace) -> int:
    _load_env_file(args.env)
    settings = Settings.from_env()

    now = datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo(settings.timezone))
    report_date = local_now.date()
    if args.hours is not None:
        since = now - timedelta(hours=args.hours)
        window_desc = f"最近 {args.hours} 小时（手动指定）"
    else:
        hours = WEEKEND_WINDOW_HOURS if local_now.weekday() == 0 else DEFAULT_WINDOW_HOURS
        since = now - timedelta(hours=hours)
        window_desc = f"最近 {hours} 小时（生产窗口）"

    print(_bar())
    print(f"模拟任务   报告日期: {report_date.isoformat()}   窗口: {window_desc}")
    print(f"since = {since.isoformat()}   now = {now.isoformat()}")
    print(_bar())

    # --- 1. 抓取 ---
    sources = build_sources(settings)
    raw = await asyncio.gather(*(_fetch_source(s, since) for s in sources))

    collected: dict[str, ContentItem] = {}
    per_source: list[tuple[str, int, str | None]] = []
    for name, items, error in raw:
        per_source.append((name, len(items), error))
        if error is not None:
            continue
        for item in items:
            scored = score_item(item, now)
            collected.setdefault(scored.checksum, scored)
    all_items = list(collected.values())

    print(f"\n【1】抓取原始新闻   源: {len(sources)} 个, 去重后 {len(all_items)} 条\n")
    for name, count, error in per_source:
        if error:
            print(f"  [FAIL] {name}: {error}")
        else:
            print(f"  [OK]   {name}: {count} 条")

    # --- 2. 打分 + 选中候选 ---
    candidates = select_candidates(all_items)
    candidate_ids = {item.external_id for item in candidates}
    ranked = sorted(all_items, key=lambda i: (i.score, i.published_at), reverse=True)

    print(f"\n{_bar('-')}")
    print(f"【2】打分并筛选候选   全部 {len(all_items)} 条 -> 选中 {len(candidates)} 条（上限 16）")
    print(_bar("-"))
    for idx, item in enumerate(ranked, 1):
        mark = "✔ 选中" if item.external_id in candidate_ids else "  落选"
        print(f"  {mark} | {item.score:6.1f} | {str(item.category):18s} | {item.source[:24]:24s} | {item.title[:70]}")

    # --- 3. 读正文 ---
    reader = PublicArticleReader(settings.extra_article_domains)
    candidates = await reader.enrich(candidates)
    readable = [c for c in candidates if c.article_text]

    print(f"\n{_bar('-')}")
    print(f"【3】读取公开正文   选中 {len(candidates)} 条 -> 读到正文 {len(readable)} 条")
    print(_bar("-"))
    for item in candidates:
        status = "read" if item.article_text else item.metadata.get("article_read_status", "unknown")
        flag = "✅" if item.article_text else "⚠️ "
        print(f"  {flag} {item.score:6.1f} | {str(item.category):18s} | {status:28s} | {item.source[:20]:20s} | {item.title[:50]}")

    if args.no_ai:
        print("\n已指定 --no-ai，跳过 AI 生成与推送模拟。")
        return 0

    # --- 4. AI 生成（最终推送内容） ---
    ai_input = sorted(readable, key=lambda i: (i.score, i.published_at), reverse=True)[:MAX_AI_INPUT_CANDIDATES]
    print(f"\n{_bar('-')}")
    print(f"【4】AI 生成   输入候选 {len(ai_input)} 条（有正文，按分数取前 {MAX_AI_INPUT_CANDIDATES}）")
    print(_bar("-"))
    for item in ai_input:
        print(f"  {item.score:6.1f} | {str(item.category):18s} | {item.source[:24]:24s} | {item.title[:70]}")

    print("\n调用 AI 网关生成报告中...")
    generator = OpenAICompatibleBriefGenerator(settings)
    try:
        report = await generator.generate(report_date, candidates)
    except Exception as exc:
        print(f"\nAI 生成失败: {_format_error(exc)}")
        print("前面三步结果仍有效；AI 失败时生产流程会改为发送故障通知。")
        return 1

    print(f"\n{_bar()}")
    print(f"【5】最终推送到钉钉的新闻   共 {len(report.analyses)} 篇（每篇一条钉钉消息）")
    print(f"    模拟模式：未调用钉钉 Webhook。如需真发请运行: python -m app.cli run-once")
    print(_bar())
    for idx, analysis in enumerate(report.analyses, 1):
        print(f"\n---------- 第 {idx}/{len(report.analyses)} 篇 ----------")
        print(f"标题: {analysis.title}")
        print(f"来源: {analysis.source}   日期: {analysis.published_at.date().isoformat()}")
        print(f"链接: {analysis.url}")
        print("\n[钉钉将发送的 markdown 内容]")
        print(report.article_to_markdown(analysis))

    # --- 漏斗汇总 ---
    print(f"\n{_bar()}")
    print("漏斗汇总:")
    print(f"  抓取去重 {len(all_items)} 条 -> 选中候选 {len(candidates)} 条 ->"
          f" 读到正文 {len(readable)} 条 -> AI 输入 {len(ai_input)} 条 ->"
          f" 推送 {len(report.analyses)} 篇")
    print(_bar())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟一次完整任务并展示每一步结果")
    parser.add_argument("--hours", type=int, help="覆盖抓取窗口（小时）；不传则用生产窗口")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 生成，只看抓取/筛选/读正文")
    parser.add_argument("--env", default=".env", help="环境变量文件路径（默认 .env）")
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
