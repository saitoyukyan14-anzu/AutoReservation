# 空き施設検索 — 東京都の施設空き状況ビューア

けやきネット（世田谷区公共施設予約システム）で **用途「その他ダンス（音量大/小）」が使える
集会施設**（区民センター・地区会館・区民集会所）と、新宿区立地域センター受付システムで
**利用目的「ダンス（軽スポーツ）」が使える地域センターの部屋**の空き状況を定期取得し、
**希望の日時を複数候補入力して横断検索**できる静的サイトです（対象区の詳細は「対象区と取得条件」）。

- **スクレイパー**（Python。世田谷区は Playwright、新宿区は HTTP クライアント `requests`）が GitHub Actions で定期実行 → JSON生成
- **フロント**（Vite + React + TypeScript + Tailwind）が JSON を読み、検索UIを提供
- **GitHub Pages** で配信（サーバー不要・無料）
- 検索は **区（複数選択可）× 日付 × 時間帯** の候補を複数並べてOR検索。同じ部屋で間隔30分以内の
  連続した枠は **表示上1行にまとめる**（例: 12:30–14:30 と 15:00–17:00 → 12:30–17:00。予約は枠ごと）
- **施設情報（施設紹介ページへのリンク等）** は `web/public/data/facilities.json` で手動管理し、空き状況に結合して表示

```
facilities.json(施設リンク・手動管理) ─┐
                              ▼
  GitHub Actions（1日2回 10時/22時）: けやきネット取得 → web/public/data/*.json をcommit
                              ▼
  GitHub Pages: 静的サイトが JSON を読み込み、区/日時で絞り込み表示
```

## 対象区と取得条件

| 区 | 予約システム | 対象 | 条件 | 取得方式 |
| --- | --- | --- | --- | --- |
| 世田谷区 | [けやきネット](https://setagaya.keyakinet.net/Web/) | 区民センター・地区会館・区民集会所 | 用途「その他ダンス（音量大/小）」 | Playwright（5分割） |
| 新宿区 | [新宿区立地域センター受付システム](https://www.shinjuku.eprs.jp/chiiki/web/) | 地域センター10館・25室（検索結果に出る部屋すべて。葬儀兼用ホールも含む） | どこで「新宿区立地域センター(すべて)」・何をする「ダンス（軽スポーツ）」 | HTTP クライアント（分割なし） |

### 新宿区（`scraper/scrapers/shinjuku.py`）

- ログイン不要の空き照会だけを使う（トップ → 検索 → 部屋×週ごとに週表示の AJAX を1回）。
  ログイン・利用者登録・予約申込みはしない。文字コードは Windows-31J。
- **「空き」はシステムの表示どおり**（週表示の状態コード `0`＝「空き」のコマだけを取得）。コマは
  午前 9:00–12:00 / 午後１ 13:00–15:00 / 午後２ 15:15–17:15 / 夜間１ 17:30–19:30 / 夜間２ 19:45–21:45。
- ⚠️ **表示上の注意（UI に注意書きを出すこと）**: 新宿区の地域センターは、登録団体（コミュニティ活動）と
  一般（目的外利用）で申込開始日が違い、若松・落合第一・角筈は一般向けにオンライン抽選期間もある。
  システムはこうした「登録団体しか今は申し込めない時期」や「抽選期間中」のコマも「空き」と表示するため、
  **「空き」でも一般の利用者がすぐ申し込めるとは限らない**。また葬儀兼用ホール（牛込箪笥の菊・百合、
  榎町の地下ホールＡ、若松のＢ１ホール、戸塚の集会室１）は葬儀の利用が優先され、予約が取り消されることがある。
- 相手サーバーへの配慮: リクエスト間隔は3秒以上・1セッションで順番に取得（並列なし）。1回の実行の
  総リクエスト数に上限（既定 450 件。通常は約 330 件・所要約 25 分）。5xx・4xx は1回で、タイムアウト・
  エラー応答は3回続いたら打ち切り（トップ＋検索からのやり直しは1実行につき1回まで）。打ち切ったときは
  取得済み分を返し、取れなかった日付は `combine` が前回データで補う（時間切れと同じ扱い）。
  User-Agent は `AkiShisetsuKensaku/1.0 (+https://github.com/saitoyukyan14-anzu/AutoReservation)`。
- 主な設定（`scraper/config.py`。環境変数で上書き可）:

| 環境変数 | 既定 | 説明 |
| --- | --- | --- |
| `SHINJUKU_PURPOSE` | `40_20` | 「何をする」のコード（ダンス（軽スポーツ）） |
| `SHINJUKU_AREA` | `1000_0` | 「どこで」のコード（地域センターすべて） |
| `SHINJUKU_EXCLUDE_ROOMS` | （なし） | 除外する部屋の施設コード（カンマ区切り。例: 葬儀兼用ホールを除くなら `10100090,10100100,10200100,10300090,10500110`） |
| `SHINJUKU_REQUEST_DELAY_SEC` | `3.0` | リクエスト間隔（3 未満は 3 にする） |
| `SHINJUKU_MAX_REQUESTS` | `450` | 1実行の総リクエスト数の上限 |
| `SHINJUKU_USER_AGENT` | 上記 | User-Agent |
| `SHINJUKU_ONLY_ROOMS` / `SHINJUKU_MAX_WEEKS` | （なし）/ `0` | 試験用の絞り込み（部屋の施設コード／週数。例: `SHINJUKU_ONLY_ROOMS=10000010 SHINJUKU_MAX_WEEKS=1`） |
| `SHINJUKU_REQUEST_LOG` | （なし） | 全リクエストの記録先（TSV） |

## ディレクトリ構成

```
scraper/                 # Python スクレイパー
  scrapers/
    base.py              # 区スクレイパーの抽象基底（区追加の差込口）
    setagaya.py          # けやきネット（世田谷区）
    shinjuku.py          # 新宿区立地域センター受付システム（新宿区）
    __init__.py          # 有効スクレイパーのレジストリ
  config.py              # 設定（対象カテゴリ・出力先・取得日数 等）
  models.py              # Slot / Facility データモデル
  main.py                # エントリポイント
web/                     # フロント（Vite + React）
  public/data/           # ★ スクレイパーの出力先（availability.json / facilities.json）
  src/                   # UI
.github/workflows/
  scrape.yml             # 定期スクレイピング（1日2回 10:00/22:00 JST。区×shard の並列ジョブ）＋公開
  deploy.yml             # GitHub Pages へのビルド・デプロイ
```

## ローカル開発

### フロント

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

`web/public/data/*.json` のサンプルデータで動作確認できます。

### スクレイパー

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scraper/requirements.txt
python -m playwright install chromium

python scraper/main.py --months 2     # 全区を2ヶ月先末日まで取得 → web/public/data/*.json
HEADFUL=1 python scraper/main.py       # ブラウザを表示してデバッグ

# 1区だけ取得（part ファイルを出力）→ 結合して availability.json に反映
python scraper/main.py --ward setagaya
python scraper/main.py --combine       # 取れなかった部分は既存の availability.json から補う

# 1区を分割して取得（GitHub Actions の matrix と同じ動かし方）
python scraper/main.py --ward setagaya --shard-index 0 --shard-count 5
```

| オプション | 説明 |
| --- | --- |
| `--ward <区キー>` | その区だけ取得し `web/public/data/availability.part.<区キー>.<shard>.json` を出力（結合は `--combine`） |
| `--shard-index` / `--shard-count` | 区内の施設を分割して担当分だけ取得。shard 非対応の区に `--shard-count 2` 以上を指定するとエラー |
| `--time-budget-min N` | 時間予算（分、既定 330）。超えたらそこまでの取得分で打ち切って書き出す。0 以下で無制限 |
| `--combine` | part ファイルを結合して `availability.json` を更新。shard 欠け・時間切れで取れなかった部分は既存データから補い、警告を出す（下記） |

> ⚠️ 時間帯までのドリル取得は相手サーバーへのアクセスが多いため、`config.REQUEST_DELAY_SEC`
> で間隔を空けています。低頻度（1日1回程度）の利用にとどめてください。

## 施設情報（facilities.json）

`web/public/data/facilities.json` を直接編集して管理します（スクレイパーは書き換えません）。
1行＝1施設で、空き状況とは `ward + facility`（区名＋施設名）で突合します。

```json
{
  "facilities": [
    { "ward": "世田谷区", "facility": "桜丘区民センター", "url": "https://…", "note": "" }
  ]
}
```

- `facility` は空き状況（`availability.json`）に出てくる施設名と**完全一致**させる
- `url` は区公式等の施設紹介ページ。不明なら `null`

## デプロイ（GitHub Pages）

1. リポジトリ Settings > Pages > Build and deployment を **GitHub Actions** に設定
2. `main` に push すると `deploy.yml` がビルド・公開
3. `scrape.yml` が1日2回（10時/22時 JST）データを更新し、その後サイトを再公開

`scrape.yml` は「区 × shard」ごとに1ジョブで並列取得し（世田谷区は5分割）、最後に `combine`
ジョブが結合・コミットします。GitHub Actions は1ジョブ6時間が上限のため、各ジョブに
`timeout-minutes: 350` を設定し、スクレイパー側も時間予算（`TIME_BUDGET_MIN`、既定 330 分）を
過ぎたらそこまでの取得分で打ち切って結果を残します。

各 part には取得結果に加えて「その shard の担当施設名一覧（`facilities`）」と「全担当施設について
取得を完了した最終日（`completed_until`）」が記録され、`combine` は取れなかった部分だけを
前回の `availability.json` から補います（今日より前の枠は捨て、同じ枠は今回取得分を優先）。

| 状況 | 前回データから補う範囲 |
| --- | --- |
| 区の part が1つも無い（ジョブが全失敗・matrix 未登録） | その区の全施設 |
| 一部の shard の part が無い（失敗）／取得開始前に時間切れ（担当施設不明・0件） | 今回どの part の担当施設にも含まれない施設 |
| shard が時間切れで打ち切られた | その shard の担当施設の `completed_until` より後の日付（`null` なら全期間）。ただし今回取得できた（施設, 日付）は補わない |
| 担当施設を記録していない part（旧形式など）がある | 補わない（警告のみ。従来どおり） |

補った場合は件数と施設名を警告（Actions では実行サマリーのアノテーション）に出します。補った分は
今回取得していない古いデータなので、同じ警告が続く場合は失敗・打ち切りの原因を確認してください。
全 shard がそろった区では、どの shard の担当にも無い施設（前回データにしか無い施設）は引き継ぎません。

> ⚠️ スクレイピングは1回あたり数時間規模になり得ます。GitHub Actions の無料枠の都合上、
> **Public（公開）リポジトリ**での運用を推奨します（Public は Actions 実行時間が無制限）。

## 区を追加するには

1. `scraper/scrapers/<区名>.py` に `WardScraper` を継承したクラスを作成する
   - `key`（区キー。英小文字・数字・`_`。例: `"bunkyo"`）と `ward_name`（例: `"文京区"`）を設定し、`scrape()` を実装
   - 施設・期間・ページ等の長いループの区切りで `self.out_of_time("どこで")` を確認し、
     True ならそこまでの取得分を返して終了する（時間予算。ログに「時間切れで打ち切り」と出る）
   - 1区を複数ジョブに分割したい場合は `supports_shard = True` にし、対象施設の一覧を
     全 shard で同じ順序に並べてから `self.shard_items(施設一覧)` で担当分だけ取得する
     （非対応のまま `--shard-count 2` 以上で動かすとエラーになり、重複取得を防ぐ）
   - 担当施設が決まったら `self.set_assigned_facilities(施設名一覧)`（`Slot.facility` と同じ表記）を、
     期間の区切りごとに「全担当施設についてその日まで取り終えた」ら `self.mark_completed_until(日付)`
     を呼ぶ。`combine` が欠けた shard・時間切れで取れなかった部分を前回データから補うのに使う
     （呼ばない場合は補完されず、従来どおり警告のみ）
   - `__init__` を独自に定義する場合は `shard_index` / `shard_count` / `deadline` を受け取り
     `super().__init__(...)` に渡す
2. `scraper/scrapers/__init__.py` の `ALL_SCRAPERS` に追加
3. `.github/workflows/scrape.yml` の `jobs.scrape.strategy.matrix.include` に「区 × shard」の行を追加
   （`shard` は 0〜`shards`-1 をすべて並べる。shard 非対応なら `shards: 1` の1行）

   ```yaml
   - { ward: bunkyo, shards: 1, shard: 0 }          # 分割なし
   - { ward: shinjuku, shards: 2, shard: 0 }        # 2分割
   - { ward: shinjuku, shards: 2, shard: 1 }
   ```

4. ローカルで `python scraper/main.py --ward <区キー>` → `python scraper/main.py --combine` を実行して確認

matrix に追加し忘れた区は part が出ないため、`combine` が前回データを引き継ぎつつ警告を出します。
出力（`Slot`）の形式は全区共通なので、フロントは無改修で新しい区に対応します。

## 注意

空き状況は参考情報です。実際の予約・最新状況は必ず各区の予約システム
（[けやきネット](https://setagaya.keyakinet.net/Web/)、
[新宿区立地域センター受付システム](https://www.shinjuku.eprs.jp/chiiki/web/)）でご確認ください。
新宿区の「空き」には、登録団体のみ申込可の時期や抽選期間中の枠も含まれます（上記「新宿区」参照）。
