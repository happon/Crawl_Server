import gspread
from google.oauth2.service_account import Credentials

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_file(
    "credentials.json",
    scopes=scopes
)

gc = gspread.authorize(credentials)

# 既存のスプレッドシートを開く（名前で指定）
sh = gc.open("RSS_記事一覧")

# 最初のワークシートを選択
worksheet = sh.sheet1

# 初期ヘッダーが未設定であれば設定
worksheet.append_row(["published", "title", "url", "source", "include_flag", "summary", "summary_detail", "logic_title", "category_main",  "tags"])
