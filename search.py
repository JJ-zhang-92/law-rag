"""
Search law database via ChromaDB + Ollama embeddings.
Usage: python search.py "查询关键词" [--top-k 5] [--full]
"""
import argparse
from pathlib import Path

import httpx
import chromadb

DB_DIR = Path(__file__).parent / "chroma_db"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"


def search(query, top_k=5):
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": query}
        )
        resp.raise_for_status()
        query_embedding = resp.json()["embedding"]

    client = chromadb.PersistentClient(path=str(DB_DIR))
    collection = client.get_collection("law_regulations")

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )
    return results


_CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}


def _cn_to_int(s):
    """中文数字（第X条 的 X）转 int，支持 一/十/百/千 及阿拉伯数字。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    for ch in s:
        if ch in _CN_NUM:
            num = _CN_NUM[ch]
        elif ch in unit:
            u = unit[ch]
            if u == 10000:
                section = (section + num) * u
                total += section
                section = num = 0
            else:
                if num == 0:
                    num = 1
                section += num * u
                num = 0
        else:
            return None
    return total + section + num


def _int_to_cn(n):
    """int 转中文数字（用于匹配 第五百六十三条 形式）。"""
    if n <= 0:
        return ""
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    s = ""
    for i, ch in enumerate(str(n)[::-1]):
        d = int(ch)
        if d:
            s = digits[d] + units[i] + s
        elif not s.startswith("零"):
            s = "零" + s
    return s.strip("零")


def stub_status(law):
    """
    查询某部法律在本地库是否为"仅目录占位"（无正文）。
    Returns: dict(is_stub, law_full, chunk_count) 或 None(库中无此法)。
    供 ensure_law 判断：命中占位 → 触发按需现场爬取补全。
    """
    import chromadb as _chromadb
    col = _chromadb.PersistentClient(path=str(DB_DIR)).get_collection("law_regulations")
    off, matched = 0, {}
    while True:
        r = col.get(limit=5000, offset=off, include=["metadatas"])
        if not r["ids"]:
            break
        for m in r["metadatas"]:
            ln = m.get("law", "")
            if law in ln:
                d = matched.setdefault(ln, {"total": 0, "stub": 0})
                d["total"] += 1
                if m.get("text_status") == "目录占位":
                    d["stub"] += 1
        off += 5000
    if not matched:
        return None
    law_full = min(matched, key=lambda x: (law != x, len(x)))
    info = matched[law_full]
    return {"law_full": law_full, "chunk_count": info["total"],
            "is_stub": info["stub"] > 0 and info["stub"] == info["total"]}


def get_article(law, no):
    """
    精确按"法典名 + 条号"取条文全文，绕开语义误命中。

    Args:
        law: 法律名称（支持简称，如 '民法典'、'公司法'，做包含匹配）
        no : 条号，int 或 '第X条'/'X'
    Returns:
        条文全文 str；未找到返回 None。
    """
    import re as _re
    import chromadb as _chromadb

    if isinstance(no, str):
        m = _re.search(r"第?\s*([一二三四五六七八九十百千万零\d]+)\s*条?", no)
        n = _cn_to_int(m.group(1)) if m else None
    else:
        n = int(no)
    if not n:
        return None

    col = _chromadb.PersistentClient(path=str(DB_DIR)).get_collection("law_regulations")

    # 1) 找到匹配的法律全名（law 元数据包含查询词）
    off, law_names = 0, set()
    while True:
        r = col.get(limit=5000, offset=off, include=["metadatas"])
        if not r["ids"]:
            break
        for m in r["metadatas"]:
            ln = m.get("law", "")
            if law in ln:
                law_names.add(ln)
        off += 5000
    if not law_names:
        return None
    # 优先完全等于/最短匹配
    law_full = min(law_names, key=lambda x: (law != x, len(x)))

    cn = _int_to_cn(n)
    art_cn = f"第{cn}条"
    art_num = f"第{n}条"

    # 2) 优先按 article 元数据精确定位（stage5 重建后 article 已规范）
    recs = col.get(where={"law": law_full}, include=["documents", "metadatas"])
    docs, metas = recs.get("documents", []), recs.get("metadatas", [])
    for doc, meta in zip(docs, metas):
        art = (meta.get("article") or "").strip()
        if art.startswith(art_cn) or art.startswith(art_num):
            return doc.strip()

    # 3) 回退：在该法所有块的正文里正则抽取该条（兼容 article 未规范化的历史数据）
    pat = _re.compile(
        rf"(?:^|\n)[\s\-*]*(?:\*\*)?{art_cn}[　\s].*?(?=(?:\n[\s\-*]*(?:\*\*)?第[一二三四五六七八九十百千万零\d]+条[　\s])|$)",
        _re.S)
    for doc in docs:
        m = pat.search(doc)
        if m:
            return m.group(0).strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Search Chinese law database")
    parser.add_argument("query", help="Search query in Chinese")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--full", action="store_true", help="Show full content")
    args = parser.parse_args()

    results = search(args.query, top_k=args.top_k)

    if not results or not results["documents"] or not results["documents"][0]:
        print("No results found.")
        return

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
        similarity = (1 - dist) * 100 if dist else 0
        print(f"\n{'=' * 60}")
        print(f"#{i + 1}  [{meta.get('law', '未知')}]  相似度: {similarity:.1f}%")
        print(f"    条款: {meta.get('article', '-')}  |  分类: {meta.get('category', '-')}")
        print(f"{'=' * 60}")
        if args.full:
            print(doc)
        else:
            print(doc[:500])
            if len(doc) > 500:
                print(f"... (共 {len(doc)} 字)")

    print()


if __name__ == "__main__":
    main()
