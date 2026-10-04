"""文京区「『文の京』施設予約ねっと」スクレイパー。

ASP.NET Core ＋ Vue2（BootstrapVue）の SPA で、世田谷のけやきネットとは別の製品。
Playwright で画面を順に操作し、データは画面が受け取る XHR の JSON と Vue の状態から読む
（DOM の表示文字は読まない）。ログイン不要の空き照会の経路だけを使い、ログイン・利用者登録・
予約申込みは一切しない。

取得の流れ（2026-10 実地確認）:
  /user/Home ─[利用目的から探す: 体操・ダンス → ダンス(40) → 検索]→
  施設選択（AvailabilityCheckApplySelectFacility）
    許可リストの施設を config.BUNKYO_FACILITY_GROUPS のグループ（3施設まで）に分け、グループを輪番の組
    （既定2組）に振り分けて、今回の組のグループだけを取得する（輪番は UTC の日付・時刻で決まる）。
    グループごとに Home から検索し直す。
    ─[グループの施設を選択 → 次へ進む]→
  施設別空き状況（AvailabilityCheckApplySelectDays）: 部屋×日付のグリッド（GetAvailability 等の JSON）
    表示期間を2週間にし、「空き」「一部空き」のセルを最大10件選ぶ ─[次へ進む]→
  時間帯別空き状況（AvailabilityCheckApplySelectTime）: コマごとの空き（Vue の model.AvailabilityTime）
    ─[前に戻る]→ グリッド（選択が残るので解除）→ 次の10件 … → [次の期間] で2週間進む

時間帯別で Status が "vacant"（空きあり）のコマだけを Slot にする（施設に問合せ・抽選・申込期間外は含めない）。
部屋は config.BUNKYO_ROOM_RULES で施設ごとに絞り込む（対象外の部屋のセルは時間帯別への選択対象にもしない）。
表示した2週間の対象セルがすべて申込期間外なら、そのグループの以降の期間は確認しない（受付期間は連続しているため）。

運用条件（robots.txt が `Disallow: /*` のため、ユーザーが承認した控えめな条件。config.py 参照）:
  - 相手サーバーへの操作（画面遷移・データ取得）は、前の操作の完了から BUNKYO_REQUEST_DELAY_SEC（3秒以上）空ける
  - 並列1本（supports_shard=False。分割すると同時セッションが増えるため。施設の分け方自体は
    shard_items() で書いてあるので、承認を得て分割する場合は supports_shard を True にする）
  - 1セッションの照会は3施設・2週間表示まで
  - 安全装置: 対象ホストへのリクエスト数が上限（BUNKYO_MAX_REQUESTS。800 が上限）に近づいたら打ち切る。
    失敗（5xx 応答・エラー画面・エラーのダイアログ・応答待ちのタイムアウト等）が取得の進まないまま
    BUNKYO_MAX_CONSECUTIVE_ERRORS 回続いたら実行全体を中止する（再試行の前は30秒以上待つ）。
    どちらも取得済みの分は返し、時間切れと同じ扱いで記録する（stop_early）。
  - アプリ自身の接続維持の通信（/user/api/Header/*）は止めない（止めるとアプリがエラー画面に遷移する）。
  - Playwright の route() は使わない（route を使うとブラウザの HTTP キャッシュが無効になり、静的ファイルを
    画面ごとに取り直して相手の負荷が増えるため）。Google Analytics / Tag Manager への送信だけ CDP で止める。

リクエスト数の数え方: 対象ホスト宛てのリクエスト（ページ・XHR・接続維持・静的ファイルのすべて）のうち、
ブラウザのキャッシュで済んでサーバーに届かなかったもの（CDP の requestServedFromCache）を除いた件数。
「CDP の送信数」と「ブラウザが発行した数 − キャッシュ分」の大きい方を使う。CDP が使えない場合は、
キャッシュで済んだものも含めた件数で数える（多めに数える側）。

取得範囲の記録: 今回取得する施設を set_assigned_facilities()、輪番で今回は取得しない施設を
set_carry_over_facilities()（combine が前回データをそのまま引き継ぐ）に記録する。グループごとに全期間を
取るので、施設ごとの完了日を mark_facility_completed_until() に記録し（打ち切り時は combine が施設単位で
補う）、全グループが最後まで取れたときだけ completed_until を記録する。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

try:
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # playwright を差し替えた検証環境（スタブ）でも import できるように
    PlaywrightError = Exception  # type: ignore[misc,assignment]

if TYPE_CHECKING:
    from playwright.sync_api import Response

import config
from models import Slot
from scrapers.base import WardScraper

HOST = "www.shisetsu.city.bunkyo.lg.jp"
BASE_URL = f"https://{HOST}/user/"
HOME_URL = BASE_URL + "Home"
#: アプリが自動で送る接続維持の通信（GetSiteClosing / ResetSessionInterval / GetSessionInterval）
PING_PATH_PREFIX = "/user/api/Header/"
#: 送信を止める第三者の計測（相手サイトの動作には影響しないことを確認済み）
BLOCKED_URL_PATTERNS = ["*google-analytics.com*", "*googletagmanager.com*"]

PATH_SELECT_FACILITY = "/user/AvailabilityCheckApplySelectFacility"
PATH_SELECT_DAYS = "/user/AvailabilityCheckApplySelectDays"
PATH_SELECT_TIME = "/user/AvailabilityCheckApplySelectTime"
API_GET_AVAILABILITY = PATH_SELECT_DAYS + "/GetAvailability"
API_SEARCH_CONDITION = PATH_SELECT_DAYS + "/SearchCondition"
API_AFTER_PERIOD = PATH_SELECT_DAYS + "/AfterPeriod"

MAX_CELLS = 10                     # 時間帯別へ進めるセル数の上限（サーバー仕様 E-203-000018）
DRILL_STATUSES = ("vacant", "some")  # 時間帯別で確認するセル（空き／一部空き）
KNOWN_GRID_STATUSES = frozenset({"vacant", "some", "full", "time-over", "closed"})
KNOWN_TIME_STATUSES = frozenset({"vacant", "full"})
TERM_LABELS = {"1": "1日", "2": "1週間", "3": "2週間"}
TERM_DAYS = {"1": 1, "2": 7, "3": 14}
FALLBACK_TERM = "2"                # 2回目の再試行は1週間表示（軽い照会）にする

# 応答待ちの上限。調査時に重い照会（14施設×1ヶ月）で約30秒後に 502 が返ったが、原因（相手側か、
# 調査環境のプロキシか）は確定していない。CI では挙動が違う可能性があるので、短く決め打ちせず長めに待つ。
NAV_TIMEOUT_MS = 120_000
ACTION_TIMEOUT_MS = 30_000         # クリック等のブラウザ内の操作
POLL_MS = 250

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_VUE_MODEL = "(() => { const a = document.querySelector('#app'); return a && a.__vue__ && a.__vue__.$data && a.__vue__.$data.model; })()"

#: 施設選択画面の施設一覧 [[コード, 施設名], ...]（未描画なら null）
_FACILITIES_JS = f"""() => {{
  const m = {_VUE_MODEL};
  const fs = m && m.SelectFacilities && m.SelectFacilities.Facilities;
  if (!fs || !fs.length) return null;
  return fs.map(f => [String(f.SelectedFacility.Value), String(f.SelectedFacility.Text)]);
}}"""

#: 時間帯別画面のデータが描画済みか
_TIME_READY_JS = f"""() => {{
  const m = {_VUE_MODEL};
  return !!(m && m.AvailabilityTime && m.AvailabilityTime.FacilityList);
}}"""

#: 時間帯別画面のデータ（model.AvailabilityTime.FacilityList）
_TIME_MODEL_JS = f"""() => {{
  const m = {_VUE_MODEL};
  const t = m && m.AvailabilityTime && m.AvailabilityTime.FacilityList;
  return t ? JSON.parse(JSON.stringify(t)) : null;
}}"""

#: グリッドが応答データどおりに描画されたか（セル数と先頭セルの日付で確認）
_GRID_RENDERED_JS = """([n, first]) => {
  const xs = document.querySelectorAll('input[name^="AvailabilitySelectDays["][name$=".IsChecked"]');
  if (xs.length !== n) return false;
  if (first === null) return true;
  const u = document.querySelector('input[name="AvailabilitySelectDays[0].Rows[0].Cells[0].UseDate"]');
  return !!u && u.value === first;
}"""

#: セルの隠しフィールド [施設コード, 利用日, 部屋コード, 選択状態]
_CELL_INFO_JS = """(bases) => bases.map(b => {
  const v = n => { const e = document.querySelector(`input[name="${b}.${n}"]`); return e ? e.value : null; };
  return [v('FacilityCode'), v('UseDate'), v('ObjectCode[0]'), v('IsChecked')];
})"""


class SiteError(RuntimeError):
    """相手サイトの異常・想定外の画面（5xx、エラー画面、エラーのダイアログ、応答待ちのタイムアウト等）。再試行の対象。"""


class SetupError(RuntimeError):
    """設定と相手サイトが合わない（利用目的のコードが無い、許可リストの施設が1つも無い等）。再試行しない。"""


class _StopRun(Exception):
    """実行全体を打ち切る（時間切れ・リクエスト上限・失敗の連続）。理由は記録済み。"""


@dataclass(frozen=True)
class _Cell:
    """グリッドの1セル（部屋×日付）。i/j/k は応答 JSON とフォームの添字。"""

    i: int
    j: int
    k: int
    facility_code: str
    object_code: str
    room: str
    date: dt.date

    @property
    def base(self) -> str:
        return f"AvailabilitySelectDays[{self.i}].Rows[{self.j}].Cells[{self.k}]"

    @property
    def key(self) -> tuple:
        return (self.facility_code, self.object_code, self.room, self.date)


@dataclass
class _Group:
    """1セッションで照会する施設のグループ。"""

    index: int
    facilities: list[tuple[int, str]]   # [(施設コード, 施設名)]
    resume_from: dt.date                # この日以降が未取得（再試行時はここから再開）
    done_until: dt.date | None = None
    finished: bool = False

    @property
    def names(self) -> str:
        return "・".join(name for _code, name in self.facilities)


class BunkyoScraper(WardScraper):
    key = "bunkyo"
    ward_name = "文京区"
    # 並列1本の運用条件（ユーザー承認）のため、shard 分割には対応させない（shard_count>1 はエラー）
    supports_shard = False

    def __init__(
        self,
        max_facilities: int | None = None,
        max_periods: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        deadline: float | None = None,
    ):
        super().__init__(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
        # 試験用の絞り込み（本番は None）。引数がなければ環境変数（config）を使う。
        self._max_facilities = max_facilities if max_facilities is not None else (config.BUNKYO_MAX_FACILITIES or None)
        self._max_periods = max_periods if max_periods is not None else (config.BUNKYO_MAX_PERIODS or None)
        self._term = config.BUNKYO_DISPLAY_TERM
        self._delay = config.BUNKYO_REQUEST_DELAY_SEC
        self._max_requests = config.BUNKYO_MAX_REQUESTS
        self._reserve = config.BUNKYO_REQUEST_RESERVE
        self._max_errors = config.BUNKYO_MAX_CONSECUTIVE_ERRORS
        self._retry_wait = config.BUNKYO_RETRY_WAIT_SEC

        self._page: Page | None = None
        self._context = None
        self._date_from = dt.date.today()
        self._fresh_select = False       # 直前の検索の施設選択画面にいる（最初のグループは検索し直さない）

        # 通信の計数（対象ホスト宛てのみ）
        self._sent: Counter[str] = Counter()   # 実際に送ったもの（CDP。種類別）
        self._cache_hits = 0                   # ブラウザのキャッシュで済んだもの（CDP）
        self._seen: Counter[str] = Counter()   # ブラウザが発行したもの（キャッシュで済んだものも含む）
        self._cdp_ids: dict[str, str] = {}
        self._cdp_ok = False
        self._cdp_failed = False
        self._http_errors: list[tuple[int, str]] = []
        self._http_errors_checked = 0
        self._ops = 0
        self._last_op_end = 0.0

        # 失敗の数え方: 取得が進まないまま続いた回数（_streak）と、実行全体の回数（_failures）
        self._streak = 0
        self._failures = 0
        self._noted: set[tuple[str, str]] = set()

    # --- エントリポイント -----------------------------------------------

    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        slots: list[Slot] = []
        if self.out_of_time("取得開始前"):
            return slots
        self._date_from = date_from
        until = date_to
        if self._max_periods:
            until = min(date_to, date_from + dt.timedelta(days=TERM_DAYS[self._term] * self._max_periods - 1))
        started = time.monotonic()
        print(
            f"[bunkyo] 取得期間 {date_from}〜{until} / 操作の間隔 {self._delay:.1f} 秒 / "
            f"リクエスト上限 {self._max_requests} 件 / 失敗の連続 {self._max_errors} 回で中止"
        )
        groups: list[_Group] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(**_launch_options())
            try:
                self._context = browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 1600},
                )
                self._context.on("request", self._on_request)
                self._context.on("response", self._on_response)
                self._page = self._new_page()
                groups = self._plan_groups()
                for g in groups:
                    if self.out_of_time(f"グループ{g.index} の開始前"):
                        break
                    self._run_group(g, until, slots)
            except _StopRun:
                pass
            finally:
                try:
                    browser.close()
                except PlaywrightError:
                    pass
                self._print_summary(started, len(_dedupe(slots)))

        if groups and all(g.finished for g in groups):
            self.mark_completed_until(min(g.done_until or until for g in groups))
        elif groups:
            done = [f"G{g.index}" for g in groups if g.finished]
            print(f"[bunkyo] 最後まで取得できたグループ: {', '.join(done) or 'なし'} / 全 {len(groups)} グループ")
        return _dedupe(slots)

    # --- グループ分け・グループごとの取得 --------------------------------

    def _plan_groups(self) -> list[_Group]:
        found = self._attempt("最初の検索", lambda: self._search(self._page))
        self._fresh_select = True
        allow = [_norm(n) for n in config.BUNKYO_TARGET_FACILITIES]
        by_name = {name: code for code, name in found}
        missing = [n for n in allow if n not in by_name]
        others = [name for _code, name in found if name not in allow]
        if missing:
            _warn(f"許可リストの施設が検索結果にありません（名称の変更・利用目的の設定変更の可能性）: {'、'.join(missing)}")
        if others:
            print(f"[bunkyo] 許可リストに無いので取得しない施設 {len(others)} 件: {'、'.join(others)}")
        targets = [n for n in allow if n in by_name]
        if not targets:
            raise SetupError("許可リストの施設が検索結果に1つもありません（config.BUNKYO_TARGET_FACILITIES を確認してください）")

        # 輪番: グループを組に振り分け、今回の組だけを取得する
        rotations = plan_rotations(targets, config.BUNKYO_ROTATIONS)
        index = rotation_index(config.BUNKYO_ROTATIONS)
        for r, rot in enumerate(rotations):
            mark = "（今回）" if r == index else ""
            names = " / ".join("・".join(grp) for grp in rot) or "なし"
            print(f"[bunkyo] 輪番 {r}{mark}: 見込み {_rotation_weight(rot)}（{names}）")
        # 見込みの小さいグループから取る（リクエスト上限で打ち切られても、最後まで取れる施設が多くなるように。
        # 打ち切られるのは見込みの大きいグループの先の日付になる）
        chosen = self.shard_items(sorted(rotations[index], key=lambda grp: _rotation_weight([grp])))
        mine = [name for grp in chosen for name in grp]
        if self._max_facilities:
            mine = mine[: self._max_facilities]
            chosen = [[n for n in grp if n in mine] for grp in chosen]
            chosen = [grp for grp in chosen if grp]
        self.set_assigned_facilities(mine)
        self.set_carry_over_facilities(n for n in targets if n not in mine)
        for name in mine:
            self.mark_facility_completed_until(name, None)
        groups = [
            _Group(index=n + 1, facilities=[(by_name[name], name) for name in grp], resume_from=self._date_from)
            for n, grp in enumerate(chosen)
        ]
        print(
            f"[bunkyo] 今回の輪番 {index}/{config.BUNKYO_ROTATIONS}: {len(mine)} 施設・{len(groups)} グループを取得"
            f"（前回データを引き継ぐ施設 {len(targets) - len(mine)}）"
        )
        for g in groups:
            print(f"[bunkyo]   G{g.index}: {g.names}")
        return groups

    def _run_group(self, g: _Group, until: dt.date, out: list[Slot]) -> None:
        def attempt() -> None:
            # 2回目の再試行は1週間表示（軽い照会）にする
            term = FALLBACK_TERM if self._streak >= 2 else self._term
            self._scrape_group(self._page, g, term, until, out)

        self._attempt(f"グループ{g.index}（{g.names}）", attempt)

    def _scrape_group(self, page: Page, g: _Group, term: str, until: dt.date, out: list[Slot]) -> None:
        t0 = time.monotonic()
        before = len(out)
        if self._fresh_select:
            self._fresh_select = False
        else:
            self._search(page)
        self._select_facilities(page, [code for code, _name in g.facilities])
        grid = self._open_grid(page, g, term)
        self._print_rooms(g, grid)
        prev_last: dt.date | None = None
        while True:
            dates = _grid_dates(grid)
            if not dates:
                raise SiteError(f"G{g.index}: グリッドに日付がありません")
            first, last = dates[0], dates[-1]
            if prev_last is not None and last <= prev_last:
                raise SiteError(f"G{g.index}: 「次の期間」で表示期間が進みませんでした（{first}〜{last}）")
            label = f"G{g.index} {first:%m/%d}〜{last:%m/%d}"
            final = last >= until
            grid = self._drill_period(page, grid, label, until, final, out)
            g.done_until = min(last, until)
            g.resume_from = last + dt.timedelta(days=1)
            for _code, name in g.facilities:
                self.mark_facility_completed_until(name, g.done_until)
            self._progress()
            if not final and _out_of_window(grid, self._date_from, until, self._room_allowed):
                # 受付期間はどの施設も「今日から先の連続した期間」なので、2週間すべてが申込期間外なら以降も同じ
                print(f"[bunkyo] {label}: すべて申込期間外のため、このグループの以降の期間は確認しません")
                g.done_until = until
                for _code, name in g.facilities:
                    self.mark_facility_completed_until(name, until)
                final = True
            if final:
                break
            prev_last = last
            button = page.locator("button.btn-gray:visible", has_text="次の期間").first
            resp = self._click_and_wait(page, button, f"{label} 次の期間", path=PATH_SELECT_DAYS, response=API_AFTER_PERIOD)
            grid = self._grid_from(resp, f"{label} 次の期間")
            self._wait_grid(page, grid, f"{label} 次の期間")
        g.finished = True
        print(
            f"[bunkyo] G{g.index} 完了: {g.names} / 空き {len(out) - before} コマ / "
            f"{(time.monotonic() - t0) / 60:.1f} 分 / 送信累計 {self._requests_sent()} 件"
        )

    # --- 画面操作 -----------------------------------------------------------

    def _search(self, page: Page) -> list[tuple[int, str]]:
        """Home から利用目的で検索し、施設選択画面の施設一覧 [(コード, 施設名)] を返す。"""
        with self._op(page, "Home を開く"):
            resp = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if resp is not None and resp.status >= 400:
                raise SiteError(f"Home を開く: HTTP {resp.status}")
            tab = page.get_by_text("利用目的から探す", exact=True)
            self._wait_until(page, "Home を開く", lambda: _path_is(page, "/user/Home") and tab.count() > 0)
        tab.first.click()
        panel = page.locator("div.tab-pane.active").first

        category = config.BUNKYO_PURPOSE_CATEGORY
        radio = panel.locator(f"input[type=radio][value='{category}']")
        if radio.count() == 0:
            raise SetupError(f"利用目的の分類（{category}）が見つかりません")
        if not radio.first.is_checked():
            panel.locator(f"label[for='{radio.first.get_attribute('id')}']").click()
        purposes = set(config.BUNKYO_PURPOSES)
        boxes = panel.locator("input[name='HomeModel.SelectedPurpose']")
        try:
            panel.locator(f"input[name='HomeModel.SelectedPurpose'][value='{config.BUNKYO_PURPOSES[0]}']").first.wait_for(
                state="attached", timeout=10_000
            )
        except PlaywrightError:
            raise SetupError(f"利用目的（{config.BUNKYO_PURPOSES[0]}）が分類 {category} にありません") from None
        for cb in boxes.all():
            if (cb.get_attribute("value") in purposes) != cb.is_checked():
                panel.locator(f"label[for='{cb.get_attribute('id')}']").click()
        checked = {cb.get_attribute("value") for cb in boxes.all() if cb.is_checked()}
        if checked != purposes:
            raise SetupError(f"利用目的を選べません（指定 {sorted(purposes)} / 選択中 {sorted(checked)}）")

        button = panel.locator("button.btn-secondary:visible", has_text="検索").first
        self._click_and_wait(
            page, button, "利用目的で検索", path=PATH_SELECT_FACILITY,
            ready=lambda: page.evaluate(_FACILITIES_JS) is not None,
        )
        found = []
        for value, text in page.evaluate(_FACILITIES_JS) or []:
            if str(value).isdigit():
                found.append((int(value), _norm(text)))
        return found

    def _select_facilities(self, page: Page, codes: list[int]) -> None:
        """施設選択画面で codes の施設だけを選ぶ（通信なし）。"""
        more = page.locator("button:visible", has_text="さらに読み込む")
        for _ in range(20):  # 表示を増やすだけのボタン（通信しない）。非表示の行は選べないので全部出す
            if more.count() == 0:
                break
            more.first.click()
            page.wait_for_timeout(300)
        wanted = {str(c) for c in codes}
        boxes = page.locator("table.facilities input[type=checkbox][name$='.SelectedFacility.Value']")
        for cb in boxes.all():
            if (cb.get_attribute("value") in wanted) != cb.is_checked():
                page.locator(f"label[for='{cb.get_attribute('id')}']").click()
        checked = {cb.get_attribute("value") for cb in boxes.all() if cb.is_checked()}
        if checked != wanted:
            raise SiteError(f"施設を選べません（指定 {sorted(wanted)} / 選択中 {sorted(checked)}）")

    def _open_grid(self, page: Page, g: _Group, term: str) -> list[dict]:
        """施設選択 → 施設別空き状況。表示期間と開始日（再開時）を合わせたグリッドの JSON を返す。"""
        what = f"G{g.index} 施設別空き状況を開く"
        button = page.locator(".fixed-bottom li.next button").first
        resp = self._click_and_wait(page, button, what, path=PATH_SELECT_DAYS, response=API_GET_AVAILABILITY)
        grid = self._grid_from(resp, what)
        self._wait_grid(page, grid, what)

        # 前回の選択が残っていることがあるので解除しておく（通信なし）
        self._clear_selection(page, what)

        dates = _grid_dates(grid)
        shown_from = dates[0] if dates else None
        current_term = page.locator("#SearchCondition_DisplayTerm").get_attribute("value")
        # 表示の開始日はサーバーのセッションに残る（Home から検索し直しても、前のグループで「次の期間」を
        # 進めた日のまま開く。2026-10 実地で確認）。このグループの取得開始日（再試行時は未取得の最初の日。
        # 相手の「今日」＝日本時間の今日より前にはしない）と違えば、必ず開始日を指定し直す。
        target = max(g.resume_from, _today_jst())
        start = target if shown_from != target else None
        if current_term == term and start is None:
            return grid
        what = f"G{g.index} 表示条件（{TERM_LABELS[term]}" + (f"・{start} から" if start else "") + "）"
        if start is not None:
            box = page.locator("#SearchCondition_StartDate")
            box.fill(start.isoformat())
            box.dispatch_event("change")
            if box.input_value() != start.isoformat():
                raise SiteError(f"{what}: 開始日を入力できません")
        if current_term != term:
            page.locator(".disp-condition label.custom-control-label", has_text=TERM_LABELS[term]).first.click()
            hidden = page.locator("#SearchCondition_DisplayTerm")
            if not _poll(page, lambda: hidden.get_attribute("value") == term, 10_000):
                raise SiteError(f"{what}: 表示期間を選べません")
        button = page.locator("button.btn:visible").filter(has_text=re.compile(r"^\s*表示\s*$")).first
        resp = self._click_and_wait(page, button, what, path=PATH_SELECT_DAYS, response=API_SEARCH_CONDITION)
        grid = self._grid_from(resp, what)
        self._wait_grid(page, grid, what)
        dates = _grid_dates(grid)
        if start is not None and (not dates or dates[0] != start):
            # 指定より後から表示したまま続けると、その間の日を取り漏らすので失敗として扱う
            raise SiteError(f"{what}: 表示の開始日が指定と違います（{dates[0] if dates else 'なし'}）")
        return grid

    def _drill_period(
        self, page: Page, grid: list[dict], label: str, until: dt.date, final: bool, out: list[Slot]
    ) -> list[dict]:
        """表示中の期間の「空き」「一部空き」のセルを10件ずつ時間帯別で確認する。最新のグリッドを返す。"""
        self._print_period(label, grid, until)
        done: set[tuple] = set()
        batch_no = 0
        while True:
            cells = self._candidates(grid, until, done)
            if not cells:
                return grid
            batch = cells[:MAX_CELLS]
            rest = len(cells) - len(batch)
            batch_no += 1
            what = f"{label} バッチ{batch_no}"
            self._clear_selection(page, what)
            self._select_cells(page, batch, what)
            self._click_and_wait(
                page, page.locator(".fixed-bottom li.next button").first, f"{what} 時間帯別へ",
                path=PATH_SELECT_TIME, ready=lambda: bool(page.evaluate(_TIME_READY_JS)),
            )
            new = self._parse_time(page.evaluate(_TIME_MODEL_JS), batch, until, what)
            out.extend(new)
            done.update(c.key for c in batch)
            self._progress()
            print(f"[bunkyo] {what}: {len(batch)} セル（残り {rest}）→ 空き {len(new)} コマ / 送信累計 {self._requests_sent()} 件")
            if rest == 0 and final:
                # グループ最後のバッチ: グリッドへは戻らない（次のグループは Home から検索し直す）
                return grid
            resp = self._click_and_wait(
                page, page.locator(".fixed-bottom li.prev button").first, f"{what} グリッドへ戻る",
                path=PATH_SELECT_DAYS, response=API_GET_AVAILABILITY,
            )
            grid = self._grid_from(resp, f"{what} グリッドへ戻る")
            self._wait_grid(page, grid, f"{what} グリッドへ戻る")
            # 戻っても選択が残るので、次のバッチ・「次の期間」の前に解除しておく
            self._clear_selection(page, f"{what} グリッドへ戻る")

    def _candidates(self, grid: list[dict], until: dt.date, done: set[tuple]) -> list[_Cell]:
        cells: list[_Cell] = []
        for i, fac in enumerate(grid):
            facility = _norm(fac.get("FacilityName") or "")
            for j, row in enumerate(fac.get("Rows") or []):
                room = _norm(row.get("ObjectName") or "")
                if not self._room_allowed(facility, room):
                    continue  # 対象外の部屋は時間帯別への選択対象にもしない
                for k, cell in enumerate(row.get("Cells") or []):
                    status = str(cell.get("Status") or "")
                    if status not in KNOWN_GRID_STATUSES:
                        self._note("施設別", status, f"{fac.get('FacilityName')} {room} {cell.get('UseDate')}")
                    if status not in DRILL_STATUSES or cell.get("Disabled") is True:
                        continue
                    d = _date(cell.get("UseDate"))
                    if d is None or not (self._date_from <= d <= until):
                        continue
                    codes = cell.get("ObjectCode") or []
                    c = _Cell(
                        i=i, j=j, k=k,
                        facility_code=str(cell.get("FacilityCode") or fac.get("FacilityCode") or ""),
                        object_code=str(codes[0]) if codes else "",
                        room=room,
                        date=d,
                    )
                    if c.key not in done:
                        cells.append(c)
        return cells

    def _select_cells(self, page: Page, batch: list[_Cell], what: str) -> None:
        """セルを選ぶ（通信なし）。画面の隠しフィールドが応答データと一致することを確かめてからクリックする。"""
        bases = [c.base for c in batch]
        for c, (fc, use_date, oc, _checked) in zip(batch, page.evaluate(_CELL_INFO_JS, bases)):
            if (
                use_date is None
                or use_date[:10] != c.date.isoformat()
                or str(fc) != c.facility_code
                or (c.object_code and str(oc) != c.object_code)
            ):
                raise SiteError(f"{what}: 画面のセルが応答データと一致しません（{c.base}: {fc} {use_date} {oc}）")
        for c in batch:
            page.locator(f'input[name="{c.base}.IsChecked"]').locator("xpath=..").click()
        active = page.locator("label.btn-toggle.active")

        def selected() -> bool:
            states = page.evaluate(_CELL_INFO_JS, bases)
            return all(s[3] == "true" for s in states) and active.count() == len(batch)

        # 選択状態は Vue が非同期に反映するので、少し待って確かめる
        if not _poll(page, selected, 10_000):
            raise SiteError(f"{what}: セルを選べません（選択中 {active.count()} 件 / 指定 {len(batch)} 件）")

    def _clear_selection(self, page: Page, what: str) -> None:
        """「前に戻る」で残ったセルの選択を解除する（通信なし）。解除後に0件であることを確かめる。"""
        active = page.locator("label.btn-toggle.active")
        for _ in range(3):
            handles = active.element_handles()
            if not handles:
                return
            for h in handles:
                h.click()
            if _poll(page, lambda: active.count() == 0, 5_000):
                return
        raise SiteError(f"{what}: 選択を解除できません（{active.count()} 件）")

    def _parse_time(self, model, batch: list[_Cell], until: dt.date, what: str) -> list[Slot]:
        """時間帯別のデータから空き（Status == "vacant"）のコマを Slot にする。"""
        if not isinstance(model, list):
            raise SiteError(f"{what}: 時間帯別のデータを読めません")
        slots: list[Slot] = []
        shown = 0
        for fac in model:
            facility = _norm(fac.get("FacilityName") or "")
            for table in fac.get("Tables") or []:
                for place in table.get("Places") or []:
                    shown += 1
                    room = _norm(place.get("ObjectName") or "")
                    d = _date(place.get("UseDate") or table.get("UseDate"))
                    if not facility or not room or d is None or not (self._date_from <= d <= until):
                        continue
                    if not self._room_allowed(facility, room):
                        continue
                    for cell in place.get("Cells") or []:
                        status = str(cell.get("Status") or "")
                        if status not in KNOWN_TIME_STATUSES:
                            self._note(
                                "時間帯別", status,
                                f"{facility} {room} {d} {str(cell.get('FrameName') or '').strip()} "
                                f"{cell.get('TimeFrom')}-{cell.get('TimeTo')}",
                            )
                        if status != "vacant":
                            continue
                        start, end = _hhmm(cell.get("TimeFrom")), _hhmm(cell.get("TimeTo"))
                        if start and end:
                            slots.append(Slot(self.ward_name, facility, room, d.isoformat(), start, end))
        if shown != len(batch):
            print(f"[bunkyo] {what}: 時間帯別の表示件数が選んだセル数と違います（表示 {shown} / 選択 {len(batch)}）")
        return slots

    def _room_allowed(self, facility: str, room: str) -> bool:
        """部屋が取得対象か（config.BUNKYO_ROOM_RULES と合体室の設定）。"""
        if config.BUNKYO_EXCLUDE_COMBINED_ROOMS and _is_combined(room):
            return False
        rule = config.BUNKYO_ROOM_RULES.get(facility) or {}
        if "include" in rule and room not in {_norm(r) for r in rule["include"]}:
            return False
        if room in {_norm(r) for r in rule.get("exclude", [])}:
            return False
        return True

    def _print_rooms(self, g: _Group, grid: list[dict]) -> None:
        """施設ごとの部屋（対象／対象外）をログに出し、部屋の絞り込みの名前が画面に無ければ警告する。"""
        for fac in grid:
            facility = _norm(fac.get("FacilityName") or "")
            rooms = [_norm(r.get("ObjectName") or "") for r in fac.get("Rows") or []]
            used = [r for r in rooms if self._room_allowed(facility, r)]
            skipped = [r for r in rooms if r not in used]
            extra = f" / 対象外: {'・'.join(skipped)}" if skipped else ""
            print(f"[bunkyo] G{g.index} 部屋: {facility}（{'・'.join(used) or 'なし'}{extra}）")
            rule = config.BUNKYO_ROOM_RULES.get(facility) or {}
            unknown = [r for r in rule.get("include", []) + rule.get("exclude", []) if _norm(r) not in rooms]
            if unknown:
                _warn(f"{facility}: 部屋の絞り込み（config.BUNKYO_ROOM_RULES）にある部屋が画面にありません: {'、'.join(unknown)}")
            if not used:
                _warn(f"{facility}: 対象の部屋が1つもありません（部屋名の変更の可能性）")

    # --- 操作の共通処理（待機・上限・異常の検出） --------------------------

    @contextmanager
    def _op(self, page: Page, what: str):
        """相手サーバーへの操作1回（画面遷移・データ取得）。

        前の操作の完了から待機秒数を空け、時間・リクエスト数の上限と 5xx を確認してから実行する。
        """
        self._check_stop(what)
        self._raise_if_http_error(what)
        wait = self._delay - (time.monotonic() - self._last_op_end)
        if wait > 0:
            page.wait_for_timeout(int(wait * 1000) + 1)
        self._check_stop(what)
        self._raise_if_http_error(what)
        self._ops += 1
        try:
            yield
        finally:
            self._last_op_end = time.monotonic()
        self._raise_if_http_error(what)

    def _click_and_wait(
        self,
        page: Page,
        locator,
        what: str,
        *,
        path: str,
        response: str | None = None,
        ready: Callable[[], bool] | None = None,
    ) -> Response | None:
        """locator をクリックし、URL が path になり（response の応答を受け取り）ready が真になるまで待つ。"""
        got: list[Response] = []

        def on_response(r: Response) -> None:
            if response and urlparse(r.url).path == response:
                got.append(r)

        page.on("response", on_response)
        try:
            with self._op(page, what):
                locator.click()
                self._wait_until(
                    page, what,
                    lambda: _path_is(page, path) and (response is None or bool(got)) and (ready is None or ready()),
                )
        finally:
            page.remove_listener("response", on_response)
        resp = got[-1] if got else None
        if resp is not None and resp.status >= 400:
            raise SiteError(f"{what}: HTTP {resp.status}")
        return resp

    def _wait_until(self, page: Page, what: str, done: Callable[[], bool], timeout_ms: int = NAV_TIMEOUT_MS) -> None:
        """done() が真になるまで待つ。エラー画面・エラーのダイアログ・5xx・タイムアウトは SiteError。"""
        limit = time.monotonic() + timeout_ms / 1000
        while True:
            if self.timed_out and self.stop_reason:
                raise _StopRun()  # 打ち切りの要求（stop_early）は待たずに反映する
            self._raise_if_http_error(what)
            if urlparse(page.url).path.rstrip("/").endswith("/Error"):
                raise SiteError(f"{what}: エラー画面に遷移しました（{page.url}）")
            try:
                if done():
                    return
            except PlaywrightError:
                pass  # 画面の切り替わり中（実行コンテキストが破棄された等）
            message = self._modal_text(page)
            if message:
                self._close_modal(page)
                raise SiteError(f"{what}: ダイアログ「{message}」")
            if time.monotonic() > limit:
                raise SiteError(f"{what}: {timeout_ms // 1000} 秒待っても完了しませんでした（{page.url}）")
            page.wait_for_timeout(POLL_MS)

    def _grid_from(self, resp: Response | None, what: str) -> list[dict]:
        """グリッドの応答（GetAvailability / SearchCondition / AfterPeriod）から AvailabilitySelectDays を取り出す。"""
        if resp is None:
            raise SiteError(f"{what}: 空き状況の応答がありません")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001 - 本文が読めない・JSON でない
            raise SiteError(f"{what}: 応答を読めません（{_one_line(e)}）") from None
        items = data if isinstance(data, list) else [data]
        for x in items:
            if isinstance(x, dict) and "Result" in x and x.get("Result") != "Ok":
                raise SiteError(f"{what}: 応答がエラーでした（{x}）")
        for x in items:
            if isinstance(x, dict) and isinstance(x.get("AvailabilitySelectDays"), list):
                return x["AvailabilitySelectDays"]
        raise SiteError(f"{what}: 応答に空き状況のデータがありません")

    def _wait_grid(self, page: Page, grid: list[dict], what: str) -> None:
        """グリッドが応答データどおりに描画されるまで待つ（セルをクリックする前提）。"""
        n = sum(len(row.get("Cells") or []) for fac in grid for row in fac.get("Rows") or [])
        first = None
        if grid and grid[0].get("Rows") and grid[0]["Rows"][0].get("Cells"):
            first = grid[0]["Rows"][0]["Cells"][0].get("UseDate")
        self._wait_until(page, f"{what}（描画）", lambda: bool(page.evaluate(_GRID_RENDERED_JS, [n, first])))

    @staticmethod
    def _modal_text(page: Page) -> str:
        try:
            modal = page.locator(".modal.show")
            if modal.count() == 0:
                return ""
            return " ".join(modal.first.inner_text(timeout=2000).split())[:200]
        except PlaywrightError:
            return ""

    @staticmethod
    def _close_modal(page: Page) -> None:
        try:
            button = page.locator(".modal.show button", has_text="閉じる")
            if button.count():
                button.first.click(timeout=5000)
        except PlaywrightError:
            pass

    # --- 再試行・打ち切り ---------------------------------------------------

    def _attempt(self, what: str, fn: Callable):
        """fn() を実行する。失敗したら待って Home からやり直す。失敗が続いたら実行全体を打ち切る。"""
        while True:
            try:
                result = fn()
                self._progress()
                return result
            except (_StopRun, SetupError):
                raise
            except Exception as e:  # noqa: BLE001 - サイト側の異常・想定外の画面はまとめて再試行の対象
                self._streak += 1
                self._failures += 1
                url = ""
                try:
                    url = self._page.url if self._page else ""
                except PlaywrightError:
                    pass
                _warn(f"{what}で失敗（連続 {self._streak} 回目・この実行で {self._failures} 回目）: {_one_line(e)} [{url}]")
                if self._streak >= self._max_errors or self._failures >= self._max_errors * 2:
                    self.stop_early(f"失敗が続いた（連続 {self._streak} 回・計 {self._failures} 回。最後: {what}）")
                    raise _StopRun() from None
                if self.time_left() < self._retry_wait + 60:
                    self.stop_early(f"時間予算の残りが少なく再試行できない（最後: {what}）")
                    raise _StopRun() from None
                print(f"[bunkyo] {self._retry_wait:.0f} 秒待ってから Home からやり直します")
                self._sleep(self._retry_wait)
                if self._page is None or self._page.is_closed():
                    self._page = self._new_page()
                self._fresh_select = False

    def _progress(self) -> None:
        """取得が進んだ（失敗の連続を数え直す）。"""
        self._streak = 0

    def _check_stop(self, what: str) -> None:
        if self.out_of_time(what):
            raise _StopRun()
        sent = self._requests_sent()
        if sent >= self._max_requests - self._reserve:
            self.stop_early(
                f"対象ホストへのリクエスト数が上限に近づいた（{sent} 件 / 上限 {self._max_requests} 件。{what}の前）"
            )
            raise _StopRun()

    def _raise_if_http_error(self, what: str) -> None:
        if len(self._http_errors) > self._http_errors_checked:
            new = self._http_errors[self._http_errors_checked :]
            self._http_errors_checked = len(self._http_errors)
            shown = ", ".join(f"{status} {path}" for status, path in new[:3])
            raise SiteError(f"{what}: サーバーエラーの応答（{shown}）")

    def _sleep(self, seconds: float) -> None:
        try:
            if self._page is not None and not self._page.is_closed():
                self._page.wait_for_timeout(int(seconds * 1000))
                return
        except PlaywrightError:
            pass
        time.sleep(seconds)

    # --- ブラウザ・通信の計数 ----------------------------------------------

    def _new_page(self) -> Page:
        page = self._context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        # 途中の画面から Home へ移るときの「このページを離れますか」（beforeunload）は承諾する
        page.on("dialog", lambda d: d.accept())
        if not self._cdp_failed:
            try:
                cdp = self._context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.on("Network.requestWillBeSent", self._on_cdp_request)
                cdp.on("Network.requestServedFromCache", self._on_cdp_from_cache)
                cdp.send("Network.setBlockedURLs", {"urls": BLOCKED_URL_PATTERNS})
                self._cdp_ok = True
            except PlaywrightError as e:
                self._cdp_ok = False
                self._cdp_failed = True
                _warn(f"CDP が使えないため、ブラウザのキャッシュで済んだ分も含めてリクエストを数えます（{_one_line(e)}）")
        return page

    def _requests_sent(self) -> int:
        """対象ホストへ送ったリクエスト数（安全装置の判定に使う）。

        「CDP で見た送信数（キャッシュで済んだ分を除く）」と「ブラウザが発行した数 − キャッシュで済んだ数」の
        大きい方。CDP が使えなければ、ブラウザが発行した数（キャッシュ分も含む・多めに数える側）。
        """
        seen = sum(self._seen.values())
        if self._cdp_ok and not self._cdp_failed:
            return max(sum(self._sent.values()), seen - self._cache_hits)
        return seen

    def _on_request(self, request) -> None:
        if _is_target(request.url):
            self._seen[_kind(request.url, request.resource_type)] += 1

    def _on_response(self, response: Response) -> None:
        if _is_target(response.url) and response.status >= 500:
            self._http_errors.append((response.status, urlparse(response.url).path))

    def _on_cdp_request(self, event: dict) -> None:
        url = (event.get("request") or {}).get("url", "")
        if not _is_target(url):
            return
        kind = _kind(url, event.get("type"))
        self._cdp_ids[event.get("requestId", "")] = kind
        self._sent[kind] += 1

    def _on_cdp_from_cache(self, event: dict) -> None:
        kind = self._cdp_ids.pop(event.get("requestId", ""), None)
        if kind:
            self._sent[kind] -= 1
            self._cache_hits += 1

    # --- ログ -------------------------------------------------------------

    def _note(self, where: str, status: str, example: str) -> None:
        """未知の Status を1回だけログに出す（凡例の「抽選」「施設に問合せ」等の値を確かめるため）。"""
        if (where, status) in self._noted:
            return
        self._noted.add((where, status))
        print(f"[bunkyo] {where}で未知の状態 {status!r}（例: {example}）。空きとしては扱いません")

    def _print_period(self, label: str, grid: list[dict], until: dt.date) -> None:
        counts: Counter[str] = Counter()
        for fac in grid:
            for row in fac.get("Rows") or []:
                for cell in row.get("Cells") or []:
                    d = _date(cell.get("UseDate"))
                    if d is not None and self._date_from <= d <= until:
                        counts[str(cell.get("Status") or "")] += 1
        detail = "・".join(f"{k} {v}" for k, v in counts.most_common())
        print(f"[bunkyo] {label}: セル {sum(counts.values())}（{detail}）")

    def _print_summary(self, started: float, n_slots: int) -> None:
        basis = "CDP" if self._cdp_ok and not self._cdp_failed else "キャッシュ分も含む"
        sent = self._sent if basis == "CDP" else self._seen
        print(
            f"[bunkyo] 通信のまとめ: 対象ホストへ送信 {self._requests_sent()} 件（{basis}・CDP の送信数 "
            f"{sum(self._sent.values())} と ブラウザ発行数−キャッシュ分 {sum(self._seen.values()) - self._cache_hits} の大きい方。"
            f"ページ {sent['document']} / データ {sent['xhr']} / 接続維持 {sent['ping']} / 静的ファイル {sent['static']}）"
            f"、キャッシュで済んだもの {self._cache_hits} 件、ブラウザが発行した総数 {sum(self._seen.values())} 件、"
            f"操作 {self._ops} 回、5xx 応答 {len(self._http_errors)} 件、失敗 {self._failures} 回、"
            f"空き {n_slots} コマ、所要 {(time.monotonic() - started) / 60:.1f} 分"
        )


# --- モジュール関数 ------------------------------------------------------


def _launch_options() -> dict:
    options: dict = {"headless": config.HEADLESS}
    exe = config.LOCAL_CHROMIUM_EXECUTABLE
    if exe:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("[bunkyo] LOCAL_CHROMIUM_EXECUTABLE はローカル実行専用のため、GitHub Actions 上では使いません")
        else:
            options["executable_path"] = exe
    return options


def _warn(msg: str) -> None:
    """警告ログ。GitHub Actions 上ではアノテーション（実行サマリーに表示）にする。"""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=bunkyo::{msg}")
    else:
        print(f"[bunkyo] 警告: {msg}")


def _is_target(url: str) -> bool:
    try:
        return urlparse(url).hostname == HOST
    except ValueError:
        return False


def _kind(url: str, resource_type: str | None) -> str:
    """リクエストの種類（ログ用）: document / xhr / ping / static。"""
    if urlparse(url).path.startswith(PING_PATH_PREFIX):
        return "ping"
    t = (resource_type or "").lower()
    if t == "document":
        return "document"
    if t in ("xhr", "fetch"):
        return "xhr"
    return "static"


def _out_of_window(grid: list[dict], date_from: dt.date, until: dt.date, allowed) -> bool:
    """表示中の期間の対象セル（今日より後）がすべて「申込期間外」（と休館）か。対象セルが無ければ False。"""
    statuses = []
    for fac in grid:
        facility = _norm(fac.get("FacilityName") or "")
        for row in fac.get("Rows") or []:
            if not allowed(facility, _norm(row.get("ObjectName") or "")):
                continue
            for cell in row.get("Cells") or []:
                d = _date(cell.get("UseDate"))
                if d is not None and date_from < d <= until:
                    statuses.append(str(cell.get("Status") or ""))
    return bool(statuses) and "time-over" in statuses and all(x in ("time-over", "closed") for x in statuses)


def plan_rotations(targets: list[str], count: int) -> list[list[list[str]]]:
    """取得対象の施設を1セッションのグループ（config.BUNKYO_FACILITY_GROUPS）に分け、count 組の輪番に振り分ける。

    グループに入っていない施設は、targets の順に3施設ずつの新しいグループにする。振り分けは見込み
    （config.BUNKYO_FACILITY_WEIGHTS）の大きいグループから順に、その時点で見込みの合計が最も小さい組に入れる
    （同じならグループの定義順・組の番号順。入力が同じなら毎回同じ結果になる）。
    """
    size = config.BUNKYO_FACILITIES_PER_SESSION
    wanted = set(targets)
    groups: list[list[str]] = []
    placed: set[str] = set()
    for grp in config.BUNKYO_FACILITY_GROUPS:
        members = [n for n in (_norm(x) for x in grp) if n in wanted and n not in placed]
        placed.update(members)
        groups += [members[i : i + size] for i in range(0, len(members), size)]
    rest = [n for n in targets if n not in placed]
    groups += [rest[i : i + size] for i in range(0, len(rest), size)]
    order = sorted(range(len(groups)), key=lambda i: (-_rotation_weight([groups[i]]), i))
    rotations: list[list[int]] = [[] for _ in range(max(1, count))]
    for i in order:
        r = min(range(len(rotations)), key=lambda r: (_rotation_weight([groups[j] for j in rotations[r]]), r))
        rotations[r].append(i)
    # 組の中はグループの定義順に並べる（ログ・再現性のため）
    return [[groups[i] for i in sorted(rot)] for rot in rotations]


def _rotation_weight(groups: list[list[str]]) -> int:
    weights = config.BUNKYO_FACILITY_WEIGHTS
    return sum(weights.get(n, config.BUNKYO_DEFAULT_FACILITY_WEIGHT) for grp in groups for n in grp)


def rotation_index(count: int, now: dt.datetime | None = None) -> int:
    """今回取得する輪番の組の番号。BUNKYO_ROTATION_INDEX が指定されていればそれ（count で割った余り）。

    指定が無ければ UTC の (通算日 × 2 + (12時以降なら 1)) mod count。定期実行（UTC 01:00 / 13:00）で
    count=2 なら、朝の実行が 0 番・夜の実行が 1 番になる（どの施設も1日1回更新）。
    """
    count = max(1, count)
    value = config.BUNKYO_ROTATION_INDEX
    if value:
        try:
            return int(value) % count
        except ValueError:
            _warn(f"BUNKYO_ROTATION_INDEX が不正です（{value!r}）。日付と時刻から決めます")
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return (now.date().toordinal() * 2 + (1 if now.hour >= 12 else 0)) % count


def _today_jst() -> dt.date:
    """相手システムの「今日」（日本時間）。実行環境のタイムゾーンが UTC でもずれないように。"""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


def _poll(page: Page, fn: Callable[[], bool], timeout_ms: int) -> bool:
    """ブラウザ内の状態の反映を待つ（通信の異常は見ない）。期限内に fn() が真になれば True。"""
    limit = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            if fn():
                return True
        except PlaywrightError:
            pass
        if time.monotonic() > limit:
            return False
        page.wait_for_timeout(100)


def _path_is(page: Page, path: str) -> bool:
    return urlparse(page.url).path.rstrip("/") == path


def _norm(text: str) -> str:
    """施設名・部屋名の空白を整える（全角の文字はそのまま）。"""
    return re.sub(r"\s+", " ", str(text)).strip()


def _is_combined(room: str) -> bool:
    return "＋" in room or "+" in room


def _date(value) -> dt.date | None:
    """"2026-10-13T00:00:00" → date(2026, 10, 13)"""
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _grid_dates(grid: list[dict]) -> list[dt.date]:
    dates = {d for fac in grid for item in fac.get("Dates") or [] if (d := _date((item or {}).get("Item1")))}
    return sorted(dates)


def _hhmm(value) -> str | None:
    """900 → "09:00"、1230 → "12:30"、2130 → "21:30"。不正なら None。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= v <= 2400 or v % 100 >= 60:
        return None
    return f"{v // 100:02d}:{v % 100:02d}"


def _one_line(e: BaseException) -> str:
    text = str(e).strip().splitlines()
    return f"{type(e).__name__}: {text[0] if text else ''}"[:300]


def _dedupe(slots: list[Slot]) -> list[Slot]:
    return list(dict.fromkeys(slots))
