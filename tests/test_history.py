from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.history import DeliveryHistory


def test_read_only_history_filters_without_pruning_or_updating_file(tmp_path):
    path = tmp_path / "history.json"
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    original = DeliveryHistory(str(path))
    original.mark_delivered(["https://example.test/sent"], now, now.date())
    before = path.read_bytes()
    history = DeliveryHistory(str(path), read_only=True)
    assert history.delivered_urls(now) == {"https://example.test/sent"}
    assert history.mainland_delivered_on(now.date(), now)
    history.mark_delivered(["https://example.test/simulation"], now)
    assert history.delivered_urls(now + timedelta(days=15)) == set()
    assert path.read_bytes() == before
    missing = tmp_path / "missing.json"
    assert DeliveryHistory(str(missing), read_only=True).delivered_urls(now) == set()
    assert not missing.exists()


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


def test_delivery_history_tracks_mainland_daily_quota_without_article_text(
    tmp_path,
) -> None:
    path = tmp_path / "history.json"
    history = DeliveryHistory(str(path), retention_days=14)
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    report_date = date(2026, 7, 24)

    assert not history.mainland_delivered_on(report_date, now)

    history.mark_delivered(
        ["https://static.cninfo.com.cn/example.pdf"],
        now,
        mainland_report_date=report_date,
    )

    reloaded = DeliveryHistory(str(path), retention_days=14)
    assert reloaded.mainland_delivered_on(report_date, now)
    assert not reloaded.mainland_delivered_on(date(2026, 7, 25), now)
    assert not reloaded.mainland_delivered_on(
        report_date,
        now + timedelta(days=15),
    )
