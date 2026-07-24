"""Small URL-only delivery history used to support a wider source window."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


class DeliveryHistory:
    """Persist delivered URLs without storing article text or generated reports."""

    def __init__(self, path: str, retention_days: int = 14) -> None:
        self.path = Path(path) if path else None
        self.retention_days = retention_days

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def delivered_urls(self, now: datetime) -> set[str]:
        if not self.enabled:
            return set()
        records = self._pruned_records(self._load(), now)
        self._save(records)
        return set(records)

    def mark_delivered(self, urls: Iterable[str], delivered_at: datetime) -> None:
        if not self.enabled:
            return
        records = self._pruned_records(self._load(), delivered_at)
        timestamp = delivered_at.astimezone(timezone.utc).isoformat()
        for url in urls:
            normalized = url.strip()
            if normalized:
                records[normalized] = timestamp
        self._save(records)

    def _load(self) -> dict[str, str]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            urls = payload.get("urls", {})
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(urls, dict):
            return {}
        return {
            str(url): str(timestamp)
            for url, timestamp in urls.items()
            if isinstance(url, str) and isinstance(timestamp, str)
        }

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

    def _save(self, records: dict[str, str]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "urls": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
