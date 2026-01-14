import feedparser
from datetime import datetime, timezone

# 対象のRSS URL（BleepingComputer）
RSS_URL = "https://www.bleepingcomputer.com/feed/"

# 媒体名
SOURCE_NAME = "BleepingComputer"


def _to_iso8601(entry) -> str:
    """
    feedparserのpublished_parsed(推奨)があればISO8601へ正規化。
    なければpublished文字列をそのまま返す。
    """
    dt_struct = entry.get("published_parsed")
    if dt_struct:
        # tzが不明な場合があるためUTC扱いで付与（Z）
        dt = datetime(*dt_struct[:6], tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    return entry.get("published", "") or ""


def _get_author(entry) -> str:
    """
    RSSにauthorが無い/形式が違うケースがあるため、複数パターンを吸収。
    """
    author = entry.get("author")
    if author:
        return str(author).strip()

    authors = entry.get("authors")
    if isinstance(authors, list) and len(authors) > 0:
        name = authors[0].get("name") if isinstance(authors[0], dict) else None
        if name:
            return str(name).strip()

    return ""


def fetch_rss_articles(rss_url: str, source_name: str) -> list[dict]:
    """
    Stage1仕様：
      - published
      - title
      - url
      - source
      - author
    のみを返す。
    """
    feed = feedparser.parse(rss_url)
    articles: list[dict] = []

    for entry in getattr(feed, "entries", []):
        article = {
            "published": _to_iso8601(entry),
            "source": source_name,
            "author": _get_author(entry),
            "title": entry.get("title", "") or "",
            "url": entry.get("link", "") or "",
        }
        articles.append(article)

    return articles


if __name__ == "__main__":
    articles = fetch_rss_articles(RSS_URL, SOURCE_NAME)

    # 動作確認：先頭5件だけ表示
    for i, a in enumerate(articles[:5], 1):
        print(f"{i}. {a['published']} | {a['source']} | {a['author']}")
        print(f"   {a['title']}")
        print(f"   {a['url']}\n")
