import os
import re
import json
import gspread
import google.genai as genai
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ----------------- 設定 -----------------
load_dotenv()
SPREADSHEET_NAME = "RSS_記事一覧"
CREDENTIALS_FILE = "credentials.json"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel(model_name="gemini-3-pro-preview")

# ----------------- Google Sheets接続 -----------------
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
gc = gspread.authorize(credentials)
ws = gc.open(SPREADSHEET_NAME).sheet1

# ----------------- 対象データ取得 -----------------
rows = ws.get_all_records()

for i, row in enumerate(rows):
    if row["summary"] and row["category_main"] and row["logic_title"]:
        continue

    title = row["title"]
    url = row["url"]

    prompt = f"""
以下のニュース記事を読み、指定された出力形式で分類・タグ付け・要約を行ってください。

【分析観点】
- 主分類（category_main）を以下から1つ選んでください：
  - "Cyber_Tech": 技術中心
  - "Cyber_Threat": 攻撃・脅威中心
  - "PMESII": 政治・政策・社会構造など

- 関連する技術や用語（tags）を5個以内で抽出してください。
- 5W1Hに基づいて以下のJSON形式で出力してください。

```json
{{
  "logic_title": "5W1H形式のタイトル（1文）",
  "category_main": "Cyber_Tech | Cyber_Threat | PMESII",
  "tags": ["tag1", "tag2", "tag3"],
  "summary": "100文字以内の簡潔要約",
  "summary_detail": "5W1Hを元に3〜5文で詳しく説明"
}}
【対象記事】
タイトル: {title}
URL: {url}

JSON形式以外の文字列は含めないでください。
"""
    
try:
    response = model.generate_content(prompt)
    content = response.text.strip()
    print("🔍 DEBUGレスポンス:\n", content)

    content = re.sub(r"^```json\s*|\s*```$", "", content)
    parsed = json.loads(content)

    row_num = i + 2
    ws.update_cell(row_num, ws.find("logic_title").col, parsed["logic_title"])
    ws.update_cell(row_num, ws.find("category_main").col, parsed["category_main"])
    ws.update_cell(row_num, ws.find("tags").col, json.dumps(parsed["tags"], ensure_ascii=False))
    ws.update_cell(row_num, ws.find("summary").col, parsed["summary"])
    ws.update_cell(row_num, ws.find("summary_detail").col, parsed["summary_detail"])

    print(f"✅ Row {row_num - 1}: 更新完了")

except Exception as e:
    print(f"⚠️ Row {i+1}: エラー - {e}")
