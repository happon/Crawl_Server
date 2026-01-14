from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig


# ====== 保存設定 ======
RAW_SAVE_MAX_CHARS = 250_000
CLEAN_KEEP_MAX_CHARS = 120_000  # JSON肥大化/暴発対策（必要なら調整）


# ====== LLM出力（raw + clean を受け取る）=====
class Stage4ALLMOut(TypedDict, total=False):
    retrieval_status: str  # ok / error
    language: str          # en / ja / other
    raw_text: str          # 本文相当
    clean_text: str        # 中心テーマのみ
    removed_notes: List[str]
    focus_summary: str


# ====== JSON出力（cleanは保持、rawは参照のみ）=====
class Stage4AItemOut(TypedDict, total=False):
    _row_num: int
    title: str
    url: str

    retrieval_status: str
    language: str
    removed_notes: List[str]
    focus_summary: str

    # cleanはReport.contentに入れる想定なのでJSONに残す
    clean_text: str
    clean_sha256: str
    clean_char_len: int
    clean_truncated: bool

    # rawは外部ファイルに保存し、参照情報だけJSONに残す
    raw_saved_path: str
    raw_sha256: str
    raw_char_len: int
    raw_truncated: bool


class Stage4AOutput(TypedDict):
    generated_at: str
    model: str
    count: int
    items: List[Stage4AItemOut]


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_json_parse(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        pass
    try:
        start = t.find("{")
        end = t.rfind("}")
        if 0 <= start < end:
            return json.loads(t[start : end + 1])
    except Exception:
        return None
    return None


def read_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def extract_rows_from_stage4_input(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Unsupported input JSON format. Expected {'rows':[...]} or a list.")


def sanitize_filename(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80] if s else "article"


def sha256_text(text: str) -> str:
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8", errors="ignore"))
    return h.hexdigest()


def save_raw_text(raw_dir: Path, row_num: int, title: str, raw_text: str) -> Dict[str, Any]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    base = f"row_{row_num:06d}_{sanitize_filename(title)}_raw.txt"
    path = raw_dir / base

    raw_char_len = len(raw_text or "")
    truncated = False
    to_save = raw_text or ""

    if raw_char_len > RAW_SAVE_MAX_CHARS:
        to_save = to_save[:RAW_SAVE_MAX_CHARS]
        truncated = True

    path.write_text(to_save, encoding="utf-8", errors="ignore")

    return {
        "raw_saved_path": str(path),
        "raw_sha256": sha256_text(to_save),
        "raw_char_len": raw_char_len,
        "raw_truncated": truncated,
    }


def keep_clean_text(clean_text: str) -> Dict[str, Any]:
    clean_char_len = len(clean_text or "")
    truncated = False
    kept = clean_text or ""

    if clean_char_len > CLEAN_KEEP_MAX_CHARS:
        kept = kept[:CLEAN_KEEP_MAX_CHARS]
        truncated = True

    return {
        "clean_text": kept,
        "clean_sha256": sha256_text(kept),
        "clean_char_len": clean_char_len,
        "clean_truncated": truncated,
    }


def build_prompt(prompt_base: str, title: str, url: str) -> str:
    return f"{prompt_base}\n\nTarget:\n- title: {title}\n- url: {url}\n"


def main():
    parser = argparse.ArgumentParser(
        description="Stage4A: fetch raw + generate clean; save raw as file; keep clean in JSON."
    )
    parser.add_argument("--in", dest="in_path", default=None,
                        help="Input JSON path (default: <root>/data/stage4_input_included.json)")
    parser.add_argument("--out", dest="out_path", default=None,
                        help="Output JSON path (default: <root>/data/stage4_articles_cleaned.json)")
    parser.add_argument("--raw-dir", dest="raw_dir", default=None,
                        help="Raw text dir (default: <root>/data/raw_articles)")
    parser.add_argument("--prompt", dest="prompt_path", default=None,
                        help="Prompt path (default: <root>/prompts/stage4a_clean.md)")
    parser.add_argument("--model", default="gemini-3-pro-preview", help="Gemini model name")
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep seconds between calls")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N rows (0=all)")
    parser.add_argument("--max-output-tokens", type=int, default=8192, help="Max output tokens")
    args = parser.parse_args()

    root = project_root()
    in_path = Path(args.in_path) if args.in_path else (root / "data" / "stage4_input_included.json")
    out_path = Path(args.out_path) if args.out_path else (root / "data" / "stage4_articles_cleaned.json")
    raw_dir = Path(args.raw_dir) if args.raw_dir else (root / "data" / "raw_articles")
    prompt_path = Path(args.prompt_path) if args.prompt_path else (root / "prompts" / "stage4a_clean.md")

    if not in_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {in_path}")

    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing (.env).")

    client = genai.Client(api_key=api_key)
    prompt_base = read_prompt(prompt_path)

    payload = load_json(in_path)
    rows = extract_rows_from_stage4_input(payload)

    out: Stage4AOutput = {
        "generated_at": now_utc_iso(),
        "model": args.model,
        "count": 0,
        "items": [],
    }

    processed = 0

    for row in rows:
        row_num = row.get("_row_num")
        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()

        if not row_num or not title or not url:
            continue

        prompt = build_prompt(prompt_base, title, url)

        try:
            cfg = GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Stage4ALLMOut,
                max_output_tokens=args.max_output_tokens,
                tools=[{"url_context": {}}],
            )

            resp = client.models.generate_content(
                model=args.model,
                contents=prompt,
                config=cfg,
            )

            parsed = getattr(resp, "parsed", None)
            data: Optional[Dict[str, Any]] = dict(parsed) if parsed else safe_json_parse(resp.text or "")

            if not data:
                llm_out: Stage4ALLMOut = {
                    "retrieval_status": "error",
                    "language": "other",
                    "raw_text": "",
                    "clean_text": "",
                    "removed_notes": ["empty_or_unparseable_response"],
                    "focus_summary": "",
                }
            else:
                llm_out = data  # type: ignore[assignment]
                llm_out.setdefault("retrieval_status", "ok")
                llm_out.setdefault("language", "other")
                llm_out.setdefault("raw_text", "")
                llm_out.setdefault("clean_text", "")
                llm_out.setdefault("removed_notes", [])
                llm_out.setdefault("focus_summary", "")

            raw_text = (llm_out.get("raw_text") or "").strip()
            clean_text = (llm_out.get("clean_text") or "").strip()

            item_out: Stage4AItemOut = {
                "_row_num": int(row_num),
                "title": title,
                "url": url,
                "retrieval_status": str(llm_out.get("retrieval_status") or "ok"),
                "language": str(llm_out.get("language") or "other"),
                "removed_notes": list(llm_out.get("removed_notes") or []),
                "focus_summary": str(llm_out.get("focus_summary") or ""),
            }

            # raw保存（txt）
            if raw_text:
                item_out.update(save_raw_text(raw_dir, int(row_num), title, raw_text))
            else:
                item_out.update({
                    "raw_saved_path": "",
                    "raw_sha256": "",
                    "raw_char_len": 0,
                    "raw_truncated": False,
                })

            # cleanはJSONに保持（必要なら上限で切る）
            item_out.update(keep_clean_text(clean_text))

            out["items"].append(item_out)
            out["count"] += 1
            processed += 1

            print(f"✅ Stage4A ok: row={row_num} title={title[:60]}")

        except Exception as e:
            print(f"⚠️ Stage4A fail: row={row_num} title={title[:60]} err={e}")
            out["items"].append(
                {
                    "_row_num": int(row_num),
                    "title": title,
                    "url": url,
                    "retrieval_status": "error",
                    "language": "other",
                    "removed_notes": ["exception"],
                    "focus_summary": "",
                    "raw_saved_path": "",
                    "raw_sha256": "",
                    "raw_char_len": 0,
                    "raw_truncated": False,
                    "clean_text": "",
                    "clean_sha256": "",
                    "clean_char_len": 0,
                    "clean_truncated": False,
                }
            )
            out["count"] += 1
            processed += 1

        if args.sleep > 0:
            time.sleep(args.sleep)

        if args.limit and processed >= args.limit:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ wrote: {out_path}")
    print(f"✅ raw saved dir: {raw_dir}")


if __name__ == "__main__":
    main()
