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

# 取得対象期間：今日から何日先まで見るか
SCRAPE_DAYS_AHEAD = int(os.environ.get("SCRAPE_DAYS_AHEAD", "30"))

# 世田谷区で対象とする施設カテゴリー（「カテゴリーから探す」のコード）。
# 01:区民センター 02:地区会館 03:区民集会所 04:ふれあいの家 05:敬老会館・高齢者集会所
# まずは集会施設の中核（区民センター/地区会館/区民集会所）のみ。学校開放等は対象外。
SETAGAYA_CATEGORIES = ["01", "02", "03"]

# 広さデータベース（Googleスプレッドシート）のCSV公開URL。
# 「ファイル > 共有 > ウェブに公開」で取得したCSVリンクを環境変数で渡す。
# 未設定なら data/facilities.json をそのまま使う（手動編集モード）。
FACILITIES_SHEET_CSV_URL = os.environ.get("FACILITIES_SHEET_CSV_URL", "").strip()

# Playwright をヘッドレスで動かすか（デバッグ時は HEADFUL=1 で画面表示）
HEADLESS = os.environ.get("HEADFUL", "") != "1"

# 相手サーバーへの配慮：各リクエスト後の待機秒数
REQUEST_DELAY_SEC = float(os.environ.get("REQUEST_DELAY_SEC", "1.0"))
