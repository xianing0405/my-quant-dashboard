#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日宏观监测脚本（daily_macro_monitor.py，升级版）

通过已配置的 Wind 接口，抓取并输出三块内容：
1. 宏观与加息动态：过去 24 小时经济数据公布（财经新闻摘录）+ CME FedWatch 加息/降息概率。
2. 全球大类资产与汇率：隔夜美股、美债、外汇与商品的最新价与日涨跌幅。
3. 亚洲市场与异动新闻：A 股 / 港股 / 日经 / 韩国指数 + 今日 A 股、港股盘面异动新闻。

数据路由：
- 上证指数 / 恒生指数：index_data 实时行情。
- 美股 / 外汇 / 商品 / 日韩指数：economic_data 宏观 EDB 日频序列。
- 财经新闻：financial_docs.get_financial_news。
- FedWatch 概率：economic_data 联储观察工具（EDB）。

纪律约束（见 AGENTS.md）：所有数字来自取数、不凭记忆；只做数据摘录与确定性计算，
不给出投资动作建议。报告输出：本脚本同级的 reports/macro_report_YYYYMMDD.md
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
WIND_SKILL_DIR = os.path.expanduser("~/.agents/skills/wind-mcp-skill")
CLI = ["node", "scripts/cli.mjs"]
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(HERE, "reports")

# 全球大类资产与汇率（隔夜美股 + 美债 + 外汇 + 商品）
GLOBAL_ASSETS = [
    {"name": "标普500", "code": "SPX.GI", "route": "edb", "edb": "G0001672", "decimals": 2},
    {"name": "纳斯达克", "code": "IXIC.GI", "route": "edb", "edb": "G0001677", "decimals": 2},
    {"name": "10年期美债收益率", "code": "G1000114.CJ", "route": "edb", "edb": "G0000891", "decimals": 2, "kind": "yield"},
    {"name": "美元指数", "code": "UDI.FX", "route": "edb", "edb": "M0000271", "decimals": 3},
    {"name": "美元兑日元", "code": "USDJPY.FX", "route": "edb", "edb": "M0000199", "decimals": 3},
    {"name": "欧元兑美元", "code": "EURUSD.FX", "route": "edb", "edb": "M0000200", "decimals": 4},
    {"name": "COMEX黄金", "code": "AU.CME", "route": "edb", "edb": "S0069669", "decimals": 1, "unit": "美元/盎司"},
    {"name": "NYMEX原油", "code": "CL.NYM", "route": "edb", "edb": "S0180896", "decimals": 2, "unit": "美元/桶"},
]

# 亚洲市场指数
ASIA_ASSETS = [
    {"name": "上证指数", "code": "000001.SH", "route": "index", "decimals": 2},
    {"name": "恒生指数", "code": "HSI.HI", "route": "index", "decimals": 2},
    {"name": "日经225", "code": "N225.GI", "route": "edb", "edb": "G0001665", "decimals": 2},
    {"name": "韩国综合指数", "code": "KS11.GI", "route": "edb", "edb": "G0001671", "decimals": 2},
]

# CME FedWatch（联储观察工具）EDB 指标
FEDWATCH_HIKE = "F9822336"  # 目标利率变动可能性:上升（加息）
FEDWATCH_CUT = "D7577252"   # 目标利率变动可能性:下降（降息）

# 新闻查询主题
NEWS_ECON = "近24小时全球重要经济数据公布"
NEWS_ASHR = "今日A股盘面异动 板块 资金"
NEWS_HK = "今日港股盘面异动 板块"

# 大模型提炼（DeepSeek 的 Anthropic 兼容接口，凭据来自环境变量）
LLM_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
LLM_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
LLM_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"


# ---------------------------------------------------------------------------
# Wind 取数
# ---------------------------------------------------------------------------
def call_wind(server_type: str, tool: str, params: dict):
    """调用 Wind CLI，返回解析后的 dict 或 {'error': ...}。"""
    cmd = CLI + ["call", server_type, tool, json.dumps(params, ensure_ascii=False)]
    try:
        proc = subprocess.run(cmd, cwd=WIND_SKILL_DIR, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"error": f"调用 Wind CLI 失败: {e}"}
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {"error": f"CLI 无输出 (stderr: {(proc.stderr or '').strip()[:200]})"}
    try:
        top = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": f"无法解析 CLI 输出: {stdout[:300]}"}
    if top.get("ok") is False:
        return {"error": f"{top.get('code')}: {top.get('message')}"}
    try:
        return json.loads(top["content"][0]["text"])
    except Exception as e:
        return {"error": f"解析 content 失败: {e}"}


def to_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_index_asset(asset: dict) -> dict:
    """index_data 路由：取最新成交价与涨跌幅。"""
    r = call_wind("index_data", "get_index_price_indicators",
                  {"windcode": asset["code"], "indexes": "最新交易日,最新成交价,涨跌幅"})
    base = {"name": asset["name"], "code": asset["code"], "decimals": asset.get("decimals", 2)}
    if r.get("error"):
        base["error"] = r["error"]
        return base
    data = r.get("data") or {}
    cols = [c["name"] for c in data.get("columns", [])]
    rows = data.get("rows", [])
    if not rows:
        base["error"] = "接口无数据"
        return base
    rec = dict(zip(cols, rows[0]))
    base["date"] = rec.get("最新交易日")
    base["latest"] = to_float(rec.get("最新成交价"))
    base["change_pct"] = to_float(rec.get("涨跌幅"))
    return base


def fetch_edb_asset(asset: dict) -> dict:
    """EDB 路由：取日频序列，算最新值与日涨跌幅（美债额外算 BP）。"""
    r = call_wind("economic_data", "query_economic_indicator_data",
                  {"question": asset["edb"], "observation": "3"})
    base = {"name": asset["name"], "code": asset["code"], "decimals": asset.get("decimals", 2),
            "kind": asset.get("kind"), "unit": asset.get("unit")}
    if r.get("error"):
        base["error"] = r["error"]
        return base
    metrics = r.get("metrics") or []
    if not metrics:
        base["error"] = "EDB 无该指标数据"
        return base
    m = metrics[0]
    dates = m.get("date") or []
    values = m.get("value") or []
    valid = [(d, v) for d, v in zip(dates, values) if v is not None]
    if len(valid) < 2:
        base["error"] = "序列不足两期"
        return base
    (_, prev), (d1, latest) = valid[-2], valid[-1]
    base["date"] = d1
    base["latest"] = to_float(latest)
    base["prev"] = to_float(prev)
    base["change_pct"] = (base["latest"] - base["prev"]) / base["prev"] * 100 if base["prev"] else None
    if asset.get("kind") == "yield" and base["latest"] is not None and base["prev"] is not None:
        base["bp"] = (base["latest"] - base["prev"]) * 100  # 收益率单位 %，1% = 100bp
    return base


def fetch_asset(asset: dict) -> dict:
    if asset.get("route") == "index":
        return fetch_index_asset(asset)
    return fetch_edb_asset(asset)


# ---------------------------------------------------------------------------
# 新闻与 FedWatch
# ---------------------------------------------------------------------------
def fetch_news(query: str, top_k: int = 4) -> list:
    """财经新闻：返回 [{'title','date','url','content'}...]，失败返回 []。"""
    r = call_wind("financial_docs", "get_financial_news", {"query": query, "top_k": top_k})
    if r.get("error"):
        return []
    items = (r.get("data") or {}).get("items") or []
    return [{"title": it.get("title") or "", "date": it.get("date") or "",
             "url": it.get("url") or "", "content": it.get("content") or ""} for it in items]


def summarize_news(title: str, content: str, max_chars: int = 60) -> str | None:
    """调用大模型，把新闻标题与摘要提炼为一句通顺、完整、无省略号的中文总结。

    失败（无凭据 / 网络错误 / 无 text 返回）时返回 None，由调用方回退到标题。
    """
    if not LLM_TOKEN:
        return None
    prompt = (
        "你是财经新闻编辑。把下面这条新闻的标题与正文摘要，整合提炼成一句通顺、完整、"
        f"无省略号、无残缺的中文总结，控制在 {max_chars} 字以内，只输出总结本身"
        "（不要任何前缀、引号或解释）。\n\n"
        f"标题：{title}\n正文摘要：{content}"
    )
    payload = {
        "model": LLM_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {LLM_TOKEN}",
        "anthropic-version": "2023-06-01",
    }
    try:
        resp = requests.post(f"{LLM_BASE_URL}/v1/messages", json=payload, headers=headers, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            b.get("text", "") for b in data.get("content") or []
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip().strip('"\'“”')
        return text or None
    except Exception:
        return None


def fetch_fedwatch() -> dict:
    """CME FedWatch 加息/降息概率（取最近一次 FOMC 会议）。"""
    today = datetime.now()
    begin = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=120)).strftime("%Y-%m-%d")

    def _first_value(code):
        r = call_wind("economic_data", "query_economic_indicator_data",
                      {"question": code, "beginDate": begin, "endDate": end})
        if r.get("error"):
            return None, None
        ms = r.get("metrics") or []
        if not ms:
            return None, None
        m = ms[0]
        dates = m.get("date") or []
        values = m.get("value") or []
        for d, v in zip(dates, values):
            if v is not None:
                return d, to_float(v)
        return None, None

    meeting, hike = _first_value(FEDWATCH_HIKE)
    _, cut = _first_value(FEDWATCH_CUT)
    return {"meeting": meeting, "hike": hike, "cut": cut}


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
def fmt_num(v, decimals=2):
    if v is None:
        return "N/A"
    return f"{v:,.{decimals}f}"


def fmt_date(d):
    if not d:
        return "—"
    s = str(d).replace("-", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(d)


def classify_trend(change_pct):
    if change_pct is None:
        return "—"
    if change_pct >= 0.3:
        return "偏强"
    if change_pct <= -0.3:
        return "偏弱"
    return "震荡"


def yield_trend(bp):
    if bp is None:
        return "—"
    if bp > 0.1:
        return "收益率上行"
    if bp < -0.1:
        return "收益率下行"
    return "收益率持平"


def render_change(a: dict) -> str:
    if a.get("kind") == "yield":
        return f"{a['bp']:+.1f} bp" if a.get("bp") is not None else "N/A"
    return f"{a['change_pct']:+.2f}%" if a.get("change_pct") is not None else "N/A"


def render_latest(a: dict) -> str:
    if a.get("latest") is None:
        return "N/A"
    if a.get("kind") == "yield":
        return f"{a['latest']:.2f}%"
    suffix = f" {a['unit']}" if a.get("unit") else ""
    return fmt_num(a["latest"], a.get("decimals", 2)) + suffix


def render_trend(a: dict) -> str:
    if a.get("kind") == "yield":
        return yield_trend(a.get("bp"))
    return classify_trend(a.get("change_pct"))


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def render_asset_table(assets: list) -> str:
    lines = ["| 标的 | 最新价 | 日涨跌幅 | 趋势 | 数据日期 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for asset in assets:
        a = fetch_asset(asset)
        if a.get("error"):
            lines.append(f"| {a['name']} | 获取失败 | — | — | — |")
        else:
            lines.append(f"| {a['name']} | {render_latest(a)} | {render_change(a)} | "
                         f"{render_trend(a)} | {fmt_date(a.get('date'))} |")
    return "\n".join(lines)


def render_news(items: list, limit: int = 3) -> str:
    """把新闻经大模型提炼为一句话总结后渲染（消除残缺与省略号）。"""
    if not items:
        return "_（未获取到相关新闻）_"
    lines = []
    for it in items[:limit]:
        date = it["date"] or "—"
        summary = summarize_news(it["title"], it["content"]) or it["title"] or "（无标题）"
        lines.append(f"- {summary}（{date}）")
    return "\n".join(lines)


def build_report(now: datetime) -> str:
    L = []
    L.append("# 每日宏观监测报告")
    L.append("")
    L.append(f"> 生成时间：{now:%Y-%m-%d %H:%M:%S}")
    L.append("> 数据来源：万得 Wind 金融数据服务")
    L.append("")
    L.append("> **数据时效说明**：隔夜美股、美债、外汇及商品为 T-1 日（前一交易日收盘）；"
             "亚洲市场（A 股 / 港股 / 日经 / 韩国）为 T 日（今日盘中或收盘）；"
             "新闻与 FedWatch 概率的更新时间以各条目标注为准。")
    L.append("> **新闻提炼说明**：新闻标题与摘要由大模型整理为一句通顺、无省略号的总结，仅作信息归整，不构成投资建议。")
    L.append("")

    # 一、宏观与加息动态
    L.append("## 一、宏观与加息动态")
    L.append("")
    L.append("### 过去 24 小时经济数据公布（简评）")
    L.append("")
    econ_news = fetch_news(NEWS_ECON, top_k=4)
    L.append(render_news(econ_news))
    L.append("")
    L.append("### 美联储加息 / 降息概率（CME FedWatch）")
    L.append("")
    fw = fetch_fedwatch()
    meeting = fmt_date(fw.get("meeting"))
    hike = fw.get("hike")
    cut = fw.get("cut")
    L.append(f"- **加息概率**（下次 FOMC 会议 {meeting}）："
             + (f"{hike:.2f}%" if hike is not None else "未获取"))
    L.append(f"- **降息概率**：" + (f"{cut:.2f}%" if cut is not None else "未获取（该指标无近期数据）"))
    if hike is not None and cut is None:
        L.append(f"- **维持不变概率（推算）**：{100 - hike:.2f}%（= 100% − 加息概率，假设降息概率为 0）")
    L.append("")
    L.append("_注：加息概率来自 Wind EDB「联储观察工具」，反映市场对下次会议目标利率变动的隐含概率。_")
    L.append("")

    # 二、全球大类资产与汇率
    L.append("## 二、全球大类资产与汇率")
    L.append("")
    L.append(render_asset_table(GLOBAL_ASSETS))
    L.append("")

    # 三、亚洲市场与异动新闻
    L.append("## 三、亚洲市场与异动新闻")
    L.append("")
    L.append("### 亚洲指数早盘")
    L.append("")
    L.append(render_asset_table(ASIA_ASSETS))
    L.append("")
    L.append("### A 股盘面异动")
    L.append("")
    L.append(render_news(fetch_news(NEWS_ASHR, top_k=4)))
    L.append("")
    L.append("### 港股盘面异动")
    L.append("")
    L.append(render_news(fetch_news(NEWS_HK, top_k=4)))
    L.append("")

    L.append("---")
    L.append("")
    L.append("*本报告由 `daily_macro_monitor.py` 自动生成，数据来源于万得 Wind 金融数据服务；"
             "内容仅为数据与资讯复盘，不构成任何投资建议。*")
    L.append("")
    return "\n".join(L)


def main():
    now = datetime.now()
    report = build_report(now)
    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"macro_report_{now:%Y%m%d}.md"
    path = os.path.join(REPORT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n✅ 报告已写入：{path}")


if __name__ == "__main__":
    main()
