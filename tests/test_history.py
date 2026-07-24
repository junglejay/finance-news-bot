from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.history import DeliveryHistory


def test_delivery_history_persists_urls_and_prunes_old_entries(tmp_path) -> None:
    path = tmp_path / "history.json"
    history = DeliveryHistory(str(path), retention_days=14)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)

    assert history.delivered_urls(now) == set()
    history.mark_delivered(["https://example.test/report"], now)

    assert DeliveryHistory(str(path)).delivered_urls(now) == {
        "https://example.test/report"
    }
    assert history.delivered_urls(now + timedelta(days=15)) == set()
