#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大模型调用封装（Anthropic 兼容 /v1/messages）。

复用项目既有接口约定（与 daily_macro_monitor / industry_tracker 一致）：
  ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY
  ANTHROPIC_BASE_URL（默认 https://api.deepseek.com/anthropic）
  ANTHROPIC_DEFAULT_HAIKU_MODEL（默认 deepseek-v4-pro）

任何调用失败都返回 None，绝不伪造结果；上层据此展示「模型不可用」状态。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

import requests

_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"

# 实际模型调用统计（来自 API 返回的 usage，不编造；仅本进程内累计）
_usage_log: list[dict] = []
_usage_lock = threading.Lock()


def _token() -> str | None:
    return os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")


def available() -> bool:
    return bool(_token())


def usage_stats() -> dict:
    """返回本进程累计的实际模型调用统计（请求次数 / 输入输出 token / 模型名）。"""
    with _usage_lock:
        calls = list(_usage_log)
    total_in = sum(c.get("input_tokens") or 0 for c in calls)
    total_out = sum(c.get("output_tokens") or 0 for c in calls)
    return {
        "calls": len(calls),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "model": _MODEL,
        "base_url": _BASE_URL,
        "last_calls": calls[-20:],
    }


def complete(prompt: str, *, max_tokens: int = 2000, system: str | None = None) -> str | None:
    """单轮补全，返回纯文本；失败返回 None。"""
    token = _token()
    if not token or not prompt:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": _MODEL, "max_tokens": max_tokens, "messages": messages}
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
    }
    try:
        resp = requests.post(f"{_BASE_URL}/v1/messages", json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        # 记录真实 usage（不编造；无 usage 字段时记 None）
        u = data.get("usage") or {}
        with _usage_lock:
            _usage_log.append({
                "model": _MODEL,
                "input_tokens": u.get("input_tokens"),
                "output_tokens": u.get("output_tokens"),
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        text = "".join(
            b.get("text", "") for b in (data.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip().strip('"\'“”')
        return text or None
    except Exception:  # noqa: BLE001
        return None


def complete_json(prompt: str, *, system: str | None = None, max_tokens: int = 3000) -> dict | None:
    """请求返回 JSON，尽力解析；失败返回 None。"""
    raw = complete(prompt, max_tokens=max_tokens, system=system)
    if not raw:
        return None
    # 剥离可能的 ```json 围栏
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
