"""Typed records exchanged between ingestion, analysis, and delivery."""

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
    # Kept in memory for this run only. It is deliberately excluded from the
    # duplicate checksum so a changed extraction does not create a new story.
    article_text: str = Field(default="", max_length=16_000)
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


class ArticleAnalysis(BaseModel):
    """A traceable, detailed interpretation of one original article."""

    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2_000)
    published_at: datetime
    core_thesis: str = Field(min_length=1, max_length=1_000)
    fact_chain: list[str] = Field(min_length=1, max_length=6)
    detailed_reading: str = Field(min_length=80, max_length=4_000)
    transmission_or_risk: list[str] = Field(min_length=1, max_length=4)
    limits_and_next_checks: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("published_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DeepReadingReport(BaseModel):
    report_date: date
    analyses: list[ArticleAnalysis] = Field(min_length=1, max_length=5)
    disclaimer: str = "本文仅供研究参考，不构成投资建议。"

    def to_markdown(self) -> str:
        lines = [f"# 商品与风控情报：深度阅读（{self.report_date.isoformat()}）", ""]
        for index, item in enumerate(self.analyses, start=1):
            lines.extend(
                [
                    f"## {index}. {item.title}",
                    f"来源：{item.source}｜发布日期：{item.published_at.date().isoformat()}｜[阅读原文]({item.url})",
                    "",
                    "### 核心命题",
                    item.core_thesis,
                    "",
                    "### 事实链",
                ]
            )
            lines.extend(f"- {fact}" for fact in item.fact_chain)
            lines.extend(["", "### 深度解读", item.detailed_reading, "", "### 影响传导与风险观察"])
            lines.extend(f"- {entry}" for entry in item.transmission_or_risk)
            if item.limits_and_next_checks:
                lines.extend(["", "### 反证、局限与后续核验"])
                lines.extend(f"- {entry}" for entry in item.limits_and_next_checks)
            lines.append("")
        lines.extend(["---", self.disclaimer])
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
