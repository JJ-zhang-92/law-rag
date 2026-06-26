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
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def split_by_article(text, law_name, category):
    chunks = []

    article_pattern = re.compile(r"(?:^|\n)(?:\*\*)?第[一二三四五六七八九十百千万\d]+条\s*(.*?)(?:\*\*)?(?:\n|$)")
    sections = article_pattern.split(text)

    if len(sections) <= 2:
        chapter_pattern = re.compile(r"(?:^|\n)(?:#{1,3}\s*)?第[一二三四五六七八九十百千万\d]+章\s*(.*?)(?:\n|$)")
        sections = chapter_pattern.split(text)

    if len(sections) <= 2:
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

    header = sections[0].strip()
    if header:
        chunks.append({"content": header, "article": "序言", "law": law_name, "category": category})

    i = 1
    while i < len(sections) - 1:
        article_header = sections[i].strip()
        content = sections[i + 1].strip() if i + 1 < len(sections) else ""
        full_text = article_header + "\n" + content
        if len(full_text) > 50:
            chunks.append({
                "content": full_text,
                "article": article_header[:80],
                "law": law_name,
                "category": category
            })
        i += 2

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
