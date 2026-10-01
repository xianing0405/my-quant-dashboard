#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方公开经济数据获取（国家统计局官网，免费，无需凭证/额外服务）。

首个指标：中国采购经理指数（PMI）。通用流程：
  指标 + 地区 + 统计期 → 定位官方发布页 → 抓取 → 解析 → 返回
  {来源链接, 发布时间, 本期/前值/变化/口径}，逐项可追溯到原文。

纪律：以官方原文字段为准；取不到就明确失败，不让模型凭记忆补数。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

CST = timezone(timedelta(hours=8))
NBS_ZXFB = "https://www.stats.gov.cn/sj/zxfb/"
_UA = {"User-Agent": "Mozilla/5.0"}


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _html_to_text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    html = re.sub(r"</tr>", "\n", html)
    html = re.sub(r"<td[^>]*>", " | ", html)
    html = re.sub(r"<p[^>]*>", "\n", html)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&nbsp;", " ")
    html = re.sub(r"[ \t]+", " ", html)
    return html


def _find_pmi_release() -> str | None:
    """在国家统计局数据发布页找到最新一条「中国采购经理指数运行情况」的链接。"""
    r = requests.get(NBS_ZXFB, timeout=20, headers=_UA)
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="ignore")
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        t = re.sub(r"<[^>]+>", "", text).strip()
        if "中国采购经理指数运行情况" in t:
            return href if href.startswith("http") else NBS_ZXFB + href.lstrip("./")
    return None


def _parse_pmi(html: str) -> dict:
    plain = _html_to_text(html)
    # 发布时间（国家统计局发布页通常有「发布时间」或从标题日期推断）
    pub = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2}[ 0-9:]{0,9})", plain)
    title = re.search(r"(\d{4})年(\d{1,2})月中国采购经理指数运行情况", plain)
    period = f"{title.group(1)}年{title.group(2)}月" if title else ""

    # 制造业 PMI 与变化（含前值推算）
    mfg = re.search(r"制造业采购经理指数（\s*PMI\s*）为\s*(\d+\.\d+)\s*%", plain)
    headline = None
    if mfg:
        headline = {"name": "制造业PMI", "sector": "制造业", "value": float(mfg.group(1)), "unit": "%",
                    "change": None, "change_text": None, "previous": None, "quote": ""}
        m = re.search(r"制造业采购经理指数（\s*PMI\s*）为\s*\d+\.\d+\s*%[，,](.{0,40}?)[。；;]", plain)
        if m:
            headline["quote"] = m.group(0)[:80]
            cm = re.search(r"比上月(上升|下降)(\d+\.\d+)个百分点", m.group(1))
            if cm:
                d = float(cm.group(2))
                sign = 1 if cm.group(1) == "上升" else -1
                headline["change"] = round(sign * d, 1)
                headline["change_text"] = cm.group(0)
                headline["previous"] = round(headline["value"] - sign * d, 1)

    # 分项：按板块位置归因，避免同名「新订单/从业人员」串值（制造业/非制造业/建筑业各不同）
    sub_items: list[dict] = []
    sec_pos = []
    for name, pat in [("制造业", "一、中国制造业采购经理指数运行情况"),
                      ("非制造业", "二、中国非制造业采购经理指数运行情况"),
                      ("综合", "三、中国综合PMI产出指数运行情况")]:
        p = plain.find(pat)
        if p >= 0:
            sec_pos.append((name, p))
    sec_pos.sort(key=lambda x: x[1])

    for m in re.finditer(r"([一-鿿]{2,12}指数)为\s*(\d+\.\d+)\s*%[，,](.{0,40}?比上月(?:上升|下降)\d+\.\d+个百分点)", plain):
        name, val = m.group(1), float(m.group(2))
        sector = "综合"
        for sn, sp in sec_pos:
            if sp <= m.start():
                sector = sn
        if "建筑" in name:
            sector = "建筑业"
        cm = re.search(r"比上月(上升|下降)(\d+\.\d+)个百分点", m.group(3))
        change = float(cm.group(2)) if cm.group(1) == "上升" else -float(cm.group(2))
        sub_items.append({"name": name, "sector": sector, "value": val,
                          "change": round(change, 1), "previous": round(val - change, 1),
                          "change_text": cm.group(0), "quote": m.group(0)[:100]})

    # 去重：综合节末尾的汇总表会重复列示各板块分项（按名称+数值保留首个，即带正确板块归属者）
    seen: set = set()
    deduped: list[dict] = []
    for s in sub_items:
        key = (s["name"], s["value"])
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    sub_items = deduped

    # 非制造业 / 综合 PMI
    non_mfg = re.search(r"非制造业商务活动指数为\s*(\d+\.\d+)\s*%", plain)
    comp = re.search(r"综合PMI产出指数为\s*(\d+\.\d+)\s*%", plain)

    return {
        "period": period,
        "published_at": pub.group(1).strip() if pub else "",
        "headline": headline,
        "sub_items": sub_items,
        "non_manufacturing": float(non_mfg.group(1)) if non_mfg else None,
        "composite_pmi": float(comp.group(1)) if comp else None,
        "raw": plain[:6000],
    }


def fetch_pmi() -> dict:
    """获取最新一期中国 PMI（NBS 官方）。"""
    try:
        url = _find_pmi_release()
        if not url:
            return {"ok": False, "error": "未在国家统计局数据发布页找到 PMI 发布"}
        r = requests.get(url, timeout=25, headers=_UA)
        r.raise_for_status()
        html = r.content.decode("utf-8", errors="ignore")
        data = _parse_pmi(html)
        data["ok"] = True
        data["source"] = "国家统计局"
        data["source_url"] = url
        return data
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"获取失败：{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# 任务识别（确定性规则优先，避免额外模型调用）
# ---------------------------------------------------------------------------
def parse_task(text: str) -> dict:
    """从「点评今天中国PMI…」类输入提取指标/地区/统计期（确定性规则）。"""
    t = text or ""
    indicator = None
    region = None
    period = today_cst()

    if re.search(r"PMI|采购经理", t, re.I):
        indicator = "PMI"
    if "中国" in t:
        region = "中国"
    # 「今天」按 Asia/Shanghai；显式日期优先
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", t)
    if m:
        period = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return {"indicator": indicator, "region": region, "period": period}
