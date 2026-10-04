"""スクレイパー全体の設定。

環境変数で上書きできる項目は GitHub Actions から差し替えやすくしてある。
"""
from __future__ import annotations

import os
from pathlib import Path

# リポジトリのルート（このファイルの2階層上）
ROOT = Path(__file__).resolve().parent.parent
# 出力先はフロント(Vite)の public 配下。ビルド時に自動的にサイトへ含まれる。
DATA_DIR = ROOT / "web" / "public" / "data"

AVAILABILITY_JSON = DATA_DIR / "availability.json"
# 施設情報（リンク等）は facilities.json を直接編集して管理する（スクレイパーは書き換えない）
FACILITIES_JSON = DATA_DIR / "facilities.json"

# 取得対象期間：今日から「Nヶ月先の末日」まで取得する。
# 例) 2ヶ月 → 6月中に実行すれば 8月31日まで。
SCRAPE_MONTHS_AHEAD = int(os.environ.get("SCRAPE_MONTHS_AHEAD", "2"))

# 取得の時間予算（分）。プロセス開始からこの時間を過ぎたら、各スクレイパーは
# そこまでの取得分を返して打ち切る（GitHub Actions の1ジョブ6時間上限で強制終了され、
# 結果が全損するのを防ぐ）。0 以下で無制限。`main.py --time-budget-min` で上書きできる。
TIME_BUDGET_MIN = float(os.environ.get("SCRAPE_TIME_BUDGET_MIN", "330"))

# 世田谷区：「使用目的から探す」で指定する用途コード（checkPurposeMiddle の値）。
# 131:その他ダンス（音量大） 136:その他ダンス（音量小）
SETAGAYA_PURPOSES = os.environ.get("SETAGAYA_PURPOSES", "131,136").split(",")

# 取得対象とする施設名のキーワード（用途検索結果から、この種類だけ残す）。
# 区民センター/地区会館/区民集会所のみ。小学校・中学校・運動場等は除外。
SETAGAYA_TARGET_KEYWORDS = ["区民センター", "地区会館", "集会所"]
SETAGAYA_EXCLUDE_KEYWORDS = ["小学校", "中学校"]

# Playwright をヘッドレスで動かすか（デバッグ時は HEADFUL=1 で画面表示）
HEADLESS = os.environ.get("HEADFUL", "") != "1"

# 相手サーバーへの配慮：各リクエスト後の待機秒数（高速化のため控えめ）
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "0.3"))

# ── 新宿区：新宿区立地域センター受付システム（https://www.shinjuku.eprs.jp/chiiki/web/）──
# 「何をする」（#purpose の value）。40_20 = ダンス（軽スポーツ）
SHINJUKU_PURPOSE = os.environ.get("SHINJUKU_PURPOSE", "40_20")
# 「どこで」。1000_0 = 新宿区立地域センター(すべて)
SHINJUKU_AREA = os.environ.get("SHINJUKU_AREA", "1000_0")
# 除外する部屋（施設コード、カンマ区切り）。既定は除外なし（検索結果の全部屋＝葬儀兼用ホールも含む）。
#   葬儀兼用ホール: 10100090 菊, 10100100 百合（牛込箪笥）, 10200100 地下ホールＡ（榎町）,
#                   10300090 Ｂ１ホール（若松）, 10500110 集会室１（戸塚）
SHINJUKU_EXCLUDE_ROOMS = [c.strip() for c in os.environ.get("SHINJUKU_EXCLUDE_ROOMS", "").split(",") if c.strip()]
# 想定している部屋数（検索結果がこれと違えば警告ログを出す）
SHINJUKU_EXPECTED_ROOMS = int(os.environ.get("SHINJUKU_EXPECTED_ROOMS", "25"))
# リクエスト間の待機秒数（全リクエスト共通。3 未満を指定しても 3 秒にする）
SHINJUKU_REQUEST_DELAY_SEC = float(os.environ.get("SHINJUKU_REQUEST_DELAY_SEC", "3.0"))
# 1回の実行で対象ホストに送る総リクエスト数の上限（超えたら打ち切り、取得済み分を返す）。
# 見積もり: トップ＋検索 2 件 ＋ 25室 × 最大14週 ≒ 330〜352 件
SHINJUKU_MAX_REQUESTS = int(os.environ.get("SHINJUKU_MAX_REQUESTS", "450"))
# User-Agent（ブラウザを偽装せず、正直に名乗る）
SHINJUKU_USER_AGENT = os.environ.get(
    "SHINJUKU_USER_AGENT",
    "AkiShisetsuKensaku/1.0 (+https://github.com/saitoyukyan14-anzu/AutoReservation)",
)
# 試験用の絞り込み: 取得する部屋（施設コード、カンマ区切り。空なら全部屋）と週数の上限（0 で無制限）
SHINJUKU_ONLY_ROOMS = [c.strip() for c in os.environ.get("SHINJUKU_ONLY_ROOMS", "").split(",") if c.strip()]
SHINJUKU_MAX_WEEKS = int(os.environ.get("SHINJUKU_MAX_WEEKS", "0"))
# 全リクエストの記録先（TSV。空なら記録しない）。試験・調査用
SHINJUKU_REQUEST_LOG = os.environ.get("SHINJUKU_REQUEST_LOG", "")


def _env_list(name: str, default: list[str]) -> list[str]:
    """カンマ区切りの環境変数をリストにする（未設定・空なら default）。"""
    items = [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]
    return items or list(default)


# ── 文京区「文の京」施設予約ねっと ─────────────────────────────────────────
# robots.txt は `Disallow: /*`（許可は *.html と /*/Home のみ）で、空き照会の画面はクロール禁止に当たる。
# ユーザーが承知の上で、次の控えめな条件での運用を承認している:
#   1日1〜2回（scrape.yml の cron）／操作ごとに3秒以上待機／並列1本（shard 分割しない）／
#   1セッションの照会は3施設・2週間表示まで。
# 下の値のうち、この条件に関わるものは環境変数で緩められないよう上限・下限をかけている。

# 「利用目的から探す」の分類と利用目的（HomeModel の value）。
# 分類 3:体操・ダンス ／ 利用目的 40:ダンス（41:バレエ 37:体操・ストレッチ 39:ヨガ・ピラティス）
BUNKYO_PURPOSE_CATEGORY = os.environ.get("BUNKYO_PURPOSE_CATEGORY", "3")
BUNKYO_PURPOSES = _env_list("BUNKYO_PURPOSES", ["40"])

# 取得対象の施設名（許可リスト。施設選択画面の表記と完全一致）。ユーザー承認済みの B案（14施設）。
# 検索結果にあってここに無い施設は取得しない（ログに出す）。ここにあって検索結果に無い施設は警告を出す。
BUNKYO_TARGET_FACILITIES = _env_list("BUNKYO_TARGET_FACILITIES", [
    # 地域の集会施設（A案）
    "区民会議室",
    "大原地域活動センター",
    "大塚地域活動センター",
    "向丘地域活動センター",
    "汐見地域活動センター",
    "駒込地域活動センター",
    "元町多目的室",
    "目白台交流館",
    "不忍通りふれあい館",
    # 目的が決まった集会施設（B案で追加）
    "シルバーセンター",
    "男女平等センター",
    "福祉センター江戸川橋",
    "松聲閣集会室",
    "勤労福祉会館",
])

# 「洋室Ａ＋Ｂ」のような合体室（名前に＋を含む部屋）を除外するか。既定は含める（別の部屋として扱う）。
BUNKYO_EXCLUDE_COMBINED_ROOMS = os.environ.get("BUNKYO_EXCLUDE_COMBINED_ROOMS", "") == "1"

# 1セッション（Home からの1回の検索）で選ぶ施設数。重い照会を避けるため 1〜3 に制限。
BUNKYO_FACILITIES_PER_SESSION = min(3, max(1, int(os.environ.get("BUNKYO_FACILITIES_PER_SESSION", "3"))))

# 施設別空き状況の表示期間（1:1日 2:1週間 3:2週間）。1ヶ月(4)は重いので使わない。
BUNKYO_DISPLAY_TERM = os.environ.get("BUNKYO_DISPLAY_TERM", "3")
if BUNKYO_DISPLAY_TERM not in ("1", "2", "3"):
    BUNKYO_DISPLAY_TERM = "3"

# 相手サーバーへの操作（画面遷移・データ取得）の間隔（秒）。3秒未満にはできない。
BUNKYO_REQUEST_DELAY_SEC = max(3.0, float(os.environ.get("BUNKYO_REQUEST_DELAY_SEC", "3")))

# 安全装置: 1回の実行で対象ホストへ送るリクエスト数の上限。ページ・XHR・自動の接続維持通信・
# 静的ファイルをすべて数える（ブラウザのキャッシュから読んだだけでサーバーに届かないものは数えない）。
# 上限に近づいたら（残り BUNKYO_REQUEST_RESERVE 件）取得を打ち切り、時間切れと同じ扱いで記録する。
BUNKYO_MAX_REQUESTS = int(os.environ.get("BUNKYO_MAX_REQUESTS", "800"))
BUNKYO_REQUEST_RESERVE = max(0, int(os.environ.get("BUNKYO_REQUEST_RESERVE", "10")))

# 安全装置: 5xx 応答・エラー画面への遷移などの失敗が、取得が進まないまま連続でこの回数に達したら
# 実行全体を中止する（取得済み分は返す）。再試行の前には BUNKYO_RETRY_WAIT_SEC 秒（30秒以上）待つ。
BUNKYO_MAX_CONSECUTIVE_ERRORS = max(1, int(os.environ.get("BUNKYO_MAX_CONSECUTIVE_ERRORS", "3")))
BUNKYO_RETRY_WAIT_SEC = max(30.0, float(os.environ.get("BUNKYO_RETRY_WAIT_SEC", "30")))

# 試験用の絞り込み（本番は未設定＝制限なし）。施設数（許可リストに合った施設のコード順の先頭から）と、
# 表示期間の数（BUNKYO_MAX_PERIODS=1 なら今日から2週間分だけ）。
BUNKYO_MAX_FACILITIES = int(os.environ.get("BUNKYO_MAX_FACILITIES", "0") or 0)
BUNKYO_MAX_PERIODS = int(os.environ.get("BUNKYO_MAX_PERIODS", "0") or 0)

# ── ローカル実行専用（GitHub Actions 上では無視する） ──────────────────────────
# Playwright が想定する版のブラウザを `playwright install` できない環境で、手元にあるブラウザを使うときの実行ファイル。
LOCAL_CHROMIUM_EXECUTABLE = os.environ.get("LOCAL_CHROMIUM_EXECUTABLE", "")
