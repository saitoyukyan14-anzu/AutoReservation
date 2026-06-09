"""広さデータベース（Googleスプレッドシート）の取り込み。

スプレッドシートを「ウェブに公開」したCSV URL から読み込み、`facilities.json`
を生成する。URL未設定時は既存の `facilities.json` を尊重し、何もしない
（＝手動編集モード）。

想定するスプレッドシートのヘッダ行（日本語可・順不同）:
    ward, facility, room, area_sqm, capacity, note
    区,   施設,     部屋,  面積,     定員,     備考
"""
from __future__ import annotations

import csv
import io
import json

import requests

import config
from models import Facility

# CSVヘッダの日本語/英語ゆれを正規化するマップ
HEADER_ALIASES = {
    "ward": "ward", "区": "ward", "区名": "ward",
    "facility": "facility", "施設": "facility", "施設名": "facility",
    "room": "room", "部屋": "room", "部屋名": "room",
    "area_sqm": "area_sqm", "面積": "area_sqm", "広さ": "area_sqm",
    "capacity": "capacity", "定員": "capacity",
    "note": "note", "備考": "note", "メモ": "note",
}


def _to_float(v: str):
    v = (v or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _to_int(v: str):
    v = (v or "").strip()
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


def fetch_from_sheet(csv_url: str) -> list[Facility]:
    resp = requests.get(csv_url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    reader = csv.DictReader(io.StringIO(resp.text))
    facilities: list[Facility] = []
    for raw in reader:
        row = {HEADER_ALIASES.get(k.strip(), k.strip()): v for k, v in raw.items()}
        facility = (row.get("facility") or "").strip()
        if not facility:
            continue
        facilities.append(
            Facility(
                ward=(row.get("ward") or "").strip(),
                facility=facility,
                room=(row.get("room") or "").strip(),
                area_sqm=_to_float(row.get("area_sqm")),
                capacity=_to_int(row.get("capacity")),
                note=(row.get("note") or "").strip(),
            )
        )
    return facilities


def build_facilities_json() -> None:
    """設定に応じて facilities.json を生成（またはスキップ）する。"""
    if not config.FACILITIES_SHEET_CSV_URL:
        print("[facilities] スプレッドシートURL未設定。既存の facilities.json を使用します。")
        return
    facilities = fetch_from_sheet(config.FACILITIES_SHEET_CSV_URL)
    payload = {"facilities": [f.to_dict() for f in facilities]}
    config.FACILITIES_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[facilities] {len(facilities)} 件を {config.FACILITIES_JSON} に書き出しました。")
