"""产业链：定义产业链环节与上下游关系，用于结构化的产业动态分析。"""
from dataclasses import dataclass, field


@dataclass
class ChainNode:
    name: str                      # 环节名称，如「上游原材料」
    description: str = ""          # 环节说明
    downstream: list = field(default_factory=list)  # 下游环节名列表


# 示例：某产业链的简化登记（占位，按需扩展）
EXAMPLE_CHAIN = [
    ChainNode("上游原材料", "矿产 / 基础原料", ["中游制造"]),
    ChainNode("中游制造", "核心零部件 / 整机", ["下游应用"]),
    ChainNode("下游应用", "终端需求", []),
]
