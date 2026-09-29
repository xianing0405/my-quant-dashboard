#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业主题库（持续维护的主题 / 公司关联 / 原文证据 / 历史版本）。

与「每日新闻概念提取」不同：主题是长期稳定的研究单元，不因某天没有新闻而消失。
数据文件（JSON，随 Git 版本化）位于 data/themes/：
  - themes.json     主题注册表（theme_id / 名称 / 定义 / 别名 / 产业链环节）
  - members.json    主题成员（公司 + 证券代码 + 关联类型 + 状态 + 证据 + 生效时间 + 版本）
  - evidence.json   原文证据（来源文本 / 链接 / 日期 / 类型）
  - history.json    成员历史版本快照（避免用今日名单回算过去）

纪律：
  - 状态仅「待核验」/「已确认」。无可靠证据的成员留空或标「待核验」，不编造。
  - 证券代码经可验证的证券主表匹配，模型不猜代码；未匹配时 security_code 为 null。
  - 历史成员保留（version + effective_from/effective_to），不覆盖旧记录。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent            # Website/core/industry/
DATA_DIR = HERE.parent.parent / "data" / "themes"  # Website/data/themes/

STATUS_PENDING = "待核验"
STATUS_CONFIRMED = "已确认"

# 关联类型（已有业务/在建项目/研发/客户或供应商关系）
ASSOCIATION_TYPES = ["已有业务", "在建项目", "研发", "客户或供应商关系"]

# 证据来源类型（区分资讯/研报/公告/AI推断）
EVIDENCE_TYPES = ["资讯", "研报", "公告", "AI推断"]


# ---------------------------------------------------------------------------
# JSON 读写（缺失或损坏时返回空，不阻断页面）
# ---------------------------------------------------------------------------
def _load(filename: str, default: dict) -> dict:
    path = DATA_DIR / filename
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def list_themes() -> list[dict]:
    return _load("themes.json", {"themes": []}).get("themes", [])


def list_members(theme_id: str | None = None) -> list[dict]:
    ms = _load("members.json", {"members": []}).get("members", [])
    return [m for m in ms if theme_id is None or m.get("theme_id") == theme_id]


def list_evidence(theme_id: str | None = None) -> list[dict]:
    es = _load("evidence.json", {"evidence": []}).get("evidence", [])
    return [e for e in es if theme_id is None or e.get("theme_id") == theme_id]


def list_member_history(theme_id: str | None = None) -> list[dict]:
    hs = _load("history.json", {"history": []}).get("history", [])
    return [h for h in hs if theme_id is None or h.get("theme_id") == theme_id]


# ---------------------------------------------------------------------------
# 服务函数
# ---------------------------------------------------------------------------
def theme_summary() -> list[dict]:
    """主题总览：成员覆盖 / 状态统计。行情指标由上层在取得行情后补充（无行情时留空）。"""
    themes = list_themes()
    members = list_members()
    out = []
    for t in themes:
        ms = [m for m in members if m.get("theme_id") == t.get("theme_id")]
        out.append({
            **t,
            "member_total": len(ms),
            "member_confirmed": sum(1 for m in ms if m.get("status") == STATUS_CONFIRMED),
            "member_pending": sum(1 for m in ms if m.get("status") == STATUS_PENDING),
            # 行情指标：未取得行情时置 None，页面显示「暂无」
            "return_pct": None,
            "up_ratio": None,
            "turnover": None,
        })
    return out


def get_theme(theme_id: str) -> dict | None:
    for t in list_themes():
        if t.get("theme_id") == theme_id:
            return t
    return None
