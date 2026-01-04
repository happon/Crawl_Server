import json
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials

# ----------------- 設定 -----------------
SPREADSHEET_NAME = "RSS_記事一覧"
CREDENTIALS_FILE = "credentials.json"
OUTPUT_JSON = "stage4_input_included.json"  # ステージ4へ渡すファイル

# include_flag 列名（シートのヘッダーと一致させてください）
INCLUDE_COL = "include_flag"

# ----------------- Google Sheets 接続 -----------------
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
gc = gspread.authorize(credentials)
ws = gc.open(SPREADSHEET_NAME).sheet1

# ----------------- ユーティリティ -----------------
def is_included(val) -> bool:
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

# ----------------- 取得 & 抽出 -----------------
rows = ws.get_all_records()  # 1行目ヘッダー前提

included_rows = []
for idx, row in enumerate(rows, start=2):  # 実シート行番号は2行目から
    if is_included(row.get(INCLUDE_COL)):
        row["_row_num"] = idx  # 後段で書き戻し等に使える
        included_rows.append(row)

print(f"include対象: {len(included_rows)} 件")

# ----------------- JSON 出力（ステージ4入力） -----------------
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "spreadsheet": SPREADSHEET_NAME,
    "include_col": INCLUDE_COL,
    "count": len(included_rows),
    "rows": included_rows,
}

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(f"✅ 書き出し完了: {OUTPUT_JSON}")
