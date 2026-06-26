"""
Crawler: incrementally fetch new laws from flk.npc.gov.cn (国家法律法规数据库).
Used by update.py as fallback when upstream GitHub sync is lagging.
"""
import re
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timedelta

import httpx

BASE_URL = "https://flk.npc.gov.cn"
API_URL = f"{BASE_URL}/api"
LAW_DIR = Path(__file__).parent / "law-book" / "content"
META_FILE = Path(__file__).parent / ".last_update"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{BASE_URL}/",
}

# Category mapping: API category → local directory
CATEGORY_MAP = {
    "宪法": "宪法",
    "法律": "法律",
    "行政法规": "行政法规",
    "监察法规": "监察法规",
    "司法解释": "司法解释",
}


def log(msg):
    print(f"  [crawler] {msg}")


def request_with_retry(client, method, url, **kwargs):
    """Request with retry and exponential backoff."""
    for attempt in range(3):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code == 200:
                return resp
            log(f"HTTP {resp.status_code} on {url}, retry {attempt+1}/3")
        except Exception as e:
            log(f"Request error: {e}, retry {attempt+1}/3")
        time.sleep(2 ** attempt)
    return None


def get_new_laws(client, since_date):
    """Fetch laws published after since_date from NPC database."""
    log(f"Checking for laws since {since_date}...")

    # Try the sitemap/new laws API first
    resp = request_with_retry(client, "POST", API_URL, json={
        "type": "flfg",
        "page": 1,
        "size": 50,
        "sort": "publishDate,desc",
    }, headers=HEADERS, timeout=30)

    laws = []
    if resp:
        try:
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            for item in items:
                pub_date = item.get("publish", "")
                if pub_date and pub_date >= since_date:
                    laws.append(item)
        except Exception as e:
            log(f"API parse error: {e}")

    if not laws:
        log("No new laws found via API, trying homepage parse...")
        laws = parse_homepage_new_laws(client, since_date)

    return laws


def parse_homepage_new_laws(client, since_date):
    """Fallback: parse '新法速递' from homepage HTML."""
    resp = request_with_retry(client, "GET", f"{BASE_URL}/index", headers=HEADERS, timeout=30)
    if not resp:
        return []

    # Extract embedded API data or JSON from script tags
    text = resp.text
    laws = []

    # Try extracting from embedded JSON
    json_match = re.search(r'window\.__NUXT__\s*=\s*(\{.+?\});', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            # Navigate the Nuxt state to find new laws
            state = data.get("state", {}) if isinstance(data, dict) else {}
            for key in state:
                if "newLaw" in key.lower() or "flfg" in key.lower():
                    items = state[key] if isinstance(state[key], list) else []
                    for item in items:
                        pub = item.get("publish", "") or item.get("publishDate", "")
                        if pub >= since_date:
                            laws.append(item)
        except Exception:
            pass

    if not laws:
        log("Could not extract new laws from homepage")

    return laws


def get_law_detail(client, law_id):
    """Fetch law detail including download URLs."""
    resp = request_with_retry(client, "POST", f"{API_URL}/detail", json={
        "id": law_id
    }, headers=HEADERS, timeout=30)

    if not resp:
        return None

    try:
        data = resp.json()
        detail = data.get("result", data)
        return {
            "title": detail.get("title", ""),
            "category": detail.get("type", ""),
            "publish": detail.get("publish", ""),
            "files": detail.get("files", []),
        }
    except Exception:
        return None


def download_law_content(client, file_url):
    """Download law content (Word/HTML) and extract text."""
    url = file_url if file_url.startswith("http") else f"{BASE_URL}{file_url}"
    resp = request_with_retry(client, "GET", url, headers=HEADERS, timeout=60)
    if not resp:
        return ""

    content_type = resp.headers.get("content-type", "")
    content = resp.text

    if "text/html" in content_type or url.endswith(".html"):
        # Strip HTML tags
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
        content = re.sub(r"<[^>]+>", "\n", content)
        content = re.sub(r"&nbsp;", " ", content)
        content = re.sub(r"&lt;", "<", content)
        content = re.sub(r"&gt;", ">", content)
        content = re.sub(r"&amp;", "&", content)
        content = re.sub(r"\n{3,}", "\n\n", content)

    # Clean up
    lines = [l.strip() for l in content.split("\n")]
    lines = [l for l in lines if l]
    return "\n".join(lines)


def detect_category(title, api_category):
    """Map law to local directory name based on content analysis."""
    if api_category in CATEGORY_MAP:
        return CATEGORY_MAP[api_category]

    # Heuristic based on title keywords
    cat = api_category
    for kw, c in [
        ("宪法", "宪法和宪法性法律"),
        ("刑法", "刑法及相关"),
        ("民法典", "民法典及相关"),
        ("诉讼法", "诉讼与非诉讼程序法"),
        ("行政", "行政法"),
        ("经济", "经济法"),
        ("社会", "社会法"),
    ]:
        if kw in title:
            return c
    return cat


def local_category_dirs():
    """Get list of existing category directories."""
    if not LAW_DIR.exists():
        return []
    return [d.name for d in LAW_DIR.iterdir() if d.is_dir()]


def find_best_category(title, api_category):
    """Find the best matching local category directory."""
    detected = detect_category(title, api_category)
    existing = local_category_dirs()

    if detected in existing:
        return detected

    # Fuzzy match
    for d in existing:
        if any(kw in d for kw in detected[:2]):
            return d

    # First existing directory or create new
    if existing:
        return existing[0]
    return detected or "法律法规"


def save_law(title, publish_date, content, category):
    """Save law as markdown file in the appropriate category directory."""
    target_dir = LAW_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = title.replace("/", "-").replace("\\", "-")[:80]
    fname = f"{safe_name}.md"

    frontmatter = f"""---
title: {title}
date: '{publish_date}'
categories:
  - {category}
---

"""
    with open(target_dir / fname, "w", encoding="utf-8") as f:
        f.write(frontmatter + content)

    log(f"Saved: {category}/{fname}")
    return target_dir / fname


def crawl(since_date=None):
    """
    Incremental crawl of new laws from flk.npc.gov.cn.
    Returns number of new laws added.
    """
    if since_date is None:
        # Default: check last 90 days if no last update record
        if META_FILE.exists():
            since_date = META_FILE.read_text().strip()
        else:
            since_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    log(f"Looking for laws since {since_date}")

    with httpx.Client(http2=True, timeout=30, follow_redirects=True) as client:
        new_laws = get_new_laws(client, since_date)

        if not new_laws:
            log("No new laws found from API. The database may be up to date.")
            return 0

        log(f"Found {len(new_laws)} potentially new laws, checking local...")

        added = 0
        for law in new_laws:
            law_id = law.get("id", "")
            title = law.get("title", "")

            if not law_id or not title:
                continue

            # Check if already exists locally
            existing = list(LAW_DIR.rglob(f"*{law_id}*")) if LAW_DIR.exists() else []
            if existing:
                continue

            log(f"Fetching: {title}")
            detail = get_law_detail(client, law_id)
            if not detail:
                log(f"  Failed to get detail for {title}")
                continue

            # Get the best content file
            files = detail.get("files", [])
            best_file = None
            for f in files:
                ext = f.get("name", "").lower()
                if ext.endswith(".docx") or ext.endswith(".doc"):
                    best_file = f
                    break
                if ext.endswith(".html") or ext.endswith(".htm"):
                    best_file = best_file or f

            if not best_file:
                log(f"  No downloadable file for {title}")
                continue

            content = download_law_content(client, best_file.get("url", ""))
            if not content or len(content) < 100:
                log(f"  Empty/too short content for {title}")
                continue

            category = find_best_category(title, detail.get("category", ""))
            save_law(title, detail.get("publish", ""), content, category)
            added += 1
            time.sleep(1)  # Rate limit

        if added:
            log(f"Added {added} new laws")
            # Update last crawl timestamp
            META_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))

        return added


if __name__ == "__main__":
    print("Law Crawler - NPC Database")
    print("=" * 40)
    n = crawl()
    print(f"\nDone. {n} new laws added." if n else "\nNo new laws. Database is current.")
