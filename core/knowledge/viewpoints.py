#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史观点记录：在原始证据之上建立可追溯的观点层。

观点字段：研究对象 / 提出者 / 来源类别 / 日期 / 核心判断 / 支持依据 / 成立条件 /
风险与反证 / 关联证据 / 状态 / 与前后观点的关系。

原则：
- AI 提取默认「待核验」，需人工确认；
- 外部研报不自动成为团队观点，AI 回答不自动成为老师认可的观点；
- 条件性判断保留条件；新观点不覆盖旧观点，历史可追溯。
"""

from __future__ import annotations

import re
from typing import Any

from . import llm, storage
from .auth import require_admin
from .models import (
    REL_CONFLICTS,
    REL_REVISES,
    REL_SUPPLEMENTS,
    SOURCE_AI,
    SOURCE_INTERNAL,
    VIEWPOINT_CONFIRMED,
    VIEWPOINT_PENDING,
    Viewpoint,
    new_id,
)

_EXTRACT_PROMPT = """你是金融研究助理。请从下面给定的内部材料片段中提取「历史观点」。
只提取明确表述的判断/观点，不编造。若无明确观点，返回空列表。

对每个观点输出 JSON，字段：
- research_subject：研究对象（公司 / 产业 / 主题）
- proposer：提出者（材料中的发言人或作者；未知填 null）
- viewpoint_date：观点日期（YYYY-MM-DD；未知填 null）
- core_judgment：核心判断（原文观点，条件性判断须保留「如果/若/前提」等条件）
- supporting_evidence：支持依据（引述原文关键论据）
- conditions：成立条件（无则填 null）
- risks：风险与反证（无则填 null）

只输出一个 JSON 对象，不要任何解释文字。格式：{{"viewpoints": [...]}}

【材料片段】
{text}
"""


def _parse_viewpoints(raw: dict | None) -> list[dict]:
    if not raw:
        return []
    items = raw.get("viewpoints")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict) and it.get("core_judgment")]


def add_viewpoint(
    user: str | None,
    *,
    research_subject: str,
    core_judgment: str,
    proposer: str | None = None,
    source_category: str = SOURCE_INTERNAL,
    viewpoint_date: str | None = None,
    supporting_evidence: str | None = None,
    conditions: str | None = None,
    risks: str | None = None,
    evidence_ids: list[str] | None = None,
    document_id: str | None = None,
    status: str = VIEWPOINT_PENDING,
    relationships: list[dict] | None = None,
    is_ai_generated: bool = False,
) -> str:
    require_admin(user, "创建历史观点")
    vp = Viewpoint(
        viewpoint_id=new_id("vp"),
        research_subject=research_subject,
        proposer=proposer,
        source_category=source_category,
        viewpoint_date=viewpoint_date,
        core_judgment=core_judgment,
        supporting_evidence=supporting_evidence,
        conditions=conditions,
        risks=risks,
        evidence_ids=list(evidence_ids or []),
        status=status,
        relationships=list(relationships or []),
        document_id=document_id,
        is_ai_generated=is_ai_generated,
        created_at=storage.now_iso(),
        updated_at=storage.now_iso(),
    )
    storage.get_store().add_viewpoint(vp)
    return vp.viewpoint_id


def extract_viewpoints_from_document(
    document_id: str,
    user: str | None,
    *,
    top_chunks: int = 8,
) -> dict[str, Any]:
    """用大模型从文档片段提取观点（默认「待核验」）。无模型时返回不可用状态。"""
    require_admin(user, "提取历史观点")
    if not llm.available():
        return {"ok": False, "reason": "模型不可用（未配置 ANTHROPIC_AUTH_TOKEN）", "created": []}

    store = storage.get_store()
    doc = store.get_document(document_id)
    if doc is None:
        return {"ok": False, "reason": "文档不存在", "created": []}
    chunks = store.list_chunks(document_id)[:top_chunks]
    if not chunks:
        return {"ok": False, "reason": "文档无可检索片段", "created": []}

    text = "\n\n".join(c.text for c in chunks)
    raw = llm.complete_json(_EXTRACT_PROMPT.format(text=text))
    if raw is None:
        err = llm.last_error() or {}
        return {"ok": False, "reason": f"提取失败：{err.get('detail', '模型调用失败')}", "created": []}
    items = _parse_viewpoints(raw)
    if not items:
        return {"ok": False, "reason": "模型返回中未包含明确观点（该材料可能无明确观点表述）", "created": []}

    created = []
    for it in items:
        vid = add_viewpoint(
            user,
            research_subject=it.get("research_subject") or doc.title,
            core_judgment=it.get("core_judgment") or "",
            proposer=it.get("proposer") or doc.author,
            source_category=doc.source_category,
            viewpoint_date=it.get("viewpoint_date") or doc.publish_date,
            supporting_evidence=it.get("supporting_evidence"),
            conditions=it.get("conditions"),
            risks=it.get("risks"),
            evidence_ids=[c.evidence_id for c in chunks],
            document_id=document_id,
            status=VIEWPOINT_PENDING,
            is_ai_generated=True,
        )
        created.append(vid)
    return {"ok": True, "created": created, "count": len(created)}


def get_viewpoint_history(
    subject: str | None = None,
    user: str | None = None,
    *,
    cutoff_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """查询历史观点（时间顺序）。支持按研究对象、截止日期、状态筛选。

    返回 {viewpoints: [...], latest: {...}|None, date_unknown: N}
    """
    require_admin(user, "查看历史观点")
    store = storage.get_store()
    vps = store.list_viewpoints()  # 已按日期降序

    out = []
    for vp in vps:
        if subject and subject.lower() not in (vp.research_subject or "").lower():
            continue
        if status and vp.status != status:
            continue
        if cutoff_date and vp.viewpoint_date and vp.viewpoint_date > cutoff_date:
            continue
        out.append(vp)

    # 时间正序（早 → 晚）便于看演进
    def _sort_key(vp: Viewpoint):
        return vp.viewpoint_date or vp.created_at or "9999"
    out.sort(key=_sort_key)

    unknown = sum(1 for vp in out if not vp.viewpoint_date)
    # 最近一次有记录的判断（按日期，取最后一条）
    dated = [vp for vp in out if vp.viewpoint_date]
    latest = dated[-1] if dated else (out[-1] if out else None)

    return {
        "viewpoints": [vp.to_dict() for vp in out],
        "latest": latest.to_dict() if latest else None,
        "date_unknown": unknown,
    }


def get_viewpoint(viewpoint_id: str, user: str | None) -> dict | None:
    require_admin(user, "查看历史观点")
    vp = storage.get_store().get_viewpoint(viewpoint_id)
    return vp.to_dict() if vp else None


def set_viewpoint_status(
    viewpoint_id: str,
    user: str | None,
    status: str,
    *,
    note: str | None = None,
) -> None:
    """确认 / 修订 / 推翻观点。修订或推翻时记录与旧观点的关系，保留旧记录。"""
    require_admin(user, "确认或修正历史观点")
    store = storage.get_store()
    vp = store.get_viewpoint(viewpoint_id)
    if vp is None:
        return
    if status == VIEWPOINT_CONFIRMED:
        vp.status = VIEWPOINT_CONFIRMED
    else:
        # 修订/推翻：生成一条新观点记录，旧观点保持不变（历史可追溯）
        new_vp = Viewpoint(
            viewpoint_id=new_id("vp"),
            research_subject=vp.research_subject,
            proposer=vp.proposer,
            source_category=vp.source_category,
            viewpoint_date=vp.viewpoint_date,
            core_judgment=vp.core_judgment,
            supporting_evidence=vp.supporting_evidence,
            conditions=vp.conditions,
            risks=vp.risks,
            evidence_ids=vp.evidence_ids,
            status=status,
            relationships=[{
                "type": REL_REVISES if status == "已修订" else REL_CONFLICTS,
                "target_viewpoint_id": vp.viewpoint_id,
                "note": note or "人工修正",
            }],
            document_id=vp.document_id,
            is_ai_generated=False,
            created_at=storage.now_iso(),
            updated_at=storage.now_iso(),
        )
        store.add_viewpoint(new_vp)
        # 旧观点标记为已修订/已推翻（保留原记录与内容）
        vp.status = status
        vp.updated_at = storage.now_iso()
    store.update_viewpoint(vp)


def update_viewpoint(user: str | None, viewpoint_id: str, **fields) -> None:
    require_admin(user, "修正历史观点")
    store = storage.get_store()
    vp = store.get_viewpoint(viewpoint_id)
    if vp is None:
        return
    for k, v in fields.items():
        if hasattr(vp, k):
            setattr(vp, k, v)
    vp.updated_at = storage.now_iso()
    store.update_viewpoint(vp)


def delete_viewpoint(user: str | None, viewpoint_id: str) -> None:
    require_admin(user, "删除历史观点")
    storage.get_store().delete_viewpoint(viewpoint_id)


def viewpoint_with_evidence(viewpoint_id: str, user: str | None) -> dict[str, Any]:
    """给定观点，解析其支持依据与相关证据（含可能冲突的材料）。"""
    require_admin(user, "查看观点依据")
    from .retriever import read_evidence, search_materials  # noqa: PLC0415

    store = storage.get_store()
    vp = store.get_viewpoint(viewpoint_id)
    if vp is None:
        return {"viewpoint": None, "supporting": [], "related": []}

    supporting = []
    for eid in (vp.evidence_ids or []):
        ev = read_evidence(eid, user)
        if ev:
            supporting.append(ev)

    related = search_materials(
        vp.research_subject or "", user, top_k=5
    ).get("hits", [])

    return {
        "viewpoint": vp.to_dict(),
        "supporting": supporting,
        "related": related,
    }
