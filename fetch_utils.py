# scripts/fetch_utils.py
from seleniumbase import SB
import requests
import feedparser
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
import time

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

def fetch_article_uc_selenium(url, timeout=6):
    with SB(uc=True, test=True) as sb:
        sb.uc_open_with_reconnect(url, reconnect_time=timeout)
        sb.uc_gui_click_captcha()  # Turnstile対策
        html = sb.get_page_source()
    return html

def extract_full_article_thn(url):
    html = fetch_article_uc_selenium(url)
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", class_="post-body")  # THN特有のクラス
    if div:
        return div.get_text(separator="\n\n", strip=True)
    paragraphs = soup.find_all("p")
    return "\n\n".join(p.get_text(strip=True) for p in paragraphs)

# HTML取得 (requests)
def fetch_html(url):
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.text

# RSSフィードをパース
def parse_feed(url):
    feed = feedparser.parse(url)
    return feed.entries

# RSSから取得したエントリに対応する記事全文を取得（Selenium使用）
def extract_full_article_rss(entry):
    return extract_full_article_html(entry.link)

# BeautifulSoupで記事本文を抽出（HTML文字列を渡す）
def parse_article_body(html):
    soup = BeautifulSoup(html, "html.parser")

    # 優先順位で探す：post-body > article > entry-content など
    for class_hint in ["post-body", "article-body", "entry-content", "post-content"]:
        div = soup.find("div", class_=class_hint)
        if div:
            return div.get_text(separator="\n\n", strip=True)

    # 最後の手段：<article>タグ
    article = soup.find("article")
    if article:
        return article.get_text(separator="\n\n", strip=True)

    # 最後の最後の手段：すべての<p>
    paragraphs = soup.find_all("p")
    return "\n\n".join(p.get_text(strip=True) for p in paragraphs)

# Seleniumでページを開いて全文を取得
def extract_full_article_html(url):
    options = uc.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    try:
        driver.get(url)
        time.sleep(5)  # JavaScriptでの描画を待つ（必要なら調整）

        html = driver.page_source
        return parse_article_body(html)

    except Exception as e:
        return f"[ERROR] Failed to extract article from {url}: {str(e)}"

    finally:
        driver.quit()
