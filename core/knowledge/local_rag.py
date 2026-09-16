#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识检索引擎（RAG 检索端，基础版）

读取 `core/knowledge/raw_docs/` 下的内部材料（.txt / .md / .markdown / .rtf），用轻量
TF-IDF（英文/数字整体成词 + 中文单字与二字组）做段落级余弦相似度检索，返回最相关
段落及其来源文件名。纯标准库实现，不引入向量模型或外部依赖。

检索结果会落一个可复用的索引文件到 `core/knowledge/index/knowledge_index.json`，
当 raw_docs 内容变更（按文件名 + 大小 + mtime 签名判定）时自动重建。

用法：
    from core.knowledge.local_rag import search_internal_knowledge

    hits = search_internal_knowledge("美联储加息对A股的影响", top_k=3)
    for h in hits:
        print(h["source"], h["score"], h["text"])

返回每个命中项的字段：
    source  来源文件名（不含目录）
    path    来源文件绝对路径
    text    命中段落原文
    score   余弦相似度（0~1，越大越相关）
"""

from __future__ import annotations

import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw_docs")
INDEX_DIR = os.path.join(HERE, "index")
INDEX_FILE = os.path.join(INDEX_DIR, "knowledge_index.json")

_EXTENSIONS = {".txt", ".md", ".markdown", ".rtf"}

# 英文/数字整体成词；中文拆成单字 + 二字组（bigram）以应对无分词器场景
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """把文本切成检索 token：英文/数字整体成词，中文做单字与二字组。"""
    text = (text or "").lower()
    tokens: list[str] = []
    tokens.extend(_WORD_RE.findall(text))
    cjk_chars = _CJK_RE.findall(text)
    tokens.extend(cjk_chars)                                            # 单字
    tokens.extend(
        cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)
    )                                                                    # 二字组
    return tokens


def _term_freq(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


def _split_paragraphs(text: str) -> list[str]:
    """按空行或 Markdown 标题切分段落，过滤空段。"""
    text = re.sub(r"\r\n?", "\n", text)
    parts = re.split(r"\n\s*\n|(?=\n#{1,6}\s)", text)
    return [p.strip() for p in parts if p.strip()]


def _list_raw_files() -> list[dict]:
    """列出 raw_docs 下的全部文本文件（仅顶层，不含子目录）。"""
    if not os.path.isdir(RAW_DIR):
        return []
    files = []
    for name in sorted(os.listdir(RAW_DIR)):
        if os.path.splitext(name)[1].lower() not in _EXTENSIONS:
            continue
        path = os.path.join(RAW_DIR, name)
        if os.path.isfile(path):
            files.append({"name": name, "path": path})
    return files


# RTF 中 \'91-\'94 对应智能引号（MacRoman/CP1252 常见写法）
_SMART_QUOTES = {0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201c", 0x94: "\u201d"}

_CJK_PUNCT = "\u3000-\u303f\uff00-\uffef"


def _decode_hex_escapes(rtf: str) -> str:
    """把连续的 \\'hh 十六进制转义成字节，按 GBK（ansicpg936）解码，兼容智能引号。"""
    def _repl(m: re.Match) -> str:
        hexstr = re.sub(r"\\'", "", m.group(0))
        bs = bytes.fromhex(hexstr)
        if all(b < 0x80 for b in bs):
            return bs.decode("ascii", "replace")
        try:
            return bs.decode("gbk")
        except UnicodeDecodeError:
            return "".join(_SMART_QUOTES.get(b, chr(b)) for b in bs)
    return re.sub(r"(?:\\'[0-9a-fA-F]{2})+", _repl, rtf)


def _rtf_to_text(rtf: str) -> str:
    """把 RTF 富文本解码为纯文本（纯标准库，无外部依赖）。

    支持 \\uN 转义、\\'hh 十六进制转义、段落/换行，并丢弃字体表/颜色表等元数据与控制字。
    """
    # 丢弃元数据目标组（字体表/颜色表/样式表等）与 \* 组
    rtf = re.sub(r"{\\\*.*?}", "", rtf, flags=re.S)
    for grp in ("fonttbl", "colortbl", "stylesheet", "info", "listtable", "listoverridetable"):
        rtf = re.sub(r"{\\" + grp + r".*?}", "", rtf, flags=re.S)
    # \uN → Unicode 字符（\uc0 表示其后无回退字符）
    rtf = re.sub(r"\\u(-?\d+)", lambda m: chr(int(m.group(1)) & 0xFFFF), rtf)
    # \'hh → 字节（GBK / 智能引号）
    rtf = _decode_hex_escapes(rtf)
    # 段落 / 换行
    rtf = rtf.replace("\\par", "\n").replace("\\line", "\n")
    # 去掉剩余控制字（\word 或 \wordN）
    rtf = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", rtf)
    rtf = rtf.replace("{", "").replace("}", "")
    rtf = re.sub(r"[ \t]+", " ", rtf)
    # 去除中文与中文标点之间的分隔空格
    rtf = re.sub(rf"(?<=[\u4e00-\u9fff{_CJK_PUNCT}])\s+(?=[\u4e00-\u9fff{_CJK_PUNCT}])",
                 "", rtf)
    rtf = re.sub(r"\n\s*\n+", "\n\n", rtf)
    return rtf.strip()


def _read_text(path: str) -> str:
    """读取文本文件：.rtf 先解码为纯文本，其余按 utf-8 / gbk / utf-16 尝试解码。"""
    with open(path, "rb") as f:
        raw = f.read()
    if os.path.splitext(path)[1].lower() == ".rtf":
        return _rtf_to_text(raw.decode("latin-1"))
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _corpus_signature(files: list[dict]) -> str:
    """用（文件名 + 大小 + mtime）生成签名，判定索引是否过期。"""
    sig = []
    for f in files:
        try:
            st = os.stat(f["path"])
            sig.append(f"{f['name']}|{st.st_size}|{int(st.st_mtime)}")
        except OSError:
            sig.append(f"{f['name']}|missing")
    return "|".join(sorted(sig))


def _build_corpus() -> tuple[list[dict], dict[str, float]]:
    """解析 raw_docs 全部文件为段落，并计算 idf。

    返回 (corpus, idf)。corpus 元素：{source, text, tf}。
    """
    files = _list_raw_files()
    if not files:
        return [], {}

    paragraphs = []
    for f in files:
        text = _read_text(f["path"])
        for para in _split_paragraphs(text):
            tokens = _tokenize(para)
            if not tokens:
                continue
            paragraphs.append({
                "source": f["name"],
                "text": para,
                "tf": _term_freq(tokens),
            })

    df: dict[str, int] = {}
    for p in paragraphs:
        for term in p["tf"]:
            df[term] = df.get(term, 0) + 1

    n = len(paragraphs)
    idf = {t: math.log((1 + n) / (1 + df[t])) + 1.0 for t in df}
    return paragraphs, idf


def _save_index(signature: str, corpus: list[dict], idf: dict[str, float]) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    payload = {"signature": signature, "idf": idf, "corpus": corpus}
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, INDEX_FILE)  # 原子替换，避免半截索引


def _load_or_build() -> tuple[list[dict], dict[str, float]]:
    """优先复用 index/ 下的索引；raw_docs 变更则重建并写回。"""
    files = _list_raw_files()
    signature = _corpus_signature(files)
    if os.path.isfile(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("signature") == signature:
                return cached["corpus"], cached["idf"]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            pass
    corpus, idf = _build_corpus()
    if corpus:
        _save_index(signature, corpus, idf)
    return corpus, idf


def _cosine(q_tf: dict[str, int], p_tf: dict[str, int], idf: dict[str, float]) -> float:
    """tf-idf 加权余弦相似度。"""
    q_terms = set(q_tf)
    p_terms = set(p_tf)
    common = q_terms & p_terms
    if not common:
        return 0.0
    dot = sum(q_tf[t] * idf.get(t, 0.0) * p_tf[t] * idf.get(t, 0.0) for t in common)
    q_norm = math.sqrt(sum((q_tf[t] * idf.get(t, 0.0)) ** 2 for t in q_terms))
    p_norm = math.sqrt(sum((p_tf[t] * idf.get(t, 0.0)) ** 2 for t in p_terms))
    if q_norm == 0.0 or p_norm == 0.0:
        return 0.0
    return dot / (q_norm * p_norm)


def search_internal_knowledge(query: str, top_k: int = 3) -> list[dict]:
    """在内部知识库中检索与 query 最相关的段落。

    - query 为空 / 无效，或 raw_docs 为空时返回 []。
    - 命中返回 [{source, path, text, score}]，按 score 降序取前 top_k 条。
    """
    if not query or not str(query).strip():
        return []
    q_tokens = _tokenize(str(query))
    if not q_tokens:
        return []

    try:
        k = max(1, int(top_k))
    except (TypeError, ValueError):
        k = 3

    corpus, idf = _load_or_build()
    if not corpus:
        return []

    q_tf = _term_freq(q_tokens)
    scored = []
    for p in corpus:
        s = _cosine(q_tf, p["tf"], idf)
        if s > 0:
            scored.append({
                "source": p["source"],
                "path": os.path.join(RAW_DIR, p["source"]),
                "text": p["text"],
                "score": round(s, 4),
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def _cli() -> None:
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m core.knowledge.local_rag <查询关键词> [top_k]")
        return
    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    hits = search_internal_knowledge(query, top_k)
    if not hits:
        print("（未命中内部知识库）")
        return
    for i, h in enumerate(hits, 1):
        print(f"[{i}] {h['source']}  (score={h['score']:.4f})")
        print(f"    {h['text']}")
        print()


if __name__ == "__main__":
    _cli()
