import feedparser
from datetime import datetime

# 対象のRSS URL
RSS_URL = "https://www.bleepingcomputer.com/feed/"

# 媒体名
SOURCE_NAME = "BleepingComputer"

def fetch_rss_articles(rss_url, source_name):
    feed = feedparser.parse(rss_url)
    articles = []

    for entry in feed.entries:
        article = {
            "source": source_name,
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published_date": entry.get("published", ""),
            "summary": entry.get("summary", "")
        }
        articles.append(article)

    return articles

if __name__ == "__main__":
    articles = fetch_rss_articles(RSS_URL, SOURCE_NAME)
    for i, a in enumerate(articles[:5], 1):
        print(f"{i}. {a['published_date']} | {a['title']}")
        print(f"   {a['url']}\n")
