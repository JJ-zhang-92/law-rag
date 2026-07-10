# -*- coding: utf-8 -*-
"""
fetch_law_by_name.py — 定向单法官方校验 + 正文抓取（按名称）。

面向 ensure_law：当本地 RAG 缺失某部法律时，先来官方数据库
flk.npc.gov.cn（国家法律法规数据库）做权威确认，再抓正文。

flk 可靠返回：官方标准名称、制定机关、施行日期、时效性(有效/已废止/已修改)、
条文目录(第1条…第N条)、source_url。
flk 正文限制：正文文件走 OFD 阅读器 + 内网 OBS 签名直链，脚本通常无法直取，
故正文采用"最佳努力 docx 直取；失败则返回条文目录 + 官方元数据"，
由上层 ensure_law 用权威镜像补正文并以 article_count 交叉校验。

覆盖范围（flk）：宪法/法律/行政法规/监察法规/司法解释/地方性法规。
不含：国务院部门规章（如《律师执业管理办法》）→ 上层走通用官网 fetch 兜底。

CLI:  python fetch_law_by_name.py "中华人民共和国公司法"
API:  from fetch_law_by_name import fetch_law
"""
import re
import json
import time
import sys
import tempfile
import os
from pathlib import Path

BASE = Path(__file__).parent
LAW_DIR = BASE / "law-book" / "content"

CATEGORY_MAP = {
    "宪法": "宪法", "法律": "法律", "行政法规": "行政法规",
    "监察法规": "监察法规", "司法解释": "司法解释",
    "地方性法规": "地方法规", "自治条例": "地方法规", "单行条例": "地方法规",
}
SXX_MAP = {1: "尚未生效", 2: "有效", 3: "有效", 4: "已修改", 5: "已废止", 9: "已废止"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
FLK = "https://flk.npc.gov.cn"


def _clean_title(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()


def _score(query, title):
    q = query.strip().strip("《》")
    t = title.strip().strip("《》")
    if q == t:
        return 100
    if q.replace("中华人民共和国", "") == t.replace("中华人民共和国", ""):
        return 90
    if q in t or t in q:
        return 70
    return 0


def _search(page, name, size=10):
    body = json.dumps({
        "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
        "gbrqYear": [], "flfgCodeId": [], "zdjgCodeId": [], "searchContent": name,
        "orderByParam": {"order": "-1", "sort": ""}, "pageNum": 1, "pageSize": size,
    })
    res = page.evaluate(
        """async (bd) => {
            const r = await fetch('/law-search/search/list', {method:'POST',
                headers:{'Content-Type':'application/json;charset=UTF-8'}, body: bd});
            return await r.text();
        }""", body)
    return json.loads(res)


def _detail(page, bbbs):
    res = page.evaluate(
        """async (id) => {
            const r = await fetch('/law-search/search/flfgDetails?bbbs='+id);
            return await r.text();
        }""", bbbs)
    return json.loads(res)


def _walk_toc(node, acc):
    """从 content 树抽取条文/章节标题目录。"""
    if isinstance(node, dict):
        title = (node.get("title") or "").strip()
        if title:
            acc.append(title)
        for ch in node.get("children") or []:
            _walk_toc(ch, acc)
    elif isinstance(node, list):
        for ch in node:
            _walk_toc(ch, acc)
    return acc


def _count_articles(toc):
    return sum(1 for t in toc if re.fullmatch(r"第[一二三四五六七八九十百千万零\d]+条", t.strip()))


def _try_download_text(ctx, oss):
    """最佳努力：经 ofdGenerateLink 取签名直链下载 docx 并提取文本。失败返回 ''。"""
    wp = (oss or {}).get("ossWordPath")
    if not wp:
        return ""
    try:
        j = ctx.request.get(f"{FLK}/law-search/amazonFile/ofdGenerateLink?filePath={wp}",
                            headers={"Referer": f"{FLK}/"})
        data = json.loads(j.text())
        du = (data.get("file") or {}).get("download_url", "")
        if not du:
            return ""
        r = ctx.request.get(du)
        body = r.body()
        if r.status != 200 or body[:2] != b"PK":
            return ""
        fp = os.path.join(tempfile.gettempdir(), f"flk_{os.getpid()}.docx")
        with open(fp, "wb") as f:
            f.write(body)
        try:
            from docx import Document
            doc = Document(fp)
            txt = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        finally:
            try:
                os.remove(fp)
            except OSError:
                pass
        return txt if len(txt) > 100 else ""
    except Exception:
        return ""


def _save_md(title, category, text, meta):
    target = LAW_DIR / category
    target.mkdir(parents=True, exist_ok=True)
    safe = title.replace("/", "-").replace("\\", "-")[:80]
    fpath = target / f"{safe}.md"
    fm = (f"---\ntitle: {title}\n"
          f"date: '{meta.get('gbrq','')}'\n"
          f"effective_date: '{meta.get('sxrq','')}'\n"
          f"status: {meta.get('status','')}\n"
          f"source_id: {meta.get('bbbs','')}\n"
          f"source_url: {FLK}/detail?id={meta.get('bbbs','')}\n"
          f"categories:\n  - {category}\n---\n\n")
    fpath.write_text(fm + text, encoding="utf-8")
    return str(fpath)


def fetch_law(name, prefer_valid=True, save=True, want_text=True):
    """
    按名称检索 flk，做官方校验并尽力抓正文。

    Returns dict:
      found            : 是否在 flk 找到匹配法律
      title            : 官方标准名称
      category         : 本地分类目录名
      status           : 时效性（有效/已废止/已修改/…）
      article_count    : flk 条文数（供交叉校验）
      toc              : 条文/章节标题目录
      text             : 正文（抓到则非空；flk 锁定时为空，需上层补）
      text_available   : 是否成功取到正文
      source_url       : flk 详情页 URL
      bbbs             : flk 内部 id
      md_path          : 保存路径（save 且有正文时）
      reason           : 未命中/无正文原因
      candidates       : 候选标题列表
    """
    result = {"found": False, "title": "", "category": "", "status": "",
              "article_count": 0, "toc": [], "text": "", "text_available": False,
              "source_url": "", "bbbs": "", "md_path": "", "reason": "",
              "candidates": []}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["reason"] = "playwright 未安装，无法访问 flk"
        return result

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="zh-CN", ignore_https_errors=True)
        page = ctx.new_page()
        try:
            for _att in range(3):
                try:
                    page.goto(f"{FLK}/index", wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    if _att == 2:
                        result["reason"] = "flk 访问超时（网络不稳定，请重试）"
                        return result
                    time.sleep(3)
            time.sleep(2.5)
            rows = (_search(page, name).get("rows") or [])
            cands = []
            for r in rows:
                title = _clean_title(r.get("title", ""))
                sxx = r.get("sxx", 3)
                cands.append({"title": title, "bbbs": r.get("bbbs", ""),
                              "flxz": r.get("flxz", ""), "sxx": sxx,
                              "status": SXX_MAP.get(sxx, "未知"),
                              "score": _score(name, title)})
            result["candidates"] = [c["title"] for c in cands]
            if not cands:
                result["reason"] = "flk 无检索结果（可能为部门规章，需上层通用 fetch 兜底）"
                return result

            cands.sort(key=lambda c: (c["score"] + (10 if prefer_valid and c["sxx"] in (2, 3) else 0),
                                      c["sxx"] in (2, 3)), reverse=True)
            best = cands[0]
            if best["score"] < 70:
                result["reason"] = f"无足够匹配的法律（最高候选：{best['title']}）"
                return result

            det = _detail(page, best["bbbs"])
            if det.get("code") != 200:
                result["reason"] = f"详情接口失败 code={det.get('code')}"
                return result
            d = det.get("data", {})
            title = _clean_title(d.get("title", "")) or best["title"]
            category = CATEGORY_MAP.get(best["flxz"], "法律")
            status = SXX_MAP.get(d.get("sxx", best["sxx"]), best["status"])
            toc = _walk_toc(d.get("content", {}), [])
            result.update({
                "found": True, "title": title, "category": category,
                "status": status, "toc": toc, "article_count": _count_articles(toc),
                "source_url": f"{FLK}/detail?id={best['bbbs']}", "bbbs": best["bbbs"],
            })

            if want_text:
                text = _try_download_text(ctx, d.get("ossFile", {}))
                if text:
                    result["text"] = text
                    result["text_available"] = True
                    if save:
                        meta = {"gbrq": d.get("gbrq", ""), "sxrq": d.get("sxrq", ""),
                                "status": status, "bbbs": best["bbbs"]}
                        result["md_path"] = _save_md(title, category, text, meta)
                else:
                    result["reason"] = "flk 正文受 OFD/OBS 保护无法直取；请上层用权威镜像补正文（可用 toc/article_count 交叉校验）"
            return result
        finally:
            ctx.close()
            browser.close()


def add_verified_text(title, category, text, bbbs="", gbrq="", sxrq="", status="有效"):
    """上层用权威镜像取到正文后回存 md（供 index_append 入库）。"""
    meta = {"gbrq": gbrq, "sxrq": sxrq, "status": status, "bbbs": bbbs}
    return _save_md(title, category, text, meta)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fetch_law_by_name.py <法律名称>")
        sys.exit(1)
    r = fetch_law(sys.argv[1])
    if r["found"]:
        print(f"[FOUND] {r['title']} | {r['category']} | {r['status']} | 条文数={r['article_count']}")
        print(f"        source: {r['source_url']}")
        if r["text_available"]:
            print(f"        正文已抓取 {len(r['text'])} 字，保存: {r['md_path']}")
        else:
            print(f"        正文未取到：{r['reason']}")
            print(f"        条文目录示例: {' '.join(r['toc'][:8])} ...")
    else:
        print(f"[MISS] {r['reason']}")
        if r["candidates"]:
            print("  候选:", " / ".join(r["candidates"][:5]))
