import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig

# ===== パス固定（この .py と同じ場所）=====
BASE_DIR = Path(__file__).resolve().parent

INPUT_JSON = BASE_DIR / "stage4_input_included.json"
OUTPUT_JSON = BASE_DIR / "stage4_articles_cleaned.json"

PROMPT_FILE = BASE_DIR / "prompt_stage4a_clean.md"

# モデル（まず動作確認できているものを使うのが安全）
MODEL_NAME = "gemini-3-pro-preview"   # 速度優先なら "gemini-3-flash-preview" に変更

# 手動投入テキスト置き場（任意）
MANUAL_DIR = BASE_DIR / "manual_articles"

# API連打回避（必要に応じて調整）
SLEEP_SEC = 1.0

# 生成上限（整理済み本文を返すので大きめ）
MAX_OUTPUT_TOKENS = 8192


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_prompt_template() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"{PROMPT_FILE} が見つかりません。同じフォルダに作成してください。")
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def build_prompt(prompt_base: str, title: str, url: str, raw_text: str | None) -> str:
    # raw_text がある場合はそれを優先して解析させる
    # raw_text がない場合は URL context tool に読ませる（後段のconfigでtools指定）
    if raw_text:
        return f"""{prompt_base}

対象:
- title: {title}
- url: {url}

raw_text:
\"\"\"{raw_text}\"\"\"
"""
    else:
        return f"""{prompt_base}

対象:
- title: {title}
- url: {url}

raw_text:
（未提供。必要ならURLを参照して本文を取得してください）
"""


def read_manual_text(row_num: int) -> str | None:
    """
    manual_articles/row_<rownum>.txt があればそれを使う
    例: manual_articles/row_123.txt
    """
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    p = MANUAL_DIR / f"row_{row_num}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8", errors="ignore").strip() or None
    return None


def main():
    load_dotenv()
    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise ValueError("環境変数 GOOGLE_API_KEY が未設定です（.env を確認してください）。")

    client = genai.Client(api_key=google_api_key)

    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"入力が見つかりません: {INPUT_JSON}")

    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    rows = data.get("rows", [])

    prompt_base = load_prompt_template()

    out = {
        "generated_at": now_utc_iso(),
        "model": MODEL_NAME,
        "count": 0,
        "items": []
    }

    for row in rows:
        row_num = row.get("_row_num")
        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()

        if not row_num or not title or not url:
            continue

        # 1) 手動テキストがあれば優先
        manual_text = read_manual_text(int(row_num))

        prompt = build_prompt(prompt_base, title, url, manual_text)

        try:
            # 2) URL参照は「手動テキストが無いときだけ」有効化
            cfg = GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=MAX_OUTPUT_TOKENS,
                tools=[{"url_context": {}}] if not manual_text else None,
            )

            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=cfg,
            )

            text = (resp.text or "").strip()
            if not text:
                item = {
                    "_row_num": row_num,
                    "title": title,
                    "url": url,
                    "retrieval_status": "error",
                    "language": "other",
                    "clean_text": "",
                    "removed_notes": ["empty_response"],
                    "focus_summary": ""
                }
            else:
                item = json.loads(text)
                item["_row_num"] = row_num

            out["items"].append(item)
            out["count"] += 1
            print(f"✅ Stage4A ok: row={row_num} title={title[:40]}")

        except Exception as e:
            print(f"⚠️ Stage4A fail: row={row_num} title={title[:40]} err={e}")
            out["items"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": "error",
                "language": "other",
                "clean_text": "",
                "removed_notes": ["exception"],
                "focus_summary": ""
            })

        time.sleep(SLEEP_SEC)

    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ wrote: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
