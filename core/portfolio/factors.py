"""因子库（预留模块）。

预留因子定义与计算接口，供后续量化选股 / 组合优化接入。
当前仅提供占位，不在骨架阶段实现具体因子。
"""


class Factor:
    """因子抽象基类：后续因子的计算统一实现 `compute`。"""

    name: str = "factor"

    def compute(self, universe: list):
        raise NotImplementedError("因子计算逻辑待实现")
