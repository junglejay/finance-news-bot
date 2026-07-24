"""One-shot ingestion-to-delivery workflow with URL-only duplicate history."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .ai import BriefGenerator, OpenAICompatibleBriefGenerator
from .config import Settings
from .dingtalk import DingTalkNotifier
from .history import DeliveryHistory
from .models import ContentItem
from .rules import DEFAULT_WINDOW_HOURS, WEEKEND_WINDOW_HOURS
from .scoring import score_item, select_candidates
from .sources import PublicArticleReader, Source, build_sources


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    status: str
    report_date: str
    collected_count: int = 0
    candidate_count: int = 0
    readable_count: int = 0
    history_skipped_count: int = 0
    source_failures: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class BriefService:
    """Run one deep-reading report entirely in memory.

    The service stores no source excerpts, credentials, or generated reports. A small
    URL-and-timestamp history prevents repeated delivery after manual reruns or
    overlapping source timestamps while the default source window remains 24 hours.
    """

    def __init__(
        self,
        settings: Settings,
        sources: list[Source] | None = None,
        generator: BriefGenerator | None = None,
        notifier: DingTalkNotifier | None = None,
        article_reader: PublicArticleReader | None = None,
        history: DeliveryHistory | None = None,
    ) -> None:
        self.settings = settings
        self.sources = sources if sources is not None else build_sources(settings)
        self.generator = generator if generator is not None else OpenAICompatibleBriefGenerator(settings)
        self.notifier = notifier if notifier is not None else DingTalkNotifier(settings)
        self.article_reader = (
            article_reader
            if article_reader is not None
            else PublicArticleReader(settings.extra_article_domains)
        )
        self.history = (
            history
            if history is not None
            else DeliveryHistory(settings.delivery_history_file)
        )
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
        delivered_urls = self.history.delivered_urls(now)

        async def fetch_one(source: Source) -> tuple[Source, list[ContentItem], Exception | None]:
            try:
                items = await self._fetch_source_with_retries(source, since)
                return source, items, None
            except Exception as exc:  # Source failures are isolated from every other collector.
                return source, [], exc

        outcomes = await asyncio.gather(*(fetch_one(source) for source in self.sources))
        for source, items, error in outcomes:
            if error is not None:
                exc = error
                logger.warning("source %s failed: %s", source.name, exc)
                result.source_failures.append(f"{source.name}: {exc}")
                continue
            for item in items:
                if item.url in delivered_urls:
                    result.history_skipped_count += 1
                    continue
                collected.setdefault(item.checksum, item)

        result.collected_count = len(collected)
        logger.info("Collected %d unique articles", result.collected_count)

        # Official release feeds often expose only a respondent name or a broad
        # headline. Read allowed public bodies before classification so specific
        # accounting, reporting, and audit facts can participate in scoring.
        all_items = list(collected.values())
        try:
            await self.article_reader.enrich(all_items)
        except Exception as exc:
            logger.warning("pre-scoring public article reading failed: %s", exc)
            result.source_failures.append(f"Public article reader: {exc}")
        scored_items = [score_item(item, now) for item in all_items]

        candidates = select_candidates(scored_items)
        result.candidate_count = len(candidates)
        logger.info("Selected %d candidates after full-text-aware scoring", result.candidate_count)
        if not candidates:
            await self.notifier.send_no_update(report_date.isoformat())
            result.status = "no_update"
            return result

        result.readable_count = sum(bool(item.article_text) for item in candidates)
        logger.info("Successfully read full text from %d candidates", result.readable_count)

        failed_reads = [item for item in candidates if not item.article_text]
        if failed_reads:
            logger.warning("Failed to read full text from %d candidates:", len(failed_reads))
            for item in failed_reads:
                status = item.metadata.get("article_read_status", "unknown_status")
                logger.warning(
                    "  - %s '%s': %s (url: %s)",
                    item.source,
                    item.title[:60],
                    status,
                    item.url,
                )

        try:
            report = await self.generator.generate(report_date, candidates)
            await self.notifier.send_report(report)
            self.history.mark_delivered(
                (analysis.url for analysis in report.analyses),
                now,
            )
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
        # Keep the weekday branch for compatibility even though both defaults are
        # currently 24 hours.
        return now - timedelta(hours=WEEKEND_WINDOW_HOURS if weekday == 0 else DEFAULT_WINDOW_HOURS)

    async def _notify_fault_safely(self, error: str) -> None:
        try:
            await self.notifier.send_fault(f"深度阅读报告未生成：{error[:1_000]}")
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
