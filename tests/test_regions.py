from datetime import datetime, timedelta, timezone

from app.models import ContentItem
from app.regions import article_region, source_window_hours
from app.service import BriefService
from inspect_fetch import _resolve_window


def test_foreign_window_matches_production_and_inspection_on_weekdays_and_monday():
    for now in [datetime(2026, 10, 19, 2, tzinfo=timezone.utc), datetime(2026, 10, 20, 2, tzinfo=timezone.utc)]:
        for source, hours in [("SEC Press Releases", 168), ("Canada CPAB", 168), ("Japan TDnet Audit & Reporting", 168), ("Journal of Accountancy", 168), ("HKEX Audit & Reporting", 24), ("AFRC Hong Kong", 24), ("巨潮资讯年报问询与审计回复", 24)]:
            assert source_window_hours(source, now.weekday()) == hours
            assert BriefService._window_start(now, now.weekday(), source) == now - timedelta(hours=hours)
            assert _resolve_window(None, now, "Asia/Shanghai", source)[0] == now - timedelta(hours=hours)
            assert _resolve_window(48, now, "Asia/Shanghai", source)[0] == now - timedelta(hours=48)


def test_region_does_not_treat_hong_kong_as_foreign_and_can_recognize_extra_feed_domain():
    for source, url, expected in [
        ("AFRC Hong Kong", "https://www.afrc.org.hk/news", "hong_kong"),
        ("中国证监会要闻", "https://www.csrc.gov.cn/news", "mainland"),
        ("Custom feed", "https://www.fca.org.uk/news/example", "foreign"),
        ("SEC impostor", "https://example.test/news", "foreign"),
        ("Secret report", "https://example.test/news", "other"),
    ]:
        item = ContentItem(source=source, title="Example", url=url, published_at=datetime.now(timezone.utc))
        assert article_region(item) == expected
