"""
Browser crawler for local regulations from flk.npc.gov.cn.
Two-phase: (1) build catalog, (2) fetch details + save markdown.
Supports resume from checkpoint. Rate-limited to avoid WAF.

Usage:
    python browser_crawl.py              # Phase 1+2: full crawl
    python browser_crawl.py --catalog    # Phase 1 only: build catalog
    python browser_crawl.py --fetch      # Phase 2 only: details from catalog
    python browser_crawl.py --resume     # Resume from checkpoint
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).parent
CACHE = BASE / ".browser_cache"
CATALOG_FILE = CACHE / "catalog.json"
DETAIL_DIR = CACHE / "detail"
OUTPUT_DIR = BASE / "law-book" / "content" / "地方法规"

PROVINCES = [
    ("上海", 250),
    ("江苏", 260),
    ("浙江", 270),
]

# Crawl control
PAGE_SIZE = 50
DELAY_BETWEEN_REQUESTS = 1.5       # seconds between API calls
DELAY_BETWEEN_PAGES = 3.0          # seconds between list pages
BATCH_SAVE_EVERY = 10              # save catalog after every N details

# Ensure directories exist
CACHE.mkdir(parents=True, exist_ok=True)
DETAIL_DIR.mkdir(parents=True, exist_ok=True)


def load_catalog():
    """Load or create empty catalog."""
    if CATALOG_FILE.exists():
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return {}


def save_catalog(cat):
    """Save catalog atomically."""
    tmp = CATALOG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CATALOG_FILE)


def init_catalog_entry(cat, name, org_id):
    """Ensure catalog has an entry for this province."""
    if name not in cat:
        cat[name] = {"org_id": org_id, "total": 0, "laws": {}}
    return cat[name]


def phase1_build_catalog():
    """Fetch all law lists and build catalog.json."""
    cat = load_catalog()
    total_new = 0

    print("=" * 50)
    print("  Phase 1: Building catalog")
    print("=" * 50)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = context.new_page()

        # Navigate to warm up session
        page.goto("https://flk.npc.gov.cn/index", wait_until="domcontentloaded")
        time.sleep(3)

        for name, org_id in PROVINCES:
            info = init_catalog_entry(cat, name, org_id)
            existing_ids = set(info.get("laws", {}).keys())
            new_count = 0

            # Get total
            data = _fetch_list(page, org_id, 1, 1)
            total = data.get("total", 0)
            info["total"] = total
            print(f"\n  {name}: {total} total laws, {len(existing_ids)} already in catalog")

            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            for pg in range(1, total_pages + 1):
                print(f"    Page {pg}/{total_pages}...", end=" ", flush=True)
                data = _fetch_list(page, org_id, pg, PAGE_SIZE)
                rows = data.get("rows", [])
                if not rows:
                    print("empty")
                    break

                page_new = 0
                for item in rows:
                    bbbs = item.get("bbbs", "")
                    if bbbs and bbbs not in existing_ids:
                        info["laws"][bbbs] = {
                            "title": item.get("title", ""),
                            "publish_date": item.get("gbrq", ""),
                            "effective_date": item.get("sxrq", ""),
                            "organ": item.get("zdjgName", ""),
                            "status": "pending",
                        }
                        page_new += 1
                        new_count += 1

                print(f"{page_new} new")
                save_catalog(cat)
                time.sleep(DELAY_BETWEEN_PAGES)

            print(f"    -> {name}: {new_count} new laws added")
            total_new += new_count

        context.close()
        browser.close()

    print(f"\n  Catalog saved: {CATALOG_FILE}")
    print(f"  Total new entries: {total_new}")
    return cat


def phase2_fetch_details():
    """Fetch detail for each pending law and save as markdown."""
    cat = load_catalog()
    if not cat:
        print("No catalog found. Run --catalog first.")
        return

    saved = 0
    failed = 0
    skipped = 0

    print("=" * 50)
    print("  Phase 2: Fetching details + saving markdown")
    print("=" * 50)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = context.new_page()

        # Warm up
        page.goto("https://flk.npc.gov.cn/index", wait_until="domcontentloaded")
        time.sleep(2)

        batch_counter = 0

        for name, org_id in PROVINCES:
            if name not in cat:
                continue

            info = cat[name]
            laws = info.get("laws", {})
            pending = [(bbbs, d) for bbbs, d in laws.items() if d.get("status") == "pending"]

            print(f"\n  {name}: {len(pending)} pending out of {len(laws)} total")

            for bbbs, law_data in pending:
                title = law_data.get("title", "?")
                print(f"    [{title[:45]}] ", end="", flush=True)

                # Check if already saved
                fname = title.replace("/", "-")[:60] + ".md"
                target = OUTPUT_DIR / name
                target.mkdir(parents=True, exist_ok=True)
                if (target / fname).exists():
                    print("EXISTS")
                    law_data["status"] = "done"
                    skipped += 1
                    continue

                try:
                    detail = _fetch_detail(page, bbbs)
                    time.sleep(DELAY_BETWEEN_REQUESTS)

                    if detail.get("code") != 200:
                        print(f"API_FAIL ({detail.get('msg','')})")
                        law_data["status"] = "failed"
                        law_data["error"] = f"API code {detail.get('code')}"
                        failed += 1
                        continue

                    _save_markdown(title, name, detail)
                    law_data["status"] = "done"
                    saved += 1
                    print("OK")
                except Exception as e:
                    print(f"ERR {str(e)[:50]}")
                    law_data["status"] = "failed"
                    law_data["error"] = str(e)[:200]
                    failed += 1

                batch_counter += 1
                if batch_counter % BATCH_SAVE_EVERY == 0:
                    save_catalog(cat)

            save_catalog(cat)

        context.close()
        browser.close()

    print(f"\n  Summary: {saved} saved, {failed} failed, {skipped} skipped")
    return saved


def _fetch_list(page, org_id, pg, size):
    """Fetch one list page via browser API call."""
    body = json.dumps({
        "searchRange": 1, "sxrq": [], "gbrq": [],
        "searchType": 2, "sxx": [], "gbrqYear": [],
        "flfgCodeId": [230], "zdjgCodeId": [org_id],
        "searchContent": "",
        "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": pg, "pageSize": size,
    })

    result = page.evaluate(
        f"""
        async () => {{
            const r = await fetch('/law-search/search/list', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json;charset=UTF-8'}},
                body: '{body}'
            }});
            return await r.text();
        }}
    """
    )
    return json.loads(result)


def _fetch_detail(page, bbbs):
    """Fetch law detail metadata."""
    result = page.evaluate(
        f"""
        async () => {{
            const r = await fetch('/law-search/search/flfgDetails?bbbs={bbbs}');
            return await r.text();
        }}
    """
    )
    return json.loads(result)


def _save_markdown(title, province, detail):
    """Save law data as markdown file."""
    data = detail.get("data", {})
    pub = data.get("gbrq", "")
    eff = data.get("sxrq", "")
    organ = data.get("zdjgName", "")
    sxx_map = {1: "尚未生效", 2: "有效", 3: "有效", 4: "已修改", 5: "已废止"}
    status = sxx_map.get(data.get("sxx", 3), "未知")
    flxz = data.get("flxz", "地方法规")
    bbbs = data.get("bbbs", "")

    target = OUTPUT_DIR / province
    target.mkdir(parents=True, exist_ok=True)
    fname = title.replace("/", "-")[:60] + ".md"

    md = f"""---
title: {title}
office: {organ}
type: {flxz}
publish_date: {pub}
effective_date: {eff}
status: {status}
categories:
  - 地方法规
  - {province}
source_id: {bbbs}
---

# {title}

- **制定机关**: {organ}
- **法规性质**: {flxz}
- **公布日期**: {pub}
- **施行日期**: {eff}
- **时效性**: {status}

> 本文本为元数据记录。法规全文请访问 [flk.npc.gov.cn](https://flk.npc.gov.cn/detail?id={bbbs}) 查看。
"""
    (target / fname).write_text(md, encoding="utf-8")


def main():
    mode_catalog = "--catalog" in sys.argv
    mode_fetch = "--fetch" in sys.argv

    if not mode_catalog and not mode_fetch:
        # Default: Phase 1 + Phase 2
        phase1_build_catalog()
        phase2_fetch_details()
    elif mode_catalog:
        phase1_build_catalog()
    elif mode_fetch:
        phase2_fetch_details()

    print("\nDone.")


if __name__ == "__main__":
    main()
