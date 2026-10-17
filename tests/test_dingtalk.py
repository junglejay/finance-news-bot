from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from app.dingtalk import DingTalkDeliveryError, DingTalkNotifier, build_report_messages, signed_webhook_url
from app.models import ArticleAnalysis, DeepReadingReport, DingTalkBrief
from simulate_run import _print_delivery_preview


def _short_brief() -> dict:
    return {
        "headline": "示例公司：年报更正，整改仍待核验",
        "summary": "公司披露年报更正，尚未披露监管处罚。",
        "facts": ["更正涉及2025年年报。", "利润调整比例为119.20%，不是收入调整比例。"],
        "reading": "需区分错报影响与责任认定，现有资料不能证明已受到处罚。",
        "next_check": "核验更正报表与内控整改情况。",
    }


def _report() -> DeepReadingReport:
    return DeepReadingReport(
        report_date=date(2026, 7, 17),
        analyses=[
            ArticleAnalysis(
                title=f"Analysis {index}",
                source="Fixture source",
                url=f"https://example.test/{index}",
                published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
                core_thesis="A traceable fixture thesis.",
                fact_chain=["A source fact."],
                detailed_reading="This fixture explanation is intentionally long enough to satisfy the report schema requirements.",
                transmission_or_risk=["Monitor a later source release."],
                dingtalk_brief=DingTalkBrief.model_validate(_short_brief()),
            )
            for index in range(1, 4)
        ],
    )


def test_signed_webhook_url_adds_timestamp_and_signature() -> None:
    url = signed_webhook_url(
        "https://oapi.dingtalk.test/robot/send?access_token=token",
        "secret",
        timestamp_ms=1_700_000_000_000,
    )
    query = parse_qs(urlsplit(url).query)
    assert query["access_token"] == ["token"]
    assert query["timestamp"] == ["1700000000000"]
    assert len(query["sign"][0]) > 20


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_notifier_posts_markdown(settings) -> None:
    route = respx.post(url__regex=r"https://oapi\.dingtalk\.test/robot/send.*").mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"})
    )
    notifier = DingTalkNotifier(settings)

    result = await notifier.send_markdown("test", "# test")

    assert route.called
    assert result.status_code == 200
    assert json.loads(route.calls[0].request.content)["msgtype"] == "markdown"


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_rejection_raises(settings) -> None:
    respx.post(url__regex=r"https://oapi\.dingtalk\.test/robot/send.*").mock(
        return_value=Response(200, json={"errcode": 310000, "errmsg": "invalid signature"})
    )
    with pytest.raises(DingTalkDeliveryError, match="invalid signature"):
        await DingTalkNotifier(settings).send_markdown("test", "test")


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_webhook_can_be_used_without_a_signing_secret(settings) -> None:
    local_settings = replace(settings, dingtalk_secret="")
    route = respx.post("https://oapi.dingtalk.test/robot/send?access_token=test").mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"})
    )

    await DingTalkNotifier(local_settings).send_markdown("test", "# test")

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_notifier_sends_one_message_per_analysis(settings, capsys) -> None:
    route = respx.post(url__regex=r"https://oapi\.dingtalk\.test/robot/send.*").mock(
        return_value=Response(200, json={"errcode": 0, "errmsg": "ok"})
    )
    report = _report()
    original = report.model_dump()

    results = await DingTalkNotifier(settings).send_report(report)

    assert len(results) == 3
    assert len(route.calls) == 3
    payloads = [json.loads(call.request.content) for call in route.calls]
    _print_delivery_preview(report)
    preview = capsys.readouterr().out
    assert [payload["markdown"]["title"].endswith(f"{index}/3") for index, payload in enumerate(payloads, start=1)] == [True, True, True]
    for index, payload in enumerate(payloads, start=1):
        title, body = payload["markdown"]["title"], payload["markdown"]["text"]
        assert title in preview and body in preview
        assert f"https://example.test/{index}" in body
        assert f"{index}/3" in body
        assert body.startswith("### 新闻｜示例公司")
        assert "119.20%，不是收入调整比例" in body
        assert "尚未披露监管处罚" in body
        assert "### 深度解读" not in body
        assert report.analyses[index - 1].detailed_reading not in body
    assert report.model_dump() == original
    assert "### 深度解读" in report.to_markdown()
    assert "Analysis 1" in report.to_markdown()


@pytest.mark.asyncio
@respx.mock
async def test_missing_brief_is_rejected_before_any_message_is_sent(settings) -> None:
    report = _report()
    report.analyses[-1].dingtalk_brief = None

    with pytest.raises(DingTalkDeliveryError, match="Article 3 is missing dingtalk_brief"):
        await DingTalkNotifier(settings).send_report(report)
    assert not respx.calls


@pytest.mark.parametrize("field,limit", [("headline", 36), ("summary", 70), ("reading", 100), ("next_check", 70)])
def test_short_brief_rejects_excess_length_without_truncating(field, limit) -> None:
    data = _short_brief()
    data[field] = "字" * limit
    assert getattr(DingTalkBrief.model_validate(data), field) == data[field]
    data[field] += "字"
    with pytest.raises(ValidationError):
        DingTalkBrief.model_validate(data)


@pytest.mark.parametrize("facts", [[], ["事实"] * 4, ["字" * 66], ["   "]])
def test_short_brief_rejects_unreadable_fact_lists(facts) -> None:
    with pytest.raises(ValidationError):
        DingTalkBrief.model_validate({**_short_brief(), "facts": facts})


def test_legacy_report_can_be_read_without_sending_long_text() -> None:
    report = _report()
    legacy = report.model_dump(exclude={"analyses": {"__all__": {"dingtalk_brief"}}})
    restored = DeepReadingReport.model_validate(legacy)
    assert restored.analyses[0].title in restored.to_markdown()
    with pytest.raises(DingTalkDeliveryError, match="missing dingtalk_brief"):
        build_report_messages(restored)


@pytest.mark.asyncio
async def test_notifier_prints_when_webhook_is_not_configured(settings, capsys) -> None:
    local_settings = replace(settings, dingtalk_webhook="", dingtalk_secret="")

    result = await DingTalkNotifier(local_settings).send_markdown("test", "# 本地新闻测试")

    assert result.response["mode"] == "stdout"
    assert "本地新闻测试" in capsys.readouterr().out
