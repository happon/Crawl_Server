from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import List, Optional, TypedDict

import gspread
from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig
from google.oauth2.service_account import Credentials


# ===== Sheetsの列（確定）=====
HEADERS = [
    "published",
    "source",
    "author",
    "title",
    "url",
    "logic_title",
    "summary",
    "summary_detail",
    "category_main",
    "tags",
    "include_flag",
]

# ===== Gemini 出力スキーマ（Stage3が埋める列）=====
class Stage3Out(TypedDict):
    logic_title: str
    summary: str
    summary_detail: str
    category_main: str
    tags: List[str]  # モデル側は配列で返す想定（Sheetsには文字列で格納）


def project_root() -> Path:
    # .../src/stage3/gemini_summarize.py -> 3階層上がプロジェクトルート想定
    return Path(__file__).resolve().parents[3]


def get_ws(spreadsheet_name: str, worksheet_name: str, credentials_path: Optional[str]):
    root = project_root()
    cred_path = Path(credentials_path) if credentials_path else (root / "credentials.json")
    if not cred_path.exists():
        raise FileNotFoundError(f"credentials.json not found: {cred_path}")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    gc = gspread.authorize(credentials)

    sh = gc.open(spreadsheet_name)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=1000, cols=30)
    return ws


def ensure_header_row(ws):
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(HEADERS, value_input_option="RAW")
        return

    # 先頭HEADERS分だけ一致していることを要求（列が増える運用は許容）
    got = [c.strip() for c in first_row[: len(HEADERS)]]
    if got != HEADERS:
        raise ValueError(
            "Header mismatch. Please align the sheet header to:\n"
            + "\t" + "\t".join(HEADERS)
        )


def load_prompt(prompt_path: Optional[str]) -> str:
    root = project_root()
    p = Path(prompt_path) if prompt_path else (root / "prompts" / "stage3_prompt.md")
    if not p.exists():
        raise FileNotFoundError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


def build_prompt(template: str, title: str, url: str) -> str:
    return template.replace("{{title}}", title).replace("{{url}}", url)


def tags_to_cell(tags: List[str]) -> str:
    # Sheets側は「カンマ区切り文字列」に統一（後段処理が楽）
    cleaned = [t.strip() for t in (tags or []) if str(t).strip()]
    return ", ".join(cleaned[:5])


def update_row_cells(ws, row_num: int, col_map: dict, out: Stage3Out):
    """
    1行ぶんをまとめて更新（A1記法で横一括更新）
    """
    values = [[
        out.get("logic_title", ""),
        out.get("summary", ""),
        out.get("summary_detail", ""),
        out.get("category_main", ""),
        tags_to_cell(out.get("tags", [])),
    ]]

    start_col = col_map["logic_title"]
    end_col = col_map["tags"]
    # 例：F2:J2 のようなレンジ
    start_a1 = gspread.utils.rowcol_to_a1(row_num, start_col)
    end_a1 = gspread.utils.rowcol_to_a1(row_num, end_col)
    rng = f"{start_a1}:{end_a1}"

    ws.update(rng, values, value_input_option="RAW")


def main():
    parser = argparse.ArgumentParser(description="Stage3: Summarize/classify from URL using Gemini and write back to Sheets.")
    parser.add_argument("--sheet", default="RSS_記事一覧", help="Spreadsheet name")
    parser.add_argument("--worksheet", default="Sheet1", help="Worksheet name")
    parser.add_argument("--credentials", default=None, help="Path to credentials.json (optional)")
    parser.add_argument("--prompt", default=None, help="Path to stage3 prompt markdown (optional)")
    parser.add_argument("--model", default="gemini-3-pro-preview", help="Gemini model name")
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep seconds between API calls")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N target rows (0=all)")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY is missing in environment (.env).")

    client = genai.Client(api_key=api_key)

    ws = get_ws(args.sheet, args.worksheet, args.credentials)
    ensure_header_row(ws)

    headers = ws.row_values(1)
    col_map = {name: i + 1 for i, name in enumerate(headers)}

    required = ["title", "url", "logic_title", "summary", "summary_detail", "category_main", "tags", "include_flag"]
    for c in required:
        if c not in col_map:
            raise ValueError(f"Missing column in sheet: {c}")

    prompt_template = load_prompt(args.prompt)

    rows = ws.get_all_records()
    print(f"Loaded {len(rows)} rows.")

    processed = 0
    for i, row in enumerate(rows):
        row_num = i + 2  # header is row1

        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()
        if not title or not url:
            continue

        # 既にStage3済み（summaryが埋まっている）ならスキップ
        if str(row.get("summary", "")).strip():
            continue

        # （任意）include_flagが既に入っている行は、人が触っている可能性があるのでスキップしたい場合
        # if str(row.get("include_flag", "")).strip():
        #     continue

        prompt = build_prompt(prompt_template, title, url)

        try:
            resp = client.models.generate_content(
                model=args.model,
                contents=prompt,
                config=GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Stage3Out,
                    tools=[{"url_context": {}}],
                ),
            )

            # SDKがparsedを返せる場合はそれを優先
            out: Stage3Out
            if getattr(resp, "parsed", None):
                out = resp.parsed  # type: ignore[assignment]
            else:
                out = json.loads((resp.text or "").strip())

            update_row_cells(ws, row_num, col_map, out)

            processed += 1
            print(f"Row {row_num}: updated")

            if args.sleep > 0:
                time.sleep(args.sleep)

            if args.limit and processed >= args.limit:
                break

        except Exception as e:
            print(f"Row {row_num}: ERROR - {e}")
            # デバッグ用（必要なら）
            # import traceback; traceback.print_exc()

    print(f"Done. Updated rows: {processed}")


if __name__ == "__main__":
    main()
