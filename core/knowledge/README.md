# 内部知识库（core/knowledge/）

本地 RAG（检索增强生成）检索端，为市场点评与策略问答提供团队内部历史材料引用能力。
采用轻量 TF-IDF 关键词检索，不引入向量模型、不做微调。

## 目录结构

- `raw_docs/`：存放内部原始材料（.txt / .md / .markdown / .rtf，自动解码 RTF）。把历史研报、会议纪要等
  直接放入即可，文件名即引用时的来源标识。
- `index/`：存放检索索引文件 `knowledge_index.json`（首次检索时自动生成，raw_docs
  内容变更后自动重建）。

## 使用

```python
from core.knowledge.local_rag import search_internal_knowledge

hits = search_internal_knowledge("美联储加息对A股的影响", top_k=3)
for h in hits:
    print(h["source"], h["score"])
    print(h["text"])
```

返回每个命中项字段：`source`（来源文件名）、`path`（绝对路径）、`text`（段落原文）、
`score`（余弦相似度，0~1）。

命令行测试：

```bash
python -m core.knowledge.local_rag "美联储加息" 3
```

## 检索原理（基础版）

- 英文/数字整体成词；中文拆为单字 + 二字组（bigram），无需分词器。
- 段落级 TF-IDF 加权余弦相似度，按相关度降序返回前 top_k 条。

## 纪律约束（见 AGENTS.md）

进行市场点评或策略问答时，必须优先调用 `search_internal_knowledge` 查找内部历史
材料；若命中历史观点，须以 `[引用自: 文件名]` 标注来源，禁止凭空捏造内部观点。

## 后续可升级方向

当前为纯关键词/TF-IDF 基础版。如内部材料规模变大、需要语义检索，可在此模块内
替换为向量检索（embedding + 向量索引），对外接口 `search_internal_knowledge` 保持
不变。
