import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig

BASE_DIR = Path(__file__).resolve().parent

INPUT_JSON = BASE_DIR / "stage4_articles_cleaned.json"
OUTPUT_JSON = BASE_DIR / "stage4b_extracted_stix.json"
PROMPT_FILE = BASE_DIR / "prompt_stage4b_extract.md"

MODEL_NAME = "gemini-3-pro-preview"
SLEEP_SEC = 1.0
MAX_OUTPUT_TOKENS = 4096


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_prompt_template() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"{PROMPT_FILE} が見つかりません。同じフォルダに作成してください。")
    return PROMPT_FILE.read_text(encoding="utf-8")


def safe_json_loads(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        raise ValueError("empty_response")
    return json.loads(t)


def fill_prompt(template: str, title: str, url: str, clean_text: str) -> str:
    # str.format を使わず、固定トークンを置換する（JSON例の {} を壊さない）
    return (
        template
        .replace("__TITLE__", title)
        .replace("__URL__", url)
        .replace("__CLEAN_TEXT__", clean_text)
    )


def main():
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("環境変数 GOOGLE_API_KEY が未設定です（.env を確認してください）。")

    client = genai.Client(api_key=api_key)

    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"入力が見つかりません: {INPUT_JSON}")

    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    items = data.get("items", [])

    prompt_tmpl = load_prompt_template()

    out = {
        "generated_at": now_utc_iso(),
        "model": MODEL_NAME,
        "source_file": str(INPUT_JSON.name),
        "count_total": len(items),
        "count_processed": 0,
        "items": []
    }

    for item in items:
        row_num = item.get("_row_num")
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        status = str(item.get("retrieval_status", "")).strip()
        clean_text = str(item.get("clean_text", "")).strip()

        if status != "ok" or not clean_text:
            out["items"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": status,
                "extraction_status": "error",
                "objects": [],
                "indicators": [],
                "relationships": [],
                "notes": "skipped: no clean_text or retrieval not ok"
            })
            continue

        prompt = fill_prompt(prompt_tmpl, title=title, url=url, clean_text=clean_text)

        try:
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )

            text = (resp.text or "").strip()
            parsed = safe_json_loads(text)

            out_item = {
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": status,
                **parsed
            }

            out_item.setdefault("extraction_status", "ok")
            out_item.setdefault("objects", [])
            out_item.setdefault("indicators", [])
            out_item.setdefault("relationships", [])
            out_item.setdefault("notes", "")

            out["items"].append(out_item)
            out["count_processed"] += 1
            print(f"✅ Stage4B ok: row={row_num} title={title[:40]}")

        except Exception as e:
            print(f"⚠️ Stage4B fail: row={row_num} title={title[:40]} err={e}")
            out["items"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": status,
                "extraction_status": "error",
                "objects": [],
                "indicators": [],
                "relationships": [],
                "notes": f"exception: {e}"
            })

        time.sleep(SLEEP_SEC)

    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ wrote: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
