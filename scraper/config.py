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
#   1日2回（scrape.yml の cron。施設グループを輪番にするので、各施設の更新は1日1回）／
#   操作ごとに3秒以上待機／並列1本（shard 分割しない）／1セッションの照会は3施設・2週間表示まで／
#   1回の実行で対象ホストへ送るリクエストは800件まで。
# 下の値のうち、この条件に関わるものは環境変数で緩められないよう上限・下限をかけている
# （緩める方向に変えるにはユーザーの承認が必要）。

# 「利用目的から探す」の分類と利用目的（HomeModel の value）。
# 分類 3:体操・ダンス ／ 利用目的 40:ダンス（41:バレエ 37:体操・ストレッチ 39:ヨガ・ピラティス）
BUNKYO_PURPOSE_CATEGORY = os.environ.get("BUNKYO_PURPOSE_CATEGORY", "3")
BUNKYO_PURPOSES = _env_list("BUNKYO_PURPOSES", ["40"])

# 取得対象の施設と、1セッションで一緒に照会するグループ（1グループ3施設まで）。ユーザー決定の11施設。
# 名前は施設選択画面の表記と完全一致。グループは下の見込み（BUNKYO_FACILITY_WEIGHTS）で輪番に振り分ける。
BUNKYO_FACILITY_GROUPS: list[list[str]] = [
    ["福祉センター江戸川橋"],
    ["区民会議室", "シルバーセンター", "男女平等センター"],
    ["駒込地域活動センター", "不忍通りふれあい館", "元町多目的室"],
    ["汐見地域活動センター", "向丘地域活動センター"],
    ["大原地域活動センター", "大塚地域活動センター"],
]
# 取得対象の施設名（許可リスト）。既定は上のグループの全施設。BUNKYO_TARGET_FACILITIES（カンマ区切り）で
# 絞れる（試験用）。検索結果にあってここに無い施設は取得しない（ログに出す）。ここにあって検索結果に無い施設は警告。
BUNKYO_TARGET_FACILITIES = _env_list(
    "BUNKYO_TARGET_FACILITIES", [name for group in BUNKYO_FACILITY_GROUPS for name in group]
)

# 施設ごとの部屋の絞り込み（施設名 → {"include": [...]} または {"exclude": [...]}。部屋名は完全一致）。
# 除外した部屋のセルは時間帯別への選択対象にもしない（リクエストを減らすため）。
BUNKYO_ROOM_RULES: dict[str, dict[str, list[str]]] = {
    # ユーザー決定: ホールのみ（ホール＋スタジオ・３階会議室・４階会議室は対象外）
    "不忍通りふれあい館": {"include": ["ホール"]},
}

# 「洋室Ａ＋Ｂ」のような合体室（名前に＋を含む部屋）を除外するか。既定は含める（別の部屋として扱う）。
BUNKYO_EXCLUDE_COMBINED_ROOMS = os.environ.get("BUNKYO_EXCLUDE_COMBINED_ROOMS", "") == "1"

# 輪番: 施設グループを BUNKYO_ROTATIONS 組に分け、1回の実行ではそのうち1組だけを取得する
# （取得しない組の施設は、combine が前回データをそのまま引き継ぐ）。組の番号は UTC の日付と時刻から決める:
#   (通算日 × 2 + (12時以降なら 1)) mod BUNKYO_ROTATIONS
# 定期実行（UTC 01:00 / 13:00）で輪番2組なら、朝が 0 番・夜が 1 番になり、どの施設も1日1回更新される。
# 手動実行などで番号を指定するときは BUNKYO_ROTATION_INDEX（0 始まり）。
BUNKYO_ROTATIONS = max(1, int(os.environ.get("BUNKYO_ROTATIONS", "2") or 2))
BUNKYO_ROTATION_INDEX = os.environ.get("BUNKYO_ROTATION_INDEX", "").strip()
# グループを輪番に振り分けるときの見込み（施設ごとの「時間帯別で確認するセル数」の見込み。
# = 部屋数 × 受付期間の平均日数 × 空き・一部空きの割合 0.55。受付期間は区の利用案内 5-3 から）。
# 例: 福祉センター江戸川橋は 10部屋 × 75日 × 0.55 ≒ 413。リクエスト数はおおむねこの 1.15 倍＋グループごとに約40。
BUNKYO_FACILITY_WEIGHTS: dict[str, int] = {
    # 10部屋・2か月前の月の1日から。2026-10-05 の実測では空き・一部空きの割合が高く約550セル（約700リクエスト）
    # だったが、この値のままにして「福祉センター江戸川橋＋大原・大塚」と「残り」の2組に分けている
    # （上限 800 件で打ち切られるのが福祉センター江戸川橋の先の日付になるように。README 参照）
    "福祉センター江戸川橋": 413,
    "区民会議室": 37,            # 1部屋・2か月前の月の8日から
    "シルバーセンター": 41,       # 1部屋・2か月前の月の1日から
    "男女平等センター": 83,       # 2部屋・2か月前の月の1日から
    "駒込地域活動センター": 112,  # ホールＡ・Ｂ・Ａ＋Ｂ・2か月前の月の8日から
    "不忍通りふれあい館": 37,     # ホールのみ・2か月前の月の8日から
    "元町多目的室": 21,           # 1部屋・1か月前の月の8日から
    "汐見地域活動センター": 125,  # 6部屋（合体室含む）・1か月前の月の8日から
    "向丘地域活動センター": 21,   # 1部屋・1か月前の月の8日から
    "大原地域活動センター": 63,   # 3部屋・1か月前の月の8日から
    "大塚地域活動センター": 63,   # 3部屋（合体室含む）・1か月前の月の8日から
}
BUNKYO_DEFAULT_FACILITY_WEIGHT = 60

# 1セッション（Home からの1回の検索）で選ぶ施設数。重い照会を避けるため 1〜3 に制限。
# （BUNKYO_FACILITY_GROUPS のグループもこの数を超えたら分割する）
BUNKYO_FACILITIES_PER_SESSION = min(3, max(1, int(os.environ.get("BUNKYO_FACILITIES_PER_SESSION", "3"))))

# 施設別空き状況の表示期間（1:1日 2:1週間 3:2週間）。1ヶ月(4)は重いので使わない。
BUNKYO_DISPLAY_TERM = os.environ.get("BUNKYO_DISPLAY_TERM", "3")
if BUNKYO_DISPLAY_TERM not in ("1", "2", "3"):
    BUNKYO_DISPLAY_TERM = "3"

# 相手サーバーへの操作（画面遷移・データ取得）の間隔（秒）。3秒未満にはできない。
BUNKYO_REQUEST_DELAY_SEC = max(3.0, float(os.environ.get("BUNKYO_REQUEST_DELAY_SEC", "3")))

# 安全装置: 1回の実行で対象ホストへ送るリクエスト数の上限（承認済みの 800 件が上限。環境変数では下げることだけできる）。
# ページ・XHR・自動の接続維持通信・静的ファイルをすべて数える（ブラウザのキャッシュから読んだだけで
# サーバーに届かないものは数えない）。数え方は「CDP で見た送信数」と「ブラウザが発行した数 − キャッシュで
# 済んだ数」の大きい方。上限に近づいたら（残り BUNKYO_REQUEST_RESERVE 件）取得を打ち切り、時間切れと同じ扱いで記録する。
BUNKYO_MAX_REQUESTS_LIMIT = 800
BUNKYO_MAX_REQUESTS = min(BUNKYO_MAX_REQUESTS_LIMIT, max(1, int(os.environ.get("BUNKYO_MAX_REQUESTS", "800") or 800)))
BUNKYO_REQUEST_RESERVE = max(10, int(os.environ.get("BUNKYO_REQUEST_RESERVE", "10") or 10))

# 安全装置: 5xx 応答・エラー画面への遷移などの失敗が、取得が進まないまま連続でこの回数に達したら
# 実行全体を中止する（取得済み分は返す。3回が上限で、環境変数では下げることだけできる）。
# 再試行の前には BUNKYO_RETRY_WAIT_SEC 秒（30秒以上）待つ。
BUNKYO_MAX_CONSECUTIVE_ERRORS = min(3, max(1, int(os.environ.get("BUNKYO_MAX_CONSECUTIVE_ERRORS", "3") or 3)))
BUNKYO_RETRY_WAIT_SEC = max(30.0, float(os.environ.get("BUNKYO_RETRY_WAIT_SEC", "30")))

# 試験用の絞り込み（本番は未設定＝制限なし）。施設数（今回の輪番の施設の先頭から）と、
# 表示期間の数（BUNKYO_MAX_PERIODS=1 なら今日から2週間分だけ）。
BUNKYO_MAX_FACILITIES = int(os.environ.get("BUNKYO_MAX_FACILITIES", "0") or 0)
BUNKYO_MAX_PERIODS = int(os.environ.get("BUNKYO_MAX_PERIODS", "0") or 0)

# ── ローカル実行専用（GitHub Actions 上では無視する） ──────────────────────────
# Playwright が想定する版のブラウザを `playwright install` できない環境で、手元にあるブラウザを使うときの実行ファイル。
LOCAL_CHROMIUM_EXECUTABLE = os.environ.get("LOCAL_CHROMIUM_EXECUTABLE", "")
