"""区スクレイパーの抽象基底クラス。

新しい区を追加するときは、このクラスを継承して `scrape()` を実装し、
`registry.py` に登録するだけでよい。出力は必ず `list[Slot]` に揃える。
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from models import Slot


class WardScraper(ABC):
    #: 区名。出力JSONの `ward` フィールドおよびUI表示に使われる。
    ward_name: str = ""

    @abstractmethod
    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        """指定期間の空き状況を取得して `Slot` のリストを返す。

        Args:
            date_from: 取得開始日（含む）
            date_to:   取得終了日（含む）
        """
        raise NotImplementedError
