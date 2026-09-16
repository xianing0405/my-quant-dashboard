#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态概念主题追踪器（Dynamic Concept Tracker）

扫描当日资讯 / 研报文本，用大模型动态提取高频轮动的细分科技 / 周期概念
（如「碳化硅」「电子布」），关联相关个股的当日涨跌幅与成交额，落盘 JSON 缓存供前端读取。

数据流：
  Wind 财经新闻 → 大模型提取概念 → Wind 个股行情 → data/dynamic_concepts_cache.json

用法：
  python -m core.industry_tracker                       # 跑一次完整提取并写缓存
  from core.industry_tracker import load_cache, build_dynamic_concepts, run_daily

设计要点（轻量、可降级）：
  - 概念提取优先走大模型；无凭据或失败时回退到内置规则字典 FALLBACK_CONCEPTS。
  - 个股行情走 Wind；单只失败则对应字段留空，不拖垮整体。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent          # Website/core/
DATA_DIR = HERE.parent / "data"                  # Website/data/
CACHE_FILE = DATA_DIR / "dynamic_concepts_cache.json"

# ---- Wind CLI ----
WIND_SKILL_DIR = os.path.expanduser("~/.agents/skills/wind-mcp-skill")
_CLI = ["node", "scripts/cli.mjs"]

# ---- 大模型配置（复用 daily_macro_monitor 的 Anthropic 兼容接口）----
_LLM_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
_LLM_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
_LLM_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"

# ---- 规则兜底：常见高频轮动概念 → 相关个股 ----
FALLBACK_CONCEPTS = [
    {"name": "碳化硅", "catalyst": "新能源车 800V 高压平台放量，SiC 器件渗透率提升", "stocks": ["三安光电", "天岳先进", "露笑科技"]},
    {"name": "电子布", "catalyst": "AI 服务器 PCB 升级带动高端电子布需求", "stocks": ["生益科技", "华正新材", "宏和科技"]},
    {"name": "光模块", "catalyst": "海外云厂商资本开支上修，800G/1.6T 光模块放量", "stocks": ["中际旭创", "新易盛", "天孚通信"]},
    {"name": "液冷算力", "catalyst": "AI 数据中心散热需求爆发，液冷渗透率提升", "stocks": ["英维克", "高澜股份"]},
]


# ---------------------------------------------------------------------------
# Wind 取数
# ---------------------------------------------------------------------------
def _call_wind(server_type: str, tool: str, params: dict) -> dict:
    cmd = _CLI + ["call", server_type, tool, json.dumps(params, ensure_ascii=False)]
    try:
        proc = subprocess.run(cmd, cwd=WIND_SKILL_DIR, capture_output=True, text=True, timeout=120)
        top = json.loads(proc.stdout or "{}")
        if top.get("ok") is False:
            return {"error": f"{top.get('code')}: {top.get('message')}"}
        return json.loads(top["content"][0]["text"])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _search_stock_code(name: str) -> str | None:
    r = _call_wind("stock_data", "search_stocks", {"question": name})
    if r.get("error"):
        return None
    inner = (r.get("data") or {}).get("data") or []
    if not inner:
        return None
    block = inner[0]
    cols = [c["name"] for c in block.get("columns", [])]
    row = (block.get("rows") or [[]])[0]
    rec = dict(zip(cols, row))
    return rec.get("Wind代码")


def _stock_quote(code: str) -> dict:
    """返回 {'change_pct': %, 'turnover': 亿元}，失败返回 {}。"""
    r = _call_wind("stock_data", "get_stock_price_indicators",
                   {"windcode": code, "indexes": "涨跌幅,成交额"})
    if r.get("error"):
        return {}
    data = r.get("data") or {}
    cols = [c["name"] for c in data.get("columns", [])]
    rows = data.get("rows", [])
    if not rows:
        return {}
    rec = dict(zip(cols, rows[0]))
    turnover = _to_float(rec.get("成交额"))
    return {
        "change_pct": _to_float(rec.get("涨跌幅")),
        "turnover": round(turnover / 1e8, 2) if turnover is not None else None,  # 元 → 亿元
    }


# ---------------------------------------------------------------------------
# 资讯与大模型
# ---------------------------------------------------------------------------
def _fetch_news_text(top_k: int = 5) -> str:
    r = _call_wind("financial_docs", "get_financial_news",
                   {"query": "今日A股 热点 概念 板块 异动", "top_k": top_k})
    if r.get("error"):
        return ""
    items = (r.get("data") or {}).get("items") or []
    parts = []
    for it in items:
        title = it.get("title") or ""
        content = (it.get("content") or "")[:600]
        parts.append(f"标题：{title}\n{content}")
    return "\n\n".join(parts)


def _llm_complete(prompt: str, max_tokens: int = 2000) -> str | None:
    if not _LLM_TOKEN:
        return None
    payload = {"model": _LLM_MODEL, "max_tokens": max_tokens,
               "thinking": {"type": "disabled"},  # 跳过推理模型 thinking，避免输出额度被吞
               "messages": [{"role": "user", "content": prompt}]}
    headers = {"content-type": "application/json", "authorization": f"Bearer {_LLM_TOKEN}",
               "anthropic-version": "2023-06-01"}
    try:
        resp = requests.post(f"{_LLM_BASE_URL}/v1/messages", json=payload, headers=headers, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content") or []
                       if isinstance(b, dict) and b.get("type") == "text").strip()
        return text or None
    except Exception:  # noqa: BLE001
        return None


def _parse_concepts_json(text: str) -> list[dict]:
    if not text:
        return []
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return [d for d in data if isinstance(d, dict) and d.get("name")]
    except json.JSONDecodeError:
        return []


def _extract_concepts_llm(news_text: str) -> list[dict]:
    prompt = (
        "你是A股主题策略研究员。从下面的财经资讯中，识别当前市场热议的细分科技/周期概念"
        "（高频轮动概念，如碳化硅、电子布、光模块，而非申万大类行业）。\n"
        "对每个概念输出：概念名、核心催化剂（一句话）、相关个股（2-4只A股简称）。\n"
        "严格只输出 JSON 数组，不要输出 JSON 以外的任何文字：\n"
        '[{"name":"概念名","catalyst":"催化剂","stocks":["股票简称",...]}]\n\n'
        f"资讯：\n{news_text[:1200]}"
    )
    text = _llm_complete(prompt, max_tokens=3000)
    return _parse_concepts_json(text or "")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build_dynamic_concepts(news_text: str | None = None, use_llm: bool = True,
                           max_stocks_per_concept: int = 4) -> list[dict]:
    """扫描资讯，提取概念并关联个股行情，返回概念列表。"""
    if news_text is None:
        news_text = _fetch_news_text()

    concepts = _extract_concepts_llm(news_text) if (use_llm and news_text) else []
    if not concepts:
        concepts = FALLBACK_CONCEPTS

    result = []
    for c in concepts:
        stocks = []
        for sname in (c.get("stocks") or [])[:max_stocks_per_concept]:
            code = _search_stock_code(sname)
            quote = _stock_quote(code) if code else {}
            stocks.append({
                "name": sname,
                "code": code or "—",
                "change_pct": quote.get("change_pct"),
                "turnover": quote.get("turnover"),
            })
        result.append({
            "name": c.get("name", ""),
            "catalyst": c.get("catalyst", ""),
            "stocks": stocks,
        })
    return result


def save_cache(concepts: list[dict], path: Path | None = None) -> Path:
    path = path or CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "Wind 资讯 + 大模型动态概念提取",
        "concepts": concepts,
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_cache(path: Path | None = None) -> dict:
    path = path or CACHE_FILE
    if not path.is_file():
        return {"updated_at": None, "source": "", "concepts": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "source": "", "concepts": []}


def run_daily(news_text: str | None = None, use_llm: bool = True) -> dict:
    """跑一次完整提取并写缓存，返回缓存内容。"""
    concepts = build_dynamic_concepts(news_text=news_text, use_llm=use_llm)
    save_cache(concepts)
    return load_cache()


if __name__ == "__main__":
    cache = run_daily()
    print(f"已提取 {len(cache.get('concepts', []))} 个概念 → {CACHE_FILE}")
    for c in cache.get("concepts", []):
        print(f"  · {c['name']}：{c['catalyst']}（{len(c.get('stocks', []))} 只个股）")
