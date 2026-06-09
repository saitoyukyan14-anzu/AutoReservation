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
FACILITIES_JSON = DATA_DIR / "facilities.json"

# 取得対象期間：今日から「Nヶ月先の末日」まで取得する。
# 例) 2ヶ月 → 6月中に実行すれば 8月31日まで。
SCRAPE_MONTHS_AHEAD = int(os.environ.get("SCRAPE_MONTHS_AHEAD", "2"))

# 世田谷区：「使用目的から探す」で指定する用途コード（checkPurposeMiddle の値）。
# 131:その他ダンス（音量大） 136:その他ダンス（音量小）
SETAGAYA_PURPOSES = os.environ.get("SETAGAYA_PURPOSES", "131,136").split(",")

# 取得対象とする施設名のキーワード（用途検索結果から、この種類だけ残す）。
# 区民センター/地区会館/区民集会所のみ。小学校・中学校・運動場等は除外。
SETAGAYA_TARGET_KEYWORDS = ["区民センター", "地区会館", "集会所"]
SETAGAYA_EXCLUDE_KEYWORDS = ["小学校", "中学校"]

# 広さデータベース（Googleスプレッドシート）のCSV公開URL。
# 「ファイル > 共有 > ウェブに公開」で取得したCSVリンクを環境変数で渡す。
# 未設定なら data/facilities.json をそのまま使う（手動編集モード）。
FACILITIES_SHEET_CSV_URL = os.environ.get("FACILITIES_SHEET_CSV_URL", "").strip()

# Playwright をヘッドレスで動かすか（デバッグ時は HEADFUL=1 で画面表示）
HEADLESS = os.environ.get("HEADFUL", "") != "1"

# 相手サーバーへの配慮：各リクエスト後の待機秒数（高速化のため控えめ）
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "0.3"))
