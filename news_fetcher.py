#!/usr/bin/env python3
import os, time, json, feedparser, requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

CACHE = Path("cache/news.json")
FEEDS = [u.strip() for u in os.getenv("NEWS_FEEDS", "").split(";") if u.strip()]
MAX_ITEMS = 30
TTL_SECONDS = 15 * 60  # refresh every 15 minutes

def fetch_all():
    items = []
    seen = set()
    for url in FEEDS:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:15]:
                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                if not title or not link: 
                    continue
                key = (title, link)
                if key in seen: 
                    continue
                seen.add(key)
                items.append({
                    "title": title,
                    "link": link,
                    "source": (d.get("feed", {}).get("title") or "").strip()[:80],
                })
        except Exception as ex:
            print(f"[news] feed error {url}: {ex}")
    # Dedup and cap
    return items[:MAX_ITEMS]

def main():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            ts = data.get("_fetched_ts", 0)
            if time.time() - ts < TTL_SECONDS and data.get("items"):
                print("[news] cache fresh; nothing to do.")
                return
        except Exception:
            pass
    items = fetch_all()
    out = {
        "_fetched_ts": int(time.time()),
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "items": items
    }
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[news] saved {len(items)} items to {CACHE}")

if __name__ == "__main__":
    main()
