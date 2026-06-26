"""
Query law database - use in any session.
Usage: python query.py "关键词" [--top-k 5] [--format prompt|json|markdown]
"""
import argparse
import sys
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


def format_prompt(results):
    """Format as LLM-ready prompt context."""
    lines = ["## 相关法律法规（检索结果）\n"]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
        sim = (1 - dist) * 100 if dist else 0
        law = meta.get("law", "未知")
        article = meta.get("article", "-")
        cat = meta.get("category", "-")
        lines.append(f"### 结果 #{i+1} [{law}] (相似度: {sim:.0f}%)\n")
        lines.append(f"条文: {article} | 分类: {cat}\n\n{doc}\n")

    return "\n".join(lines)


def format_markdown(results):
    """Format as clean markdown for reference."""
    return format_prompt(results)


def format_json(results):
    import json
    output = []
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, distances):
        output.append({
            "law": meta.get("law"),
            "article": meta.get("article"),
            "category": meta.get("category"),
            "similarity": round((1 - dist) * 100, 1) if dist else 0,
            "content": doc
        })
    return json.dumps(output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Query Chinese law database")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--format", choices=["prompt", "markdown", "json", "text"], default="prompt")
    parser.add_argument("-o", "--output", help="Save to file")
    args = parser.parse_args()

    results = search(args.query, top_k=args.top_k)

    if not results or not results["documents"] or not results["documents"][0]:
        print("No results found.", file=sys.stderr)
        return

    if args.format == "json":
        output = format_json(results)
    else:
        output = format_prompt(results)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
