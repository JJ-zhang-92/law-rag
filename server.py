"""
Simple HTTP API for law database - call from any session/tool.
Start: python server.py (runs on http://localhost:8720)
Query: curl "http://localhost:8720?q=关键词&top=5"
"""
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
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


def format_context(results):
    lines = ["## 相关法律法规（检索结果）\n"]
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
        sim = (1 - dist) * 100 if dist else 0
        law = meta.get("law", "未知")
        article = meta.get("article", "-")
        cat = meta.get("category", "-")
        lines.append(f"### #{i+1} [{law}] 相似度: {sim:.0f}%")
        lines.append(f"分类: {cat} | 条文: {article}\n")
        lines.append(doc)
        lines.append("")

    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # quiet

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        query = params.get("q", [None])[0]
        if not query:
            self.send_error(400, "Missing 'q' parameter")
            return

        top_k = int(params.get("top", [5])[0])
        fmt = params.get("format", ["text"])[0]

        try:
            results = search(query, top_k=top_k)
        except Exception as e:
            self.send_error(500, str(e))
            return

        if fmt == "json":
            data = []
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, distances):
                data.append({
                    "law": meta.get("law"),
                    "article": meta.get("article"),
                    "category": meta.get("category"),
                    "similarity": round((1 - dist) * 100, 1) if dist else 0,
                    "content": doc
                })
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            context = format_context(results)
            body = context.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def main():
    port = 8720
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Law RAG API running on http://localhost:{port}")
    print(f"Usage: http://localhost:{port}?q=关键词&top=5&format=text|json")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
