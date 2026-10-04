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
