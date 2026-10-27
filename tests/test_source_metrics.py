import json
from datetime import datetime, timedelta, timezone

from app.source_metrics import SourceMetricsStore


def test_metrics_aggregate_runs_and_prune_old_days_without_article_data(tmp_path):
    path = tmp_path / "metrics.json"
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    store = SourceMetricsStore(str(path))
    row = {"source": "PCAOB", "runs": 1, "fetch_successes": 1, "raw_count": 6,
           "evaluated_count": 6, "readable_count": 6, "eligible_count": 2,
           "event_candidate_count": 1, "delivered_count": 1}
    store.record(now-timedelta(days=31), [row])
    store.record(now, [row])
    store.record(now, [{**row, "fetch_successes": 0, "raw_count": 0, "evaluated_count": 0,
                        "readable_count": 0, "eligible_count": 0, "event_candidate_count": 0, "delivered_count": 0}])
    payload = json.loads(path.read_text())
    assert set(payload["days"]) == {"2026-10-27"}
    assert set(payload["days"]["2026-10-27"]["PCAOB"]) == set(row)-{"source"}
    summary = store.summary(now)["sources"]["PCAOB"]
    assert summary["runs"] == 2
    assert summary["raw_count"] == 6
    assert summary["fetch_success_rate"] == 0.5
    assert summary["readable_rate"] == 1
    assert summary["delivered_count"] == 1


def test_missing_metrics_are_unknown_not_zero_rates(tmp_path):
    store = SourceMetricsStore(str(tmp_path/"metrics.json"))
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert store.summary(now)["sources"] == {}
    store.record(now, [{"source": "Quiet regulator", "runs": 1, "fetch_successes": 1,
                        "evaluated_count": 0, "readable_count": 0}])
    assert store.summary(now)["sources"]["Quiet regulator"]["readable_rate"] is None
