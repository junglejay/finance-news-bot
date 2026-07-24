from __future__ import annotations

from datetime import datetime, timezone

from app.models import ContentItem, ItemCategory
from app.rules import MAX_CANDIDATES, MAX_ITEMS_PER_SOURCE
from app.scoring import score_item, select_candidates


NOW = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)


def _item(
    title: str,
    summary: str,
    *,
    source: str = "General Business",
    url: str = "https://example.test/item",
    category: ItemCategory = ItemCategory.OTHER,
) -> ContentItem:
    return ContentItem(
        source=source,
        category=category,
        title=title,
        url=url,
        summary=summary,
        published_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )


def test_financial_reporting_fraud_routes_to_enforcement_category() -> None:
    item = score_item(
        _item(
            "Listed company charged with financial statement fraud",
            "The issuer allegedly used fictitious revenue and inflated earnings in its annual report.",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.FRAUD_ENFORCEMENT
    assert any(reason.startswith("财务造假：") for reason in item.score_reasons)


def test_full_text_can_supply_topic_context_missing_from_feed_headline() -> None:
    item = _item(
        "Example Holdings and its former controller",
        "Official enforcement release.",
        source="SEC Litigation Releases",
        url="https://www.sec.gov/enforcement-litigation/example",
    )
    item.article_text = (
        "The listed company filed annual financial statements that materially "
        "overstated revenue through fictitious sales and fraudulent financial reporting."
    )

    scored = score_item(item, now=NOW)

    assert scored.category == ItemCategory.FRAUD_ENFORCEMENT


def test_generic_retail_fraud_does_not_pass_topic_gate_even_from_sec() -> None:
    item = score_item(
        _item(
            "SEC forms new retail fraud working group",
            "The initiative addresses scams targeting individual investors.",
            source="SEC Press Releases",
            url="https://www.sec.gov/newsroom/press-releases/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER
    assert item.score == 0


def test_market_manipulation_penalty_is_not_financial_reporting_enforcement() -> None:
    item = score_item(
        _item(
            "中国证券监督管理委员会行政处罚决定书",
            "当事人通过多个账户操纵股票并受到行政处罚。",
            source="中国证监会行政处罚",
            url="https://www.csrc.gov.cn/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER


def test_chinese_financial_fraud_and_penalty_pass_compound_gate() -> None:
    item = score_item(
        _item(
            "上市公司财务造假被行政处罚",
            "公司连续三年通过虚构业务虚增收入，年度报告存在虚假记载。",
            source="中国证监会行政处罚",
            url="https://www.csrc.gov.cn/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.FRAUD_ENFORCEMENT
    assert "第一方权威来源" in item.score_reasons


def test_public_company_audit_failure_routes_to_audit_category() -> None:
    item = score_item(
        _item(
            "Audit firm sanctioned over listed company audit",
            "The auditor failed to obtain sufficient audit evidence and exercise professional skepticism.",
            source="UK FRC Audit Enforcement",
            url="https://www.frc.org.uk/example",
            category=ItemCategory.PUBLIC_COMPANY_AUDIT,
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.PUBLIC_COMPANY_AUDIT
    assert any(reason.startswith("审计质量：") for reason in item.score_reasons)


def test_generic_energy_audit_is_not_a_public_company_audit() -> None:
    item = score_item(
        _item(
            "Factory completes energy audit",
            "Engineers assessed electricity usage and equipment efficiency.",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER


def test_smsf_auditor_enforcement_is_not_a_listed_company_audit() -> None:
    item = score_item(
        _item(
            "ASIC acts against 36 SMSF auditors",
            "The regulator took action against self-managed super fund auditors.",
            source="ASIC Financial Reporting & Audit",
            url="https://asic.gov.au/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER
    assert item.score == 0


def test_generic_regulatory_meeting_is_not_promoted_by_body_boilerplate() -> None:
    item = _item(
        "中国证监会召开监管工作座谈会",
        "会议强调依法从严打击财务造假，提升上市公司财务报告质量。",
        source="中国证监会要闻",
        url="https://www.csrc.gov.cn/example",
    )
    item.article_text = "会议部署监管工作，要求压实责任并防范财务造假。"

    scored = score_item(item, now=NOW)

    assert scored.category == ItemCategory.OTHER
    assert scored.score == 0


def test_listed_company_internal_control_issue_routes_to_reporting_controls() -> None:
    item = score_item(
        _item(
            "Listed company discloses material weakness",
            "The annual report describes internal control over financial reporting deficiencies.",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.REPORTING_CONTROLS


def test_cninfo_inquiry_reply_keeps_document_type_when_quoting_fraud_context() -> None:
    item = score_item(
        _item(
            "关于2025年年度报告监管问询函的回复公告",
            "回复说明监管机构询问是否存在虚假记载及收入确认问题。",
            source="巨潮资讯年报问询与审计回复",
            url="https://static.cninfo.com.cn/example.pdf",
            category=ItemCategory.REPORTING_CONTROLS,
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.REPORTING_CONTROLS


def test_pcaob_inspection_reports_route_to_public_company_audit() -> None:
    item = score_item(
        _item(
            "PCAOB posts ten new inspection reports",
            "The reports contain inspection findings.",
            source="PCAOB",
            url="https://pcaobus.org/news-events/news-releases/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.PUBLIC_COMPANY_AUDIT


def test_dedicated_sec_aaer_accepts_respondent_only_headline() -> None:
    item = score_item(
        _item(
            "Example Corporation and Jane Doe, CPA",
            "Release No. 34-12345, AAER-9999",
            source="SEC Accounting & Auditing Enforcement",
            url="https://www.sec.gov/files/litigation/admin/example.pdf",
            category=ItemCategory.FRAUD_ENFORCEMENT,
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.FRAUD_ENFORCEMENT
    assert "会计审计执法专门栏目" in item.score_reasons


def test_dedicated_sec_aaer_drops_low_value_scheduling_orders() -> None:
    item = score_item(
        _item(
            "Example Corporation (Order Granting Extension of Time)",
            "Release No. 34-12345, AAER-9999",
            source="SEC Accounting & Auditing Enforcement",
            url="https://www.sec.gov/files/litigation/admin/example.pdf",
            category=ItemCategory.FRAUD_ENFORCEMENT,
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER
    assert item.score == 0


def test_audit_regulator_mou_is_not_selected_as_substantive_audit_news() -> None:
    item = score_item(
        _item(
            "AFRC signs MoU with overseas regulator",
            "The memorandum of understanding covers audit oversight cooperation.",
            source="AFRC Hong Kong",
            url="https://www.afrc.org.hk/example",
        ),
        now=NOW,
    )

    assert item.category == ItemCategory.OTHER
    assert item.score == 0


def test_scholar_alert_requires_a_focused_research_term() -> None:
    unrelated = score_item(
        _item(
            "Commodity futures paper",
            "Google Scholar Alert about oil prices.",
            source="Google Scholar Alert",
            category=ItemCategory.RESEARCH,
        ),
        now=NOW,
    )
    relevant = score_item(
        _item(
            "Audit quality after financial statement fraud",
            "A forensic accounting study of public companies and auditor independence.",
            source="Google Scholar Alert",
            url="https://example.test/research",
            category=ItemCategory.RESEARCH,
        ),
        now=NOW,
    )

    assert unrelated.category == ItemCategory.OTHER
    assert relevant.category == ItemCategory.RESEARCH


def test_candidate_selection_never_backfills_with_unrelated_items() -> None:
    relevant = score_item(
        _item(
            "Listed company financial statement fraud",
            "The issuer inflated earnings and restated its annual report.",
            url="https://example.test/relevant",
        ),
        now=NOW,
    )
    unrelated = [
        score_item(
            _item(
                f"Central bank statistical release {index}",
                "Interest rates and inflation data.",
                source="Official statistics",
                url=f"https://example.test/unrelated-{index}",
            ),
            now=NOW,
        )
        for index in range(20)
    ]

    candidates = select_candidates([relevant, *unrelated])

    assert candidates == [relevant]


def test_candidate_selection_caps_each_source_and_total() -> None:
    items: list[ContentItem] = []
    for source_index, source in enumerate(("SEC Press Releases", "PCAOB", "UK FRC Audit Enforcement")):
        for item_index in range(10):
            items.append(
                score_item(
                    _item(
                        f"Audit firm {source_index}-{item_index} sanctioned",
                        "A public company auditor failed to obtain sufficient audit evidence.",
                        source=source,
                        url=f"https://example.test/{source_index}-{item_index}",
                    ),
                    now=NOW,
                )
            )

    candidates = select_candidates(items)

    assert len(candidates) == MAX_CANDIDATES
    for source in {item.source for item in candidates}:
        assert sum(item.source == source for item in candidates) <= MAX_ITEMS_PER_SOURCE
