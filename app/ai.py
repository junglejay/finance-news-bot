"""OpenAI-compatible structured Chinese brief generation."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from typing import Protocol

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from pydantic import ValidationError

from .config import Settings
from .models import BriefItem, ContentItem, DailyBrief, ItemCategory, ResearchBrief


class BriefGenerationError(RuntimeError):
    pass


class BriefGenerator(Protocol):
    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DailyBrief: ...


SYSTEM_PROMPT = """你是审慎的中文金融研究编辑。你只能使用输入资料中明确给出的事实，绝不补充
未被来源支持的数字、指控、市场走势或结论。用简体中文，凝练、客观，避免确定性投资建议。
优先分析商品、期货、原油、黄金、财务舞弊和内部控制对美国市场研究的潜在影响。
严格输出 JSON 对象，不使用 Markdown 代码块，不输出任何额外文字。"""


def _candidate_payload(candidates: list[ContentItem]) -> list[dict[str, str | float | list[str]]]:
    return [
        {
            "source": item.source,
            "category": item.category.value,
            "title": item.title,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "summary": item.summary[:2_000],
            "score": item.score,
            "score_reasons": item.score_reasons,
        }
        for item in candidates
    ]


def _request_prompt(report_date: date, candidates: list[ContentItem]) -> str:
    schema = {
        "report_date": report_date.isoformat(),
        "key_judgements": ["不超过两条、仅从资料归纳的核心判断"],
        "commodity_items": [
            {
                "title": "必须逐字取自输入资料",
                "source": "必须逐字取自输入资料",
                "url": "必须逐字取自输入资料",
                "published_at": "必须逐字取自输入资料",
                "what_happened": "发生了什么",
                "why_it_matters": "为何重要",
                "market_impact": "对美国市场研究的潜在影响；若资料不足则明确说明",
            }
        ],
        "risk_items": [
            {
                "title": "必须逐字取自输入资料",
                "source": "必须逐字取自输入资料",
                "url": "必须逐字取自输入资料",
                "published_at": "必须逐字取自输入资料",
                "what_happened": "发生了什么",
                "why_it_matters": "为何重要",
                "market_impact": "对投资研究的潜在影响",
            }
        ],
        "research_item": {
            "title": "必须逐字取自输入资料",
            "source": "Google Scholar Alert",
            "url": "必须逐字取自输入资料",
            "published_at": "必须逐字取自输入资料",
            "research_question": "研究问题",
            "key_finding": "仅依据输入摘要；资料不足时写明资料未提供结论",
            "practical_implication": "实践启示",
        },
        "disclaimer": "本简报仅供研究参考，不构成投资建议。",
    }
    instructions = {
        "规则": [
            "key_judgements 生成 1 至 2 条。",
            "commodity_items 最多 3 条，仅使用 category=commodity 的资料。",
            "risk_items 最多 2 条，仅使用 category=risk 的资料。",
            "research_item 仅能使用 category=research 的资料；没有可用论文时必须为 null。",
            "每个条目的 title/source/url/published_at 必须与输入完全一致。",
            "不要为了凑数而创作内容；资料不足的栏目允许为空数组。",
        ],
        "目标 JSON": schema,
        "输入资料": _candidate_payload(candidates),
    }
    return json.dumps(instructions, ensure_ascii=False)


def _extract_json(content: str) -> dict:
    trimmed = content.strip()
    if trimmed.startswith("```"):
        trimmed = re.sub(r"^```(?:json)?\s*|\s*```$", "", trimmed, flags=re.IGNORECASE)
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise BriefGenerationError("AI gateway did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise BriefGenerationError("AI gateway returned JSON that is not an object")
    return parsed


def _validate_source_references(brief: DailyBrief, candidates: list[ContentItem]) -> None:
    source_by_url = {item.url: item for item in candidates}

    def validate(item: BriefItem | ResearchBrief, category: ItemCategory) -> None:
        source_item = source_by_url.get(item.url)
        if source_item is None:
            raise BriefGenerationError(f"brief references an unknown URL: {item.url}")
        if source_item.category != category:
            raise BriefGenerationError("brief placed an item in the wrong section")
        if item.title != source_item.title or item.source != source_item.source:
            raise BriefGenerationError("brief altered a source title or attribution")
        if item.published_at.date() != source_item.published_at.date():
            raise BriefGenerationError("brief altered a source publication date")

    for entry in brief.commodity_items:
        validate(entry, ItemCategory.COMMODITY)
    for entry in brief.risk_items:
        validate(entry, ItemCategory.RISK)
    if brief.research_item:
        validate(brief.research_item, ItemCategory.RESEARCH)


class OpenAICompatibleBriefGenerator:
    def __init__(self, settings: Settings, attempts: int = 3) -> None:
        self.settings = settings
        self.attempts = attempts

    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DailyBrief:
        if not self.settings.ai_api_key:
            raise BriefGenerationError("AI_API_KEY is not configured")
        if not candidates:
            raise BriefGenerationError("no eligible content items were collected")

        payload = {
            "model": self.settings.ai_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _request_prompt(report_date, candidates)},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with AsyncOpenAI(
                    api_key=self.settings.ai_api_key,
                    base_url=self.settings.ai_base_url,
                    timeout=60,
                    max_retries=0,
                ) as client:
                    response = await client.chat.completions.create(**payload)
                content = response.choices[0].message.content
                if not content:
                    raise BriefGenerationError("AI gateway returned an empty response")
                brief = DailyBrief.model_validate(_extract_json(content))
                if brief.report_date != report_date:
                    raise BriefGenerationError("AI gateway returned the wrong report date")
                _validate_source_references(brief, candidates)
                return brief
            except (
                APIConnectionError,
                APIStatusError,
                APIError,
                IndexError,
                TypeError,
                ValidationError,
                BriefGenerationError,
            ) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(attempt)
        raise BriefGenerationError(f"AI generation failed after {self.attempts} attempts: {last_error}")
