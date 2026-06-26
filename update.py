"""
Smart law database updater.
1. Try qundao/law-book (primary, GitHub weekly sync)
2. Fallback: crawl flk.npc.gov.cn for missing laws
3. Rebuild ChromaDB index
4. Update README.md version stamp
"""
import re
import sys
import zipfile
import shutil
from pathlib import Path
from datetime import datetime

import httpx

BASE_DIR = Path(__file__).parent
LAWBOOK_ZIP = BASE_DIR / "law-book-update.zip"
LAWBOOK_DIR = BASE_DIR / "law-book"
EXTRACT_DIR = BASE_DIR / "_extract_temp"
README = BASE_DIR / "README.md"
META_FILE = BASE_DIR / ".last_update"


def log(msg):
    print(f"  [update] {msg}")


def download_lawbook():
    """Download latest law-book zip from GitHub."""
    url = "https://codeload.github.com/qundao/law-book/zip/refs/heads/main"
    log(f"Downloading from {url}...")
    resp = httpx.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    LAWBOOK_ZIP.write_bytes(resp.content)
    log(f"Downloaded {len(resp.content) / 1024 / 1024:.1f} MB")


def extract_lawbook():
    """Extract law-book content directory from zip."""
    log("Extracting...")
    if EXTRACT_DIR.exists():
        shutil.rmtree(EXTRACT_DIR)
    EXTRACT_DIR.mkdir(parents=True)

    with zipfile.ZipFile(LAWBOOK_ZIP) as zf:
        zf.extractall(EXTRACT_DIR)

    inner = list(EXTRACT_DIR.glob("*"))
    if not inner:
        raise RuntimeError("Empty archive")
    content_src = inner[0] / "content"
    if not content_src.exists():
        raise RuntimeError("content dir not found")

    # Merge: keep existing local files, add/overwrite from zip
    if LAWBOOK_DIR.exists():
        shutil.rmtree(LAWBOOK_DIR)
    shutil.move(str(content_src), str(LAWBOOK_DIR))

    shutil.rmtree(EXTRACT_DIR)
    LAWBOOK_ZIP.unlink(missing_ok=True)

    md_count = len(list(LAWBOOK_DIR.rglob("*.md")))
    log(f"Extracted {md_count} markdown files")


def rebuild_index():
    """Rebuild ChromaDB vector index."""
    log("Rebuilding ChromaDB index...")
    import subprocess
    result = subprocess.run([sys.executable, str(BASE_DIR / "index.py")],
                          capture_output=False, cwd=str(BASE_DIR))
    if result.returncode != 0:
        log("WARNING: index rebuild may have errors, continuing...")


def update_readme():
    """Update README.md database version stamp."""
    if not README.exists():
        return

    content = README.read_text(encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")

    # Update the date in the version table
    pattern = r'(\*\*最后更新\*\*.*\|.*\|).*?(\|)'
    replacement = rf'\1 **{today}**（本次自动更新） \2'
    content = re.sub(pattern, replacement, content)

    README.write_text(content, encoding="utf-8")
    log(f"Updated README version stamp to {today}")


def record_update():
    """Record last successful update."""
    META_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))


def main():
    print("=" * 50)
    print("  Chinese Law RAG - Database Updater")
    print("=" * 50)
    print()

    # Step 1: Primary source - law-book zip
    try:
        download_lawbook()
        extract_lawbook()
        primary_ok = True
    except Exception as e:
        log(f"Primary update failed: {e}")
        primary_ok = False

    # Step 2: Fallback - crawl for incremental updates
    try:
        from crawler import crawl
        new_count = crawl()
        if new_count > 0:
            log(f"Crawler added {new_count} new laws")
    except Exception as e:
        log(f"Crawler fallback failed: {e}")

    # Step 3: Rebuild index
    try:
        rebuild_index()
    except Exception as e:
        log(f"Index rebuild failed: {e}")
        print("You may need to run 'python index.py' manually.")

    # Step 4: Update version stamp
    if primary_ok:
        try:
            update_readme()
        except Exception:
            pass

    record_update()

    print()
    print("=" * 50)
    print("  Update complete.")
    print(f"  Query: python query.py '你的问题'")
    print("=" * 50)


if __name__ == "__main__":
    main()
