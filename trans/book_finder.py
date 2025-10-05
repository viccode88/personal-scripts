# filename: book_finder.py
# -*- coding: utf-8 -*-
import sys
import csv
import json
import time
import urllib.parse
from typing import List, Dict, Any, Optional
import re

import requests

USER_AGENT = "BookFinder/1.0 (+legal-only; contact: local-user)"
TIMEOUT = 20
SLEEP_BETWEEN_QUERIES = 0.8  # 避免打太兇被限流

def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def token_overlap_score(a: str, b: str) -> float:
    # 簡單 token overlap（0~1）
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union

def pick_best(candidates: List[Dict[str, Any]], target_title: str) -> Optional[Dict[str, Any]]:
    best = None
    best_score = -1.0
    for c in candidates:
        title = c.get("title") or ""
        score = token_overlap_score(title, target_title)
        # 稍微偏好有作者與年份的
        if c.get("author"):
            score += 0.05
        if c.get("year"):
            score += 0.03
        if score > best_score:
            best_score = score
            best = c
    return best

def search_openlibrary(title: str) -> List[Dict[str, Any]]:
    q = urllib.parse.quote(title)
    url = f"https://openlibrary.org/search.json?q={q}&limit=10"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for d in data.get("docs", []):
        olid = d.get("key")
        author_name = ", ".join(d.get("author_name", [])[:2]) if d.get("author_name") else None
        year = d.get("first_publish_year")
        # 嘗試組可讀連結（優先 work，再用 edition）
        web = f"https://openlibrary.org{olid}" if olid else None
        out.append({
            "source": "Open Library",
            "title": d.get("title"),
            "author": author_name,
            "year": year,
            "link": web
        })
    return out

def search_gutenberg(title: str) -> List[Dict[str, Any]]:
    # 使用 gutendex（Project Gutenberg 的非官方 JSON API，公領域）
    q = urllib.parse.quote(title)
    url = f"https://gutendex.com/books?search={q}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for b in data.get("results", []):
        authors = ", ".join([a.get("name") for a in b.get("authors", []) if a.get("name")]) or None
        # 選擇一個常見格式的下載位址（如果有）
        formats = b.get("formats", {}) or {}
        epub = formats.get("application/epub+zip")
        txt = formats.get("text/plain; charset=utf-8") or formats.get("text/plain")
        pdf = formats.get("application/pdf")
        link = epub or pdf or txt or b.get("url")
        out.append({
            "source": "Project Gutenberg",
            "title": b.get("title"),
            "author": authors,
            "year": None,
            "link": link
        })
    return out

def search_internet_archive(title: str) -> List[Dict[str, Any]]:
    # IA 進階搜尋：只看 texts 類型
    # 返回前 10 筆，抓 title, creator, year, identifier
    query = f'title:("{title}") AND mediatype:texts'
    params = {
        "q": query,
        "fl[]": ["identifier", "title", "creator", "year", "mediatype", "rights"],
        "rows": 10,
        "page": 1,
        "output": "json"
    }
    url = "https://archive.org/advancedsearch.php"
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for d in data.get("response", {}).get("docs", []):
        identifier = d.get("identifier")
        # 一般閱讀頁
        details = f"https://archive.org/details/{identifier}" if identifier else None
        out.append({
            "source": "Internet Archive",
            "title": d.get("title"),
            "author": (", ".join(d["creator"]) if isinstance(d.get("creator"), list) else d.get("creator")),
            "year": d.get("year"),
            "link": details
        })
    return out

def find_best_match_for_title(raw_title: str) -> Dict[str, Any]:
    candidates = []
    # 查詢三個來源
    try:
        candidates += search_openlibrary(raw_title)
    except Exception as e:
        candidates.append({"source": "Open Library", "title": None, "author": None, "year": None, "link": None, "error": str(e)})
    time.sleep(SLEEP_BETWEEN_QUERIES)
    try:
        candidates += search_gutenberg(raw_title)
    except Exception as e:
        candidates.append({"source": "Project Gutenberg", "title": None, "author": None, "year": None, "link": None, "error": str(e)})
    time.sleep(SLEEP_BETWEEN_QUERIES)
    try:
        candidates += search_internet_archive(raw_title)
    except Exception as e:
        candidates.append({"source": "Internet Archive", "title": None, "author": None, "year": None, "link": None, "error": str(e)})

    best = pick_best([c for c in candidates if c.get("title")], raw_title)
    result = {
        "input_title": raw_title.strip(),
        "match_title": best.get("title") if best else None,
        "author": best.get("author") if best else None,
        "year": best.get("year") if best else None,
        "source": best.get("source") if best else None,
        "link": best.get("link") if best else None,
        "all_candidates": candidates
    }
    return result

def custom_downloader(record: Dict[str, Any]) -> None:
    """
    預留：如果你對某些來源擁有**明確授權**（例如公領域、CC 授權、或你已購買且來源允許下載），
    你可以在這裡實作你的下載邏輯（例如用 requests 下載 EPUB/PDF）。
    出於版權與法律風險考量，此函式預設不做任何下載動作。
    """
    return

def main():
    if len(sys.argv) < 2:
        print("用法：python book_finder.py \"書名1, 書名2, 書名3\"")
        sys.exit(1)
    raw = sys.argv[1]
    titles = [t.strip() for t in raw.split(",") if t.strip()]

    results = []
    for t in titles:
        print(f"🔎 搜尋：{t} ...")
        rec = find_best_match_for_title(t)
        results.append(rec)
        # 顯示摘要
        print(f"   → 來源: {rec.get('source') or '-'}")
        print(f"   → 標題: {rec.get('match_title') or '-'}")
        print(f"   → 作者: {rec.get('author') or '-'}")
        print(f"   → 年份: {rec.get('year') or '-'}")
        print(f"   → 連結: {rec.get('link') or '-'}\n")
        # 如果你確認擁有合法下載權限，可在這裡啟用：
        # custom_downloader(rec)

    # 輸出 CSV
    csv_name = "results.csv"
    with open(csv_name, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["input_title", "match_title", "author", "year", "source", "link"])
        for r in results:
            writer.writerow([r.get("input_title"), r.get("match_title"), r.get("author"), r.get("year"), r.get("source"), r.get("link")])
    print(f"✅ 已輸出 {csv_name}")

    # 也順手給你 JSON 方便程式後續接
    with open("results.json", "w", encoding="utf-8") as jf:
        json.dump(results, jf, ensure_ascii=False, indent=2)
    print("✅ 已輸出 results.json")

if __name__ == "__main__":
    main()
