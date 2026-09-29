#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库数据模型与枚举常量。

文档、检索片段（证据）、历史观点三类核心对象，及其允许状态 / 来源类别。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# 枚举常量
# ---------------------------------------------------------------------------
# 解析状态
PARSE_PENDING = "待解析"
PARSE_PROCESSING = "处理中"
PARSE_READY = "可检索"
PARSE_FAILED = "解析失败"
PARSE_NEEDS_OCR = "需要OCR"

PARSE_STATUSES = [PARSE_PENDING, PARSE_PROCESSING, PARSE_READY, PARSE_FAILED, PARSE_NEEDS_OCR]

# 来源类别
SOURCE_INTERNAL = "内部观点"
SOURCE_EXTERNAL = "外部研报"
SOURCE_ANNOUNCEMENT = "公告"
SOURCE_DIALOGUE = "对话"
SOURCE_AI = "AI生成内容"
SOURCE_OTHER = "其他"

SOURCE_CATEGORIES = [SOURCE_INTERNAL, SOURCE_EXTERNAL, SOURCE_ANNOUNCEMENT,
                     SOURCE_DIALOGUE, SOURCE_AI, SOURCE_OTHER]

# 观点状态
VIEWPOINT_PENDING = "待核验"
VIEWPOINT_CONFIRMED = "已确认"
VIEWPOINT_REVISED = "已修订"
VIEWPOINT_OVERTURNED = "已推翻"

VIEWPOINT_STATUSES = [VIEWPOINT_PENDING, VIEWPOINT_CONFIRMED, VIEWPOINT_REVISED, VIEWPOINT_OVERTURNED]

# 观点间关系
REL_SUPPLEMENTS = "补充"
REL_REVISES = "修订"
REL_CONFLICTS = "冲突"

RELATION_TYPES = [REL_SUPPLEMENTS, REL_REVISES, REL_CONFLICTS]

# 支持的文件类型
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt", ".rtf", ".json"}


def new_id(prefix: str) -> str:
    """生成稳定标识（代码生成，绝不交由模型生成）。"""
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Document:
    document_id: str
    title: str
    file_type: str
    file_hash: str
    version: int = 1
    publish_date: str | None = None
    uploaded_at: str | None = None
    author: str | None = None
    organization: str | None = None
    source_category: str = SOURCE_OTHER
    companies: list[str] = field(default_factory=list)
    industry_topics: list[str] = field(default_factory=list)
    permission_scope: str = "internal"
    parse_status: str = PARSE_PENDING
    error_message: str | None = None
    original_path: str | None = None
    provenance: str | None = None
    previous_version_id: str | None = None
    ai_generated_note: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Document":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Evidence:
    """检索片段（原文），带稳定 evidence_id 与定位信息。"""
    evidence_id: str
    document_id: str
    seq: int
    text: str
    page_number: int | None = None
    position: str | None = None
    heading: str | None = None
    speaker: str | None = None
    ts: str | None = None
    role: str | None = None            # question / answer / statement / view
    context_before: str | None = None
    context_after: str | None = None
    parent_evidence_id: str | None = None
    is_ai_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Viewpoint:
    viewpoint_id: str
    research_subject: str
    proposer: str | None
    source_category: str
    viewpoint_date: str | None
    core_judgment: str
    supporting_evidence: str | None
    conditions: str | None
    risks: str | None
    evidence_ids: list[str] = field(default_factory=list)
    status: str = VIEWPOINT_PENDING
    relationships: list[dict] = field(default_factory=list)
    document_id: str | None = None
    is_ai_generated: bool = True
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Viewpoint":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
