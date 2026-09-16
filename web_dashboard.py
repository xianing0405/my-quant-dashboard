#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投研数据监控台（web_dashboard.py，三大核心模块版）

Streamlit 仪表盘，侧边栏四大模块导航：
  1. 宏观与大类资产
  2. 行业动态（细分产业）
  3. 因子分析（待建）
  4. 内部知识库（RAG）

说明：
- 报告搜索目录见 REPORT_DIRS（默认 core/macro/reports/，另含项目根目录兜底）。
- 生成脚本见 REPORT_GEN_SCRIPT（默认 core/macro/daily_macro_monitor.py，本项目
  当前不存在 daily_stock_analysis.py，如另有脚本改此行即可）。
- 模块一的核心指标从最新 .md 报告中正则提取，取不到时显示「—」，不伪造数据。

启动：
    streamlit run web_dashboard.py
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from core.industry_tracker import load_cache as load_concept_cache, run_daily as run_concept_daily
from core.knowledge.local_rag import search_internal_knowledge

HERE = Path(__file__).resolve().parent

REPORT_DIRS = [
    HERE / "core" / "macro" / "reports",
    HERE,
]
REPORT_GEN_SCRIPT = HERE / "core" / "macro" / "daily_macro_monitor.py"

_REPORT_RE = re.compile(r"report|(?<!\d)\d{8}(?!\d)", re.IGNORECASE)

# 模块一四个分类标签 → 对应资产名称集合
CATEGORY_ASSETS = {
    "美元与汇率": {"美元指数", "美元兑日元", "欧元兑美元"},
    "美债与加息": {"10年期美债收益率"},
    "大宗商品": {"COMEX黄金", "NYMEX原油"},
    "全球股市": {"标普500", "纳斯达克", "上证指数", "恒生指数", "日经225", "韩国综合指数"},
}

# 报告章节标题（用于按 tab 归类展示）
SECTION_FEDWATCH = "## 一、宏观与加息动态"
SECTION_ASIA = "## 三、亚洲市场与异动新闻"


# ---------------------------------------------------------------------------
# 全局自定义 CSS（高端金融 SaaS 视觉）
# ---------------------------------------------------------------------------
_GLOBAL_CSS = """
<style>
/* ===== 主背景：浅灰，衬托白色卡片 ===== */
[data-testid="stAppViewContainer"] {
    background: #f5f7fb;
}

/* ===== 侧边栏：深邃夜蓝 ===== */
[data-testid="stSidebar"] {
    background-color: #171b26 !important;
}
[data-testid="stSidebar"] > div {
    background-color: #171b26 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #ffffff !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label {
    color: #c9cfe0;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    color: #8b93a7 !important;
}

/* ===== 侧边栏 radio → 现代文字导航菜单 ===== */
[data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"] {
    position: absolute;
    opacity: 0;
    width: 0;
    height: 0;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 9px 14px;
    border-radius: 8px;
    margin-bottom: 2px;
    cursor: pointer;
    color: #c9cfe0;
    transition: background 0.15s ease, color 0.15s ease;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #ffffff;
}
/* 隐藏原生圆形单选框 */
[data-testid="stSidebar"] [data-testid="stRadio"] label > div:first-of-type {
    display: none;
}
/* 选中态：半透明蓝色背景 + 白字 */
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: rgba(59, 130, 246, 0.28) !important;
    color: #ffffff !important;
}

/* ===== 侧边栏按钮 ===== */
[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    background: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 8px;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #1d4ed8;
    color: #ffffff;
}

/* ===== 指标卡片：白底 / 圆角 / 内边距 / 投影悬浮 ===== */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #eef1f6;
    border-radius: 8px;
    padding: 15px;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 4px 12px rgba(16, 24, 40, 0.08);
}
[data-testid="stMetricLabel"] {
    color: #6b7280;
}
[data-testid="stMetricValue"] {
    color: #111827;
}

/* ===== 标签页胶囊化 ===== */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 8px;
    background: transparent;
}
[data-testid="stTabs"] [data-baseweb="tab-border"] {
    background: transparent;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: #e7ebf3;
    color: #4b5563;
    border-radius: 999px;
    padding: 6px 18px;
    margin-right: 6px;
}
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
    background: #2563eb;
    color: #ffffff;
}
</style>
"""


def _inject_css() -> None:
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


def _render_banner(summary: str) -> None:
    summary = html.escape(summary)
    st.markdown(
        f"""
        <div style="background: linear-gradient(90deg, #eef3ff 0%, #f6f8fc 100%);
                    border: 1px solid #dbe4f5; border-radius: 12px;
                    padding: 16px 20px; display: flex; align-items: center; gap: 14px;
                    margin: 4px 0 18px 0;">
            <span style="font-size: 24px; line-height: 1;">⚡️</span>
            <div>
                <div style="font-weight: 700; font-size: 16px; color: #111827;">晨观 5 分钟</div>
                <div style="font-size: 14px; color: #4b5563; margin-top: 2px;">{summary}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 报告发现与解析
# ---------------------------------------------------------------------------
def _is_report(path: Path) -> bool:
    return path.suffix.lower() == ".md" and bool(_REPORT_RE.search(path.stem))


def find_reports() -> list[Path]:
    seen: dict[str, Path] = {}
    for d in REPORT_DIRS:
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            if _is_report(p):
                seen[str(p)] = p
    return sorted(seen.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_asset_table(text: str) -> list[dict]:
    """解析报告中的 Markdown 资产表（标的/最新价/日涨跌幅/趋势/数据日期）。"""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("标的", "---", ""):
            continue
        rows.append({
            "name": cells[0],
            "latest": cells[1] if len(cells) > 1 else "",
            "change": cells[2] if len(cells) > 2 else "",
            "trend": cells[3] if len(cells) > 3 else "",
            "date": cells[4] if len(cells) > 4 else "",
        })
    return rows


def _parse_fedwatch(text: str) -> dict:
    """从报告提取加息/降息概率与下次 FOMC 会议日期。"""
    out: dict = {"hike": None, "cut": None, "meeting": None}
    m = re.search(r"加息概率.*?([\d.]+)%", text)
    if m:
        out["hike"] = m.group(1)
    m = re.search(r"降息概率.*?([\d.]+)%", text)
    if m:
        out["cut"] = m.group(1)
    m = re.search(r"FOMC 会议 ([0-9\-]+)", text)
    if m:
        out["meeting"] = m.group(1)
    return out


def _split_sections(text: str) -> dict[str, str]:
    """按 ## 标题切分报告正文。"""
    parts = re.split(r"(?m)^(## .+)$", text)
    result: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        result[parts[i].strip()] = (parts[i + 1] if i + 1 < len(parts) else "").strip()
    return result


def _extract_banner_text(text: str, limit: int = 150) -> str:
    """从报告「过去 24 小时经济数据公布」小节提取快讯要点，截取前 limit 字。"""
    m = re.search(r"### 过去 24 小时经济数据公布.*?(?=\n###|\n##|$)", text, re.S)
    if not m:
        return "（暂无当日快讯摘要）"
    items = []
    for ln in m.group(0).splitlines():
        ln = ln.strip()
        if not ln.startswith("-"):
            continue
        item = re.sub(r"^\s*-\s*", "", ln).strip()
        item = re.sub(r"[（(]\d{4}-\d{2}-\d{2}[)）]\s*$", "", item).strip()
        if item:
            items.append(item)
    joined = " ".join(items)
    if not joined:
        return "（暂无当日快讯摘要）"
    return joined[:limit] + ("…" if len(joined) > limit else "")


def _find_asset(assets: list[dict], name: str) -> dict | None:
    for a in assets:
        if a["name"] == name:
            return a
    return None


def _build_metrics(assets: list[dict], fedwatch: dict) -> list[dict]:
    """从报告中提取 8 个核心指标（含加息概率）。"""
    def pick(name: str) -> tuple[str, str | None]:
        a = _find_asset(assets, name)
        return (a["latest"], a["change"]) if a else ("—", None)

    specs = [
        ("标普500", "标普500"),
        ("纳斯达克", "纳斯达克"),
        ("10年期美债", "10年期美债收益率"),
        ("美元指数", "美元指数"),
        ("COMEX黄金", "COMEX黄金"),
        ("WTI原油", "NYMEX原油"),
        ("上证指数", "上证指数"),
    ]
    metrics = [{"label": label, "value": v, "delta": d}
               for label, name in specs for v, d in [pick(name)]]

    hike = fedwatch.get("hike")
    metrics.append({
        "label": "加息概率", "value": f"{hike}%" if hike else "—",
        "delta": f"会议 {fedwatch['meeting']}" if fedwatch.get("meeting") else None,
    })
    return metrics


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def _run_generator() -> tuple[bool, str, str]:
    if not REPORT_GEN_SCRIPT.is_file():
        return False, f"生成脚本不存在：{REPORT_GEN_SCRIPT}", ""
    try:
        proc = subprocess.run(
            [sys.executable, str(REPORT_GEN_SCRIPT)],
            cwd=str(HERE), capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "生成脚本超时（>600s）", ""
    except Exception as e:  # noqa: BLE001
        return False, f"运行出错：{e}", ""
    if proc.returncode == 0:
        return True, "报告生成完成", ""
    return False, f"生成脚本退出码 {proc.returncode}", (proc.stderr or proc.stdout or "")[-2000:]


# ---------------------------------------------------------------------------
# 各模块渲染
# ---------------------------------------------------------------------------
def _render_metric_row(metrics: list[dict], per_row: int = 4) -> None:
    """将指标卡片按每行 per_row 个分多行展示。"""
    for i in range(0, len(metrics), per_row):
        chunk = metrics[i:i + per_row]
        cols = st.columns(len(chunk))
        for col, m in zip(cols, chunk):
            col.metric(m["label"], m["value"], delta=m["delta"])


def _render_asset_df(assets: list[dict], names: set[str]) -> None:
    rows = [a for a in assets if a["name"] in names]
    if not rows:
        st.caption("（本分类暂无数据）")
        return
    df = pd.DataFrame(rows)[["name", "latest", "change", "trend", "date"]]
    df.columns = ["标的", "最新价", "日涨跌幅", "趋势", "数据日期"]
    st.dataframe(df, hide_index=True)


def _render_macro_page() -> None:
    st.subheader("🌍 宏观与大类资产")

    reports = find_reports()
    if not reports:
        st.info("尚未找到任何复盘报告（.md）。请点击左侧「重新生成今日报告」。")
        return

    # 晨观 5 分钟：取最新一份报告的快讯摘要（与历史选择无关）
    latest_text = reports[0].read_text(encoding="utf-8")
    _render_banner(_extract_banner_text(latest_text))

    # 历史报告选择（用于下方明细展示）
    labels = [p.name for p in reports]
    choice = st.selectbox("选择报告", labels, index=0)
    latest = reports[labels.index(choice)]

    text = latest.read_text(encoding="utf-8")
    assets = _parse_asset_table(text)
    fedwatch = _parse_fedwatch(text)
    sections = _split_sections(text)

    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
    st.caption(f"当前展示：{latest.name} · 生成于 {mtime:%Y-%m-%d %H:%M:%S}")

    # 顶部核心指标（8 个，分两行）
    _render_metric_row(_build_metrics(assets, fedwatch))

    # 分类标签
    tabs = st.tabs(list(CATEGORY_ASSETS.keys()))
    for tab, (label, names) in zip(tabs, CATEGORY_ASSETS.items()):
        with tab:
            _render_asset_df(assets, names)
            if label == "美债与加息" and fedwatch.get("hike"):
                st.markdown(
                    f"- **加息概率**（下次 FOMC 会议 {fedwatch.get('meeting') or '—'}）："
                    f"{fedwatch['hike']}%\n"
                    f"- **降息概率**：{fedwatch.get('cut') or '—'}%"
                )
            if label == "全球股市":
                asia = sections.get(SECTION_ASIA)
                if asia:
                    st.markdown("##### 亚洲市场与异动新闻")
                    st.markdown(asia)

    # 完整报告原文
    with st.expander("查看完整报告原文", expanded=False):
        st.markdown(text)


def _render_industry_page() -> None:
    st.subheader("🏭 细分产业与概念追踪")

    st.markdown(
        "系统通过大模型每日读取新闻与研报，动态提取「碳化硅」「电子布」等"
        "高频轮动概念，替代传统的申万行业分类。"
    )

    if st.button("🔄 重新提取今日概念", key="refresh_concepts"):
        with st.spinner("正在扫描资讯并提取概念，请稍候（约 1-3 分钟）…"):
            run_concept_daily()
        st.rerun()

    cache = load_concept_cache()
    concepts = cache.get("concepts", [])
    st.caption(f"数据来源：Wind 资讯 + 大模型动态提取 · 最近更新：{cache.get('updated_at') or '—'}")

    if not concepts:
        st.info("暂无动态概念数据，请点击上方「重新提取今日概念」。")
        return

    rows = []
    for c in concepts:
        name = c.get("name", "")
        catalyst = c.get("catalyst", "")
        stocks = c.get("stocks") or []
        if not stocks:
            rows.append({"概念": name, "核心催化剂（AI总结）": catalyst,
                         "相关个股": "—", "当日涨跌幅": None, "成交额(亿)": None})
            continue
        for s in stocks:
            rows.append({
                "概念": name,
                "核心催化剂（AI总结）": catalyst,
                "相关个股": f"{s.get('name', '')} ({s.get('code', '')})",
                "当日涨跌幅": s.get("change_pct"),
                "成交额(亿)": s.get("turnover"),
            })

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, column_config={
        "当日涨跌幅": st.column_config.NumberColumn("当日涨跌幅", format="%+.2f%%"),
        "成交额(亿)": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
    })


# 已验证分类色（散点气泡前三槽，全配对通过）与墨色
_CAT_COLORS = {"全国两会": "#2a78d6", "人大常委会": "#eb6834", "国常会": "#1baf7a"}
_INK = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_INK_MUTED = "#898781"
_GRID = "#e1e0d9"


def _build_fiscal_calendar_fig() -> go.Figure:
    """图表一：中国增量财政政策 × 重大会议时间博弈（气泡散点图）。"""
    events = [
        {"year": 2020, "month": 5, "meeting": "全国两会", "policy": "抗疫特别国债1万亿 + 赤字率3.6%以上", "scale": 2.0},
        {"year": 2022, "month": 8, "meeting": "国常会", "policy": "政策性金融工具 + 专项债结存限额", "scale": 1.1},
        {"year": 2023, "month": 10, "meeting": "人大常委会", "policy": "增发1万亿国债，赤字率提至3.8%", "scale": 1.0},
        {"year": 2024, "month": 3, "meeting": "全国两会", "policy": "超长期特别国债1万亿（连续多年）", "scale": 1.0},
        {"year": 2024, "month": 11, "meeting": "人大常委会", "policy": "10万亿化债方案", "scale": 10.0},
        {"year": 2025, "month": 3, "meeting": "全国两会", "policy": "赤字率4% + 新增政府债务11.86万亿", "scale": 11.86},
    ]
    df = pd.DataFrame(events)
    df["size"] = (14 + 10 * df["scale"] ** 0.5).round(1)
    df["hover"] = df.apply(lambda r: f"{r['year']}年{r['month']}月：{r['meeting']}<br>{r['policy']}", axis=1)

    fig = go.Figure()
    for mt, color in _CAT_COLORS.items():
        sub = df[df["meeting"] == mt]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["month"], y=sub["year"], mode="markers", name=mt,
            marker=dict(size=sub["size"], color=color, opacity=0.85,
                        line=dict(width=1, color="rgba(255,255,255,0.8)")),
            customdata=sub["hover"],
            hovertemplate="%{customdata}<extra></extra>",
        ))

    # 关键会议月份参考线 + 顶部标签
    for m, label in [(3, "两会"), (7, "政治局"), (10, "人大常委会"), (12, "中央经济工作会议")]:
        fig.add_vline(x=m, line_width=1, line_dash="dot", line_color=_INK_MUTED, opacity=0.6)
        fig.add_annotation(x=m, y=2026.1, text=label, showarrow=False,
                           font=dict(size=10, color=_INK_MUTED), yanchor="bottom")

    fig.update_xaxes(title="月份", tickvals=list(range(1, 13)),
                     ticktext=[f"{i}月" for i in range(1, 13)], range=[0.2, 12.8],
                     gridcolor=_GRID, zeroline=False)
    fig.update_yaxes(title="年份", tickvals=list(range(2019, 2026)),
                     range=[2018.4, 2026.4], gridcolor=_GRID, zeroline=False)
    fig.update_layout(
        title=dict(text="中国增量财政政策 × 重大会议时间博弈", font=dict(size=16, color=_INK)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_INK, size=12),
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(color=_INK_SECONDARY)),
        hoverlabel=dict(bgcolor="white", font=dict(color=_INK)),
        height=480,
    )
    return fig


def _build_basel_evolution_fig() -> go.Figure:
    """图表二：Basel III Endgame 资本要求演变（阶梯下行 + 投行观点标注）。"""
    x = ["2023.7<br>首发", "2024<br>修订", "2025.10<br>新框架", "2026.3<br>重提案"]
    y = [19, 9, 5, 5]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers+text",
        line=dict(color="#2a78d6", width=3, shape="hv"),
        marker=dict(size=11, color="#2a78d6", line=dict(width=2, color="white")),
        text=["19%", "9%", "3%–7%", "3%–7%"],
        textposition="top center", textfont=dict(color=_INK, size=12),
        hovertemplate="%{x}：大型银行资本增幅约 %{y}%<extra></extra>",
        name="资本增幅要求",
    ))

    fig.add_annotation(x="2026.3<br>重提案", y=13.5, text="释放约 1750 亿美元<br>超额资本",
                       showarrow=True, arrowhead=2, arrowwidth=1.5, arrowcolor=_INK_SECONDARY,
                       ax="2026.3<br>重提案", ay=6.5,
                       font=dict(color=_INK, size=12), bgcolor="white",
                       bordercolor=_GRID, borderwidth=1, borderpad=6)
    fig.add_annotation(x="2024<br>修订", y=15.5, text="六大行 Q3 回购<br>同比 +75%",
                       showarrow=True, arrowhead=2, arrowwidth=1.5, arrowcolor=_INK_SECONDARY,
                       ax="2024<br>修订", ay=7.5,
                       font=dict(color=_INK, size=12), bgcolor="white",
                       bordercolor=_GRID, borderwidth=1, borderpad=6)

    fig.update_yaxes(range=[0, 20], title="大型银行资本增幅要求（%）",
                     gridcolor=_GRID, zeroline=True, zerolinecolor=_INK_MUTED)
    fig.update_xaxes(showgrid=False)
    fig.update_layout(
        title=dict(text="Basel III Endgame 资本要求演变：监管大松绑", font=dict(size=16, color=_INK)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_INK, size=12),
        margin=dict(l=10, r=10, t=60, b=10),
        hoverlabel=dict(bgcolor="white", font=dict(color=_INK)),
        height=440,
    )
    return fig


def _render_deep_research_page() -> None:
    st.subheader("📈 深度研究可视化")
    st.caption("基于 deep_research/deep_research_macro_policies.md 的硬数据，交互式呈现政策博弈与监管松绑。")

    st.plotly_chart(_build_fiscal_calendar_fig(), use_container_width=True)
    st.caption("气泡大小 ∝ 政策规模（万亿）；灰色虚线为两会 / 政治局 / 人大常委会 / 中央经济工作会议的固定月份。")
    st.markdown("---")
    st.plotly_chart(_build_basel_evolution_fig(), use_container_width=True)
    st.caption("19%（2023 提案）→ 9%（2024 修订）→ 3%–7%（2025.10 新框架 / 2026.3 重提案）。2025.6 压力测试 22 家大行中 21 家 SCB 下降。")


def _render_factor_page() -> None:
    st.subheader("📊 因子分析")
    st.info("因子挖掘与回测模块正在建设中 (Under Construction)")


def _llm_complete(prompt: str, max_tokens: int = 2000) -> str | None:
    """复用 daily_macro_monitor.py 的 LLM 接口配置（Anthropic 兼容 /v1/messages）。"""
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
    model = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"
    if not token:
        return None
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
    }
    try:
        resp = requests.post(f"{base_url}/v1/messages", json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            b.get("text", "") for b in data.get("content") or []
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip().strip('"\'“”')
        return text or None
    except Exception:
        return None


def _rag_answer(query: str, hits: list[dict]) -> tuple[str, list[dict]]:
    """把检索命中作为 Context 交给大模型，综合点评并强制引用来源。"""
    if not hits:
        return ("未在内部知识库（raw_docs）中检索到与问题相关的历史材料，"
                "因此无法基于内部观点回答，也不作凭空判断。"), []

    context = "\n".join(
        f"[片段{i}]（来源：《{h['source']}》）\n{h['text']}"
        for i, h in enumerate(hits, 1)
    )
    prompt = (
        "你是一个专业分析师。请根据以下提供的内部历史研报（Context），回答用户的问题。\n"
        "要求：\n"
        "1. 回答必须明确引用来源，例如「根据《xx纪要》显示……」。\n"
        "2. 如果 Context 中没有与问题相关的信息，请诚实说明，禁止编造。\n\n"
        f"【Context（内部历史研报片段）】\n{context}\n\n"
        f"【用户问题】\n{query}"
    )
    answer = _llm_complete(prompt)
    if not answer:
        return ("（大模型调用失败，无法生成点评。以下为检索到的原始材料，仅供参考。）", hits)
    return answer, hits


def _render_rag_page() -> None:
    st.subheader("📚 内部知识库（RAG 问答助手）")
    st.caption("检索内部研报 / 会议纪要，并由大模型综合点评、强制引用来源。")

    if "rag_messages" not in st.session_state:
        st.session_state.rag_messages = []

    # 渲染历史对话
    for msg in st.session_state.rag_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                with st.expander("参考来源", expanded=False):
                    for s in msg["sources"]:
                        st.markdown(f"**`[引用自: {s['source']}]`** · 相关度 {s['score']:.3f}")
                        st.markdown(s["text"])
                        st.markdown("---")

    query = st.chat_input("输入你的问题，例如：我们上周对碳化硅的看法是什么？")
    if query:
        st.session_state.rag_messages.append({"role": "user", "content": query, "sources": []})
        with st.chat_message("user"):
            st.markdown(query)

        with st.chat_message("assistant"):
            with st.spinner("正在检索内部知识库并生成点评…"):
                hits = search_internal_knowledge(query, top_k=3)
                answer, sources = _rag_answer(query, hits)
            st.markdown(answer)
            if sources:
                with st.expander("参考来源", expanded=False):
                    for s in sources:
                        st.markdown(f"**`[引用自: {s['source']}]`** · 相关度 {s['score']:.3f}")
                        st.markdown(s["text"])
                        st.markdown("---")
            else:
                st.caption("（未命中内部知识库，无参考来源）")

        st.session_state.rag_messages.append({"role": "assistant", "content": answer, "sources": sources})


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="投研数据监控台", layout="wide", page_icon="📊")
    _inject_css()

    with st.sidebar:
        st.title("投研数据监控台")
        st.caption("数据来源：万得 Wind 金融数据服务")

        page = st.radio(
            "导航",
            [
                "🌍 宏观与大类资产",
                "🏭 行业动态 (细分产业)",
                "📈 深度研究可视化",
                "📊 因子分析 (待建)",
                "📚 内部知识库 (RAG)",
            ],
        )

        st.markdown("---")
        if st.button("重新生成今日报告"):
            with st.spinner("正在生成今日报告，请稍候…"):
                ok, msg, detail = _run_generator()
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
                if detail:
                    st.code(detail)

    if page == "🌍 宏观与大类资产":
        _render_macro_page()
    elif page == "🏭 行业动态 (细分产业)":
        _render_industry_page()
    elif page == "📈 深度研究可视化":
        _render_deep_research_page()
    elif page == "📊 因子分析 (待建)":
        _render_factor_page()
    else:
        _render_rag_page()

    st.markdown("---")
    st.caption("本页内容为数据与资讯复盘，不构成任何投资建议。")


if __name__ == "__main__":
    main()
