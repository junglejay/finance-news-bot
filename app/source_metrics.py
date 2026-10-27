"""Thirty days of aggregate source counters; no article text or reports."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class SourceMetricsStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path) if path else None

    def record(self, now: datetime, rows: list[dict]) -> None:
        if self.path is None:
            return
        cutoff = (now - timedelta(days=29)).date().isoformat()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            days = payload.get("days", {})
            if not isinstance(days, dict):
                days = {}
        except (OSError, ValueError, AttributeError):
            days = {}
        days = {day: value for day, value in days.items() if cutoff <= day <= now.date().isoformat() and isinstance(value, dict)}
        day = days.setdefault(now.astimezone(timezone.utc).date().isoformat(), {})
        for row in rows:
            counters = day.setdefault(row["source"], {})
            if not isinstance(counters, dict):
                counters = day[row["source"]] = {}
            for key, value in row.items():
                if key != "source":
                    counters[key] = int(counters.get(key, 0)) + int(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "days": days}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def summary(self, now: datetime) -> dict:
        """Return machine counters, not an estimate of human relevance."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path else {}
            days = payload.get("days", {})
            if not isinstance(days, dict):
                days = {}
        except (OSError, ValueError, AttributeError):
            days = {}
        totals = {}
        cutoff = (now - timedelta(days=29)).date().isoformat()
        for day, sources in days.items():
            if not cutoff <= day <= now.date().isoformat() or not isinstance(sources, dict):
                continue
            for source, counters in sources.items():
                if not isinstance(counters, dict):
                    continue
                target = totals.setdefault(source, {})
                for key, value in counters.items():
                    if isinstance(value, int) and value >= 0:
                        target[key] = target.get(key, 0) + value
        for row in totals.values():
            row["fetch_success_rate"] = row.get("fetch_successes", 0) / row["runs"] if row.get("runs") else None
            row["readable_rate"] = row.get("readable_count", 0) / row["evaluated_count"] if row.get("evaluated_count") else None
            row["mean_fetch_seconds"] = row["fetch_milliseconds"] / row["runs"] / 1_000 if row.get("runs") and "fetch_milliseconds" in row else None
        return {"days": 30, "sources": totals, "note": "规则通过数与事件候选数均为逐次运行计数，不等于人工相关率或跨运行独有事件数。"}
