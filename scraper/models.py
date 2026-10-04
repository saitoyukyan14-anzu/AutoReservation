"""共通データモデル。

全区のスクレイパーはここで定義する `Slot` のリストを返す。
これによりフロントエンドは区が増えても無改修で対応できる。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Slot:
    """ある施設・部屋・日付における「空いている時間帯」1件。"""

    ward: str          # 区名（例: "世田谷区"）
    facility: str      # 施設名（例: "北沢タウンホール"）
    room: str          # 部屋名（例: "第1会議室"）
    date: str          # YYYY-MM-DD
    start: str         # HH:MM
    end: str           # HH:MM

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Facility:
    """施設情報 `web/public/data/facilities.json` の1行（手動管理・施設単位）。

    空き状況とは `ward` + `facility` で突合する（部屋単位ではない）。
    """

    ward: str
    facility: str
    url: Optional[str] = None   # 施設紹介ページ（区公式等）のURL
    note: str = ""

    def key(self) -> tuple[str, str]:
        return (self.ward, self.facility)

    def to_dict(self) -> dict:
        return asdict(self)
