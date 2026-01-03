import os
import re
import json
import gspread
import time
from google import genai
from google.genai.types import GenerateContentConfig
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ----------------- 設定 -----------------
load_dotenv()
SPREADSHEET_NAME = "RSS_記事一覧"
CREDENTIALS_FILE = "credentials.json"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 新しいSDKのクライアント初期化
client = genai.Client(api_key=GOOGLE_API_KEY)
MODEL_NAME = "gemini-3-pro-preview" # もしくは "gemini-1.5-pro" など利用可能なモデル
PROMPT_FILE = "prompt.md"           # 同じディレクトリに置く

# ----------------- prompt.md 読み込み（起動時に1回だけ） -----------------
if not os.path.exists(PROMPT_FILE):
    raise FileNotFoundError(f"{PROMPT_FILE} が見つかりません。stage3_gemini_classify.py と同じフォルダに置いてください。")

with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    prompt_template = f.read()

# ----------------- Google Sheets接続 -----------------
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
gc = gspread.authorize(credentials)
ws = gc.open(SPREADSHEET_NAME).sheet1

# ----------------- ヘッダー列の特定（高速化のためループ外で実行） -----------------
# 1行目のヘッダーを取得し、列名と列番号の対応辞書を作成
headers = ws.row_values(1)
col_map = {name: i + 1 for i, name in enumerate(headers)}

# 必要な列が存在するか確認
required_cols = ["title", "url", "logic_title", "category_main", "tags", "summary", "summary_detail"]
for col in required_cols:
    if col not in col_map:
        raise ValueError(f"スプレッドシートに列 '{col}' が見つかりません。")

# ----------------- 対象データ取得 -----------------
rows = ws.get_all_records()

print(f"全 {len(rows)} 件のデータを読み込みました。処理を開始します...")

for i, row in enumerate(rows):
    row_num = i + 2 # スプレッドシート上の行番号（ヘッダーが1行目なので+2）

    # 必須データの欠損チェック
    title = str(row.get("title", "")).strip()
    url = str(row.get("url", "")).strip()

    if not title or not url:
        print(f"⏭️ Row {row_num}: タイトルまたはURLがないためスキップ")
        continue

    # すでに処理済みの行はスキップ
    # (値が空文字でない場合は処理済みとみなす)
    if str(row.get("summary", "")).strip() and str(row.get("category_main", "")).strip():
        # print(f"⏭️ Row {row_num}: 処理済みのためスキップ")
        continue

    print(f"🚀 Processing Row {row_num}: {title[:30]}...")

    prompt = (
        prompt_template
        .replace("{{title}}", title)
        .replace("{{url}}", url)
    )

    try:
        # API呼び出し
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=GenerateContentConfig(
                response_mime_type="application/json", 
                tools=[{"url_context": {}}] # URL読み込みツール
            ),
        )

        content = response.text.strip()
        # print(f"🔍 DEBUGレスポンス(Row {row_num}):\n", content[:100], "...") 

        # JSONパース処理
        clean_content = re.sub(r"^```json\s*|\s*```$", "", content)
        parsed = json.loads(clean_content)

        # スプレッドシート更新（事前に取得した列番号を使用）
        # API制限回避のため、必要なら time.sleep(1) を入れる
        ws.update_cell(row_num, col_map["logic_title"], parsed.get("logic_title", ""))
        ws.update_cell(row_num, col_map["category_main"], parsed.get("category_main", ""))
        ws.update_cell(row_num, col_map["tags"], json.dumps(parsed.get("tags", []), ensure_ascii=False))
        ws.update_cell(row_num, col_map["summary"], parsed.get("summary", ""))
        ws.update_cell(row_num, col_map["summary_detail"], parsed.get("summary_detail", ""))

        print(f"✅ Row {row_num}: 更新完了")
        
        # 連続書き込みによるAPIエラー回避のため少し待機
        time.sleep(1)

    except Exception as e:
        print(f"⚠️ Row {row_num}: エラー発生 - {e}")
        # 詳細なエラー情報を表示（デバッグ用）
        import traceback
        traceback.print_exc()