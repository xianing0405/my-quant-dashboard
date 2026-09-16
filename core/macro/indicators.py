"""宏观指标：登记常用宏观指标的标识与口径，供 data 层取数与 core 层计算使用。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MacroIndicator:
    code: str          # 指标代码（如 CPI_YOY、PMI）
    name: str          # 中文名
    frequency: str     # 频率：月度 / 季度 / 年度
    unit: str          # 单位


# 常用宏观指标登记（示例，按需扩展）
MACRO_INDICATORS = [
    MacroIndicator("CPI_YOY", "居民消费价格指数同比", "月度", "%"),
    MacroIndicator("PPI_YOY", "工业生产者出厂价格指数同比", "月度", "%"),
    MacroIndicator("PMI", "制造业采购经理指数", "月度", "—"),
    MacroIndicator("M2_YOY", "广义货币供应量同比", "月度", "%"),
    MacroIndicator("GDP_YOY", "国内生产总值同比", "季度", "%"),
]


def find_indicator(code: str) -> MacroIndicator | None:
    """按代码查找已登记的宏观指标。"""
    for ind in MACRO_INDICATORS:
        if ind.code == code:
            return ind
    return None
