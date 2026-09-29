#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索服务层：search_materials / read_evidence。

- 关键词检索（默认，中文 + 公司代码 + 专有名词，含精确命中加权）。
- 可选语义检索（配置 embedding 后融合，未配置则明确为「关键词检索模式」）。
- 支持按日期 / 作者 / 公司 / 主题 / 来源类别 / 权限筛选。
- 相关度分数是检索相关度，不是「事实可信概率」。

本层显式接收 user 身份并在入口执行权限校验，权限不只在页面层。
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import config, storage
from .auth import require_authorized
from .models import Evidence

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens = _WORD_RE.findall(text)
    cjk = _CJK_RE.findall(text)
    tokens.extend(cjk)
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def _tf(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


def _idf(chunks: list[Evidence]) -> dict[str, float]:
    df: dict[str, int] = {}
    for c in chunks:
        for t in set(_tokenize(c.text)):
            df[t] = df.get(t, 0) + 1
    n = len(chunks) or 1
    return {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}


def _keyword_score(query: str, q_tf: dict[str, int], c: Evidence, idf: dict[str, float]) -> float:
    c_tf = _tf(_tokenize(c.text))
    q_terms = set(q_tf)
    common = q_terms & set(c_tf)
    if not common:
        return 0.0
    dot = sum(q_tf[t] * idf.get(t, 0.0) * c_tf[t] * idf.get(t, 0.0) for t in common)
    q_norm = math.sqrt(sum((q_tf[t] * idf.get(t, 0.0)) ** 2 for t in q_terms))
    c_norm = math.sqrt(sum((c_tf[t] * idf.get(t, 0.0)) ** 2 for t in c_tf))
    base = dot / (q_norm * c_norm) if q_norm and c_norm else 0.0

    # 精确命中加权：整句 / 长 token / 代码子串命中
    text = c.text.lower()
    boost = 0.0
    if query and query.lower() in text:
        boost += 0.35
    for t in q_terms:
        if len(t) >= 2 and t in text:
            boost += 0.05
    return min(1.0, base + boost)


def _apply_filters(chunks: list[Evidence], filters: dict | None) -> list[Evidence]:
    if not filters:
        return chunks
    docs = {d.document_id: d for d in storage.get_store().list_documents()}
    out = []
    for c in chunks:
        d = docs.get(c.document_id)
        if d is None:
            continue
        if filters.get("document_ids") and c.document_id not in filters["document_ids"]:
            continue
        if filters.get("source_categories") and d.source_category not in filters["source_categories"]:
            continue
        if filters.get("companies"):
            wanted = set(filters["companies"])
            if not (wanted & set(d.companies)):
                continue
        if filters.get("industry_topics"):
            wanted = set(filters["industry_topics"])
            if not (wanted & set(d.industry_topics)):
                continue
        if filters.get("authors") and d.author not in filters["authors"]:
            continue
        if filters.get("permission_scope") and d.permission_scope != filters["permission_scope"]:
            continue
        # 日期筛选：publish_date 已知才参与；未知日期单独计数
        out.append(c)
    return out


def _date_unknown_count(chunks: list[Evidence], filters: dict | None) -> int:
    if not filters or not (filters.get("date_from") or filters.get("date_to")):
        return 0
    docs = {d.document_id: d for d in storage.get_store().list_documents()}
    seen = set()
    for c in chunks:
        d = docs.get(c.document_id)
        if d and not d.publish_date:
            seen.add(c.document_id)
    return len(seen)


def _apply_date_filter(chunks: list[Evidence], filters: dict | None) -> list[Evidence]:
    if not filters or not (filters.get("date_from") or filters.get("date_to")):
        return chunks
    docs = {d.document_id: d for d in storage.get_store().list_documents()}
    out = []
    for c in chunks:
        d = docs.get(c.document_id)
        if d is None:
            continue
        pd = d.publish_date
        if not pd:
            continue  # 日期未知的材料不悄悄纳入截止日期筛选
        if filters.get("date_from") and pd < filters["date_from"]:
            continue
        if filters.get("date_to") and pd > filters["date_to"]:
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 可选语义检索（embedding）
# ---------------------------------------------------------------------------
_sem_cache: dict[str, list[float]] = {}


def _embed(texts: list[str]) -> list[list[float]] | None:
    import requests  # noqa: PLC0415
    key = config.get("EMBEDDING_API_KEY")
    base = (config.get("EMBEDDING_BASE_URL") or "").rstrip("/")
    model = config.get("EMBEDDING_MODEL")
    if not (key and base and model):
        return None
    try:
        resp = requests.post(
            f"{base}/v1/embeddings",
            json={"model": model, "input": texts},
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return [d["embedding"] for d in data["data"]]
    except Exception:  # noqa: BLE001
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _semantic_scores(query: str, chunks: list[Evidence]) -> dict[str, float]:
    """对 chunks 做 embedding 语义打分；失败返回空（降级为纯关键词）。"""
    out: dict[str, float] = {}
    qv = _embed([query])
    if not qv:
        return out
    qv = qv[0]
    to_embed: list[Evidence] = []
    for c in chunks:
        if c.evidence_id not in _sem_cache:
            to_embed.append(c)
    if to_embed:
        vecs = _embed([c.text for c in to_embed])
        if vecs:
            for c, v in zip(to_embed, vecs):
                _sem_cache[c.evidence_id] = v
    for c in chunks:
        v = _sem_cache.get(c.evidence_id)
        if v is not None:
            out[c.evidence_id] = _cosine(qv, v)
    return out


# ---------------------------------------------------------------------------
# 对外服务函数
# ---------------------------------------------------------------------------
def search_materials(
    query: str,
    user: str | None,
    *,
    top_k: int = 5,
    filters: dict | None = None,
    include_semantic: bool = True,
) -> dict[str, Any]:
    """检索证据。返回 {hits, mode, date_unknown_excluded}。

    权限在此校验；未授权抛 KnowledgeAuthError。
    """
    require_authorized(user, "搜索内部资料")

    query = (query or "").strip()
    if not query:
        return {"hits": [], "mode": config.retrieval_mode(), "date_unknown_excluded": 0}

    store = storage.get_store()
    all_chunks = store.all_chunks()
    docs = {d.document_id: d for d in store.list_documents()}

    candidates = _apply_filters(all_chunks, filters)
    date_unknown = _date_unknown_count(all_chunks, filters)
    candidates = _apply_date_filter(candidates, filters)

    if not candidates:
        return {"hits": [], "mode": config.retrieval_mode(), "date_unknown_excluded": date_unknown}

    q_tf = _tf(_tokenize(query))
    idf = _idf(candidates)
    kw_scores = {c.evidence_id: _keyword_score(query, q_tf, c, idf) for c in candidates}

    use_semantic = include_semantic and config.embedding_configured()
    sem_scores: dict[str, float] = {}
    if use_semantic:
        sem_scores = _semantic_scores(query, candidates)

    scored: list[tuple[float, Evidence, str]] = []
    for c in candidates:
        s = kw_scores.get(c.evidence_id, 0.0)
        if c.evidence_id in sem_scores:
            s = 0.6 * s + 0.4 * sem_scores[c.evidence_id]
        if s > 0:
            scored.append((s, c, "fused" if c.evidence_id in sem_scores else "keyword"))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(1, top_k)]

    hits = []
    for score, c, stype in top:
        d = docs.get(c.document_id)
        hits.append({
            "evidence_id": c.evidence_id,
            "document_id": c.document_id,
            "document_version": d.version if d else None,
            "title": d.title if d else "",
            "publish_date": d.publish_date if d else None,
            "author": d.author if d else None,
            "organization": d.organization if d else None,
            "source_category": d.source_category if d else None,
            "permission_scope": d.permission_scope if d else None,
            "text": c.text,
            "page_number": c.page_number,
            "position": c.position,
            "heading": c.heading,
            "speaker": c.speaker,
            "ts": c.ts,
            "score": round(score, 4),
            "score_type": stype,
            "provenance": d.provenance if d else None,
        })

    return {
        "hits": hits,
        "mode": "关键词+语义融合" if use_semantic else "关键词检索",
        "date_unknown_excluded": date_unknown,
    }


def read_evidence(
    evidence_id: str,
    user: str | None,
    *,
    context_radius: int = 2,
) -> dict[str, Any] | None:
    """读取一条证据及其前后文上下文，用于核对原文。"""
    require_authorized(user, "读取内部资料原文")

    store = storage.get_store()
    # 定位片段
    target: Evidence | None = None
    for c in store.all_chunks():
        if c.evidence_id == evidence_id:
            target = c
            break
    if target is None:
        return None

    doc = store.get_document(target.document_id)
    neighbors = store.list_chunks(target.document_id)
    idx = next((i for i, n in enumerate(neighbors) if n.evidence_id == evidence_id), 0)
    lo, hi = max(0, idx - context_radius), min(len(neighbors), idx + context_radius + 1)
    context = [
        {
            "seq": n.seq,
            "text": n.text,
            "page_number": n.page_number,
            "position": n.position,
            "speaker": n.speaker,
            "ts": n.ts,
        }
        for n in neighbors[lo:hi]
    ]

    return {
        "evidence": {
            "evidence_id": target.evidence_id,
            "text": target.text,
            "page_number": target.page_number,
            "position": target.position,
            "heading": target.heading,
            "speaker": target.speaker,
            "ts": target.ts,
        },
        "context": context,
        "document": {
            "document_id": doc.document_id if doc else target.document_id,
            "title": doc.title if doc else "",
            "version": doc.version if doc else None,
            "publish_date": doc.publish_date if doc else None,
            "author": doc.author if doc else None,
            "organization": doc.organization if doc else None,
            "source_category": doc.source_category if doc else None,
            "provenance": doc.provenance if doc else None,
        },
    }


def list_documents(user: str | None) -> list[dict]:
    """列出可访问的文档（元数据），供资料管理页使用。"""
    require_authorized(user, "查看内部资料列表")
    return [d.to_dict() for d in storage.get_store().list_documents()]


def document_evidence(document_id: str, user: str | None) -> dict | None:
    """读取某文档的元数据与全部原文片段（供原文预览）。"""
    require_authorized(user, "查看资料原文")
    store = storage.get_store()
    doc = store.get_document(document_id)
    if doc is None:
        return None
    return {
        "document": doc.to_dict(),
        "chunks": [c.to_dict() for c in store.list_chunks(document_id)],
    }
