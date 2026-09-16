"""产业动态信号：供给、需求、景气度、价格等产业信号的登记与处理。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IndustrySignal:
    name: str        # 信号名，如「产能利用率」「排产同比」
    direction: str   # 领先 / 同步 / 滞后
    unit: str        # 单位


# 产业信号登记（示例，按需扩展）
INDUSTRY_SIGNALS = [
    IndustrySignal("月度排产同比", "领先", "%"),
    IndustrySignal("产能利用率", "同步", "%"),
    IndustrySignal("产品价格", "同步", "元"),
]
