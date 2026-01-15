from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from src.common.paths import repo_root


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


def ensure_headers(ws) -> List[str]:
    headers = ws.row_values(1)
    if not headers:
        raise ValueError("Sheet header row (row 1) is empty. Please create headers first.")
    return [h.strip() for h in headers if str(h).strip()]


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sheetの1行（get_all_recordsのdict）から、Stage4で使う列だけを抜き出す。
    欠損列は空文字で補完。
    """
    out: Dict[str, Any] = {}
    for col in EXPORT_COLUMNS:
        out[col] = row.get(col, "")
    return out


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_sheet_id_from_url_or_id(s: str) -> str:
    """
    入力がIDでもURLでも、可能ならIDに正規化する。
    - IDのみ: そのまま返す
    - URL: /d/<ID>/ から抜く
    """
    s = (s or "").strip()
    if not s:
        return ""
    # URL形式なら /d/<id>/ を抜く
    if "docs.google.com/spreadsheets" in s and "/d/" in s:
        try:
            part = s.split("/d/", 1)[1]
            sheet_id = part.split("/", 1)[0]
            return sheet_id.strip()
        except Exception:
            return ""
    return s


def _open_spreadsheet_with_retry(
    gc: gspread.Client,
    *,
    spreadsheet_id: Optional[str],
    spreadsheet_title: Optional[str],
    max_attempts: int = 5,
    base_sleep: float = 1.0,
):
    """
    gspread の open/open_by_key が 500 を返すことがあるため、指数バックオフでリトライする。
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            if spreadsheet_id:
                return gc.open_by_key(spreadsheet_id)
            if spreadsheet_title:
                return gc.open(spreadsheet_title)
            raise ValueError("No spreadsheet_id or spreadsheet_title specified.")
        except gspread.exceptions.APIError as e:
            last_exc = e
            msg = str(e)
            # 500/503系はリトライ対象。その他は即時終了でもよいが、ここでは保守的に少しだけリトライ。
            sleep_sec = base_sleep * (2 ** (attempt - 1))
            if attempt >= max_attempts:
                raise
            print(f"⚠️ gspread APIError (attempt {attempt}/{max_attempts}): {msg}")
            print(f"   retrying in {sleep_sec:.1f}s...")
            time.sleep(sleep_sec)
        except Exception as e:
            last_exc = e
            # 認証・権限・存在しない等はリトライしても無駄が多いので即時終了
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to open spreadsheet for unknown reason.")


def get_worksheet(
    *,
    root: Path,
    spreadsheet_id: Optional[str],
    spreadsheet_title: Optional[str],
    worksheet_name: str,
    credentials_path: Optional[str],
):
    cred_path = Path(credentials_path).expanduser().resolve() if credentials_path else (root / "credentials.json")
    if not cred_path.exists():
        raise FileNotFoundError(
            f"credentials.json not found: {cred_path} (set --credentials or place it at {root})"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    gc = gspread.authorize(credentials)

    sh = _open_spreadsheet_with_retry(
        gc,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_title=spreadsheet_title,
        max_attempts=5,
        base_sleep=1.0,
    )

    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound as e:
        raise ValueError(f"Worksheet not found: {worksheet_name}") from e

    return ws


def main() -> None:
    root = repo_root()

    # .env（repo_root側で読み込み済みの前提）からデフォルトを拾う
    env_sheet_id = _extract_sheet_id_from_url_or_id(os.getenv("SPREADSHEET_ID", ""))
    env_sheet_title = (os.getenv("SPREADSHEET_TITLE", "") or "").strip()
    env_ws_name = (os.getenv("WORKSHEET_NAME", "") or "").strip() or "Sheet1"

    parser = argparse.ArgumentParser(description="Stage4入口: include_flag=Y/TRUEの行を抽出してJSON出力する")
    parser.add_argument(
        "--spreadsheet-id",
        default=None,
        help="Spreadsheet ID (recommended). If omitted, uses .env SPREADSHEET_ID",
    )
    parser.add_argument(
        "--sheet",
        default=None,
        help="Spreadsheet title (fallback). If omitted, uses .env SPREADSHEET_TITLE or old default",
    )
    parser.add_argument(
        "--worksheet",
        default=None,
        help="Worksheet name (default: .env WORKSHEET_NAME or 'Sheet1')",
    )
    parser.add_argument("--credentials", default=None, help="Path to credentials.json (optional)")
    parser.add_argument(
        "--out",
        default=None,
        help="Output json path (default: <root>/data/stage4_input_included.json)",
    )
    parser.add_argument(
        "--include-col",
        default="include_flag",
        help="Column name for include flag (default: include_flag)",
    )

    # 後方互換のため、タイトルの旧デフォルトも残す（IDが無い場合のみ使う）
    parser.add_argument(
        "--legacy-default-title",
        default="RSS_記事一覧",
        help="Used only if no spreadsheet-id and no sheet(.env/arg) is provided.",
    )

    args = parser.parse_args()

    spreadsheet_id = _extract_sheet_id_from_url_or_id(args.spreadsheet_id or "") or env_sheet_id
    spreadsheet_title = (args.sheet or "").strip() or env_sheet_title or args.legacy_default_title
    worksheet_name = (args.worksheet or "").strip() or env_ws_name

    ws = get_worksheet(
        root=root,
        spreadsheet_id=spreadsheet_id or None,
        spreadsheet_title=spreadsheet_title or None,
        worksheet_name=worksheet_name,
        credentials_path=args.credentials,
    )

    headers = ensure_headers(ws)
    if args.include_col not in headers:
        raise ValueError(f"Missing include column '{args.include_col}' in sheet header.")

    rows = ws.get_all_records()  # 1行目ヘッダー前提
    included_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(rows, start=2):  # 実シート行番号は2行目から
        if is_included(row.get(args.include_col)):
            normalized = normalize_row(row)
            normalized["_row_num"] = idx
            included_rows.append(normalized)

    print(f"include対象: {len(included_rows)} 件")

    out_path = Path(args.out).expanduser().resolve() if args.out else (root / "data" / "stage4_input_included.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": now_utc_iso(),
        "spreadsheet_id": spreadsheet_id or "",
        "spreadsheet_title": spreadsheet_title,
        "worksheet": worksheet_name,
        "include_col": args.include_col,
        "count": len(included_rows),
        "columns": EXPORT_COLUMNS,
        "rows": included_rows,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 書き出し完了: {out_path}")


if __name__ == "__main__":
    main()
