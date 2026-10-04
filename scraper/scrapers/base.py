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
  時間以外の理由（リクエスト数の上限・相手サイトのエラーの連続など）で打ち切るときは
  `self.stop_early("理由")` を呼ぶ。part には時間切れと同じく `timed_out=True` として記録され
  （理由は `stop_reason`）、以後 `out_of_time()` も True を返す。

- **取得範囲の記録**（`set_assigned_facilities()` / `mark_completed_until()`）
  `main.py` は part ファイルに「この shard の担当施設名一覧」と「全担当施設について取得を
  完了した最終日（completed_until）」を書き、`--combine` はそれを使って、欠けた shard の
  施設や時間切れで取れなかった後半の日付を前回データから補う。
  - 担当施設が決まった時点（shard で分割した直後）で `self.set_assigned_facilities(施設名一覧)`
    を呼ぶ。施設名は出力する `Slot.facility` と同じ表記（正規化後）にすること。
  - 期間ウィンドウ等の区切りで「全担当施設についてその日まで取得し終えた」ら
    `self.mark_completed_until(その日)` を呼ぶ（日付は単調増加。後退はしない）。
  - 呼ばなかった場合: 担当施設は「不明」（None）として記録され、combine はその part について
    従来どおりの扱い（補完なし）になる。completed_until は、時間切れでなければ取得期間の
    最終日、時間切れなら「完了日なし」（None）として記録される。

サブクラスで `__init__` を定義する場合は、必ず `shard_index` / `shard_count` / `deadline`
を受け取り `super().__init__(...)` に渡すこと。
"""
from __future__ import annotations

import datetime as dt
import math
import time
from abc import ABC, abstractmethod
from typing import Iterable, Sequence, TypeVar

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
        #: 期限超過（または stop_early()）で打ち切ったか（main.py が part ファイルに記録する）
        self.timed_out = False
        #: 打ち切りの理由（ログ・part 用）。None は「時間切れ」または打ち切りなし。
        self.stop_reason: str | None = None
        #: この shard の担当施設名（正規化後・Slot.facility と同じ表記）。None は「不明（未記録）」。
        self.assigned_facilities: list[str] | None = None
        #: 全担当施設について取得を完了した最終日。None は「1日も完了していない（未記録）」。
        self.completed_until: dt.date | None = None

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

    def stop_early(self, reason: str) -> None:
        """時間切れ以外の理由で取得を打ち切る（最初の1回だけ記録・ログ出力）。

        時間切れと同じ扱い（`timed_out=True`）で part に記録されるので、combine は
        `completed_until` より後の日付を前回データから補う。呼んだ後は `out_of_time()` が True を返す。
        """
        if self.timed_out:
            return
        self.timed_out = True
        self.stop_reason = reason
        print(f"[{self.key or self.ward_name}] {reason}のため打ち切り。ここまでの取得分を返します。")

    # --- 取得範囲の記録（combine での補完に使う） ------------------------------

    def set_assigned_facilities(self, names: Iterable[str]) -> None:
        """この shard の担当施設名一覧を記録する（重複・空文字は除く）。

        施設名は出力する `Slot.facility` と同じ表記（正規化後）にすること。
        """
        self.assigned_facilities = sorted({n for n in names if n})

    def mark_completed_until(self, date: dt.date) -> None:
        """全担当施設について date まで取得を完了したことを記録する（後退はしない）。"""
        if self.completed_until is None or date > self.completed_until:
            self.completed_until = date

    # --- 取得本体 -----------------------------------------------------------

    @abstractmethod
    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        """指定期間の空き状況を取得して `Slot` のリストを返す。

        Args:
            date_from: 取得開始日（含む）
            date_to:   取得終了日（含む）
        """
        raise NotImplementedError
