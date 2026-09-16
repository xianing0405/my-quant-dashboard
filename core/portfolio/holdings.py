"""持仓管理：持仓条目与组合台账的确定性计算。"""
from dataclasses import dataclass


@dataclass
class Holding:
    code: str             # 标的代码
    name: str = ""        # 名称
    quantity: float = 0.0    # 数量
    cost_price: float = 0.0  # 成本价


def position_value(holding: Holding, price: float | None) -> float | None:
    """持仓市值 = 数量 × 现价。"""
    if price is None:
        return None
    return holding.quantity * price


def position_pnl(holding: Holding, price: float | None) -> float | None:
    """持仓盈亏 = (现价 - 成本价) × 数量。"""
    if price is None:
        return None
    return (price - holding.cost_price) * holding.quantity
