#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大模型调用封装（Anthropic 兼容 /v1/messages）。

诊断：区分 配置缺失 / 网络错误 / HTTP 错误 / 超时 / 空响应 / 截断 / JSON 解析失败，
记录脱敏的错误类型、状态码、耗时、request_id、结束原因与 usage（不记录密钥/正文）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

import requests

_BASE_URL = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic").rstrip("/")
_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or "deepseek-v4-pro"

_usage_log: list[dict] = []
_last_error: dict = {}
_lock = threading.Lock()


def _token() -> str | None:
    return os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")


def available() -> bool:
    return bool(_token())


def _record_usage(input_tokens, output_tokens, finish_reason=None, request_id=None, status=None):
    with _lock:
        _usage_log.append({
            "model": _MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "finish_reason": finish_reason,
            "request_id": request_id,
            "status": status,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


def _record_error(kind: str, detail: str) -> None:
    with _lock:
        _last_error.clear()
        _last_error.update({"kind": kind, "detail": detail, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


def usage_stats() -> dict:
    """本进程累计的实际模型调用统计 + 最近一次脱敏错误诊断。"""
    with _lock:
        calls = list(_usage_log)
        err = dict(_last_error) if _last_error else None
    total_in = sum(c.get("input_tokens") or 0 for c in calls)
    total_out = sum(c.get("output_tokens") or 0 for c in calls)
    return {
        "calls": len(calls),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "model": _MODEL,
        "base_url": _BASE_URL,
        "last_error": err,
        "last_calls": calls[-20:],
    }


def last_error() -> dict | None:
    with _lock:
        return dict(_last_error) if _last_error else None


def _sanitize_exc(e: Exception) -> str:
    """脱敏异常信息：仅保留异常类型，不含密钥/URL/正文。"""
    return type(e).__name__


def complete(prompt: str, *, max_tokens: int = 2000, system: str | None = None) -> str | None:
    """单轮补全。失败返回 None，并记录脱敏诊断到 usage_stats()['last_error']。"""
    token = _token()
    if not token:
        _record_error("config_missing", "未配置 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY")
        return None
    if not prompt:
        _record_error("config_missing", "prompt 为空")
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

    t0 = time.time()
    try:
        resp = requests.post(f"{_BASE_URL}/v1/messages", json=payload, headers=headers, timeout=120)
    except requests.Timeout:
        _record_error("timeout", "请求超时（>120s）")
        return None
    except requests.RequestException as e:
        _record_error("network_error", _sanitize_exc(e))
        return None
    elapsed = round(time.time() - t0, 2)

    if resp.status_code >= 400:
        rid = resp.headers.get("x-request-id") or resp.headers.get("request-id")
        _record_usage(None, None, status=resp.status_code, request_id=rid)
        _record_error("http_error", f"HTTP {resp.status_code} · 耗时 {elapsed}s")
        return None

    try:
        data = resp.json()
    except json.JSONDecodeError:
        _record_error("parse_failure", f"响应非 JSON · HTTP {resp.status_code} · 耗时 {elapsed}s")
        return None

    rid = data.get("id") or resp.headers.get("x-request-id") or resp.headers.get("request-id")
    u = data.get("usage") or {}
    finish = data.get("stop_reason") or data.get("finish_reason")
    _record_usage(u.get("input_tokens"), u.get("output_tokens"),
                  finish_reason=finish, request_id=rid, status=resp.status_code)

    content = data.get("content") or []
    text = "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip().strip('"\'“”')

    if not text:
        _record_error("empty_response", f"响应无文本 content · finish={finish} · 耗时 {elapsed}s")
        return None
    if finish in ("max_tokens", "length", "tool_calls", "content_filter"):
        _record_error("truncated", f"结束原因 {finish}（可能截断） · 耗时 {elapsed}s")
    return text


def complete_json(prompt: str, *, system: str | None = None, max_tokens: int = 3000) -> dict | None:
    """请求返回 JSON；区分「模型调用失败」与「JSON 解析失败」。失败记录诊断并返回 None。"""
    raw = complete(prompt, max_tokens=max_tokens, system=system)
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
        _record_error("parse_failure", "返回 JSON 不是对象")
        return None
    except json.JSONDecodeError:
        _record_error("parse_failure", "返回文本无法解析为 JSON")
        return None
