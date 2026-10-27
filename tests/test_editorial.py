from datetime import datetime, timedelta, timezone

import pytest

from app.editorial import deduplicate_events, primary_references
from app.models import ContentItem, ItemCategory
from app.scoring import score_item, select_ai_candidates, select_candidates


NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
PRIMARY = "https://pcaobus.org/news-events/news-releases/news-release-detail/inspection-update"


def article(title, *, source="PCAOB", url=PRIMARY, text="Public company audit quality inspection findings."):
    return ContentItem(source=source, title=title, url=url, article_text=text * 10,
                       published_at=NOW, category=ItemCategory.PUBLIC_COMPANY_AUDIT, score=90)


@pytest.mark.parametrize("title", [
    "Statement From the Board on Passing of Former Board Member Example",
    "Readout: PCAOB Chairman Meets With Counterparts in Hong Kong",
    "PCAOB Standards and Emerging Issues Advisory Group to Meet on October 30",
])
def test_ceremonial_news_cannot_be_promoted_by_audit_boilerplate(title):
    item = score_item(article(title), NOW)
    assert item.category == ItemCategory.OTHER
    assert item.score == 0


@pytest.mark.parametrize("text", [
    "The company changed its auditor. There were no disagreements or reportable events.",
    "A routine change of auditor; an unqualified opinion was issued.",
    "Normal appointment for the next annual audit.",
    "Item 4.01 is amended and restated in its entirety as follows. The board approved a new auditor.",
    "The audit report did not contain an adverse opinion or a disclaimer of opinion. There were no disagreements or reportable events. The company did not consult on any matter that was the subject of a disagreement or a reportable event.",
])
def test_routine_auditor_changes_are_excluded(text):
    item = article("Example plc: Proposed change of auditor", source="HKEX Audit & Reporting", text=text)
    assert score_item(item, NOW).category == ItemCategory.OTHER


@pytest.mark.parametrize("text", [
    "There were no disagreements. The issuer identified a material weakness in financial reporting.",
    "The auditor could not continue because of unpaid audit fees and a scope limitation.",
    "会社は不適切な会計処理を調査している。",
    "The company restated its financial statements after the auditor found an error.",
])
def test_substantive_auditor_change_signals_survive(text):
    item = article("Example: change of auditor", source="HKEX Audit & Reporting", text=text)
    assert score_item(item, NOW).category == ItemCategory.PUBLIC_COMPANY_AUDIT


def test_explicit_primary_reference_merges_media_and_remembers_aliases():
    primary = article("PCAOB releases substantive inspection findings")
    media = article("Latest inspection results show improvement", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/coverage/")
    media.score = 200
    media.metadata["primary_references"] = [PRIMARY + "?utm_source=media"]
    selected = select_candidates([media, primary])
    assert selected == [primary]
    assert set(primary.metadata["event_aliases"]) == {primary.url, media.url}
    assert select_ai_candidates([media, primary]) == [primary]


def test_primary_citations_exclude_navigation_and_tracking():
    html = f'<nav><a href="{PRIMARY}">Navigation</a></nav><article><a href="{PRIMARY}?utm_source=site">Official release</a><a href="https://pcaobus.org/">Homepage</a></article>'
    assert primary_references(html, "https://example.test/article") == [PRIMARY]


def test_procedural_progress_is_not_merged_with_an_earlier_case():
    charges = article("SEC charges Company X with accounting fraud", source="SEC Press Releases", url="https://www.sec.gov/news/charges")
    sentence = article("Company X chief accountant sentenced", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/sentence/")
    sentence.metadata["primary_references"] = [charges.url]
    assert len(deduplicate_events([charges, sentence])) == 2


def test_later_media_rewrite_of_delivered_primary_is_skipped():
    media = article("Latest inspection results show improvement", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/new-url/")
    media.metadata["primary_references"] = [PRIMARY]
    assert deduplicate_events([media], {PRIMARY}) == []
    assert media.metadata["duplicate_of_delivered_event"]


def test_an_unreadable_primary_does_not_discard_readable_coverage():
    primary = article("PCAOB publishes an inspection update")
    primary.article_text = ""
    media = article("Latest inspection results", source="Thomson Reuters PCAOB", url="https://tax.thomsonreuters.com/news/coverage/")
    media.metadata["primary_references"] = [PRIMARY]
    assert deduplicate_events([primary, media]) == [media]


def test_distinct_case_pages_in_one_pdf_are_not_merged():
    a = article("Case one: revenue recognition", url="https://www.esma.europa.eu/cases.pdf#page=4")
    b = article("Case two: borrowing costs", url="https://www.esma.europa.eu/cases.pdf#page=9")
    assert len(deduplicate_events([a, b])) == 2


def test_roundup_does_not_bridge_distinct_primary_events():
    a = article("Audit firm A receives an inspection report", url=PRIMARY + "-a")
    b = article("Audit firm B receives an inspection report", url=PRIMARY + "-b")
    media = article("Several firms received inspection reports", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/roundup/")
    media.metadata["primary_references"] = [a.url, b.url]
    assert len(deduplicate_events([a, b, media])) == 3


def test_same_generic_title_does_not_merge_different_regulators():
    title = "Regulator releases new audit inspection findings"
    a = article(title, url="https://pcaobus.org/release-a")
    b = article(title, source="UK FRC Audit & Reporting", url="https://www.frc.org.uk/release-b")
    assert len(deduplicate_events([a, b])) == 2


def test_independent_reporting_is_not_discarded_just_for_citing_a_release():
    primary = article("PCAOB publishes substantive inspection findings")
    media = article("Exclusive interview reveals new evidence", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/exclusive/")
    media.metadata["primary_references"] = [PRIMARY]
    assert len(deduplicate_events([primary, media])) == 2
    assert deduplicate_events([media], {PRIMARY}) == [media]


def test_pcaob_subcollectors_share_the_same_source_quota():
    items = [article(f"Inspection report {index}", source="PCAOB Inspections", url=f"https://assets.pcaobus.org/{index}.pdf") for index in range(4)]
    items += [article(f"PCAOB original order {index}", source="PCAOB Enforcement", url=f"https://assets.pcaobus.org/order-{index}.pdf") for index in range(4)]
    assert len(select_candidates(items)) == 4


def test_official_report_links_unwrap_mail_security_tracking_without_visiting_it():
    from urllib.parse import quote
    pdf = "https://assets.pcaobus.org/pcaob-dev/docs/default-source/documents/staff-update.pdf"
    html = f'<article><a href="https://nam02.safelinks.protection.outlook.com/?url={quote(pdf, safe="")}&amp;data=private-tracking">Inspection results</a></article>'
    assert primary_references(html, "https://tax.thomsonreuters.com/news/article/") == [pdf]


def test_a_shared_original_pdf_connects_news_and_two_media_rewrites():
    pdf = "https://assets.pcaobus.org/inspection-results.pdf"
    primary = article("PCAOB inspection findings improve")
    joa = article("Latest inspection results show improvement", source="Journal of Accountancy", url="https://www.journalofaccountancy.com/news/coverage/")
    joa.metadata["primary_references"] = [PRIMARY, pdf]
    reuters = article("Public company audit quality continues to improve", source="Thomson Reuters PCAOB", url="https://tax.thomsonreuters.com/news/results/")
    reuters.metadata["primary_references"] = [pdf]
    assert deduplicate_events([reuters, primary, joa]) == [primary]
    assert pdf in primary.metadata["event_aliases"]
    assert reuters.url in primary.metadata["event_aliases"]


def test_quarterly_materials_never_enter_daily_candidates_or_ai():
    item = article("FRC substantive quarterly reporting case")
    item.metadata.update(topic_only=True, date_precision="quarter", publication_label="2026-Q3")
    assert select_candidates([score_item(item, NOW)]) == []
    assert select_ai_candidates([item]) == []
