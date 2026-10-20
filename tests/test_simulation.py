from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import simulate_run
from app.history import DeliveryHistory
from app.models import ContentItem
from app.sources import Source


@pytest.mark.asyncio
async def test_simulation_uses_production_windows_and_read_only_delivery_history(settings, tmp_path, monkeypatch, capsys):
    now = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
    path = tmp_path / "history.json"
    settings = replace(settings, delivery_history_file=str(path))
    url = "https://example.test/already-sent"
    DeliveryHistory(str(path)).mark_delivered([url], now)
    original = path.read_bytes()
    calls = {}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    class FixtureSource(Source):
        def __init__(self, name):
            self.name = name

        async def fetch(self, since):
            calls[self.name] = since
            if self.name.startswith("SEC"):
                return [ContentItem(source=self.name, title="Already sent", url=url, published_at=now)]
            return []

    monkeypatch.setattr(simulate_run, "datetime", Clock)
    monkeypatch.setattr(simulate_run.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(simulate_run, "build_sources", lambda _: [FixtureSource("SEC Press Releases"), FixtureSource("HKEX Audit & Reporting")])
    assert await simulate_run.amain(Namespace(env="", hours=None, no_ai=True, ignore_history=False)) == 0
    output = capsys.readouterr().out
    assert "跳过 1 条" in output
    assert "AI 输入候选 0 条" in output
    assert calls == {"SEC Press Releases": now - timedelta(hours=168), "HKEX Audit & Reporting": now - timedelta(hours=24)}
    assert path.read_bytes() == original
