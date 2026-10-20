from __future__ import annotations

from datetime import datetime, timezone

import pytest
import respx
from httpx import Response

from app.foreign_sources import CPABSource, DOJPressSource, JournalOfAccountancySource
from app.rules import CPAB_ENFORCEMENT_REPORTS_URL, CPAB_EXPRESS_ARCHIVE_URL, DOJ_PRESS_RELEASES_API, FCA_NEWS_RSS, JOA_NEWS_URL
from app.sources import RSSSource, SourceFetchError, build_sources

SINCE = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
BODY = "The issuer overstated revenue in its financial statements and its auditor identified accounting fraud. " * 8


def _joa_card(path: str, published: str) -> str:
    return f'<article><time datetime="{published}"></time><h2 class="entry-title"><a href="{path}">Audit news</a></h2></article>'


@pytest.mark.asyncio
@respx.mock
async def test_joa_uses_first_publication_and_excludes_resurfaced_old_story():
    respx.get(JOA_NEWS_URL).respond(200, text=(
        _joa_card("/news/new/", "2026-10-15T00:00:00-04:00")
        + _joa_card("/news/old/", "2026-10-15T00:00:00-04:00")
    ))
    for path, date in [("new", "2026-10-15"), ("old", "2026-08-20")]:
        respx.get(f"https://www.journalofaccountancy.com/news/{path}/").respond(200, text=(
            f'<meta property="article:published_time" content="{date}T12:00:00-04:00">'
            f'<main><div class="entry-content"><p>{BODY}</p></div><p>Unrelated sidebar</p></main>'
        ))
    items = await JournalOfAccountancySource().fetch(SINCE)
    assert [item.url for item in items] == ["https://www.journalofaccountancy.com/news/new/"]
    assert items[0].published_at == datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
    assert items[0].article_text == BODY.strip()


@pytest.mark.asyncio
@respx.mock
async def test_joa_follows_pagination_and_rejects_missing_original_date():
    respx.get(JOA_NEWS_URL).respond(200, text=(
        _joa_card("/news/one/", "2026-10-15T00:00:00-04:00")
        + '<div class="nav-previous"><a href="/news/page/2/">Next 20</a></div>'
    ))
    respx.get("https://www.journalofaccountancy.com/news/one/").respond(200, text=(
        f'<meta property="article:published_time" content="2026-10-15T12:00:00-04:00"><p>{BODY}</p>'
    ))
    page = respx.get("https://www.journalofaccountancy.com/news/page/2/").respond(
        200, text=_joa_card("/news/older/", "2026-10-01T00:00:00-04:00")
    )
    assert len(await JournalOfAccountancySource().fetch(SINCE)) == 1
    assert page.called
    respx.get("https://www.journalofaccountancy.com/news/one/").respond(200, text=f"<p>{BODY}</p>")
    with pytest.raises(SourceFetchError, match="publication date"):
        await JournalOfAccountancySource().fetch(SINCE)


def _doj_row(identifier: str, day: int) -> dict:
    return {
        "uuid": identifier, "title": "Accounting &amp; reporting case",
        "url": f"https://www.justice.gov/opa/pr/{identifier}",
        "date": str(int(datetime(2026, 10, day, 12, tzinfo=timezone.utc).timestamp())),
        "body": f"<p>{BODY}</p>",
    }


@pytest.mark.asyncio
@respx.mock
async def test_doj_sorts_and_pages_all_releases_before_topic_filtering(monkeypatch):
    monkeypatch.setattr(DOJPressSource, "_PAGE_SIZE", 2)
    monkeypatch.setattr(DOJPressSource, "_REQUEST_INTERVAL", 0)
    calls = []

    def respond(request):
        assert request.url.params["sort"] == "date"
        assert request.url.params["direction"] == "DESC"
        page = int(request.url.params["page"])
        calls.append(page)
        rows = [[_doj_row("a", 19), _doj_row("b", 18)], [_doj_row("c", 13), _doj_row("d", 12)]][page]
        return Response(200, json={"metadata": {"resultset": {}}, "results": rows})

    respx.get(DOJ_PRESS_RELEASES_API).mock(side_effect=respond)
    items = await DOJPressSource().fetch(SINCE)
    assert calls == [0, 1]
    assert [item.url.rsplit("/", 1)[-1] for item in items] == ["a", "b", "c"]
    assert items[0].title == "Accounting & reporting case"
    assert items[0].article_text == BODY.strip()
    assert items[0].metadata["date_precision"] == "day"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("failure", ["repeated", "later_http_error", "invalid_date", "invalid_json_shape"])
async def test_doj_failure_is_visible_instead_of_partial_or_empty_success(monkeypatch, failure):
    monkeypatch.setattr(DOJPressSource, "_PAGE_SIZE", 1)
    monkeypatch.setattr(DOJPressSource, "_REQUEST_INTERVAL", 0)
    row = _doj_row("a", 19)
    if failure == "invalid_date":
        row["date"] = "not a date"
    responses = [Response(200, json={"metadata": {}, "results": [row]})]
    if failure == "repeated":
        responses *= 2
    elif failure == "later_http_error":
        responses.append(Response(500))
    elif failure == "invalid_json_shape":
        responses = [Response(200, json={"message": "unavailable"})]
    respx.get(DOJ_PRESS_RELEASES_API).mock(side_effect=responses)
    with pytest.raises(SourceFetchError):
        await DOJPressSource().fetch(SINCE)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("pdf_date", ["2026-10-15", "2026‐10‐15"])
async def test_cpab_uses_pdf_publication_not_effective_date_or_inspection_year(monkeypatch, pdf_date):
    respx.get(CPAB_ENFORCEMENT_REPORTS_URL).respond(200, text='''
        <table><tbody><tr><td>Effective September 1, 2026</td><td>2026</td>
        <td><a href="/report?DownloadDisclosureDocument=1">Link</a></td></tr></tbody></table>''')
    respx.get("https://cpab-ccrc.ca/report?DownloadDisclosureDocument=1").respond(200, content=b"%PDF fixture")
    monkeypatch.setattr("app.foreign_sources._extract_pdf_text", lambda *_: f"ENFORCEMENT REPORT - Example LLP Published {pdf_date} " + BODY)
    respx.get(CPAB_EXPRESS_ARCHIVE_URL).respond(200, text='''
        <div class="cpab-card-express"><a class="card-title" href="https://secure.campaigner.com/CSB/Public/archive.aspx?args=1">New inspection reports</a>
        <div class="card-footer">October 14, 2026</div></div>''')
    respx.get("https://secure.campaigner.com/CSB/Public/archive.aspx?args=1").respond(200, text=f"<p>{BODY}</p>")
    items = await CPABSource().fetch(SINCE)
    assert len(items) == 2
    assert items[0].published_at == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert items[0].title == "ENFORCEMENT REPORT - Example LLP"
    assert items[1].published_at == datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("content", ["<html>Verify you are human</html>", "<rss><broken>"])
async def test_rss_challenge_page_does_not_look_like_zero_news(content):
    respx.get(FCA_NEWS_RSS).respond(200, text=content)
    with pytest.raises(SourceFetchError, match="valid RSS"):
        await RSSSource("UK FCA", FCA_NEWS_RSS).fetch(SINCE)


@pytest.mark.asyncio
@respx.mock
async def test_readable_official_rss_with_unescaped_ampersand_is_preserved():
    respx.get(FCA_NEWS_RSS).respond(200, text='''<rss version="2.0"><channel><title>Official feed</title><item>
        <title>Accounting & auditing</title><link>https://www.fca.org.uk/news/example</link>
        <pubDate>Thu, 15 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>''')
    items = await RSSSource("UK FCA", FCA_NEWS_RSS).fetch(SINCE)
    assert len(items) == 1
    assert items[0].title == "Accounting & auditing"


def test_four_new_sources_are_enabled(settings):
    assert {"Journal of Accountancy", "US DOJ Accounting Enforcement", "UK FCA", "Canada CPAB"} <= {
        source.name for source in build_sources(settings)
    }
