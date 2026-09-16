# 核心逻辑层（core/）

承载本项目的核心分析逻辑，明确划分三大模块。所有模块的数字计算统一走 `calc.py`，禁止模块内手搓公式。

## 三大模块

| 模块 | 职责 | 关键文件 |
| --- | --- | --- |
| `macro/` | 宏观指标与大类资产配置 | `indicators.py`、`asset_allocation.py` |
| `industry/` | 产业链与细分产业动态 | `chain.py`、`signals.py` |
| `portfolio/` | 持仓管理与因子预留 | `holdings.py`、`factors.py` |

## 确定性计算 calc.py

所有金额 / 比率 / 倍数 / 年化的计算必须经 `core/calc.py` 的函数完成，返回值与中间量可追踪、可复现。LLM 只负责选输入、解释输出，计算本身用确定性函数完成。
