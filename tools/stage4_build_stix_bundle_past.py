from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials


# Stage4に渡す「正規の列」：Sheets上の列名と一致させる
EXPORT_COLUMNS = [
    "published",
    "source",
    "author",
    "title",
    "url",
    "logic_title",
    "summary",
    "summary_detail",
    "category_main",
    "tags",
    "include_flag",
]


def project_root() -> Path:
    # .../src/stage4/stage4_extract_included.py -> 3階層上がプロジェクトルート想定
    return Path(__file__).resolve().parents[3]


def is_included(val: Any) -> bool:
    """
    include_flag が以下のどれでも True 扱いにする:
    - True（チェックボックス）
    - "TRUE", "true"
    - "Y", "y"
    - "1", "yes"
    """
    if val is True:
        return True
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("y", "true", "1", "yes")


def get_worksheet(
    *,
    spreadsheet_name: str,
    worksheet_name: str,
    credentials_path: Optional[str],
):
    root = project_root()
    cred_path = Path(credentials_path) if credentials_path else (root / "credentials.json")
    if not cred_path.exists():
        raise FileNotFoundError(
            f"credentials.json not found: {cred_path} (set --credentials or place it at project root)"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    gc = gspread.authorize(credentials)

    sh = gc.open(spreadsheet_name)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        raise ValueError(f"Worksheet not found: {worksheet_name}")

    return ws


def ensure_headers(ws) -> List[str]:
    headers = ws.row_values(1)
    if not headers:
        raise ValueError("Sheet header row (row 1) is empty. Please create headers first.")
    return [h.strip() for h in headers]


def normalize_row(row: Dict[str, Any], headers: List[str]) -> Dict[str, Any]:
    """
    Sheetの1行（get_all_recordsのdict）から、Stage4で使う列だけを抜き出す。
    欠損列は空文字で補完。
    """
    out: Dict[str, Any] = {}
    for col in EXPORT_COLUMNS:
        if col in headers:
            out[col] = row.get(col, "")
        else:
            # 列そのものが無い場合も空で埋める（後段で気づけるように）
            out[col] = ""
    return out


def main():
    parser = argparse.ArgumentParser(description="Stage4入口: include_flag=Y/TRUEの行を抽出してJSON出力する")
    parser.add_argument("--sheet", default="RSS_記事一覧", help="Spreadsheet name")
    parser.add_argument("--worksheet", default="Sheet1", help="Worksheet name")
    parser.add_argument("--credentials", default=None, help="Path to credentials.json (optional)")
    parser.add_argument(
        "--out",
        default=None,
        help="Output json path (default: <project_root>/data/stage4_input_included.json)",
    )
    parser.add_argument(
        "--include-col",
        default="include_flag",
        help="Column name for include flag (default: include_flag)",
    )
    args = parser.parse_args()

    ws = get_worksheet(
        spreadsheet_name=args.sheet,
        worksheet_name=args.worksheet,
        credentials_path=args.credentials,
    )

    headers = ensure_headers(ws)

    if args.include_col not in headers:
        raise ValueError(f"Missing include column '{args.include_col}' in sheet header.")

    rows = ws.get_all_records()  # 1行目ヘッダー前提
    included_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows, start=2):  # 実シート行番号は2行目から
        if is_included(row.get(args.include_col)):
            normalized = normalize_row(row, headers)
            normalized["_row_num"] = idx  # 後段で書き戻し等に使える
            included_rows.append(normalized)

    print(f"include対象: {len(included_rows)} 件")

    root = project_root()
    out_path = Path(args.out) if args.out else (root / "data" / "stage4_input_included.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spreadsheet": args.sheet,
        "worksheet": args.worksheet,
        "include_col": args.include_col,
        "count": len(included_rows),
        "columns": EXPORT_COLUMNS,
        "rows": included_rows,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 書き出し完了: {out_path}")


if __name__ == "__main__":
    main()
