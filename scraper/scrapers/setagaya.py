"""世田谷区「けやきネット」スクレイパー。

けやきネットは ASP.NET MVC 製で画面遷移が jQuery の __doPostBack（JS）で
行われるため、Playwright（ヘッドレスブラウザ）で実際にクリック操作して
空き状況を取得する。ログイン不要の「空き照会」経路のみを使う。

取得方針（実地調査済み）:
  /Web/ ─[使用目的から探す]→ 用途(その他ダンス 131/136)をチェック → searchMokuteki()
        → 施設一覧(WgR_ShisetsuKensaku)  ※「さらに読み込む」で全件ロード
          → 区民センター/地区会館/区民集会所のみ抽出（学校・運動場は除外）
          → 12施設ずつ選択・[次へ]→
        → 施設別空き状況(WgR_ShisetsubetsuAkiJoukyou)  部屋×日付の○△×グリッド
          ─○/△の日付セルを最大10件選択・[次へ進む]→
        → 時間帯別空き状況(WgR_JikantaibetsuAkiJoukyou) 実時間帯(9:00〜12:00等)の○/×
          ─[前に戻る]→ 選択解除 → 次の10件／次の2週間(period)へ

高速化: 遅い networkidle を使わず、URL変化(wait_for_url)と domcontentloaded で待つ。
記号: ○=空き / △=一部空き / ×=空きなし / －=申込期間外 / ＊=公開対象外

並列化: 対象施設を shard で分割する（supports_shard=True）。
時間予算: 期間ウィンドウ・日付バッチの区切りで self.out_of_time() を確認し、
          期限を過ぎたらそこまでの取得分を返して終了する。
"""
from __future__ import annotations

import datetime as dt
import math
import re

from playwright.sync_api import Page, sync_playwright

import config
from models import Slot
from scrapers.base import WardScraper

BASE_URL = "https://setagaya.keyakinet.net/Web/"

DAYS_PER_PAGE = 14            # 1画面の日数（けやきネット仕様）
MAX_SELECT_PER_BATCH = 10     # 日付セルの一度の選択上限（けやきネット仕様）
NAV_TIMEOUT = 90_000          # 施設選択後のグリッド表示は重いので長めに

TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[～~〜]\s*(\d{1,2}:\d{2})")
CAPACITY_RE = re.compile(r"定員\s*(\d+)")


class SetagayaScraper(WardScraper):
    key = "setagaya"
    ward_name = "世田谷区"
    # 並列実行用：対象施設を shard_count 個に分割し、この shard だけ担当する
    supports_shard = True

    def __init__(
        self,
        purposes: list[str] | None = None,
        max_facilities: int | None = None,
        max_windows: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        deadline: float | None = None,
    ):
        super().__init__(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
        self.purposes = purposes or config.SETAGAYA_PURPOSES
        # テスト用に施設・期間を絞り込む手段（本番は None）
        self._max_facilities = max_facilities
        self._max_windows = max_windows
        # 正規化で施設名が変わったものをログに出す（同じ名前は1回だけ）
        self._renamed: set[str] = set()

    # --- エントリポイント -----------------------------------------------

    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        windows = self._build_windows(date_from, date_to)
        if self._max_windows is not None:
            windows = windows[: self._max_windows]

        slots: list[Slot] = []
        if self.out_of_time("取得開始前"):
            return slots
        done_windows = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.HEADLESS)
            context = browser.new_context(
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                # 用途検索は1回だけ。対象施設を一括選択してグリッドへ。
                self._purpose_search(page)
                self._load_all(page, "input[name='checkShisetsu']")
                n = self._select_target_facilities(page)
                print(f"[setagaya] 対象施設 {n} 件 / 期間 {len(windows)} ウィンドウ")

                page.evaluate("__doPostBack('next','')")
                page.wait_for_url("**/WgR_ShisetsubetsuAkiJoukyou", timeout=NAV_TIMEOUT)
                self._handle_modals(page)
                self._wait_for_grid(page)

                for w in range(len(windows)):
                    label = f"ウィンドウ {w + 1}/{len(windows)}"
                    if self.out_of_time(f"{label} の開始前"):
                        break
                    if w > 0:
                        self._period_next(page)
                    self._wait_for_grid(page)
                    self._load_all(page, "td.shisetsu")
                    slots.extend(self._scrape_visible_window(page, date_from, date_to, label))
                    if self.timed_out:
                        break
                    done_windows += 1
            finally:
                context.close()
                browser.close()
        result = _dedupe(slots)
        if self.timed_out:
            covered = "完了したウィンドウなし"
            if done_windows:
                last = windows[0] + dt.timedelta(days=DAYS_PER_PAGE * done_windows - 1)
                covered = f"{windows[0]}〜{min(last, date_to)} は完了"
            print(
                f"[setagaya] 時間切れで打ち切り: {done_windows}/{len(windows)} ウィンドウ"
                f"（{covered}）、{len(result)} 件を返します。"
            )
        return result

    # --- 検索・施設選択 -------------------------------------------------

    def _purpose_search(self, page: Page) -> None:
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.get_by_text("使用目的から探す", exact=True).click()
        page.wait_for_timeout(400)
        for value in self.purposes:
            self._ensure_checked(page, f"checkPurposeMiddle{value}")
        self._pause(page)
        page.evaluate("searchMokuteki()")
        page.wait_for_url("**/WgR_ShisetsuKensaku", timeout=NAV_TIMEOUT)

    def _select_target_facilities(self, page: Page) -> int:
        """区民センター/地区会館/区民集会所のうち、この shard が担当する施設を選択。"""
        # 対象施設コードを収集（順序を安定させるためソート）
        targets: list[str] = []
        for cb in page.query_selector_all("input[name='checkShisetsu']"):
            code = cb.get_attribute("value")
            label = page.query_selector(f"label[for='checkShisetsu{code}']")
            name = label.inner_text().strip() if label else ""
            if _is_target(name):
                targets.append(code)
        targets.sort()

        # shard で分割（並列ジョブ間で重複なく分担）
        targets = self.shard_items(targets)
        if self._max_facilities is not None:
            targets = targets[: self._max_facilities]

        for code in targets:
            self._ensure_checked(page, f"checkShisetsu{code}")
        self._pause(page)
        return len(targets)

    def _ensure_checked(self, page: Page, input_id: str) -> None:
        """トグルラベルで隠れた checkbox を確実に「選択済み」にする。

        けやきネットはセッションで前回選択を記憶するため、単純クリックだと
        トグルが外れることがある。現在の状態を見て必要な時だけクリックする。
        """
        cb = page.query_selector(f"#{input_id}")
        if not cb:
            return
        if not cb.is_checked():
            label = page.query_selector(f"label[for='{input_id}']")
            if label:
                label.click()

    def _period_next(self, page: Page) -> None:
        page.click("a[href*=\"__doPostBack('period','next')\"]")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(400)

    def _wait_for_grid(self, page: Page) -> None:
        """グリッド描画の完了を待つ（遷移途中のクエリで context が壊れるのを防ぐ）。"""
        try:
            page.wait_for_selector("table.calendar", timeout=NAV_TIMEOUT, state="attached")
        except Exception:
            pass
        page.wait_for_timeout(400)

    # --- 1ウィンドウ分のドリル取得 --------------------------------------

    def _scrape_visible_window(
        self, page: Page, date_from: dt.date, date_to: dt.date, label: str = ""
    ) -> list[Slot]:
        values = self._read_available(page, date_from, date_to)
        batches = list(_chunks(values, MAX_SELECT_PER_BATCH))
        slots: list[Slot] = []
        for i, batch in enumerate(batches):
            if self.out_of_time(f"{label} の日付バッチ {i + 1}/{len(batches)} の開始前"):
                break
            self._select_cells(page, batch)
            self._click_next_step(page)
            slots.extend(self._parse_timeband_page(page))
            self._go_back_to_grid(page)
            self._select_cells(page, batch)  # 同じセルを再クリック＝選択解除
        return slots

    def _read_available(
        self, page: Page, date_from: dt.date, date_to: dt.date
    ) -> list[str]:
        """期間内の○/△セルの checkbox 値を返す。"""
        values: list[str] = []
        for cb in page.query_selector_all("input[name='checkdate']"):
            value = (cb.get_attribute("value") or "").strip()
            date = _date_from_checkdate(value)
            if not date or not (date_from <= date <= date_to):
                continue
            label = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
            if label and label.inner_text().strip() in ("○", "△"):
                values.append(value)
        return values

    def _select_cells(self, page: Page, values: list[str]) -> None:
        for value in values:
            cb = page.query_selector(f"input[name='checkdate'][value='{value}']")
            if not cb:
                continue
            label = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
            if label:
                label.click()

    def _click_next_step(self, page: Page) -> None:
        page.click("a.btnBlue:has-text('次へ進む')")
        page.wait_for_url("**/WgR_JikantaibetsuAkiJoukyou", timeout=NAV_TIMEOUT)
        self._handle_modals(page)
        try:
            page.wait_for_selector("table.calendar", timeout=NAV_TIMEOUT, state="attached")
        except Exception:
            pass

    def _go_back_to_grid(self, page: Page) -> None:
        page.click("a.btnBlue:has-text('前に戻る')")
        page.wait_for_url("**/WgR_ShisetsubetsuAkiJoukyou", timeout=NAV_TIMEOUT)
        self._handle_modals(page)
        self._wait_for_grid(page)

    # --- 時間帯別ページの解析 -------------------------------------------

    def _parse_timeband_page(self, page: Page) -> list[Slot]:
        """H3(施設)→table(1部屋×1日の時間帯)の繰り返しを文書順に解析。"""
        slots: list[Slot] = []
        facility = ""
        body = page.query_selector("#body") or page
        for el in body.query_selector_all("h3, table.calendar"):
            if el.evaluate("e => e.tagName") == "H3":
                text = el.inner_text().strip()
                if text and "記号の見方" not in text:
                    raw = re.sub(r"《.*?》", "", text).strip()
                    facility = normalize_facility_name(raw)
                    if facility != raw and raw not in self._renamed:
                        self._renamed.add(raw)
                        print(f"[setagaya] 施設名を正規化: {raw!r} → {facility!r}")
                continue
            date = _find_date(el.inner_text())
            header = el.query_selector_all("tr:first-child th, tr:first-child td")
            bands = _time_bands([c.inner_text() for c in header])
            body_row = el.query_selector("tr:nth-child(2)") or el
            tds = body_row.query_selector_all("td")
            if not date or not bands or not tds:
                continue
            room, _cap = _parse_room_label(tds[0].inner_text())
            symbols = [c.inner_text().strip() for c in tds[1:]]
            for (start, end), sym in zip(bands, symbols):
                if sym == "○":
                    slots.append(
                        Slot(self.ward_name, facility, room, date.isoformat(), start, end)
                    )
        return slots

    # --- 共通ユーティリティ ---------------------------------------------

    def _load_all(self, page: Page, item_selector: str) -> None:
        """『さらに読み込む』を、件数が増えなくなる/ボタンが消えるまで押す。"""
        for _ in range(80):
            button = self._find_load_more(page)
            if not button:
                return
            before = len(page.query_selector_all(item_selector))
            try:
                button.click(timeout=4000)
            except Exception:
                try:
                    page.evaluate("readMesaiData()")
                except Exception:
                    return
            try:
                page.wait_for_function(
                    f"document.querySelectorAll(\"{item_selector}\").length > {before}",
                    timeout=10_000,
                )
            except Exception:
                return  # 増えない＝もう全件

    @staticmethod
    def _find_load_more(page: Page):
        try:
            elements = page.query_selector_all("a, input[type=button], button")
            for el in elements:
                text = (el.get_attribute("value") or el.inner_text() or "").strip()
                if text == "さらに読み込む" and el.is_visible():
                    return el
        except Exception:
            # 遷移中などで context が壊れたら「ボタンなし」扱いにする
            return None
        return None

    def _handle_modals(self, page: Page) -> None:
        dlg = page.query_selector("#messageDlg")
        if dlg and dlg.is_visible():
            for label in ["はい", "OK", "閉じる"]:
                btn = dlg.query_selector(f"text={label}")
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(300)
                    return

    def _build_windows(self, date_from: dt.date, date_to: dt.date) -> list[dt.date]:
        total = (date_to - date_from).days + 1
        count = max(1, math.ceil(total / DAYS_PER_PAGE))
        return [date_from + dt.timedelta(days=DAYS_PER_PAGE * i) for i in range(count)]

    def _pause(self, page: Page) -> None:
        if config.REQUEST_DELAY_SEC > 0:
            page.wait_for_timeout(int(config.REQUEST_DELAY_SEC * 1000))


# --- モジュール関数 ------------------------------------------------------


# 時間帯別ページの施設名(H3)には付記が続くことがある。
#   例) 「三軒茶屋区民集会所 :太子堂出張所・…」「経堂南地区会館 和室に鏡はありません。」
# 最初の空白（半角/全角）またはコロン（: ：）より前を施設名とする。
_FACILITY_NAME_SEP_RE = re.compile(r"[\s\u3000:：]")


def normalize_facility_name(name: str) -> str:
    """施設名から付記を取り除く。異常時（空になる／施設種別のキーワードが消える）は元の名前を返す。"""
    original = name.strip()
    head = _FACILITY_NAME_SEP_RE.split(original, maxsplit=1)[0]
    if not head:
        return original
    # 施設名本体の途中で切れてしまった（「区民センター」等が消えた）場合は切らない
    keywords = config.SETAGAYA_TARGET_KEYWORDS
    if any(k in original for k in keywords) and not any(k in head for k in keywords):
        return original
    return head


def _is_target(name: str) -> bool:
    if any(k in name for k in config.SETAGAYA_EXCLUDE_KEYWORDS):
        return False
    return any(k in name for k in config.SETAGAYA_TARGET_KEYWORDS)


def _parse_room_label(text: str) -> tuple[str, int | None]:
    text = re.sub(r"\s+", " ", text).strip()
    cap = CAPACITY_RE.search(text)
    name = re.sub(r"[（(].*?[）)]", "", text).strip()
    return name, (int(cap.group(1)) if cap else None)


def _date_from_checkdate(value: str) -> dt.date | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return None
    try:
        return dt.datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _time_bands(texts: list[str]) -> list[tuple[str, str]]:
    bands = []
    for t in texts:
        m = TIME_RANGE_RE.search(t or "")
        if m:
            bands.append((m.group(1), m.group(2)))
    return bands


def _find_date(text: str) -> dt.date | None:
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text or "")
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _dedupe(slots: list[Slot]) -> list[Slot]:
    return list(dict.fromkeys(slots))
