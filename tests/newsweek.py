from ..base_parser import ParsedArticle
from ..html_common_parsers import clean_html

def extract_newsweek(html: str, meta: dict) -> ParsedArticle:
    soup = clean_html(html, "newsweek.com")

    content_div = soup.find(attrs={"data-js": "article-body"}) or soup.find(attrs={"itemprop": "articleBody"})
    paragraphs = content_div.find_all("p") if content_div else soup.find_all("p")
    text = "\n\n".join(p.get_text(strip=True) for p in paragraphs)

    meta_tag = soup.find("meta", {"name": "keywords"})
    tags = meta_tag["content"].split(",") if meta_tag and meta_tag.get("content") else []

    return ParsedArticle(
        source=meta["name"],
        category=meta["category"],
        title=soup.title.string.strip() if soup.title else "No Title",
        link=meta["url"],
        published=meta.get("published", ""),
        article=text.strip(),
        tags=[t.strip() for t in tags if t.strip()]
    )
