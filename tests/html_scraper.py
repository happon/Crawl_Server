# scripts/html_scraper.py
import json, os
from datetime import datetime
from fetch_utils import fetch_html_selenium, extract_full_article_html_selenium

CONFIG = json.load(open("config/sources.json"))
OUTPUT_DIR = "data/raw_articles"

def collect_html():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for src in CONFIG:
        if src["type"] != "selenium":
            continue
        print("Fetching:", src["name"])
        html = fetch_html_selenium(src["url"])
        article = extract_full_article_html_selenium(src["url"])

        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        outpath = os.path.join(OUTPUT_DIR, f"{src['name'].replace(' ','_')}_{ts}.json")
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump({
                "source": src["name"],
                "category": src["category"],
                "url": src["url"],
                "article": article
            }, f, ensure_ascii=False, indent=2)

        print(f"Saved: {outpath}")

def main():
    collect_html()

if __name__ == "__main__":
    main()
