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
from .auth import require_authorized
from .models import SOURCE_AI, new_id
from .retriever import search_materials
from .viewpoints import get_viewpoint_history

_COMPARE_PROMPT = """你是金融研究助理。请基于给定的历史观点与原始证据，点评一条新事件。

要求：
1. 只依据提供的历史材料，不得编造「我们此前认为」。
2. 区分：原文事实 / 历史观点 / 本次 AI 推断。
3. 条件性判断必须保留条件。
4. 关键结论标注证据编号（[证据:编号]），编号只能来自给定的证据列表。
5. 同时评估支持、削弱、尚不能验证三方面，不只找看多材料。

【新事件】
{event_text}

【历史观点】
{viewpoints}

【检索到的证据】
{evidence}

请按以下结构输出 Markdown：
## 一、新事件与新增事实
## 二、历史观点（日期、归属、出处）
## 三、支持 / 削弱 / 尚不能验证的证据
## 四、本次分析与不确定性
## 五、后续需要验证的指标或事件
## 六、来源清单
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
) -> dict[str, Any]:
    """执行一次事件点评，返回结构化结果（dict），可另存为观点记录。"""
    require_authorized(user, "生成事件点评")

    event_text = (event_text or "").strip()
    if not event_text:
        return {"ok": False, "error": "未提供新事件文本"}

    subject = subject or _extract_subject(event_text)
    event_date = _extract_event_date(event_text)

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
    ) if viewpoints else "（未找到历史观点，不编造「我们此前认为」）"

    ai_analysis: str | None = None
    note: str | None = None
    if llm.available() and (viewpoints or hits):
        ai_analysis = llm.complete(
            _COMPARE_PROMPT.format(event_text=event_text, viewpoints=vp_block, evidence=evidence_block),
            max_tokens=3000,
        )

    # 无模型或模型失败时的兜底说明（不伪装成 AI 点评）
    if ai_analysis is None:
        if not llm.available():
            note = "（模型不可用：未配置 ANTHROPIC_AUTH_TOKEN。以下仅列出检索到的原始材料，不含 AI 分析。）"
        elif not viewpoints and not hits:
            note = "（当前检索范围内未找到历史观点或支持材料，无法进行 AI 点评。）"
        else:
            note = "（模型调用失败，以下仅列出检索到的原始材料。）"

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
        user_pasted=True,
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
) -> str:
    lines: list[str] = []
    lines.append("## 一、新事件与新增事实")
    src = "用户提供，来源未独立核验" if user_pasted else "来自入库材料"
    lines.append(f"- 研究对象：{subject or '（未识别）'}")
    if company:
        lines.append(f"- 关联公司：{company}")
    if topic:
        lines.append(f"- 产业主题：{topic}")
    lines.append(f"- 事件日期：{event_date or '（未识别）'}")
    lines.append(f"- 研究截止日期：{cutoff_date or '未设置（含全部材料）'}")
    lines.append(f"- 材料来源：{src}")
    lines.append("")
    lines.append("> 新事件原文：")
    lines.append(f"> {event_text.strip()[:500]}")
    lines.append("")

    if ai_analysis:
        # 直接用模型的结构化输出（已含二~六节）
        lines.append(ai_analysis)
    else:
        lines.append("## 二、历史观点（日期、归属、出处）")
        if viewpoints:
            for v in viewpoints:
                lines.append(f"- [{v['viewpoint_date'] or '日期未知'}] {v['proposer'] or '未知'}："
                             f"{v['core_judgment']}（状态：{v['status']}）")
        else:
            lines.append("- （未找到历史观点，不编造「我们此前认为」）")
        lines.append("")
        lines.append("## 三、支持 / 削弱 / 尚不能验证的证据")
        if hits:
            for i, h in enumerate(hits, 1):
                loc = f"p.{h['page_number']}" if h.get("page_number") else (h.get("position") or "—")
                lines.append(f"- [证据:{i}] 《{h['title']}》{loc}：{h['text'][:120]}…")
        else:
            lines.append("- （未检索到支持材料）")
        lines.append("")
        lines.append("## 四、本次分析与不确定性")
        lines.append(note or "（无法生成分析）")
        lines.append("")
        lines.append("## 五、后续需要验证的指标或事件")
        lines.append("- （待人工补充）")
        lines.append("")
        lines.append("## 六、来源清单")
        for h in hits:
            lines.append(f"- {h['evidence_id']}：《{h['title']}》(v{h.get('document_version')})")

    return "\n".join(lines)


def save_commentary(
    event_text: str,
    user: str | None,
    *,
    subject: str | None,
    cutoff_date: str | None,
    output_markdown: str,
) -> str:
    require_authorized(user, "保存事件点评")
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
    require_authorized(user, "查看点评记录")
    return storage.get_store().list_commentaries()
