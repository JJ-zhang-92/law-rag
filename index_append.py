# -*- coding: utf-8 -*-
"""
index_append.py — 增量入库单部法律（不重建全库）。

复用 index.py 的 split_by_article + ollama_embed_batch，对现有 ChromaDB
collection 做增量 add；先按 law 名删除旧块再加，保证幂等（可安全重复调用）。

用法：
    from index_append import add_law
    n = add_law("中华人民共和国公司法", text, category="法律", status="有效")
    print(f"indexed {n} chunks")
CLI:
    python index_append.py "中华人民共和国公司法" path/to/text.txt [category]
"""
import sys
import hashlib
from pathlib import Path

import chromadb

_DIR = Path(__file__).parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from index import split_by_article, clean_markdown, ollama_embed_batch, DB_DIR

COLLECTION = "law_regulations"


def _get_collection():
    client = chromadb.PersistentClient(path=str(DB_DIR))
    try:
        return client.get_collection(COLLECTION)
    except Exception:
        return client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def delete_law(law_name):
    """删除某部法律的所有已有块（按 law 元数据）。返回删除数。"""
    col = _get_collection()
    existing = col.get(where={"law": law_name}, include=[])
    ids = existing.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


def add_law(law_name, text, category="法律", status="", replace=True, batch_size=128):
    """
    将单部法律切条、嵌入并增量写入现有索引。

    Args:
        law_name : 法律标准名称（写入 metadata.law，供 get_article 精确过滤）
        text     : 法律正文
        category : 分类目录名（法律/行政法规/司法解释/地方法规/部门规章…）
        status   : 时效性（有效/已废止…），写入 metadata.status
        replace  : True 时先删同名旧块（幂等）
    Returns:
        写入的块数
    """
    body = clean_markdown(text)
    if not body.strip():
        raise ValueError("空文本，无法入库")

    chunks = split_by_article(body, law_name, category)
    if not chunks:
        raise ValueError("切分结果为空")

    col = _get_collection()
    if replace:
        delete_law(law_name)

    added = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["content"] for c in batch]
        embs = ollama_embed_batch(texts)
        ids, docs, metas, vecs = [], [], [], []
        for j, (c, e) in enumerate(zip(batch, embs)):
            if e is None:
                continue
            uid = hashlib.md5(f"{law_name}:{i+j}:{c['content'][:50]}".encode()).hexdigest()[:24]
            ids.append(uid)
            docs.append(c["content"])
            metas.append({"law": law_name, "article": c["article"],
                          "category": category, "status": status})
            vecs.append(e)
        if ids:
            col.add(ids=ids, documents=docs, embeddings=vecs, metadatas=metas)
            added += len(ids)
    return added


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python index_append.py <法律名称> <正文文件> [分类]")
        sys.exit(1)
    name = sys.argv[1]
    text = Path(sys.argv[2]).read_text(encoding="utf-8")
    cat = sys.argv[3] if len(sys.argv) > 3 else "法律"
    n = add_law(name, text, category=cat)
    print(f"[OK] {name} 入库 {n} 块 (分类={cat})")
