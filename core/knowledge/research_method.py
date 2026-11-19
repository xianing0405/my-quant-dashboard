#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研报方法应用（持久化版）：方法卡 + 程序计算指标 → 模型做有依据分析。

数据流：
  迁移：本地 research/ 文件（方法卡/证据/指标 CSV）→ storage（幂等，只做一次或重复无害）。
  运行时：一律从 storage 读方法/证据/指标，不依赖本地 research/ 文件。
  生成：程序计算指标与边际变化 → 组装输入 → llm.complete → 保存结果到 storage。

日期语义：
  mode="latest"：使用 storage 中最新的指标日期，标「数据新鲜度未验证」（不把缓存最新当市场最新）。
  mode="historical"：用户指定日期复盘，标「历史复盘」；早于报告可用日期时标「事后应用该方法的历史演示」。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from . import llm, storage
from .auth import is_admin, is_demo_readonly, require_admin
from .models import new_id

# 迁移源（本地 research/ 文件，仅迁移时读取）
_METHOD_DIR = Path(__file__).resolve().parents[2] / "research" / "risk_appetite_method_v1"
_METHOD_CARD = _METHOD_DIR / "method_card.md"
_EVIDENCE_FILE = _METHOD_DIR / "evidence.json"
_DETAIL_CSV = _METHOD_DIR / "data" / "detail.csv"

# 稳定标识（幂等迁移）
_METHOD_KEY = "a_share_risk_appetite_v1"
_METHOD_ID = "rm_a_share_risk_appetite_v1"
_REPORT_AVAILABLE_FROM = "2026-10-17"  # 报告覆盖周 10-12~16，方法可用日近似取报告发布日
_SCHEME_A = "A_滚动120日Z"  # 指标计算口径（方案A：逐日滚动120日Z）

_PROMPT = """你是金融研究助理。基于下面的「方法卡」「程序计算的指标」「带编号原文证据」「辅助数据状态」，对风险偏好状态做一次克制解读。不要凭记忆补充任何最新行情、利率或资金流。

【方法卡】
{method_card}

【数据口径】
- {cutoff_note}
- 标准化口径：方案A（逐日滚动120日Z，实时信号口径；方案B仅用于历史轨迹展示，不用于本信号）

【程序计算的指标（勿自行计算）】
当前（{cur_date}）：X={cur_x}，Y={cur_y}，A_raw(21/250量比)={cur_a}，B_raw(21日超额)={cur_b}，象限={cur_q}
上一期（{prev_date}）：X={prev_x}，Y={prev_y}，A_raw={prev_a}，B_raw={prev_b}，象限={prev_q}
边际变化：ΔX={dx}，ΔY={dy}，ΔA={da}，ΔB={db}
近{hist_n}日序列（date X Y 象限）：
{history}

【带编号原文证据（引用只能来自这里）】
{evidence}

【辅助数据状态】
已具备：{available}
缺失：{missing}

【输出要求】严格按五段输出，每段只写能被上面材料支持的内容，不堆砌、不编造：
一、当前状态及边际变化：说明所处象限（状态），以及相比上一期改善还是走弱（边际）。若连续数据不足以判断趋势，明确写「无连续数据，不足以判断趋势」，不得写「持续下降/持续回升」。
二、报告使用的分析逻辑：引用对应证据编号，说明报告如何用该指标联系债市。
三、当前证据支持什么、尚不能判断什么：区分「指标能支持的状态判断」与「需资金面/资金流才能判断的债市方向」；缺失数据明确写缺口，不得推断资金流向或资产方向，不得推出「债市必涨/必跌」。
四、下一步需观察的关键条件：只列与上述逻辑相关的条件；属原型自行提出的跟踪规则标注【原型规则】。
五、引用与数据：列出用到的证据编号与数据日期。"""

# 方法元数据（供迁移写入 storage，与 method_card.md 保持一致；params/unconfirmed 为程序可读摘要）
_METHOD_PARAMS = {
    "X轴原始量": "过去1个月/过去1年日均成交量比（原文）；原型=万得全A·VOLUME、21/250交易日（自选）",
    "Y轴原始量": "过去1个月科技指数相对大盘指数超额（原文）；原型=创业50+科创50日收益率各50%、21日累计收益差（自选）",
    "标准化": "过去120交易日Z-score（原文）；原型=方案A滚动/方案B固定参考、ddof=1、含当日（自选）",
    "科技指数": "创业50 + 科创50 等权（原文）",
    "大盘指数": "沪深300（原文）",
    "频率": "日线（原型）",
}
_METHOD_UNCONFIRMED = [
    "横轴「成交量」的市场范围（原型用万得全A·VOLUME，非原文明确）",
    "「1个月/1年」具体交易日数（原型 21/250）",
    "等权与超额的具体算法（日收益率等权、分别连乘，均为自选）",
    "Z-score 的 ddof 与是否含当日（原型 ddof=1、含当日）",
    "虚线中性带阈值/用途（未披露）",
    "方案A(滚动)与方案B(固定参考)哪个是报告的展示口径（未确认）",
]


def _f(x) -> float | None:
    if x in ("", "nan", None):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 迁移：本地文件 → storage（幂等）
# ---------------------------------------------------------------------------
def migrate_research_method(store=None) -> dict:
    """把方法卡、证据、指标从本地 research/ 迁移到目标存储。重复执行不产生重复记录。

    store 默认走 config.storage_backend()；显式传入 PostgresStorage 可迁移到云端。
    """
    store = store or storage.get_store()

    evidence = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))["evidence"]
    card = _METHOD_CARD.read_text(encoding="utf-8")
    src_url = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))["source_url"]

    # 版本保护：同版本（method_id）内容变化时拒绝静默覆盖，要求新版本
    existing = store.get_method_by_key(_METHOD_KEY)
    if existing and (existing.get("method_card_md") or "") != card:
        return {"method": 0, "indicators": 0, "errors": 0,
                "error": "方法卡内容已变化，需 bump 版本（新 method_id/method_key），拒绝静默覆盖现有版本"}

    method_payload = {
        "method_id": _METHOD_ID,
        "method_key": _METHOD_KEY,
        "name": "A股二维风险偏好指标（研究原型 v1）",
        "version": "v1",
        "institution": "兴业证券（经济与金融研究院）",
        "source_url": src_url,
        "report_title": "债市｜债市行情加速中，波动变大",
        "evidence": evidence,
        "params": _METHOD_PARAMS,
        "unconfirmed": _METHOD_UNCONFIRMED,
        "method_card_md": card,
        "created_at": storage.now_iso(),
        "updated_at": storage.now_iso(),
    }
    store.upsert_method(method_payload)

    n_ind = 0
    errors = 0
    with open(_DETAIL_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["quadrant"] == "不可计算":
                continue
            try:
                store.upsert_indicator(_METHOD_ID, r["date"], {
                    "scheme": _SCHEME_A,
                    "X": _f(r["X"]), "Y": _f(r["Y"]),
                    "A_raw": _f(r["A_raw"]), "B_raw": _f(r["B_raw"]),
                    "quadrant": r["quadrant"],
                    "history": [],  # 历史序列在读取时按需重建
                    "data_source": "Wind（本地缓存）",
                    "computed_at": storage.now_iso(),
                })
                n_ind += 1
            except Exception:  # noqa: BLE001
                errors += 1

    return {"method": 1, "indicators": n_ind, "errors": errors}


# ---------------------------------------------------------------------------
# 运行时读取（只读 storage，不依赖本地文件）
# ---------------------------------------------------------------------------
def _method_from_store() -> dict:
    m = storage.get_store().get_method_by_key(_METHOD_KEY)
    if not m:
        raise RuntimeError("方法卡尚未迁移到持久化后端，请先执行 migrate_research_method()")
    return m


def load_indicators(cutoff: str, history_days: int = 8) -> dict:
    """从 storage 读取截止日的当前/上一期指标与历史序列（纯程序，不调模型）。"""
    inds = storage.get_store().list_indicators(_METHOD_ID, scheme=_SCHEME_A)  # 已按日期升序
    idx = None
    for i, r in enumerate(inds):
        if r["date"] <= cutoff:
            idx = i
        else:
            break
    if idx is None:
        return {"ok": False, "error": f"截止日期 {cutoff} 早于首个指标日 {inds[0]['date']}，无法计算"}
    if idx < 1:
        return {"ok": False, "error": f"截止日期 {cutoff} 缺少上一期数据，无法计算边际变化"}

    cur, prev = inds[idx], inds[idx - 1]
    hist = inds[max(0, idx - history_days + 1): idx + 1]
    return {
        "ok": True,
        "cutoff": cutoff,
        "latest_indicator_date": inds[-1]["date"],
        "current": {"date": cur["date"], "X": _f(cur["X"]), "Y": _f(cur["Y"]),
                    "A_raw": _f(cur["A_raw"]), "B_raw": _f(cur["B_raw"]), "quadrant": cur["quadrant"]},
        "previous": {"date": prev["date"], "X": _f(prev["X"]), "Y": _f(prev["Y"]),
                     "A_raw": _f(prev["A_raw"]), "B_raw": _f(prev["B_raw"]), "quadrant": prev["quadrant"]},
        "history": [{"date": r["date"], "X": _f(r["X"]), "Y": _f(r["Y"]), "quadrant": r["quadrant"]} for r in hist],
    }


def _fmt_history(hist: list[dict]) -> str:
    return "\n".join(f"- {h['date']} X={h['X']:.2f} Y={h['Y']:.2f} {h['quadrant']}" for h in hist)


def _evidence_block(evidence: list[dict]) -> str:
    return "\n".join(f"[证据:{e['id']}]（{e['location']}）{e['text']}" for e in evidence)


# ---------------------------------------------------------------------------
# 管理员或只读演示可读的读取入口（服务层权限门）
# ---------------------------------------------------------------------------
def _require_readable(user: str | None, action: str) -> None:
    if is_admin(user) or is_demo_readonly(user):
        return
    require_admin(user, action)  # 抛 KnowledgeAuthError


def list_methods(user: str | None) -> list[dict]:
    _require_readable(user, "查看研究方法列表")
    return storage.get_store().list_methods()


def get_method_snapshot(user: str | None, method_key: str = _METHOD_KEY) -> dict:
    _require_readable(user, "查看研究方法")
    m = storage.get_store().get_method_by_key(method_key)
    if not m:
        raise LookupError(f"方法 {method_key} 未迁移")
    return m


def get_indicators(user: str | None, cutoff: str, history_days: int = 8) -> dict:
    _require_readable(user, "查看研究方法指标")
    return load_indicators(cutoff, history_days)


def list_results(user: str | None, method_id: str | None = None) -> list[dict]:
    _require_readable(user, "查看研究方法生成结果")
    return storage.get_store().list_results(method_id or _METHOD_ID)


def apply_method(
    *,
    mode: str,  # "latest" | "historical"
    cutoff: str | None = None,  # historical 模式必填
    user: str | None,
    aux_available: list[str] | None = None,
    aux_missing: list[str] | None = None,
    history_days: int = 8,
    save: bool = True,
) -> dict[str, Any]:
    """可重复调用入口：无模型校验 → 组装输入 → llm.complete → 保存结果。"""
    require_admin(user, "应用研报方法生成分析")

    try:
        method = _method_from_store()
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "model_called": False, "output": None}

    store = storage.get_store()

    # 1. 决定 cutoff 与日期语义
    if mode == "latest":
        cutoff = store.latest_indicator_date(_METHOD_ID)
        if not cutoff:
            return {"ok": False, "error": "存储中无指标数据", "model_called": False, "output": None}
        cutoff_note = (f"数据截止 {cutoff}（最新分析口径）。数据新鲜度未验证：未检查 Wind 数据源"
                       f"是否有比缓存更新的数据，不把缓存最新当作市场最新。")
    elif mode == "historical":
        if not cutoff:
            return {"ok": False, "error": "历史复盘模式需指定日期", "model_called": False, "output": None}
        if cutoff < _REPORT_AVAILABLE_FROM:
            cutoff_note = (f"历史复盘：截至 {cutoff} 的历史数据。事后应用该方法的历史演示"
                           f"（方法所依据报告约 2026-10-17 可用），非当时已发出的实时信号。")
        else:
            cutoff_note = f"历史复盘：截至 {cutoff} 的历史数据（事后回看，非当前信号）。"
    else:
        return {"ok": False, "error": f"未知模式 {mode}", "model_called": False, "output": None}

    # 2. 无模型校验：指标由程序算出，缺字段报错、不填零、不调模型
    ind = load_indicators(cutoff, history_days)
    if not ind.get("ok"):
        return {"ok": False, "error": ind["error"], "model_called": False, "output": None}
    cur, prev = ind["current"], ind["previous"]
    for k in ("X", "Y", "A_raw", "B_raw"):
        if cur.get(k) is None or prev.get(k) is None:
            return {"ok": False, "error": f"指标字段缺失（{cutoff} 或上一期），无法计算", "model_called": False, "output": None}

    aux_available = aux_available or []
    aux_missing = aux_missing or []

    # 3. 组装输入（方法卡、证据均来自 storage）
    evidence = method.get("evidence") or []
    dx = cur["X"] - prev["X"]; dy = cur["Y"] - prev["Y"]
    da = cur["A_raw"] - prev["A_raw"]; db = cur["B_raw"] - prev["B_raw"]
    prompt = _PROMPT.format(
        method_card=method.get("method_card_md", ""),
        cutoff_note=cutoff_note,
        cur_date=cur["date"], cur_x=round(cur["X"], 3), cur_y=round(cur["Y"], 3),
        cur_a=round(cur["A_raw"], 4), cur_b=round(cur["B_raw"], 4), cur_q=cur["quadrant"],
        prev_date=prev["date"], prev_x=round(prev["X"], 3), prev_y=round(prev["Y"], 3),
        prev_a=round(prev["A_raw"], 4), prev_b=round(prev["B_raw"], 4), prev_q=prev["quadrant"],
        dx=f"{dx:+.3f}", dy=f"{dy:+.3f}", da=f"{da:+.4f}", db=f"{db:+.4f}",
        hist_n=len(ind["history"]), history=_fmt_history(ind["history"]),
        evidence=_evidence_block(evidence),
        available="、".join(aux_available) if aux_available else "（无）",
        missing="、".join(aux_missing) if aux_missing else "（无）",
    )

    # 4. 调用模型（每个场景一次）
    output = llm.complete(prompt, max_tokens=3000)
    if output is None:
        err = llm.last_error() or {}
        return {"ok": False, "error": f"模型调用失败：{err.get('detail', '未知')}", "model_called": True, "output": None}

    stats = llm.usage_stats()
    last_call = stats["last_calls"][-1] if stats["last_calls"] else {}

    # 5. 保存结果（幂等由 result_id 保证）
    result_id = None
    if save:
        result_id = new_id("rrm")
        store.save_result(result_id, {
            "method_id": _METHOD_ID, "method_version": method.get("version"),
            "input_date": cutoff, "mode": mode,
            "evidence_ids": [e["id"] for e in evidence],
            "evidence_snapshot": evidence,  # 证据内容快照（含 location/text），非仅编号
            "input_snapshot": {  # 方法参数 + 指标输入依据快照
                "params": method.get("params"),
                "unconfirmed": method.get("unconfirmed"),
                "scheme": _SCHEME_A,
                "current": cur, "previous": prev,
            },
            "output_markdown": output,
            "model": stats["model"],
            "input_tokens": last_call.get("input_tokens"),
            "output_tokens": last_call.get("output_tokens"),
            "created_at": storage.now_iso(),
        })

    return {
        "ok": True,
        "model_called": True,
        "mode": mode,
        "cutoff": cutoff,
        "cutoff_note": cutoff_note,
        "method_id": _METHOD_ID,
        "method_version": method.get("version"),
        "current": cur,
        "previous": prev,
        "evidence_ids": [e["id"] for e in evidence],
        "output": output,
        "result_id": result_id,
        "model": stats["model"],
        "usage": {"input_tokens": last_call.get("input_tokens"), "output_tokens": last_call.get("output_tokens"),
                  "cache_read_input_tokens": last_call.get("cache_read_input_tokens"),
                  "cache_creation_input_tokens": last_call.get("cache_creation_input_tokens")},
        "request_id": last_call.get("request_id"),
    }


# ---------------------------------------------------------------------------
# 本地测试
# ---------------------------------------------------------------------------
def test_latest(user: str = "admin") -> dict:
    return apply_method(mode="latest", user=user,
                        aux_missing=["资金面实时值(R001/DR001)", "资金流(纯债基金净申购/固收+申赎)", "债市利率实时值"])


def test_historical(cutoff: str, user: str = "admin") -> dict:
    return apply_method(mode="historical", cutoff=cutoff, user=user,
                        aux_missing=["资金面实时值(R001/DR001)", "资金流(纯债基金净申购/固收+申赎)", "债市利率实时值"])


if __name__ == "__main__":
    print("迁移：", migrate_research_method())
