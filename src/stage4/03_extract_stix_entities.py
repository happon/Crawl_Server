from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig


MODEL_NAME_DEFAULT = "gemini-3-pro-preview"
SLEEP_SEC_DEFAULT = 1.0
MAX_OUTPUT_TOKENS_DEFAULT = 4096


def project_root() -> Path:
    # .../src/stage4/03_extract_stix_entities.py -> 3階層上がプロジェクトルート想定
    return Path(__file__).resolve().parents[3]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(read_text(path))


def safe_json_loads(text: str) -> Dict[str, Any]:
    """
    LLMの返答に余計な文字が混ざった場合の最低限の救済。
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty_response")

    # まず素直に
    try:
        return json.loads(t)
    except Exception:
        pass

    # 最初の { と最後の } を切り出す
    start = t.find("{")
    end = t.rfind("}")
    if 0 <= start < end:
        return json.loads(t[start : end + 1])

    raise ValueError("unparseable_json")


def fill_prompt(template: str, title: str, url: str, clean_text: str) -> str:
    # str.format は {} を壊すので置換
    return (
        template
        .replace("__TITLE__", title)
        .replace("__URL__", url)
        .replace("__CLEAN_TEXT__", clean_text)
    )


def main():
    parser = argparse.ArgumentParser(description="Stage4B: extract STIX entities/relationships from clean_text using Gemini.")
    parser.add_argument("--model", default=MODEL_NAME_DEFAULT)
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC_DEFAULT)
    parser.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS_DEFAULT)
    parser.add_argument("--limit", type=int, default=0, help="process only first N ok-items (0=all)")

    parser.add_argument(
        "--in",
        dest="in_path",
        default=None,
        help="default: <root>/data/stage4_articles_cleaned.json",
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        default=None,
        help="default: <root>/data/stage4b_extracted_stix.json",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_path",
        default=None,
        help="default: <root>/prompts/stage4b_extract.md",
    )

    args = parser.parse_args()

    root = project_root()
    in_path = Path(args.in_path) if args.in_path else (root / "data" / "stage4_articles_cleaned.json")
    out_path = Path(args.out_path) if args.out_path else (root / "data" / "stage4b_extracted_stix.json")
    prompt_path = Path(args.prompt_path) if args.prompt_path else (root / "prompts" / "stage4b_extract.md")

    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing (.env).")

    client = genai.Client(api_key=api_key)

    data = load_json(in_path)
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Input JSON format error: items must be a list")

    prompt_tmpl = read_text(prompt_path)

    out: Dict[str, Any] = {
        "generated_at": now_utc_iso(),
        "model": args.model,
        "source_file": str(in_path),
        "count_total": len(items),
        "count_processed": 0,
        "items": [],
    }

    processed_ok = 0

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
                "extraction_status": "skipped",
                "objects": [],
                "indicators": [],
                "relationships": [],
                "notes": "skipped: no clean_text or retrieval_status not ok",
            })
            continue

        prompt = fill_prompt(prompt_tmpl, title=title, url=url, clean_text=clean_text)

        try:
            resp = client.models.generate_content(
                model=args.model,
                contents=prompt,
                config=GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=args.max_output_tokens,
                ),
            )

            parsed = safe_json_loads(resp.text or "")

            out_item = {
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": status,
                **parsed,
            }

            # 互換のため最低限のデフォルト
            out_item.setdefault("extraction_status", "ok")
            out_item.setdefault("objects", [])
            out_item.setdefault("indicators", [])
            out_item.setdefault("relationships", [])
            out_item.setdefault("notes", "")

            out["items"].append(out_item)
            out["count_processed"] += 1
            processed_ok += 1
            print(f"✅ Stage4B ok: row={row_num} title={title[:40]}")

            if args.limit and processed_ok >= args.limit:
                break

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
                "notes": f"exception: {e}",
            })

        if args.sleep > 0:
            time.sleep(args.sleep)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ wrote: {out_path}")


if __name__ == "__main__":
    main()
