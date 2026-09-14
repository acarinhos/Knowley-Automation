"""Knowley algoritma çalışma kitabını yerel snapshot ile eşitler.

Kullanım:
    python scripts/sync_algorithm_sheet.py

Çalışma sayfasının kullanılan servis hesabıyla paylaşılmış olması gerekir.
Yalnızca okuma kapsamı istenir; kaynak Google Sheet değiştirilmez.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SPREADSHEET_ID = "1LI36wvw4TIWHBHAPgJ2lWyWz6nkj_Hmwp3eN83aD8rs"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "knowley_algorithm_snapshot.json"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _ensure_access(response: Any, service_account_email: str) -> None:
    if response.status_code == 403:
        raise RuntimeError(
            "Google Sheet bu servis hesabıyla paylaşılmamış. "
            f"{service_account_email} adresini çalışma sayfasına Görüntüleyici olarak ekleyin."
        )
    response.raise_for_status()


def _credentials_path() -> Path:
    raw = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH") or os.getenv("FIREBASE_CREDENTIALS_PATH")
    if not raw:
        raise RuntimeError(
            "GOOGLE_SHEETS_CREDENTIALS_PATH veya FIREBASE_CREDENTIALS_PATH tanımlanmalı."
        )
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"Google Sheets servis hesabı bulunamadı: {path}")
    return path


def fetch_workbook(spreadsheet_id: str) -> Dict[str, Any]:
    credentials = service_account.Credentials.from_service_account_file(
        str(_credentials_path()),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    session = AuthorizedSession(credentials)
    metadata_response = session.get(
        f"{SHEETS_API}/{spreadsheet_id}",
        params={"includeGridData": "false"},
        timeout=30,
    )
    _ensure_access(metadata_response, credentials.service_account_email)
    metadata = metadata_response.json()

    sheets = metadata.get("sheets", [])
    ranges = [f"'{item['properties']['title'].replace(chr(39), chr(39) * 2)}'!A:Z" for item in sheets]
    values_response = session.get(
        f"{SHEETS_API}/{spreadsheet_id}/values:batchGet",
        params=[("ranges", value) for value in ranges]
        + [("majorDimension", "ROWS"), ("valueRenderOption", "FORMATTED_VALUE")],
        timeout=60,
    )
    _ensure_access(values_response, credentials.service_account_email)
    value_ranges = values_response.json().get("valueRanges", [])

    snapshot_sheets: List[Dict[str, Any]] = []
    for sheet, value_range in zip(sheets, value_ranges):
        props = sheet["properties"]
        values = value_range.get("values", [])
        if not values:
            columns: List[str] = []
            rows: List[Dict[str, Any]] = []
        else:
            columns = [str(value or "") for value in values[0]]
            columns = [value or ("_row" if index == 0 else f"column_{index}") for index, value in enumerate(columns)]
            rows = [
                {
                    column: (row[index] if index < len(row) else "")
                    for index, column in enumerate(columns)
                }
                for row in values[1:]
                if any(str(value).strip() for value in row)
            ]
        snapshot_sheets.append(
            {
                "title": props["title"],
                "sheet_id": props["sheetId"],
                "columns": columns,
                "rows": rows,
            }
        )

    title = metadata.get("properties", {}).get("title", "Knowley Kategorizasyon Algoritmaları")
    first_title = snapshot_sheets[0]["title"] if snapshot_sheets else ""
    version = first_title.rsplit(" ", 1)[-1] if "v" in first_title else "unknown"
    return {
        "source": {
            "spreadsheet_id": spreadsheet_id,
            "title": title,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "version": version,
            "locale": metadata.get("properties", {}).get("locale", "tr_TR"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        "sheets": snapshot_sheets,
    }


def write_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=output_path.parent, suffix=".tmp"
    ) as handle:
        handle.write(serialized)
        temporary = Path(handle.name)
    temporary.replace(output_path)


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")
    parser = argparse.ArgumentParser(description="Knowley Google Sheets algoritma snapshot'ını yeniler.")
    parser.add_argument(
        "--spreadsheet-id",
        default=os.getenv("KNOWLEY_ALGORITHM_SHEET_ID", DEFAULT_SPREADSHEET_ID),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        payload = fetch_workbook(args.spreadsheet_id)
    except (RuntimeError, FileNotFoundError) as exc:
        parser.exit(2, f"Hata: {exc}\n")
    write_snapshot(payload, args.output)
    print(
        f"Snapshot güncellendi: {args.output} | "
        f"{len(payload['sheets'])} sayfa | sürüm {payload['source']['version']}"
    )


if __name__ == "__main__":
    main()
