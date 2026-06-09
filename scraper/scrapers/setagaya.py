"""世田谷区「けやきネット」スクレイパー。

けやきネットは ASP.NET MVC 製で画面遷移が jQuery の __doPostBack（JS）で
行われるため、Playwright（ヘッドレスブラウザ）で実際にクリック操作して
空き状況を取得する。ログイン不要の「空き照会」経路のみを使う。

確認済みの画面遷移（実地調査済み）:
  /Web/ ─[カテゴリーから探す]→ カテゴリ選択(区民センター等)
        → 施設一覧(WgR_ShisetsuKensaku) ─全施設選択・[次へ]→
        → 施設別空き状況(WgR_ShisetsubetsuAkiJoukyou)  … 部屋×日付の○△×グリッド
        ─○/△の日付セルを選択・[次へ進む]→
        → 時間帯別空き状況(WgR_JikantaibetsuAkiJoukyou) … 実時間帯(9:00〜12:00等)の○/×

記号: ○=空き / △=一部空き / ×=空きなし / －=申込期間外 / ＊=公開対象外
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

#: 対象カテゴリーの既定値（config から取得）。
#: 01:区民センター 02:地区会館 03:区民集会所
DEFAULT_CATEGORIES = config.SETAGAYA_CATEGORIES

#: 1画面に表示される日数（けやきネットの仕様）
DAYS_PER_PAGE = 14

#: 日付セルを一度に選択できる上限（けやきネットの仕様：最大10件）
MAX_SELECT_PER_BATCH = 10

TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[～~〜]\s*(\d{1,2}:\d{2})")
CAPACITY_RE = re.compile(r"定員\s*(\d+)")


class SetagayaScraper(WardScraper):
    ward_name = "世田谷区"

    def __init__(
        self,
        categories: list[str] | None = None,
        drill_partial: bool = True,
        max_batches_per_window: int | None = None,
    ):
        self.categories = categories or DEFAULT_CATEGORIES
        # True: ○(空き)と△(一部空き)を時間帯までドリル / False: ○のみドリル（軽量）
        self.available_symbols = {"○", "△"} if drill_partial else {"○"}
        # 1ウィンドウあたりのドリル回数上限（負荷・実行時間の制御用。None=無制限）
        self.max_batches_per_window = max_batches_per_window

    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        windows = self._build_windows(date_from, date_to)
        slots: list[Slot] = []
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
                for category in self.categories:
                    for window_index in range(len(windows)):
                        slots.extend(
                            self._scrape_category_window(
                                page, category, window_index, date_from, date_to
                            )
                        )
            finally:
                context.close()
                browser.close()
        # 重複除去（カテゴリ間で施設が重なる可能性に備える）
        return _dedupe(slots)

    # --- 期間ウィンドウ -------------------------------------------------

    def _build_windows(self, date_from: dt.date, date_to: dt.date) -> list[dt.date]:
        """14日刻みのウィンドウ開始日リストを返す。"""
        total_days = (date_to - date_from).days + 1
        count = max(1, math.ceil(total_days / DAYS_PER_PAGE))
        return [date_from + dt.timedelta(days=DAYS_PER_PAGE * i) for i in range(count)]

    # --- 1カテゴリ・1ウィンドウの取得 -----------------------------------

    def _scrape_category_window(
        self,
        page: Page,
        category: str,
        window_index: int,
        date_from: dt.date,
        date_to: dt.date,
    ) -> list[Slot]:
        # 施設別空き状況グリッドまで遷移し、対象ウィンドウへページ送りする
        self._open_grid_for_category(page, category)
        for _ in range(window_index):
            self._goto_next_period(page)

        # グリッドを読み、空き(○/△)の日付セルのチェックボックス値を収集する
        available_values = self._read_grid_available(page, date_from, date_to)
        if not available_values:
            return []

        # 最大10件ずつ選択 →[次へ進む]→ 時間帯別を解析 →[前に戻る]→ 選択解除 → 次のバッチ
        # ※「前に戻る」では選択状態が保持されるため、各バッチ後に必ず解除する。
        slots: list[Slot] = []
        for i, batch in enumerate(_chunks(available_values, MAX_SELECT_PER_BATCH)):
            if self.max_batches_per_window is not None and i >= self.max_batches_per_window:
                break
            self._select_cells(page, batch)
            self._click_next_step(page)
            slots.extend(self._parse_timeband_page(page))
            self._go_back_to_grid(page)
            self._select_cells(page, batch)  # 同じセルを再クリック＝選択解除
        return slots

    # --- 画面遷移ヘルパ -------------------------------------------------

    def _open_grid_for_category(self, page: Page, category: str) -> None:
        page.goto(BASE_URL, wait_until="networkidle")
        page.get_by_text("カテゴリーから探す", exact=True).click()
        page.wait_for_timeout(int(config.REQUEST_DELAY_SEC * 500))
        page.click(f"#category_{category}")
        page.wait_for_url("**/WgR_ShisetsuKensaku", timeout=20000)
        page.wait_for_load_state("networkidle")
        self._handle_modals(page)

        # 一覧の全施設を選択して次へ
        codes = [
            cb.get_attribute("value")
            for cb in page.query_selector_all("input[name='checkShisetsu']")
        ]
        for code in codes:
            label = page.query_selector(f"label[for='checkShisetsu{code}']")
            if label:
                label.click()
        self._pause(page)
        page.click("#btnNext")
        page.wait_for_url("**/WgR_ShisetsubetsuAkiJoukyou", timeout=20000)
        page.wait_for_load_state("networkidle")
        self._handle_modals(page)

    def _goto_next_period(self, page: Page) -> None:
        page.click("a[href*=\"__doPostBack('period','next')\"]")
        page.wait_for_load_state("networkidle")
        self._pause(page)

    def _click_next_step(self, page: Page) -> None:
        page.click("a.btnBlue:has-text('次へ進む')")
        page.wait_for_url("**/WgR_JikantaibetsuAkiJoukyou", timeout=20000)
        page.wait_for_load_state("networkidle")
        self._handle_modals(page)

    def _go_back_to_grid(self, page: Page) -> None:
        page.click("a.btnBlue:has-text('前に戻る')")
        page.wait_for_url("**/WgR_ShisetsubetsuAkiJoukyou", timeout=20000)
        page.wait_for_load_state("networkidle")
        self._handle_modals(page)

    # --- グリッド解析 ---------------------------------------------------

    def _read_grid_available(
        self, page: Page, date_from: dt.date, date_to: dt.date
    ) -> list[str]:
        """施設別空き状況グリッドを読み、期間内の○/△セルの checkbox 値を返す。"""
        values: list[str] = []
        for cb in page.query_selector_all("input[name='checkdate']"):
            value = (cb.get_attribute("value") or "").strip()
            date = _date_from_checkdate(value)
            if not date or not (date_from <= date <= date_to):
                continue
            label = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
            symbol = label.inner_text().strip() if label else ""
            if symbol in self.available_symbols:
                values.append(value)
        return values

    def _select_cells(self, page: Page, values: list[str]) -> None:
        """指定した checkbox 値の日付セルを選択する。"""
        for value in values:
            cb = page.query_selector(f"input[name='checkdate'][value='{value}']")
            if not cb:
                continue
            label = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
            if label:
                label.click()
        self._pause(page)

    # --- 時間帯別ページ解析 ---------------------------------------------

    def _parse_timeband_page(self, page: Page) -> list[Slot]:
        """時間帯別ページを文書順に走査して空き(○)時間帯を Slot 化する。

        構造: H3(施設名) → H4(部屋名) → table(日付＋時間帯＋○/×) の繰り返し。
        各 table は「1部屋 × 1日」に対応する。
        """
        slots: list[Slot] = []
        current_facility = ""
        body = page.query_selector("#body") or page
        for el in body.query_selector_all("h3, table.calendar"):
            tag = el.evaluate("e => e.tagName")
            if tag == "H3":
                text = el.inner_text().strip()
                if "記号の見方" in text or not text:
                    continue
                current_facility = re.sub(r"《.*?》", "", text).strip()
                continue

            # table.calendar（1部屋×1日）
            date = _find_date_in_text(el.inner_text())
            header_cells = el.query_selector_all("tr:first-child th, tr:first-child td")
            time_bands = _extract_time_bands([c.inner_text() for c in header_cells])
            body_row = el.query_selector("tr:nth-child(2)") or el
            tds = body_row.query_selector_all("td")
            if not date or not time_bands or not tds:
                continue
            room_name, _cap = _parse_room_label(tds[0].inner_text())  # 先頭td=部屋名
            symbol_cells = [c.inner_text().strip() for c in tds[1:]]  # 以降=各時間帯の記号
            for (start, end), symbol in zip(time_bands, symbol_cells):
                if symbol == "○":
                    slots.append(
                        Slot(
                            ward=self.ward_name,
                            facility=current_facility,
                            room=room_name,
                            date=date.isoformat(),
                            start=start,
                            end=end,
                        )
                    )
        return slots

    # --- 共通 -----------------------------------------------------------

    def _handle_modals(self, page: Page) -> None:
        dlg = page.query_selector("#messageDlg")
        if dlg and dlg.is_visible():
            for label in ["はい", "OK", "閉じる"]:
                btn = dlg.query_selector(f"text={label}")
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(500)
                    return

    def _pause(self, page: Page) -> None:
        if config.REQUEST_DELAY_SEC > 0:
            page.wait_for_timeout(int(config.REQUEST_DELAY_SEC * 1000))


# --- モジュール関数（解析ユーティリティ） -------------------------------


def _parse_room_label(text: str) -> tuple[str, int | None]:
    """「第１会議室 （定員25）」→ ("第１会議室", 25)"""
    text = re.sub(r"\s+", " ", text).strip()
    cap_match = CAPACITY_RE.search(text)
    capacity = int(cap_match.group(1)) if cap_match else None
    name = re.sub(r"[（(].*?[）)]", "", text).strip()
    return name, capacity


def _date_from_checkdate(value: str) -> dt.date | None:
    """checkdate の値「2026061000101 0」先頭8桁から日付を取り出す。"""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return None
    try:
        return dt.datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _extract_time_bands(texts: list[str]) -> list[tuple[str, str]]:
    bands: list[tuple[str, str]] = []
    for t in texts:
        m = TIME_RANGE_RE.search(t or "")
        if m:
            bands.append((m.group(1), m.group(2)))
    return bands


def _find_date_in_text(text: str) -> dt.date | None:
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text or "")
    if not m:
        return None
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _dedupe(slots: list[Slot]) -> list[Slot]:
    return list(dict.fromkeys(slots))
