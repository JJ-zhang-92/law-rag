"""
Build ChromaDB index from qundao/law-book markdown files.
Uses Ollama for embeddings.
Run: ollama pull nomic-embed-text
Then: python index.py
Update: python update.py   (downloads latest law-book and rebuilds index)
"""
import os
import re
import hashlib
from pathlib import Path

import httpx
import chromadb

LAW_DIR = Path(__file__).parent / "law-book" / "content"
DB_DIR = Path(__file__).parent / "chroma_db"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

SKIP_DIRS = {"about", "appendix", "en", "search"}


def ollama_embed_batch(texts, model=EMBED_MODEL):
    import time
    inputs = [t[:4000] for t in texts]
    for attempt in range(5):
        try:
            with httpx.Client(timeout=300) as client:
                resp = client.post(
                    f"{OLLAMA_URL}/api/embed",
                    json={"model": model, "input": inputs}
                )
                if resp.status_code == 500:
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                return resp.json()["embeddings"]
        except Exception:
            time.sleep(3)
    raise RuntimeError("Batch embedding failed after 5 attempts")


def parse_lawbook(raw):
    """Parse law-book format: YAML frontmatter (--- ... ---) + markdown content."""
    law_name = ""
    if raw.startswith("---"):
        idx = raw.find("---", 4)
        if idx > 0:
            fm = raw[4:idx].strip()
            for line in fm.split("\n"):
                line = line.strip()
                if line.startswith("title:"):
                    law_name = line[6:].strip().strip('"').strip("'")
            raw = raw[idx + 3:].strip()
    return law_name, raw


def clean_markdown(text):
    text = re.sub(r"\{\{%[^}]*%\}\}", "", text)
    text = re.sub(r"\{\{<[^>]*>\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # 去除行首的 markdown 列表符（- / *），避免其挡在 "第X条" 之前导致按条切分失败
    text = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


_ART_RE = re.compile(r"(?m)^[ \t　]*(?:\*\*)?(第[一二三四五六七八九十百千万零\d]+条)(?:\*\*)?")
_CHAP_RE = re.compile(r"(?m)^[ \t　]*(?:#{1,3}[ \t]*)?(第[一二三四五六七八九十百千万零\d]+[章编节])")


def split_by_article(text, law_name, category):
    """按 第X条 切分；捕获并保留条号到 article 元数据。退化时按章/段落切分。"""
    chunks = []

    # 1) 优先按条切分：finditer 定位每个 "第X条" 起点，切片到下一条之前
    matches = list(_ART_RE.finditer(text))
    if len(matches) >= 2:
        header = text[:matches[0].start()].strip()
        if len(header) > 30:
            chunks.append({"content": header, "article": "序言", "law": law_name, "category": category})
        for idx, m in enumerate(matches):
            start = m.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if len(body) > 8:
                chunks.append({
                    "content": body,
                    "article": m.group(1),          # 形如 "第五百六十三条"
                    "law": law_name,
                    "category": category,
                })
        if chunks:
            return chunks

    # 2) 退化：按章/编/节切分
    cmatches = list(_CHAP_RE.finditer(text))
    if len(cmatches) >= 2:
        for idx, m in enumerate(cmatches):
            start = m.start()
            end = cmatches[idx + 1].start() if idx + 1 < len(cmatches) else len(text)
            body = text[start:end].strip()
            if len(body) > 30:
                chunks.append({"content": body, "article": m.group(1), "law": law_name, "category": category})
        if chunks:
            return chunks

    # 3) 退化：按段落聚合成 ~800 字块
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        if len(text) > 100:
            chunks.append({"content": text, "article": "全文", "law": law_name, "category": category})
        return chunks
    buffer = ""
    for p in paragraphs:
        if len(buffer) + len(p) < 800:
            buffer += ("\n" if buffer else "") + p
        else:
            if buffer:
                chunks.append({"content": buffer, "article": "-", "law": law_name, "category": category})
            buffer = p
    if buffer:
        chunks.append({"content": buffer, "article": "-", "law": law_name, "category": category})
    return chunks


def build_index():
    print(f"Checking Ollama at {OLLAMA_URL}...")
    with httpx.Client(timeout=5) as client:
        try:
            resp = client.get(f"{OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            print("  Ollama is running.")
        except Exception:
            print("  ERROR: Ollama is not running. Start it first (ollama serve)")
            return

    print(f"\nReading law documents from: {LAW_DIR}")
    print(f"ChromaDB path: {DB_DIR}")

    client = chromadb.PersistentClient(path=str(DB_DIR))
    try:
        client.delete_collection("law_regulations")
    except Exception:
        pass

    collection = client.create_collection(
        name="law_regulations",
        metadata={"hnsw:space": "cosine"}
    )

    all_chunks = []
    doc_count = 0

    for root, dirs, files in os.walk(LAW_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            if not fname.endswith(".md"):
                continue

            fpath = Path(root) / fname
            category = Path(root).name

            try:
                with open(fpath, encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                continue

            if len(raw) < 200:
                continue

            law_name, body = parse_lawbook(raw)
            if not law_name:
                law_name = fname.replace(".md", "")
            body = clean_markdown(body)

            if not body.strip():
                continue

            chunks = split_by_article(body, law_name, category)
            all_chunks.extend(chunks)
            doc_count += 1

            if doc_count % 200 == 0:
                print(f"  Processed {doc_count} documents, {len(all_chunks)} chunks...")

    print(f"\nTotal: {doc_count} documents, {len(all_chunks)} chunks")

    chunk_idx = 0
    batch_size = 128
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        texts = [c["content"] for c in batch]
        ids = [hashlib.md5(f"{chunk_idx+j}:{t[:50]}".encode()).hexdigest()[:24] for j, t in enumerate(texts)]
        metadatas = [{"law": c["law"], "article": c["article"], "category": c["category"]} for c in batch]

        print(f"  Embedding batch {i//batch_size + 1}/{(len(all_chunks)-1)//batch_size + 1}...")
        embeddings = ollama_embed_batch(texts)

        valid = [(t, e, mid, m) for t, e, mid, m in zip(texts, embeddings, ids, metadatas) if e is not None]
        if valid:
            v_texts, v_embs, v_ids, v_metas = zip(*valid)
            collection.add(
                ids=list(v_ids),
                documents=list(v_texts),
                embeddings=list(v_embs),
                metadatas=list(v_metas)
            )

        print(f"  Indexed {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")
        chunk_idx += len(batch)

    print(f"\nDone. Index saved to {DB_DIR}")
    print(f"Total vectors: {collection.count()}")


if __name__ == "__main__":
    build_index()
