from __future__ import annotations

from pathlib import Path
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def project_root() -> Path:
    # .../src/stage2/sheets_client.py -> 3階層上がプロジェクトルート想定
    return Path(__file__).resolve().parents[3]


def get_gspread_client(credentials_path: Optional[str] = None) -> gspread.Client:
    """
    サービスアカウント認証で gspread クライアントを返す。
    credentials_path が未指定なら <project_root>/credentials.json を使う。
    """
    root = project_root()
    cred_path = Path(credentials_path) if credentials_path else (root / "credentials.json")

    if not cred_path.exists():
        raise FileNotFoundError(
            f"credentials.json not found: {cred_path} "
            f"(set --credentials or place it at project root)"
        )

    creds = Credentials.from_service_account_file(str(cred_path), scopes=SCOPES)
    return gspread.authorize(creds)


def open_worksheet(
    gc: gspread.Client,
    *,
    spreadsheet_name: str,
    worksheet_name: str = "Sheet1",
):
    sh = gc.open(spreadsheet_name)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=1000, cols=20)
    return ws
