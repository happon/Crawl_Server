# src/stage1/schema.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, TypedDict
from urllib.parse import urlparse


class Stage1ArticleDict(TypedDict):
    """
    Stage1の標準スキーマ（Sheetsへ渡す最小5項目）
    """
    published: str  # ISO8601推奨 (e.g. "2026-01-14T12:34:56Z")
    source: str
    author: str
    title: str
    url: str


@dataclass(frozen=True)
class Stage1Article:
    """
    Stage1の内部表現（必要なら使う）
    """
    published: str
    source: str
    author: str
    title: str
    url: str

    def to_dict(self) -> Stage1ArticleDict:
        return {
            "published": self.published,
            "source": self.source,
            "author": self.author,
            "title": self.title,
            "url": self.url,
        }


def normalize_whitespace(s: str) -> str:
    return " ".join((s or "").split()).strip()


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    # 末尾のスラッシュ統一などは好みがあるので最小限に留める
    return u


def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def to_iso8601_z(dt: datetime) -> str:
    """
    datetime -> ISO8601 (UTC, Z)
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def normalize_published(
    published: Optional[str] = None,
    published_dt: Optional[datetime] = None,
) -> str:
    """
    publishedの正規化。
    - published_dt があればそれを優先してISO8601(Z)へ
    - published 文字列だけなら、そのまま返す（後で改善可能）
    """
    if published_dt is not None:
        return to_iso8601_z(published_dt)

    p = (published or "").strip()
    return p


def make_article(
    *,
    published: Optional[str],
    source: str,
    author: Optional[str],
    title: Optional[str],
    url: Optional[str],
    published_dt: Optional[datetime] = None,
) -> Stage1Article:
    """
    Stage1の入力（RSS等の揺れ）から、Stage1標準形へ正規化して作る。
    """
    src = normalize_whitespace(source)
    ttl = normalize_whitespace(title or "")
    ath = normalize_whitespace(author or "")
    u = normalize_url(url or "")

    pub = normalize_published(published=published, published_dt=published_dt)

    return Stage1Article(
        published=pub,
        source=src,
        author=ath,
        title=ttl,
        url=u,
    )


def validate_article(a: Stage1Article) -> List[str]:
    """
    最低限のバリデーション。
    ここで弾く基準は「Stage2に入れると壊れるもの」だけにするのが安全。
    """
    errors: List[str] = []

    if not a.source:
        errors.append("source is empty")
    if not a.title:
        errors.append("title is empty")
    if not a.url:
        errors.append("url is empty")
    elif not is_valid_url(a.url):
        errors.append(f"url is invalid: {a.url}")

    # author と published は欠損しうるので必須にはしない
    return errors


def dedupe_by_url(articles: Iterable[Stage1Article]) -> List[Stage1Article]:
    """
    URLで重複排除（先勝ち）。
    """
    seen: set[str] = set()
    out: List[Stage1Article] = []
    for a in articles:
        key = a.url
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def to_dicts(articles: Iterable[Stage1Article]) -> List[Stage1ArticleDict]:
    return [a.to_dict() for a in articles]


def from_feedparser_entry(entry: Dict[str, Any], *, source_name: str) -> Stage1Article:
    """
    feedparserのentry想定の“便利関数”。
    stage1/collectors 側で使うと、コレクタが薄くなる。
    """
    # published_dt: published_parsed があれば優先
    published_dt: Optional[datetime] = None
    published_parsed = entry.get("published_parsed")
    if published_parsed:
        try:
            published_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            published_dt = None

    # authorの揺れ吸収
    author = entry.get("author")
    if not author:
        authors = entry.get("authors")
        if isinstance(authors, list) and authors:
            first = authors[0]
            if isinstance(first, dict):
                author = first.get("name")

    return make_article(
        published=entry.get("published"),
        published_dt=published_dt,
        source=source_name,
        author=author,
        title=entry.get("title"),
        url=entry.get("link"),
    )
