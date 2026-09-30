#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于历史材料的新事件点评。

流程：提取新事件 → 检索历史观点与证据（同时查支持与冲突，不只看多）→
读上下文 → 比较新信息对原判断的影响 → 输出带引用的点评。

新材料若为用户粘贴，标注「用户提供，来源未独立核验」；未命中历史观点时
不编造「我们此前认为」。生成的点评来源类别为「AI生成内容」。
"""

from __future__ import annotations

import re
from typing import Any

from . import llm, storage
from .auth import require_admin
from .models import SOURCE_AI, new_id
from .retriever import search_materials
from .viewpoints import get_viewpoint_history

_COMPARE_PROMPT = """你是金融研究助理。基于给定的官方数据、历史观点与原始证据，输出一段克制的点评。

要求：
1. 先给 2—3 句核心判断；再写关键数据变化；再写「与可核实的事前预期或历史观点的差异」（没有就明确写缺失，不编造预期差）。
2. 只给出能被证据支持的两种可能解释，并各自写明需要什么数据才能验证；机制解释一律标注「待验证假设」。
3. 区分：官方数据事实 / 历史观点（标注机构与日期）/ 本次分析假设。
4. 引用编号只能来自证据列表；无合格证据的栏目直接省略，不输出空白框架，不堆砌无关材料。
5. 结论克制：环比微降不等于收缩；生产改善快于订单只作为观察，不据此认定被动累库或周期拐点。
6. 若无历史观点，明确写「本次已接入资料中没有可对照的历史观点」，不写「我们此前认为」。

【官方数据】
{event_text}

【历史观点】
{viewpoints}

【检索到的证据】
{evidence}
"""


def _extract_subject(event_text: str) -> str | None:
    """轻量研究对象抽取（关键词，非实体识别）。无命中返回 None。"""
    keywords = ["碳化硅", "光纤", "半导体", "存储", "锂电", "光伏", "风电", "储能",
                "军工", "医药", "地产", "银行", "券商", "保险", "有色", "钢铁", "煤炭",
                "宏观", "利率", "汇率", "通胀", "流动性", "财政"]
    for k in keywords:
        if k in (event_text or ""):
            return k
    return None


def _extract_event_date(event_text: str) -> str | None:
    m = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?", event_text or "")
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    return None


# 地区别名（用于排除与研究地区不相关的材料）
_REGION_ALIASES = {
    "中国": ["china", "chinese", "中国"],
    "美国": ["us", "usa", "united states", "美国", "federal reserve", "fed "],
}


def _filter_by_region(hits: list[dict], region: str | None) -> tuple[list[dict], list[dict]]:
    """仅保留与研究地区相关的证据；返回 (保留, 被排除及原因)。"""
    if not region or not hits:
        return hits, []
    aliases = [a.lower() for a in _REGION_ALIASES.get(region, [region])]
    kept, excluded = [], []
    for h in hits:
        title = (h.get("title") or "").lower()
        text = (h.get("text") or "").lower()
        if any(a in title or a in text for a in aliases):
            kept.append(h)
        else:
            excluded.append({
                "document_id": h.get("document_id"),
                "title": h.get("title"),
                "reason": f"与研究地区「{region}」不相关",
            })
    return kept, excluded


def build_commentary(
    event_text: str,
    user: str | None,
    *,
    subject: str | None = None,
    company: str | None = None,
    topic: str | None = None,
    cutoff_date: str | None = None,
    document_ids: list[str] | None = None,
    top_k: int = 6,
    save: bool = False,
    is_simulated: bool = False,
    event_date: str | None = None,
    event_facts: dict | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """执行一次事件点评，返回结构化结果（dict），可另存为观点记录。"""
    require_admin(user, "生成事件点评")

    event_text = (event_text or "").strip()
    if not event_text:
        return {"ok": False, "error": "未提供新事件文本"}

    subject = subject or _extract_subject(event_text)
    # 显式传入的事件日期优先；未提供时才从文本识别（避免误用历史报告日期）
    event_date = event_date or _extract_event_date(event_text)

    # 1. 检索历史观点（有截止日期时只取当时已形成/已公开的观点）
    vp_result = get_viewpoint_history(subject, user, cutoff_date=cutoff_date)
    viewpoints = vp_result["viewpoints"]

    # 2. 检索证据（同时覆盖支持与冲突，检索本身中性）
    filters: dict[str, Any] = {}
    if cutoff_date:
        filters["date_to"] = cutoff_date
    if document_ids:
        filters["document_ids"] = document_ids
    if topic:
        filters["industry_topics"] = [topic]
    search = search_materials(event_text[:400] or subject or "", user, top_k=top_k, filters=filters)
    hits = search["hits"]
    # 地区相关性筛选：排除与研究地区无关的材料（如美国就业/CPI/美联储），并记录原因
    hits, excluded_hits = _filter_by_region(hits, region)

    # 3. 组装检索到的证据（编号供模型引用）
    numbered = []
    for i, h in enumerate(hits, 1):
        loc = f"p.{h['page_number']}" if h.get("page_number") else (h.get("position") or "—")
        numbered.append(
            f"[证据:{i}] 《{h['title']}》(v{h.get('document_version')}) "
            f"{h.get('publish_date') or '日期未知'} {h.get('author') or ''} {loc}\n{h['text']}"
        )
    evidence_block = "\n\n".join(numbered) if numbered else "（未检索到相关证据）"

    vp_block = "\n\n".join(
        f"[观点:{i+1}] {v['research_subject']} · {v['viewpoint_date'] or '日期未知'} · "
        f"{v['proposer'] or '未知'} · 状态:{v['status']}\n{v['core_judgment']}\n"
        f"条件:{v.get('conditions') or '—'} 风险:{v.get('risks') or '—'}"
        for i, v in enumerate(viewpoints)
    ) if viewpoints else "（本次已接入资料中没有可对照的历史观点）"

    ai_analysis: str | None = None
    note: str | None = None
    if llm.available() and (viewpoints or hits):
        ai_analysis = llm.complete(
            _COMPARE_PROMPT.format(event_text=event_text, viewpoints=vp_block, evidence=evidence_block),
            max_tokens=3000,
        )

    # 无模型或模型失败时的兜底说明（不伪装成 AI 点评）
    if ai_analysis is None:
        err = llm.last_error() or {}
        if not llm.available():
            note = "（检索成功，AI 生成失败：未配置模型凭证。以下仅列出检索到的原始材料，不含 AI 分析。）"
        elif not viewpoints and not hits:
            note = "（当前检索范围内未找到历史观点或支持材料，无法进行 AI 点评。）"
        else:
            note = f"（检索成功，AI 生成失败：{err.get('detail', '未知原因')}。以下仅列出检索到的原始材料，不含 AI 分析。）"

    output = _render_output(
        event_text=event_text,
        event_date=event_date,
        subject=subject,
        company=company,
        topic=topic,
        cutoff_date=cutoff_date,
        viewpoints=viewpoints,
        hits=hits,
        ai_analysis=ai_analysis,
        note=note,
        user_pasted=(event_facts is None),
        is_simulated=is_simulated,
        event_facts=event_facts,
    )

    commentary_id = None
    if save:
        commentary_id = save_commentary(event_text, user, subject=subject, cutoff_date=cutoff_date,
                                        output_markdown=output)

    return {
        "ok": True,
        "subject": subject,
        "event_date": event_date,
        "viewpoint_count": len(viewpoints),
        "evidence_count": len(hits),
        "excluded_hits": excluded_hits,
        "date_unknown_excluded": search.get("date_unknown_excluded", 0),
        "search_mode": search.get("mode"),
        "ai_generated": bool(ai_analysis),
        "commentary_id": commentary_id,
        "output_markdown": output,
        "viewpoints": viewpoints,
        "hits": hits,
    }


def _render_output(
    *,
    event_text: str,
    event_date: str | None,
    subject: str | None,
    company: str | None,
    topic: str | None,
    cutoff_date: str | None,
    viewpoints: list[dict],
    hits: list[dict],
    ai_analysis: str | None,
    note: str | None,
    user_pasted: bool,
    is_simulated: bool = False,
    event_facts: dict | None = None,
) -> str:
    lines: list[str] = []
    if is_simulated:
        lines.append("> ⚠️ **【模拟事件】**：本点评基于模拟事件，仅供测试，不作为正式结论，也不保存为正式点评。")
        lines.append("")
    lines.append("## 一、事件与官方数据")
    if event_facts:
        src = event_facts.get("source") or "官方来源"
        url = event_facts.get("source_url") or ""
        period = event_facts.get("period") or ""
        pub = event_facts.get("published_at") or ""
        if url:
            lines.append(f"- 来源：[{src}]({url})")
        else:
            lines.append(f"- 来源：{src}")
        if period:
            lines.append(f"- 统计月份：{period}")
        if pub:
            lines.append(f"- 发布日期：{pub}")
        h = event_facts.get("headline") or {}
        if h:
            lines.append(f"- 本期：{h.get('name')} {h.get('value')}%（{h.get('change_text') or '变化未提供'}）")
            if h.get("quote"):
                lines.append(f"- 原文：{h['quote']}")
        for s in (event_facts.get("sub_items") or []):
            lines.append(f"- 分项：{s['name']} {s['value']}%（{s['change_text']}）")
    else:
        src = "用户提供，来源未独立核验" if user_pasted else "来自入库材料"
        lines.append(f"- 研究对象：{subject or '（未识别）'}")
        if company:
            lines.append(f"- 关联公司：{company}")
        if topic:
            lines.append(f"- 产业主题：{topic}")
        lines.append(f"- 事件日期：{event_date or '未指定'}")
        lines.append(f"- 材料来源：{src}")
    lines.append(f"- 研究截止日期：{cutoff_date or '未设置（含全部材料）'}")
    lines.append("")
    lines.append("> 事件内容：")
    lines.append(f"> {event_text.strip()[:500]}")
    lines.append("")

    if ai_analysis:
        lines.append(ai_analysis)
    else:
        lines.append("## 二、历史观点（可对照）")
        if viewpoints:
            for v in viewpoints:
                lines.append(f"- [{v['viewpoint_date'] or '日期未知'}] {v['proposer'] or '未知'}："
                             f"{v['core_judgment']}（状态：{v['status']}）")
        else:
            lines.append("- 本次已接入资料中没有可对照的历史观点。")
        if hits:
            lines.append("")
            lines.append("## 三、检索到的相关证据")
            for i, h in enumerate(hits, 1):
                loc = f"p.{h['page_number']}" if h.get("page_number") else (h.get("position") or "—")
                lines.append(f"- [证据:{i}] 《{h['title']}》{loc}：{h['text'][:120]}…")
        lines.append("")
        lines.append("## 四、分析与不确定性")
        lines.append(note or "（无法生成分析）")

    return "\n".join(lines)


def save_commentary(
    event_text: str,
    user: str | None,
    *,
    subject: str | None,
    cutoff_date: str | None,
    output_markdown: str,
) -> str:
    require_admin(user, "保存事件点评")
    cid = new_id("cmt")
    storage.get_store().add_commentary(
        cid,
        {
            "event_text": event_text,
            "subject": subject,
            "cutoff_date": cutoff_date,
            "output_markdown": output_markdown,
            "source_category": SOURCE_AI,
            "created_at": storage.now_iso(),
        },
    )
    return cid


def list_commentaries(user: str | None) -> list[dict]:
    require_admin(user, "查看点评记录")
    return storage.get_store().list_commentaries()
