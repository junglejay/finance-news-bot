"""Small delivery history containing URLs and an aggregate Mainland-day marker."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


class DeliveryHistory:
    """Persist delivered URLs and daily quota state, never article text or reports."""

    def __init__(self, path: str, retention_days: int = 14) -> None:
        self.path = Path(path) if path else None
        self.retention_days = retention_days

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def delivered_urls(self, now: datetime) -> set[str]:
        if not self.enabled:
            return set()
        records, mainland_days = self._load()
        records = self._pruned_records(records, now)
        mainland_days = self._pruned_mainland_days(mainland_days, now)
        self._save(records, mainland_days)
        return set(records)

    def mainland_delivered_on(self, report_date: date, now: datetime) -> bool:
        """Return whether a Mainland-China article was already sent that local day."""
        if not self.enabled:
            return False
        records, mainland_days = self._load()
        records = self._pruned_records(records, now)
        mainland_days = self._pruned_mainland_days(mainland_days, now)
        self._save(records, mainland_days)
        return report_date.isoformat() in mainland_days

    def mark_delivered(
        self,
        urls: Iterable[str],
        delivered_at: datetime,
        mainland_report_date: date | None = None,
    ) -> None:
        if not self.enabled:
            return
        records, mainland_days = self._load()
        records = self._pruned_records(records, delivered_at)
        mainland_days = self._pruned_mainland_days(mainland_days, delivered_at)
        timestamp = delivered_at.astimezone(timezone.utc).isoformat()
        for url in urls:
            normalized = url.strip()
            if normalized:
                records[normalized] = timestamp
        if mainland_report_date is not None:
            mainland_days.add(mainland_report_date.isoformat())
        self._save(records, mainland_days)

    def _load(self) -> tuple[dict[str, str], set[str]]:
        if self.path is None or not self.path.exists():
            return {}, set()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            urls = payload.get("urls", {})
            mainland_days = payload.get("mainland_days", [])
        except (OSError, ValueError, AttributeError):
            return {}, set()
        if not isinstance(urls, dict):
            urls = {}
        if not isinstance(mainland_days, list):
            mainland_days = []
        return (
            {
                str(url): str(timestamp)
                for url, timestamp in urls.items()
                if isinstance(url, str) and isinstance(timestamp, str)
            },
            {str(day) for day in mainland_days if isinstance(day, str)},
        )

    def _pruned_records(self, records: dict[str, str], now: datetime) -> dict[str, str]:
        cutoff = now.astimezone(timezone.utc) - timedelta(days=self.retention_days)
        retained: dict[str, str] = {}
        for url, raw_timestamp in records.items():
            try:
                timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if timestamp.astimezone(timezone.utc) >= cutoff:
                retained[url] = timestamp.astimezone(timezone.utc).isoformat()
        return retained

    def _pruned_mainland_days(self, days: set[str], now: datetime) -> set[str]:
        cutoff = (now.astimezone(timezone.utc) - timedelta(days=self.retention_days)).date()
        retained: set[str] = set()
        for raw_day in days:
            try:
                parsed_day = date.fromisoformat(raw_day)
            except ValueError:
                continue
            if parsed_day >= cutoff:
                retained.add(parsed_day.isoformat())
        return retained

    def _save(self, records: dict[str, str], mainland_days: set[str]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "urls": records,
                    "mainland_days": sorted(mainland_days),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
