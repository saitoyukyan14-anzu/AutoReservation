"""区スクレイパーの抽象基底クラス。

新しい区を追加するときは、このクラスを継承して `key` / `ward_name` / `scrape()` を実装し、
`scrapers/__init__.py` の `ALL_SCRAPERS` に登録する。出力は必ず `list[Slot]` に揃える。

基底クラスは次の2つを共通で受け持つ（`main.py` がコンストラクタ引数で渡す）:

- **shard 分割**（`shard_index` / `shard_count`）
  1区を複数ジョブで分担して取得するための指定。分割に対応するスクレイパーは
  `supports_shard = True` にし、`scrape()` 内で `self.shard_items(...)` 等を使って
  担当分だけを取得すること。非対応（既定）のまま `shard_count > 1` で生成しようとすると
  `ShardNotSupportedError` になる（全 shard で同じ区を丸ごと重複取得するのを防ぐため）。

- **時間予算**（`deadline`）
  GitHub Actions のジョブ上限で強制終了されると結果が全損するため、期限を過ぎたら
  それまでの取得分を返して終わる。長いループ（施設・期間・ページ等）の区切りで
  `self.out_of_time("どこで")` を確認し、True なら打ち切って取得済みの分を返すこと。

サブクラスで `__init__` を定義する場合は、必ず `shard_index` / `shard_count` / `deadline`
を受け取り `super().__init__(...)` に渡すこと。
"""
from __future__ import annotations

import datetime as dt
import math
import time
from abc import ABC, abstractmethod
from typing import Sequence, TypeVar

from models import Slot

T = TypeVar("T")


class ShardNotSupportedError(ValueError):
    """shard 分割に未対応のスクレイパーを shard_count > 1 で生成しようとした。"""


class WardScraper(ABC):
    #: 区キー（英小文字・数字・_ のみ）。`main.py --ward <key>`、part ファイル名、
    #: `.github/workflows/scrape.yml` の matrix で使う。例: "setagaya"
    key: str = ""
    #: 区名。出力JSONの `ward` フィールドおよびUI表示に使われる。
    ward_name: str = ""
    #: shard 分割に対応しているか。True にする場合は scrape() で担当分だけ取得すること。
    supports_shard: bool = False

    def __init__(
        self,
        shard_index: int = 0,
        shard_count: int = 1,
        deadline: float | None = None,
    ):
        if shard_count < 1 or not 0 <= shard_index < shard_count:
            raise ValueError(
                f"不正な shard 指定です: shard_index={shard_index}, shard_count={shard_count}"
            )
        if shard_count > 1 and not self.supports_shard:
            raise ShardNotSupportedError(
                f"{self.ward_name or type(self).__name__}（{self.key}）は shard 分割に未対応です"
                f"（shard_count={shard_count} が指定されました）。全 shard で同じ区を重複取得"
                "しないよう、shard_count=1 で実行してください。"
            )
        self.shard_index = shard_index
        self.shard_count = shard_count
        #: 取得の期限（time.monotonic() 基準の秒）。None なら無制限。
        self.deadline = deadline
        #: 期限超過で打ち切ったか（main.py が part ファイルに記録する）
        self.timed_out = False

    # --- shard 分割 ---------------------------------------------------------

    def shard_items(self, items: Sequence[T]) -> list[T]:
        """items のうち、この shard の担当分を返す（i % shard_count == shard_index）。

        全 shard で同じ順序になるよう、呼び出し側で items をソートしておくこと。
        """
        return [x for i, x in enumerate(items) if i % self.shard_count == self.shard_index]

    # --- 時間予算 -----------------------------------------------------------

    def time_left(self) -> float:
        """期限までの残り秒数。期限なしなら inf。"""
        if self.deadline is None:
            return math.inf
        return self.deadline - time.monotonic()

    def out_of_time(self, where: str = "") -> bool:
        """期限を過ぎていれば True（一度 True になったら以後も True）。

        最初に超過を検知したときに「時間切れで打ち切り」とログを出す。
        """
        if self.timed_out:
            return True
        if self.time_left() > 0:
            return False
        self.timed_out = True
        at = f"（{where}）" if where else ""
        print(f"[{self.key or self.ward_name}] 時間切れで打ち切り{at}。ここまでの取得分を返します。")
        return True

    # --- 取得本体 -----------------------------------------------------------

    @abstractmethod
    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        """指定期間の空き状況を取得して `Slot` のリストを返す。

        Args:
            date_from: 取得開始日（含む）
            date_to:   取得終了日（含む）
        """
        raise NotImplementedError
