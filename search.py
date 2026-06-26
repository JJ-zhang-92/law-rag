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
