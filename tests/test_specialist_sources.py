from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from app.models import ContentItem
from app.rules import ESMA_FINANCIAL_REPORTING_URL, FRC_CASE_OUTCOMES_URL, FRC_REPORTING_CASES_URL, PCAOB_NEWS_SEARCH_API
from app.sources import SourceFetchError
from app.specialist_sources import ESMAReportingSource, FRCEnforcementSource, FRCReportingCasesSource, PCAOBDocumentsSource, split_esma_cases


SINCE = datetime(2026, 10, 20, tzinfo=timezone.utc)
BODY = "The issuer misstated financial reporting and its auditor failed to obtain sufficient appropriate audit evidence. " * 8


def pcaob_record(identifier, published, *, effective="2026-01-01T00:00:00Z", kind="inspections"):
    import json
    attachment = json.dumps({"mediaUrl": f"https://assets.pcaobus.org/{identifier}.pdf"})
    return {"DocId": identifier, "Document": {
        "title": [identifier], "publicationdate": [published], "effectivedate": [effective],
        "inspectionyear": ["2025"], "name": [attachment], "enforcementorderdocument": [attachment],
        "enforcementordertypes": ["Settled Disciplinary Order"],
    }}


@pytest.mark.asyncio
@respx.mock
async def test_pcaob_scans_later_pages_for_new_publications_of_old_reports(monkeypatch):
    route = respx.post(PCAOB_NEWS_SEARCH_API).mock(side_effect=[
        Response(200, json={"Success": True, "Pagination": {"NofPages": 2}, "Results": [pcaob_record("older", "2026-09-01T00:00:00Z")]}),
        Response(200, json={"Success": True, "Pagination": {"NofPages": 2}, "Results": [pcaob_record("newly-public", "2026-10-22T15:10:00Z")]}),
    ])
    respx.get("https://assets.pcaobus.org/newly-public.pdf").respond(200, content=b"%PDF fixture")
    monkeypatch.setattr("app.specialist_sources._extract_pdf_text", lambda *_: BODY)
    items = await PCAOBDocumentsSource("inspections").fetch(SINCE)
    assert route.call_count == 2
    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 10, 22, 15, 10, tzinfo=timezone.utc)
    assert items[0].metadata["inspection_year"] == "2025"
    assert items[0].metadata["date_field"] == "publicationdate"
    assert items[0].article_text == BODY


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("payload", [
    {"Success": True, "Results": [], "Pagination": {"NofPages": 0}},
    {"Success": False, "Results": []},
    {"Success": True, "Results": [pcaob_record("bad-date", "invalid")], "Pagination": {"NofPages": 1}},
])
async def test_pcaob_unusable_metadata_is_not_zero_updates(payload):
    respx.post(PCAOB_NEWS_SEARCH_API).respond(200, json=payload)
    with pytest.raises(SourceFetchError):
        await PCAOBDocumentsSource("enforcement").fetch(SINCE)


@pytest.mark.asyncio
@respx.mock
async def test_pcaob_repeated_page_is_visible():
    row = pcaob_record("old", "2026-09-01T00:00:00Z")
    respx.post(PCAOB_NEWS_SEARCH_API).respond(200, json={"Success": True, "Results": [row], "Pagination": {"NofPages": 2}})
    with pytest.raises(SourceFetchError, match="repeated a page"):
        await PCAOBDocumentsSource("inspections").fetch(SINCE)


@pytest.mark.asyncio
@respx.mock
async def test_pcaob_pdf_failure_is_not_an_empty_success():
    row = pcaob_record("unreadable", "2026-10-22T00:00:00Z")
    respx.post(PCAOB_NEWS_SEARCH_API).respond(200, json={"Success": True, "Results": [row], "Pagination": {"NofPages": 1}})
    respx.get("https://assets.pcaobus.org/unreadable.pdf").respond(200, text="Human verification")
    with pytest.raises(SourceFetchError, match="not a PDF"):
        await PCAOBDocumentsSource("enforcement").fetch(SINCE)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("confirm", [True, False])
async def test_frc_requires_confirmation_that_case_date_means_publication(confirm):
    respx.get(FRC_CASE_OUTCOMES_URL).respond(200, text='''<table><tr>
        <td><input name="ids" value="case-1"></td><td>22 October 2026</td>
        <td>Example Audit LLP</td><td>Issuer plc</td><td>Audit Enforcement Procedure</td><td>Audit</td>
        </tr></table>''')
    note = "The date at the top of each case outcome refers to the publication date." if confirm else "This is the date sanctions took effect."
    respx.get(FRC_CASE_OUTCOMES_URL + "print/?ids=case-1").respond(200, text=f"<main>{note}<p>{BODY}</p></main>")
    if confirm:
        items = await FRCEnforcementSource().fetch(SINCE)
        assert len(items) == 1
        assert items[0].published_at.date().isoformat() == "2026-10-22"
        assert "Example Audit LLP" in items[0].title
        assert BODY.strip() in items[0].article_text
    else:
        with pytest.raises(SourceFetchError, match="publication date"):
            await FRCEnforcementSource().fetch(SINCE)


def test_esma_cases_use_publication_date_and_actual_pdf_pages(monkeypatch):
    texts = [
        "Table of Contents\nI. Decision ref EECS/0127-01 – Revenue\nII. Decision ref EECS/0127-02 – Borrowing costs",
        "Executive summary",
        "I. Decision ref EECS/0127-01 – Revenue recognition\nFinancial year end 30 June 2023\n" + BODY,
        "Continuation of the enforcer's decision. " + BODY,
        "II. Decision ref EECS/012 7-02 – Borrowing costs\nFinancial year end 31 December 2025\n" + BODY,
    ]
    monkeypatch.setattr("app.specialist_sources.PdfReader", lambda *_: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda text=text: text) for text in texts]))
    items = split_esma_cases(b"PDF", "Case extract", "https://www.esma.europa.eu/cases.pdf", SINCE)
    assert len(items) == 2
    assert items[0].title == "EECS/0127-01: Revenue recognition"
    assert items[1].title == "EECS/0127-02: Borrowing costs"
    assert [item.url for item in items] == ["https://www.esma.europa.eu/cases.pdf#page=3", "https://www.esma.europa.eu/cases.pdf#page=5"]
    assert all(item.published_at == SINCE for item in items)
    assert "Continuation" in items[0].article_text


@pytest.mark.asyncio
@respx.mock
async def test_esma_uses_the_date_from_its_own_document_row(monkeypatch):
    url = "https://www.esma.europa.eu/sites/default/files/cases.pdf"
    respx.get(ESMA_FINANCIAL_REPORTING_URL).respond(200, text=f'''<table>
        <tr><td><time datetime="2026-10-23T10:00:00+02:00"></time></td><td class="views-field-title"><a href="{url}">Extract of enforcement decisions</a></td></tr>
        <tr><td><time datetime="2025-10-23T10:00:00+02:00"></time></td><td class="views-field-title"><a href="/old.pdf">Older enforcement decisions</a></td></tr>
        </table>''')
    respx.get(url).respond(200, content=b"%PDF fixture")
    seen = []
    def split(content, title, pdf_url, published):
        seen.append(published)
        return [ContentItem(source="ESMA Financial Reporting", title=title, url=pdf_url, published_at=published, article_text=BODY)]
    monkeypatch.setattr("app.specialist_sources.split_esma_cases", split)
    assert len(await ESMAReportingSource().fetch(SINCE)) == 1
    assert seen == [datetime(2026, 10, 23, 8, tzinfo=timezone.utc)]


@pytest.mark.asyncio
@respx.mock
async def test_quarterly_crr_preserves_period_precision_and_ignores_balance_sheet_date():
    respx.get(FRC_REPORTING_CASES_URL).respond(200, text=f'''<table><tbody>
        <tr><th>Entity</th><td>Issuer plc</td></tr>
        <tr><th>Balance Sheet Date</th><td>31 December 2024</td></tr>
        <tr><th>Quarter Published</th><td>September 2026</td></tr>
        <tr><th>Case Summary / Press Notice</th><td><div class="rich-text">{BODY}</div></td></tr>
        <tr><th>Entity</th><td>No issues plc</td></tr>
        <tr><th>Quarter Published</th><td>September 2026</td></tr>
        <tr><th>Case Summary / Press Notice</th><td>N/A</td></tr>
        </tbody></table>''')
    items = await FRCReportingCasesSource().fetch(datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert len(items) == 1
    assert items[0].metadata["publication_label"] == "2026-Q3"
    assert items[0].metadata["date_precision"] == "quarter"
    assert items[0].metadata["topic_only"]
    assert items[0].metadata["period_anchor_only"]


def test_topic_json_does_not_publish_an_invented_exact_date(capsys):
    import json
    from inspect_fetch import _dump_json
    item = ContentItem(source="UK FRC Reporting Cases (Topics)", title="Quarterly case",
                       url="https://www.frc.org.uk/case", published_at=SINCE,
                       metadata={"topic_only": True, "date_precision": "quarter",
                                 "publication_label": "2026-Q3", "period_anchor_only": True})
    _dump_json("topics", SINCE, SINCE, [], [(item.source, [item], None)])
    record = json.loads(capsys.readouterr().out)["sources"][0]["items"][0]
    assert record["published_at"] is None
    assert record["publication_label"] == "2026-Q3"


def test_publication_date_requires_an_explicit_year():
    from app.specialist_sources import _publication
    with pytest.raises(SourceFetchError, match="missing its year"):
        _publication("October 22")
