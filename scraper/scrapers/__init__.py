"""有効な区スクレイパーのレジストリ。

新しい区を追加したら、ここに import して `ALL_SCRAPERS` に並べる。
各クラスの `key`（区キー）は一意であること。`main.py --ward <key>` と
`.github/workflows/scrape.yml` の matrix はこのキーで区を指定する。
"""
from __future__ import annotations

from scrapers.base import WardScraper
from scrapers.setagaya import SetagayaScraper

#: 実行対象のスクレイパー一覧
ALL_SCRAPERS: list[type[WardScraper]] = [
    SetagayaScraper,
    # 今後ここに追加:
    # BunkyoScraper, ShinjukuScraper, ...
]
