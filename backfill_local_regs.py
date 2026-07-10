# -*- coding: utf-8 -*-
"""
backfill_local_regs.py — 地方法规"目录占位"按需回填。

设计：本地库中 1100 部地方法规仅有目录占位（flk 无正文、docx 被锁），
标记为 metadata.text_status='目录占位'。**不预先批量抓取**，而是在
ensure_law 实际用到某部时，由上层（AI）用权威镜像 tavily/webfetch 取正文，
再调用本模块 backfill() 做机械入库（add_law 会按 law 名删除旧占位块并写入
真实条文块，占位标记随之消失）。

流程：
    ensure_law 命中占位（search.stub_status(law).is_stub == True）
        → AI 用 tavily_extract 从省人大/司法部/北大法宝取全文
        → backfill_local_regs.backfill(law, full_text, province)
        → search.get_article 回查可用

CLI（正文已存文件时）：
    python backfill_local_regs.py "上海市体育发展条例" 正文.txt 上海
"""
import sys
import json
import re
from pathlib import Path

_DIR = Path(__file__).parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from index_append import add_law
from search import stub_status

INVENTORY = _DIR / "stub_inventory.json"


def list_stubs(province=None):
    """列出占位法规清单（可按省过滤）。"""
    if not INVENTORY.exists():
        return []
    items = json.load(open(INVENTORY, encoding="utf-8")).get("items", [])
    return [x for x in items if not province or x.get("province") == province]


def _validate(text, expect_articles=None):
    """基本质量校验：条文数、正文长度。返回 (ok, msg, article_count)。"""
    arts = re.findall(r"(?:^|\n)\s*第[一二三四五六七八九十百千万零\d]+条", text)
    n = len(arts)
    if len(text) < 200:
        return False, f"正文过短({len(text)}字)，疑似抓取失败", n
    if n == 0:
        return False, "未识别到任何'第X条'，疑似非正文页", n
    if expect_articles and abs(n - expect_articles) > max(2, expect_articles * 0.1):
        return False, f"条文数不符(实得{n}/预期{expect_articles})，疑似残缺", n
    return True, f"校验通过(条文数={n})", n


def backfill(law_name, full_text, province="地方法规", status="有效",
             expect_articles=None, force=False):
    """
    将现场爬取的地方法规正文入库，替换目录占位。

    Args:
        law_name       : 法律名称（须与库中占位法规名一致）
        full_text      : 权威镜像取到的完整正文
        province       : 分类目录（上海/江苏/浙江/地方法规）
        expect_articles: 预期条文数（如"共X条"），用于完整性校验
        force          : True 跳过校验强制入库
    Returns:
        dict(ok, msg, indexed, article_count)
    """
    ok, msg, n = _validate(full_text, expect_articles)
    if not ok and not force:
        return {"ok": False, "msg": f"未入库：{msg}", "indexed": 0, "article_count": n}
    indexed = add_law(law_name, full_text, category=province, status=status, replace=True)
    # 校验占位标记已消除
    st = stub_status(law_name)
    still_stub = bool(st and st.get("is_stub"))
    return {"ok": True and not still_stub,
            "msg": f"已入库 {indexed} 块；{msg}" + ("（⚠占位标记仍在，请检查）" if still_stub else "（占位已解除）"),
            "indexed": indexed, "article_count": n}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python backfill_local_regs.py <法律名称> <正文文件> [省份] [预期条文数]")
        print("       python backfill_local_regs.py --list [省份]   # 列出占位清单")
        sys.exit(1)
    if sys.argv[1] == "--list":
        prov = sys.argv[2] if len(sys.argv) > 2 else None
        items = list_stubs(prov)
        print(f"占位法规 {len(items)} 部" + (f"（{prov}）" if prov else ""))
        for x in items[:50]:
            print(f"  [{x['province']}] {x['law']}")
        if len(items) > 50:
            print(f"  ... 共 {len(items)} 部")
    else:
        name = sys.argv[1]
        text = Path(sys.argv[2]).read_text(encoding="utf-8")
        prov = sys.argv[3] if len(sys.argv) > 3 else "地方法规"
        exp = int(sys.argv[4]) if len(sys.argv) > 4 else None
        r = backfill(name, text, province=prov, expect_articles=exp)
        print(("[OK] " if r["ok"] else "[FAIL] ") + r["msg"])
