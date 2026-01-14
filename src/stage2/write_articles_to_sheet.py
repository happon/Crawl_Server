import feedparser
import gspread
from google.oauth2.service_account import Credentials

# ---------- 設定 ----------

RSS_FEED_URL = "https://www.bleepingcomputer.com/feed/"  # 任意のRSSフィードURL
SPREADSHEET_NAME = "RSS_記事一覧"                         # 既存のスプレッドシート名
CREDENTIALS_FILE = "credentials.json"                    # 認証JSONファイル

# ---------- 認証と接続 ----------

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
gc = gspread.authorize(credentials)

sh = gc.open(SPREADSHEET_NAME)
worksheet = sh.sheet1

# ---------- RSS記事取得 ----------

feed = feedparser.parse(RSS_FEED_URL)
articles = feed.entries

# ---------- スプレッドシートに追加 ----------

for entry in articles:
    published = entry.published if "published" in entry else ""
    title = entry.title
    url = entry.link
    source = feed.feed.title  # フィード全体のタイトルをソース名とする
    include_flag = ""         # まだ人手で選別していないため空欄
    summary = ""              # 後工程（OpenAI要約など）で挿入予定

    worksheet.append_row([published, title, url, source, include_flag, summary])
