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

import requests

_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"


def _token() -> str | None:
    return os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")


def available() -> bool:
    return bool(_token())


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
