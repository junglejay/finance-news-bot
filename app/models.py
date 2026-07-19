"""Typed records exchanged between ingestion, analysis, storage, and delivery."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ItemCategory(StrEnum):
    COMMODITY = "commodity"
    RISK = "risk"
    RESEARCH = "research"
    OTHER = "other"


class ContentItem(BaseModel):
    source: str
    category: ItemCategory = ItemCategory.OTHER
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(default="", max_length=8_000)
    published_at: datetime
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    score_reasons: list[str] = Field(default_factory=list)

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def utc_datetimes(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @property
    def checksum(self) -> str:
        raw = f"{self.source}|{self.url.strip().lower()}|{self.title.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def external_id(self) -> str:
        return hashlib.sha256(self.url.strip().lower().encode("utf-8")).hexdigest()


class BriefItem(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2_000)
    published_at: datetime
    what_happened: str = Field(min_length=1, max_length=700)
    why_it_matters: str = Field(min_length=1, max_length=700)
    market_impact: str = Field(min_length=1, max_length=500)

    @field_validator("published_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ResearchBrief(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2_000)
    published_at: datetime
    research_question: str = Field(min_length=1, max_length=500)
    key_finding: str = Field(min_length=1, max_length=700)
    practical_implication: str = Field(min_length=1, max_length=500)

    @field_validator("published_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DailyBrief(BaseModel):
    report_date: date
    key_judgements: list[str] = Field(min_length=1, max_length=2)
    commodity_items: list[BriefItem] = Field(default_factory=list, max_length=3)
    risk_items: list[BriefItem] = Field(default_factory=list, max_length=2)
    research_item: ResearchBrief | None = None
    disclaimer: str = "本简报仅供研究参考，不构成投资建议。"

    def to_markdown(self) -> str:
        lines = [f"# 商品与风控情报晨报｜{self.report_date.isoformat()}", ""]
        lines.append("## 核心判断")
        for index, judgement in enumerate(self.key_judgements, start=1):
            lines.append(f"{index}. {judgement}")

        def add_item(item: BriefItem) -> None:
            published = item.published_at.date().isoformat()
            lines.extend(
                [
                    f"### [{item.title}]({item.url})",
                    f"来源：{item.source}｜发布日期：{published}",
                    f"- 发生了什么：{item.what_happened}",
                    f"- 为何重要：{item.why_it_matters}",
                    f"- 市场影响：{item.market_impact}",
                ]
            )

        lines.extend(["", "## 商品与期货"])
        if self.commodity_items:
            for item in self.commodity_items:
                add_item(item)
        else:
            lines.append("今日没有通过筛选的商品与期货新情报。")

        lines.extend(["", "## 财务舞弊与内部控制"])
        if self.risk_items:
            for item in self.risk_items:
                add_item(item)
        else:
            lines.append("今日没有通过筛选的财务舞弊或内控新情报。")

        lines.extend(["", "## 论文雷达"])
        if self.research_item:
            item = self.research_item
            lines.extend(
                [
                    f"### [{item.title}]({item.url})",
                    f"来源：{item.source}｜发布日期：{item.published_at.date().isoformat()}",
                    f"- 研究问题：{item.research_question}",
                    f"- 核心发现：{item.key_finding}",
                    f"- 实践启示：{item.practical_implication}",
                ]
            )
        else:
            lines.append("今日未收到新的相关 Scholar Alert，故不提供论文解读。")

        lines.extend(["", "---", self.disclaimer])
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
