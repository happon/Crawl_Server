from __future__ import annotations

import argparse
from typing import Dict, List

from stage2.sheets_client import get_gspread_client, open_worksheet


# Sheetsのヘッダ（列順）
HEADERS = [
    "published",
    "source",
    "author",
    "title",
    "url",
    "logic_title",
    "category_main",
    "tags",
    "summary",
    "summary_detail",
    "include_flag",
]


def ensure_header_row(ws) -> None:
    """
    1行目が空ならヘッダを書き込む。
    既にヘッダがあれば何もしない（列順が違う場合は明示的に直すべきなので自動修正しない）。
    """
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(HEADERS, value_input_option="RAW")
        return

    # 既存ヘッダが一致しない場合は警告（止める）
    if [c.strip() for c in first_row[: len(HEADERS)]] != HEADERS:
        raise ValueError(
            "Header mismatch. Please align the sheet header to:\n"
            + "\t" + "\t".join(HEADERS)
        )


def get_existing_urls(ws) -> set[str]:
    """
    URL列（5列目）を取得して重複を避ける。
    col_values は 1-indexed.
    """
    url_col_index = HEADERS.index("url") + 1
    values = ws.col_values(url_col_index)
    # 1行目はヘッダなので除外
    return {v.strip() for v in values[1:] if v and v.strip()}


def article_to_row(article: Dict[str, str]) -> List[str]:
    """
    Stage1正規化済みdict（published/source/author/title/url）を、
    Sheetsの列順に合わせた1行へ変換。
    Stage3以降の列は空で埋める。
    """
    row = [""] * len(HEADERS)
    row[HEADERS.index("published")] = (article.get("published") or "").strip()
    row[HEADERS.index("source")] = (article.get("source") or "").strip()
    row[HEADERS.index("author")] = (article.get("author") or "").strip()
    row[HEADERS.index("title")] = (article.get("title") or "").strip()
    row[HEADERS.index("url")] = (article.get("url") or "").strip()
    # include_flag は空のまま
    return row


def write_articles(
    *,
    spreadsheet_name: str,
    worksheet_name: str,
    articles: List[Dict[str, str]],
    credentials_path: str | None,
    dry_run: bool = False,
) -> int:
    """
    Sheetsに追記し、追加した件数を返す。
    """
    gc = get_gspread_client(credentials_path)
    ws = open_worksheet(gc, spreadsheet_name=spreadsheet_name, worksheet_name=worksheet_name)

    ensure_header_row(ws)

    existing = get_existing_urls(ws)

    rows_to_append: List[List[str]] = []
    for a in articles:
        url = (a.get("url") or "").strip()
        if not url:
            continue
        if url in existing:
            continue
        rows_to_append.append(article_to_row(a))
        existing.add(url)

    if dry_run:
        return len(rows_to_append)

    if rows_to_append:
        # まとめて追記（API呼び出し回数を減らす）
        ws.append_rows(rows_to_append, value_input_option="RAW")

    return len(rows_to_append)


def main():
    parser = argparse.ArgumentParser(
        description="Stage2: Write Stage1 articles to Google Sheets (B columns empty for Stage3)."
    )
    parser.add_argument("--sheet", required=True, help="Spreadsheet name (e.g., RSS_記事一覧)")
    parser.add_argument("--worksheet", default="Sheet1", help="Worksheet name (default: Sheet1)")
    parser.add_argument("--credentials", default=None, help="Path to credentials.json (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write, only count")

    args = parser.parse_args()

    # ここは「Stage1の出力を受け取る」想定です。
    # まずはテスト用に、後で Stage1 側から import して articles を渡してください。
    example_articles = [
        {
            "published": "",
            "source": "BleepingComputer",
            "author": "",
            "title": "Example title",
            "url": "https://www.bleepingcomputer.com/example",
        }
    ]

    n = write_articles(
        spreadsheet_name=args.sheet,
        worksheet_name=args.worksheet,
        articles=example_articles,
        credentials_path=args.credentials,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"[DRY RUN] Would append: {n} rows")
    else:
        print(f"Appended: {n} rows")


if __name__ == "__main__":
    main()
