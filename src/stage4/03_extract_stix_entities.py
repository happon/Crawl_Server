from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from google import genai
from google.genai.types import GenerateContentConfig

from src.common.paths import repo_root


MODEL_NAME_DEFAULT = "gemini-3-pro-preview"
SLEEP_SEC_DEFAULT = 1.0
MAX_OUTPUT_TOKENS_DEFAULT = 4096

# ★ 追加: LLMに渡すclean_text上限（長文ほど空応答/失敗が増える）
MAX_CLEAN_CHARS_FOR_LLM = 30_000

# ★ 追加: 空応答などのときのリトライ回数
MAX_RETRIES = 3

ALLOWED_INDICATOR_TYPES = {"ip", "domain", "hash"}


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


def _filter_indicators(indicators: Any) -> List[Dict[str, Any]]:
    """
    indicator_type を ip/domain/hash のみに絞る（最終防波堤）。
    """
    if not isinstance(indicators, list):
        return []
    out: List[Dict[str, Any]] = []
    for ind in indicators:
        if not isinstance(ind, dict):
            continue
        itype = str(ind.get("indicator_type") or "").strip().lower()
        value = str(ind.get("value") or "").strip()
        if not itype or not value:
            continue
        if itype not in ALLOWED_INDICATOR_TYPES:
            continue
        out.append(ind)
    return out


def _call_gemini_with_retry(
    client: genai.Client,
    *,
    model: str,
    prompt: str,
    max_output_tokens: int,
    base_sleep: float,
) -> Dict[str, Any]:
    """
    empty_response / unparseable_json が出たときだけリトライ。
    """
    last_err: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=max_output_tokens,
            ),
        )

        txt = (getattr(resp, "text", "") or "").strip()
        if not txt:
            last_err = "empty_response"
        else:
            try:
                return safe_json_loads(txt)
            except Exception as e:
                last_err = str(e)

        if attempt < MAX_RETRIES:
            sleep_sec = base_sleep * (2 ** (attempt - 1))
            print(f"⚠️ retry {attempt}/{MAX_RETRIES} due to {last_err}; sleep {sleep_sec:.1f}s")
            time.sleep(sleep_sec)

    raise ValueError(last_err or "llm_call_failed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage4B: extract STIX entities/relationships from clean_text using Gemini."
    )
    parser.add_argument("--model", default=MODEL_NAME_DEFAULT)
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC_DEFAULT)
    parser.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS_DEFAULT)
    parser.add_argument("--limit", type=int, default=0, help="process only first N ok-items (0=all)")

    # ★ 追加: clean_text投入上限をコマンドでも調整可能に
    parser.add_argument(
        "--max-clean-chars",
        type=int,
        default=MAX_CLEAN_CHARS_FOR_LLM,
        help="Max chars of clean_text passed to LLM (default: 30000).",
    )

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

    root = repo_root()
    in_path = Path(args.in_path).expanduser().resolve() if args.in_path else (root / "data" / "stage4_articles_cleaned.json")
    out_path = Path(args.out_path).expanduser().resolve() if args.out_path else (root / "data" / "stage4b_extracted_stix.json")
    prompt_path = Path(args.prompt_path).expanduser().resolve() if args.prompt_path else (root / "prompts" / "stage4b_extract.md")

    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing. Set it in <root>/.env or environment.")

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
            out["items"].append(
                {
                    "_row_num": row_num,
                    "title": title,
                    "url": url,
                    "retrieval_status": status,
                    "extraction_status": "skipped",
                    "objects": [],
                    "indicators": [],
                    "relationships": [],
                    "notes": "skipped: no clean_text or retrieval_status not ok",
                }
            )
            continue

        # ★ 長文は切ってから投入
        truncated = False
        clean_for_llm = clean_text
        if len(clean_for_llm) > int(args.max_clean_chars):
            clean_for_llm = clean_for_llm[: int(args.max_clean_chars)]
            truncated = True

        prompt = fill_prompt(prompt_tmpl, title=title, url=url, clean_text=clean_for_llm)

        try:
            parsed = _call_gemini_with_retry(
                client,
                model=args.model,
                prompt=prompt,
                max_output_tokens=args.max_output_tokens,
                base_sleep=max(args.sleep, 0.2),
            )

            # 互換のため最低限のデフォルト
            parsed.setdefault("extraction_status", "ok")
            parsed.setdefault("objects", [])
            parsed.setdefault("indicators", [])
            parsed.setdefault("relationships", [])
            parsed.setdefault("notes", "")

            # ★ indicatorを最終的に絞る
            parsed["indicators"] = _filter_indicators(parsed.get("indicators"))

            # ★ notesにトリム情報を付与（原因追跡用）
            if truncated:
                extra = f"[clean_text_truncated_to={args.max_clean_chars}]"
                parsed["notes"] = (str(parsed.get("notes") or "") + " " + extra).strip()

            out_item = {
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": status,
                **parsed,
            }

            out["items"].append(out_item)
            out["count_processed"] += 1
            processed_ok += 1
            print(f"✅ Stage4B ok: row={row_num} title={title[:40]}")

            if args.limit and processed_ok >= args.limit:
                break

        except Exception as e:
            print(f"⚠️ Stage4B fail: row={row_num} title={title[:40]} err={e}")
            out["items"].append(
                {
                    "_row_num": row_num,
                    "title": title,
                    "url": url,
                    "retrieval_status": status,
                    "extraction_status": "error",
                    "objects": [],
                    "indicators": [],
                    "relationships": [],
                    "notes": f"exception: {e}",
                }
            )

        if args.sleep > 0:
            time.sleep(args.sleep)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ wrote: {out_path}")


if __name__ == "__main__":
    main()
