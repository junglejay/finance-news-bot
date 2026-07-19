"""Stateless one-shot ingestion-to-delivery workflow for GitHub Actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .ai import BriefGenerator, OpenAICompatibleBriefGenerator
from .config import Settings
from .dingtalk import DingTalkNotifier
from .models import ContentItem
from .scoring import score_item, select_candidates
from .sources import Source, build_sources


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    status: str
    report_date: str
    collected_count: int = 0
    candidate_count: int = 0
    source_failures: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class BriefService:
    """Run one morning brief entirely in memory.

    GitHub-hosted runners are ephemeral, so this service intentionally stores no source
    excerpts, credentials, delivery logs, or cross-run database state. It limits the source
    window to 24 hours (72 hours on Monday) and removes duplicate URLs/checksums in memory.
    """

    def __init__(
        self,
        settings: Settings,
        sources: list[Source] | None = None,
        generator: BriefGenerator | None = None,
        notifier: DingTalkNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.sources = sources if sources is not None else build_sources(settings)
        self.generator = generator if generator is not None else OpenAICompatibleBriefGenerator(settings)
        self.notifier = notifier if notifier is not None else DingTalkNotifier(settings)
        self._run_lock = asyncio.Lock()

    async def run_once(self, now: datetime | None = None) -> RunResult:
        if self._run_lock.locked():
            return RunResult(status="failed", report_date="", error="a morning brief run is already in progress")
        async with self._run_lock:
            return await self._run(now or datetime.now(timezone.utc))

    async def _run(self, now: datetime) -> RunResult:
        local_now = now.astimezone(ZoneInfo(self.settings.timezone))
        report_date = local_now.date()
        since = self._window_start(now, local_now.weekday())
        result = RunResult(status="running", report_date=report_date.isoformat())
        collected: dict[str, ContentItem] = {}

        for source in self.sources:
            try:
                items = await self._fetch_source_with_retries(source, since)
                for item in items:
                    scored = score_item(item, now)
                    collected.setdefault(scored.checksum, scored)
            except Exception as exc:  # Source failures are isolated from every other collector.
                logger.warning("source %s failed: %s", source.name, exc)
                result.source_failures.append(f"{source.name}: {exc}")

        result.collected_count = len(collected)
        candidates = select_candidates(list(collected.values()))
        result.candidate_count = len(candidates)

        try:
            brief = await self.generator.generate(report_date, candidates)
            await self.notifier.send_brief(brief)
            result.status = "success"
            return result
        except Exception as exc:
            logger.exception("morning brief run failed")
            result.status = "failed"
            result.error = str(exc)
            await self._notify_fault_safely(str(exc))
            return result

    @staticmethod
    def _window_start(now: datetime, weekday: int) -> datetime:
        # Monday includes the previous Friday after the scheduled cutoff plus the weekend.
        return now - timedelta(hours=72 if weekday == 0 else 24)

    async def _notify_fault_safely(self, error: str) -> None:
        try:
            await self.notifier.send_fault(f"简报未生成：{error[:1_000]}")
        except Exception as notification_error:
            logger.error("could not send DingTalk fault notification: %s", notification_error)

    @staticmethod
    async def _fetch_source_with_retries(source: Source, since: datetime) -> list[ContentItem]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                return await source.fetch(since)
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
        assert last_error is not None
        raise last_error
