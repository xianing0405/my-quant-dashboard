"""大类资产配置（预留模块）。

预留框架位：股债性价比、美林时钟、风险平价等大类资产配置模型。
当前仅提供占位，后续在此实现确定性配置逻辑。
"""


def equity_risk_premium(equity_earnings_yield: float | None, bond_yield: float | None):
    """股权风险溢价（股债性价比）：盈利收益率 - 债券收益率。"""
    if equity_earnings_yield is None or bond_yield is None:
        return None
    return equity_earnings_yield - bond_yield
