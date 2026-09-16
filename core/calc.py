"""确定性计算库：所有金额、比率、倍数的计算必须经本模块，禁止心算。

借鉴 Vibe-Research 的 calc/ 思路：计算用确定性函数完成，结果可复现、可审计；
上游只负责选输入与解释输出，不负责「算」。
"""
from __future__ import annotations


def safe_divide(numerator: float | None, denominator: float | None, default=None):
    """安全除法：numerator / denominator。分母为 0 或任一为 None 时返回 default。"""
    if numerator is None or denominator is None or denominator == 0:
        return default
    return numerator / denominator


def pct_change(prev: float | None, curr: float | None, default=None):
    """环比涨跌幅（%）：(curr - prev) / prev * 100。"""
    if prev is None or curr is None or prev == 0:
        return default
    return (curr - prev) / prev * 100


def cagr(start: float | None, end: float | None, years: float | None, default=None):
    """年化复合增速 CAGR（%）：(end / start) ** (1 / years) - 1。"""
    if start is None or end is None or years in (None, 0):
        return default
    if start <= 0 or end <= 0:
        return default
    return ((end / start) ** (1.0 / years) - 1) * 100
