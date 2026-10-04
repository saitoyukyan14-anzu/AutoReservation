"""新宿区「新宿区立地域センター受付システム」スクレイパー。

システム: https://www.shinjuku.eprs.jp/chiiki/web/ （富士通系 e-Pares。JSP/Struts の *.do）
画面はサーバー生成の HTML、空き状況だけ jQuery の AJAX（JSON）で返る。文字コードは Windows-31J。
ブラウザでしか得られない情報がないため、Playwright は使わず HTTP クライアント（requests）で
ログイン不要の「空き照会」経路だけを辿る（ログイン・利用者登録・予約申込みは一切しない）。

取得方針（実地調査済み）:
  GET  /chiiki/web/                              トップ（JSESSIONID が発行される）
  POST rsvWOpeInstSrchVacantAction.do            どこで=地域センター(すべて)・何をする=ダンス（軽スポーツ）で検索
       → 「空き状況画面（施設ごと）」の #mansion-select（館）・#facility-select（全館の対象部屋）を読む
  POST rsvWOpeInstSrchVacantAjaxAction.do        部屋×週ごとに1回。1部屋×7日×5コマの状態が JSON で返る
       （transVacantMode=11 = useDay から7日）

状態コード（週表示の status）: 0=空き / 210=予約あり / 1=保守日 / 2=休館日 / 700=受付期間外。
**status==0（システムの「空き」表示）だけを Slot にする**。登録団体だけが申し込める時期や
抽選期間中のコマも「空き」と表示されるため、UI 側で注意書きを出す（README 参照）。

相手サーバーへの配慮（必須要件）:
  - 1実行の総リクエスト数に上限（SHINJUKU_MAX_REQUESTS、既定 450）。超えたら打ち切り、取得済み分を返す。
  - リクエストの間隔は3秒以上（SHINJUKU_REQUEST_DELAY_SEC。3未満を指定しても3秒にする）。並列にしない。
  - 5xx・4xx は1回でも出たら即打ち切り（再試行しない）。
  - タイムアウト・通信エラー・エラーJSON（ErrManager）・想定外の応答が3回続いたら打ち切り。
  - エラーJSON等のときの「トップ＋検索からやり直して再試行」は1実行につき1回まで。
  - User-Agent は正直に名乗る（SHINJUKU_USER_AGENT）。TLS 検証は無効化しない。
  打ち切ったときは timed_out=True にして取得済みの Slot と completed_until を返す
  （combine が completed_until より後を前回データで補う）。

並列化: shard の単位は館（館コード順に shard_items）。推奨は shards=1。
取得範囲の記録: 担当館名を set_assigned_facilities に、1週分を全担当部屋で取り終えるたびに
          その週の最終日を mark_completed_until に渡す。

試験用の絞り込み（環境変数）:
  SHINJUKU_ONLY_ROOMS=10000010,...  取得する部屋（施設コード）だけに絞る
  SHINJUKU_MAX_WEEKS=1              取得する週数の上限
  SHINJUKU_REQUEST_LOG=path.tsv     全リクエストの記録（時刻・メソッド・URL・ステータス・所要）を書き出す
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

import requests

import config
from models import Slot
from scrapers.base import WardScraper

ORIGIN = "https://www.shinjuku.eprs.jp"
BASE = ORIGIN + "/chiiki/web/"
TOP = BASE
SEARCH = BASE + "rsvWOpeInstSrchVacantAction.do"
WEEK = BASE + "rsvWOpeInstSrchVacantAjaxAction.do"
ENC = "cp932"  # Windows-31J

AVAILABLE = 0          # 空き
OUT_OF_PERIOD = 700    # 受付期間外
KNOWN_STATUS = {0: "空き", 1: "保守日", 2: "休館日", 210: "予約あり", 700: "受付期間外"}

MIN_DELAY_SEC = 3.0            # リクエスト間隔の下限（必須要件）
MAX_CONSECUTIVE_ERRORS = 3     # 連続エラーでの打ち切り
MAX_SESSION_RESETS = 1         # トップ＋検索のやり直しは1実行につき1回まで
RETRY_WAIT_SEC = 30.0          # タイムアウト・通信エラー後の待機
TIMEOUT = (15, 60)             # (接続, 読み取り) 秒

JST = dt.timezone(dt.timedelta(hours=9))
CAPACITY_RE = re.compile(r"\s*[（(][０-９0-9]+[）)]\s*$")


class _Abort(Exception):
    """実行全体を打ち切る（取得済み分は返す）。"""


class _Transient(Exception):
    """タイムアウト・通信エラー（待って再試行）。"""


class _SessionError(Exception):
    """エラーJSON・想定外の応答（トップ＋検索からやり直して再試行）。"""


@dataclass(frozen=True)
class _Room:
    bld: str        # 館コード
    inst: str       # 施設コード
    facility: str   # 館名（Slot.facility）
    name: str       # 部屋名（定員を除いたもの。Slot.room）


class _FormParser(HTMLParser):
    """form1 の送信内容をブラウザの form.submit() と同じ規則で集める（文書順）。"""

    def __init__(self, form_name: str = "form1"):
        super().__init__(convert_charrefs=True)
        self.form_name = form_name
        self.in_form = False
        self.fields: list[tuple[str, str]] = []
        self._sel: dict | None = None
        self._ta: list | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            if self.form_name in (a.get("name"), a.get("id")):
                self.in_form = True
            return
        if not self.in_form:
            return
        if tag == "input":
            t = (a.get("type") or "text").lower()
            name = a.get("name")
            if not name or t in ("button", "submit", "image", "file", "reset") or "disabled" in a:
                return
            if t in ("radio", "checkbox"):
                if "checked" in a:
                    self.fields.append((name, a.get("value") or "on"))
            else:
                self.fields.append((name, a.get("value") or ""))
        elif tag == "select":
            self._sel = {"name": a.get("name"), "chosen": None, "first": None, "disabled": "disabled" in a}
        elif tag == "option" and self._sel is not None:
            v = a.get("value")
            if self._sel["first"] is None:
                self._sel["first"] = v
            if "selected" in a and self._sel["chosen"] is None:
                self._sel["chosen"] = v
        elif tag == "textarea":
            self._ta = [a.get("name"), ""]

    def handle_data(self, data):
        if self._ta is not None:
            self._ta[1] += data

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False
        elif tag == "select" and self._sel is not None:
            s, self._sel = self._sel, None
            if self.in_form and s["name"] and not s["disabled"]:
                v = s["chosen"] if s["chosen"] is not None else s["first"]
                if v is not None:
                    self.fields.append((s["name"], v))
        elif tag == "textarea" and self._ta is not None:
            if self._ta[0]:
                self.fields.append((self._ta[0], self._ta[1]))
            self._ta = None


class _SelectParser(HTMLParser):
    """指定 id の <select> の option（属性・表示文字列）を集める。"""

    def __init__(self, ids: tuple[str, ...]):
        super().__init__(convert_charrefs=True)
        self.options: dict[str, list[tuple[dict, str]]] = {i: [] for i in ids}
        self._cur: str | None = None
        self._opt: list | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "select" and a.get("id") in self.options:
            self._cur = a["id"]
        elif tag == "option" and self._cur:
            self._close_option()  # 閉じタグの無い <option> にも対応
            self._opt = [a, ""]

    def handle_data(self, data):
        if self._opt is not None:
            self._opt[1] += data

    def handle_endtag(self, tag):
        if tag == "option" and self._opt is not None:
            self.options[self._cur].append((self._opt[0], self._opt[1].strip()))
            self._opt = None
        elif tag == "select":
            self._close_option()
            self._cur = None

    def _close_option(self):
        if self._opt is not None and self._cur:
            self.options[self._cur].append((self._opt[0], self._opt[1].strip()))
        self._opt = None


def _ymd(n: int) -> dt.date:
    return dt.date(n // 10000, n // 100 % 100, n % 100)


def _hhmm(n: int) -> str:
    return f"{int(n) // 100:02d}:{int(n) % 100:02d}"


class ShinjukuScraper(WardScraper):
    key = "shinjuku"
    ward_name = "新宿区"
    # 並列実行用：館を shard_count 個に分割し、この shard だけ担当する（推奨は shards=1）
    supports_shard = True

    def __init__(
        self,
        only_rooms: list[str] | None = None,
        max_weeks: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        deadline: float | None = None,
    ):
        super().__init__(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
        self._only_rooms = only_rooms if only_rooms is not None else config.SHINJUKU_ONLY_ROOMS
        self._max_weeks = max_weeks if max_weeks is not None else (config.SHINJUKU_MAX_WEEKS or None)
        self._delay = max(MIN_DELAY_SEC, config.SHINJUKU_REQUEST_DELAY_SEC)
        self._max_requests = config.SHINJUKU_MAX_REQUESTS
        self._log_path = config.SHINJUKU_REQUEST_LOG
        # 実行中の状態
        self._session: requests.Session | None = None
        self._last_req_end = 0.0
        self.request_count = 0          # 対象ホストへ送った全リクエスト（失敗も含む）
        self.error_count = 0            # エラーの総数
        self._consecutive_errors = 0
        self._session_resets = 0
        self._search_day: dt.date | None = None
        self.abort_reason: str | None = None
        self._unknown_status: set[tuple] = set()
        self._slots: list[Slot] = []
        self._weeks_done = 0

    # --- ログ -----------------------------------------------------------------

    def _log(self, msg: str) -> None:
        print(f"[{self.key}] {msg}")

    def _record(self, method: str, url: str, status: str, elapsed: float, note: str = "") -> None:
        if not self._log_path:
            return
        try:
            ts = dt.datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S")
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"{self.request_count}\t{ts}\t{method}\t{url}\t{status}\t{elapsed:.2f}\t{note}\n")
        except OSError:
            pass

    # --- 通信（全リクエストがここを通る） ---------------------------------------

    def _send(self, method: str, url: str, *, data: bytes | None = None, headers: dict | None = None,
              note: str = "") -> requests.Response:
        """1リクエスト送る。上限・間隔・5xx/4xx の打ち切りをここで強制する。"""
        if self.request_count >= self._max_requests:
            raise _Abort(f"リクエスト数の上限（{self._max_requests} 件）に達しました")
        wait = self._delay - (time.monotonic() - self._last_req_end)
        if wait > 0:
            time.sleep(wait)
        self.request_count += 1  # 送信前に数える（失敗しても数える）
        t0 = time.monotonic()
        try:
            r = self._session.request(method, url, data=data, headers=headers, timeout=TIMEOUT,
                                      allow_redirects=False)
        except requests.RequestException as e:  # タイムアウト・接続エラー・応答の途中切断など
            self._last_req_end = time.monotonic()
            self._record(method, url, "ERR", self._last_req_end - t0, f"{note} {type(e).__name__}")
            raise _Transient(f"{type(e).__name__}: {e}") from e
        self._last_req_end = time.monotonic()
        self._record(method, url, str(r.status_code), self._last_req_end - t0, note)
        if r.status_code >= 500:
            raise _Abort(f"サーバーエラー HTTP {r.status_code}（{method} {url}）")
        if r.status_code >= 400:
            raise _Abort(f"HTTP {r.status_code}（{method} {url}）")
        if r.status_code >= 300:
            raise _SessionError(f"想定外のリダイレクト HTTP {r.status_code} → {r.headers.get('Location')}")
        return r

    def _with_retry(self, what: str, fn, is_search: bool = False):
        """fn を実行する。連続エラー・やり直し回数の上限を超えたら _Abort。

        is_search=True（fn 自体がトップ＋検索）のときは、やり直し＝fn の再実行。
        """
        while True:
            try:
                result = fn()
                self._consecutive_errors = 0
                return result
            except _Transient as e:
                self._error(what, e)
                if self.out_of_time(f"{what} の再試行前"):
                    raise _Abort("時間切れ")
                time.sleep(RETRY_WAIT_SEC)
            except _SessionError as e:
                self._error(what, e)
                if self._session_resets >= MAX_SESSION_RESETS:
                    raise _Abort(f"{what}: エラー応答が続いたため打ち切ります（やり直しは1実行につき1回まで）: {e}")
                self._session_resets += 1
                self._log(f"{what}: トップ＋検索からやり直して再試行します（{self._session_resets}/{MAX_SESSION_RESETS}）")
                self._session.cookies.clear()  # セッション切れに備えて JSESSIONID を取り直す
                if not is_search:
                    self._with_retry("やり直しの検索", self._search_once, is_search=True)

    def _error(self, what: str, e: Exception) -> None:
        self.error_count += 1
        self._consecutive_errors += 1
        self._log(f"! {what}: {e}（連続 {self._consecutive_errors} 回目）")
        if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            raise _Abort(f"エラーが {MAX_CONSECUTIVE_ERRORS} 回続きました（最後: {what}: {e}）")

    # --- 検索 -------------------------------------------------------------------

    def _search_once(self) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """トップ → 検索。館 [(館コード, 館名)] と部屋 [(館コード, 施設コード, 表示名)] を返す。"""
        r = self._send("GET", TOP, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}, note="トップ")
        fp = _FormParser("form1")
        fp.feed(r.content.decode(ENC, errors="replace"))
        fields = fp.fields
        if not any(k == "displayNo" for k, _ in fields):
            raise _SessionError("トップ画面に検索フォーム（form1）が見つかりません")
        values = {
            "date": "3",
            "daystart": self._search_day.isoformat(),
            "days": "7",
            "dayofweekClearFlg": "1",   # 曜日の指定なし
            "timezoneClearFlg": "1",    # 時間帯の指定なし
            "selectAreaBcd": config.SHINJUKU_AREA,
            "selectIcd": "",
            "selectPpsClPpscd": config.SHINJUKU_PURPOSE,
        }
        out = [(k, values.pop(k) if k in values else v) for k, v in fields]
        out += list(values.items())  # フォームに無かった項目は末尾に足す
        body = urllib.parse.urlencode(out, encoding=ENC).encode("ascii")
        r = self._send("POST", SEARCH, data=body, note="検索", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer": TOP,
            "Origin": ORIGIN,
        })
        html = r.content.decode(ENC, errors="replace")
        if "空き状況画面（施設ごと）" not in html:
            raise _SessionError("検索結果が「空き状況画面（施設ごと）」ではありません")
        sp = _SelectParser(("mansion-select", "facility-select"))
        sp.feed(html)
        sp.close()
        blds = [(a.get("value", ""), text) for a, text in sp.options["mansion-select"] if a.get("value")]
        rooms = [
            (a.get("data-bldcd", ""), a.get("value", ""), text)
            for a, text in sp.options["facility-select"]
            if a.get("value") not in (None, "", "0")
        ]
        if not blds or not rooms:
            raise _SessionError(f"検索結果に館・部屋がありません（館 {len(blds)}・部屋 {len(rooms)}）")
        return blds, rooms

    # --- 週表示 -----------------------------------------------------------------

    def _week_once(self, room: _Room, start: dt.date) -> dict:
        body = urllib.parse.urlencode([
            ("displayNo", "prwrc2000"),
            ("useDay", start.strftime("%Y%m%d")),
            ("bldCd", room.bld),
            ("instCd", room.inst),
            ("transVacantMode", "11"),   # useDay から7日
            ("clearFlag", "0"),
        ]).encode("ascii")
        r = self._send("POST", WEEK, data=body, note=f"週表示 {room.inst} {start:%Y%m%d}", headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH,
            "Origin": ORIGIN,
        })
        try:
            j = json.loads(r.content.decode(ENC, errors="replace"))
        except ValueError:
            raise _SessionError("週表示の応答が JSON ではありません") from None
        if not isinstance(j, dict):
            raise _SessionError("週表示の応答が想定外の形式です")
        if "ErrManager" in j:
            msg = (j.get("ErrManager") or {}).get("message", "") if isinstance(j.get("ErrManager"), dict) else ""
            raise _SessionError(f"エラー応答（ErrManager）: {msg}")
        if not isinstance(j.get("result"), list) or not isinstance(j.get("weekDay"), list):
            raise _SessionError("週表示の応答に result / weekDay がありません")
        return j

    def _parse_week(self, j: dict, room: _Room, date_from: dt.date, date_to: dt.date):
        """Slot のリストと、応答に含まれた日付ごとの状態コード {date: [status...]} を返す。"""
        slots: list[Slot] = []
        statuses: dict[dt.date, list[int]] = {}
        if j.get("lendType") not in (None, 1):
            self._log(f"※ {room.facility} {room.name}: lendType={j.get('lendType')}（時間帯貸し以外）")
        for tz in j["result"]:
            for c in tz.get("timeResult") or []:
                try:
                    d = _ymd(int(c["useDay"]))
                    status = int(c["status"])
                except (KeyError, TypeError, ValueError):
                    continue
                statuses.setdefault(d, []).append(status)
                if status not in KNOWN_STATUS:
                    key = (status, c.get("alt"))
                    if key not in self._unknown_status:
                        self._unknown_status.add(key)
                        self._log(f"※ 未知の状態コード status={status} alt={c.get('alt')!r}（{room.facility} {room.name} {d}）")
                if status != AVAILABLE or not date_from <= d <= date_to:
                    continue
                try:
                    start, end = _hhmm(c["startTime"]), _hhmm(c["endTime"])
                except (KeyError, TypeError, ValueError):
                    self._log(f"※ 時刻のないコマ: {room.facility} {room.name} {d} {tz.get('tzoneName')}")
                    continue
                slots.append(Slot(
                    ward=self.ward_name, facility=room.facility, room=room.name,
                    date=d.isoformat(), start=start, end=end,
                ))
        return slots, statuses

    # --- 取得本体 ---------------------------------------------------------------

    def scrape(self, date_from: dt.date, date_to: dt.date) -> list[Slot]:
        # 日付は JST で扱う（実行環境が UTC でも、過去日を検索しないように）
        start = max(date_from, dt.datetime.now(JST).date())
        if start > date_to:
            return []
        if self.out_of_time("取得開始前"):
            return []
        self._search_day = start
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": config.SHINJUKU_USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
        })
        t_start = time.monotonic()
        self._slots = []
        self._weeks_done = 0
        try:
            self._run(start, date_to)
        except _Abort as e:
            self.abort_reason = str(e)
            self.timed_out = True  # 打ち切り（combine が completed_until より後を前回データで補う）
            self._log(f"打ち切り: {e}。ここまでの取得分を返します。")
        finally:
            self._session.close()
            self._session = None
        unique = sorted(set(self._slots), key=lambda s: (s.facility, s.room, s.date, s.start))
        self._log(
            f"終了: リクエスト {self.request_count} 件 / Slot {len(unique)} 件 / 週 {self._weeks_done} 週完了 / "
            f"エラー {self.error_count} 件 / やり直し {self._session_resets} 回 / "
            f"completed_until={self.completed_until} / 所要 {(time.monotonic() - t_start) / 60:.1f} 分"
            + (f" / 打ち切り理由: {self.abort_reason}" if self.abort_reason else "")
        )
        return unique

    def _run(self, start: dt.date, date_to: dt.date) -> None:
        """取得本体。Slot は self._slots に積む（_Abort で中断しても取得済み分が残るように）。"""
        blds, raw_rooms = self._with_retry("検索", self._search_once, is_search=True)

        bld_names = dict(blds)
        rooms_all = [
            _Room(bld=b, inst=i, facility=bld_names.get(b, b), name=CAPACITY_RE.sub("", label).strip())
            for b, i, label in raw_rooms
        ]
        unknown_bld = sorted({r.bld for r in rooms_all if r.bld not in bld_names})
        if unknown_bld:
            self._log(f"※ 館一覧に無い館コードの部屋があります: {unknown_bld}")
        if not self._only_rooms and len(rooms_all) != config.SHINJUKU_EXPECTED_ROOMS:
            self._log(f"※ 検索結果の部屋数が想定（{config.SHINJUKU_EXPECTED_ROOMS}）と違います: {len(rooms_all)} 室")
        self._log(f"検索結果: {len(blds)} 館・{len(rooms_all)} 室")

        # shard の単位は館（館コード順）
        my_blds = self.shard_items(sorted({r.bld for r in rooms_all}))
        rooms = [
            r for r in rooms_all
            if r.bld in my_blds
            and r.inst not in config.SHINJUKU_EXCLUDE_ROOMS
            and (not self._only_rooms or r.inst in self._only_rooms)
        ]
        rooms.sort(key=lambda r: (r.bld, r.inst))
        if self._only_rooms:
            missing = sorted(set(self._only_rooms) - {r.inst for r in rooms_all})
            if missing:
                self._log(f"※ 指定された部屋が検索結果にありません: {missing}")
        self.set_assigned_facilities(r.facility for r in rooms)
        self._log(f"担当: {len(self.assigned_facilities or [])} 館・{len(rooms)} 室"
                  f"（shard {self.shard_index + 1}/{self.shard_count}）")
        if not rooms:
            return

        today = dt.datetime.now(JST).date()
        slots = self._slots
        done_rooms: set[_Room] = set()   # 以後すべて受付期間外になった部屋
        w = start
        weeks = 0
        while w <= date_to:
            if self._max_weeks is not None and weeks >= self._max_weeks:
                break
            if self.out_of_time(f"{w} の週の開始前"):
                break
            week_end = min(w + dt.timedelta(days=6), date_to)
            expected_days = {w + dt.timedelta(days=k) for k in range((week_end - w).days + 1)}
            n_before = len(slots)
            for room in rooms:
                if room in done_rooms:
                    continue
                if self.out_of_time(f"{w} の週・{room.facility} {room.name}"):
                    break
                j = self._with_retry(f"{room.facility} {room.name} {w} の週",
                                     lambda: self._week_once(room, w))
                got, statuses = self._parse_week(j, room, start, date_to)
                slots += got
                first = j["weekDay"][0].get("useDay") if j["weekDay"] else None
                if str(first) != w.strftime("%Y%m%d"):
                    self._log(f"※ 週表示の初日が要求と違います: 要求 {w:%Y%m%d} / 応答 {first}（{room.facility} {room.name}）")
                lacking = sorted(expected_days - set(statuses))
                if lacking:
                    self._log(f"※ 週表示に含まれない日があります: {[d.isoformat() for d in lacking]}（{room.facility} {room.name}）")
                future = [s for d, ss in statuses.items() if d >= today for s in ss]
                if future and all(s == OUT_OF_PERIOD for s in future):
                    done_rooms.add(room)
            if self.timed_out:
                break
            weeks += 1
            self._weeks_done = weeks
            self.mark_completed_until(week_end)
            self._log(f"{w}〜{week_end}: {len(slots) - n_before} 件（累計 {len(slots)} 件・リクエスト {self.request_count} 件）")
            if len(done_rooms) == len(rooms):
                self._log(f"{week_end} の週で全部屋が受付期間外になったため終了します。")
                self.mark_completed_until(date_to)
                break
            w += dt.timedelta(days=7)
