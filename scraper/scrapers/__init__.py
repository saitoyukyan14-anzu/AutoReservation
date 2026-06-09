"""有効な区スクレイパーのレジストリ。

新しい区を追加したら、ここに import して `ALL_SCRAPERS` に並べる。
"""
from __future__ import annotations

from scrapers.base import WardScraper
from scrapers.setagaya import SetagayaScraper

#: 実行対象のスクレイパー一覧
ALL_SCRAPERS: list[type[WardScraper]] = [
    SetagayaScraper,
    # 今後ここに追加:
    # ShinjukuScraper, ShibuyaScraper, ...
]
