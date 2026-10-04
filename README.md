# けやき空き — 東京都の施設空き状況ビューア

けやきネット（世田谷区公共施設予約システム）で **用途「その他ダンス（音量大/小）」が使える
集会施設**（区民センター・地区会館・区民集会所）の空き状況を定期取得し、
**希望の日時を複数候補入力して横断検索**できる静的サイトです。

- **スクレイパー**（Python + Playwright）が GitHub Actions で定期実行 → JSON生成
- **フロント**（Vite + React + TypeScript + Tailwind）が JSON を読み、検索UIを提供
- **GitHub Pages** で配信（サーバー不要・無料）
- **施設情報（施設紹介ページへのリンク等）** は `web/public/data/facilities.json` で手動管理し、空き状況に結合して表示

```
facilities.json(施設リンク・手動管理) ─┐
                              ▼
  GitHub Actions（1日2回 10時/22時）: けやきネット取得 → web/public/data/*.json をcommit
                              ▼
  GitHub Pages: 静的サイトが JSON を読み込み、区/日時で絞り込み表示
```

## ディレクトリ構成

```
scraper/                 # Python スクレイパー
  scrapers/
    base.py              # 区スクレイパーの抽象基底（区追加の差込口）
    setagaya.py          # けやきネット（世田谷区）
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
python scraper/main.py --combine       # part が無い区は既存の availability.json から引き継ぐ

# 1区を分割して取得（GitHub Actions の matrix と同じ動かし方）
python scraper/main.py --ward setagaya --shard-index 0 --shard-count 5
```

| オプション | 説明 |
| --- | --- |
| `--ward <区キー>` | その区だけ取得し `web/public/data/availability.part.<区キー>.<shard>.json` を出力（結合は `--combine`） |
| `--shard-index` / `--shard-count` | 区内の施設を分割して担当分だけ取得。shard 非対応の区に `--shard-count 2` 以上を指定するとエラー |
| `--time-budget-min N` | 時間予算（分、既定 330）。超えたらそこまでの取得分で打ち切って書き出す。0 以下で無制限 |
| `--combine` | part ファイルを結合して `availability.json` を更新。part が1つも無い区は既存データを引き継ぎ、shard 欠け・時間切れは警告を出す |

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
過ぎたらそこまでの取得分で打ち切って結果を残します。一部の区のジョブが全て失敗しても、
その区は前回のデータを引き継いで他の区だけ更新します。

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

空き状況は参考情報です。実際の予約・最新状況は必ず
[けやきネット](https://setagaya.keyakinet.net/Web/) でご確認ください。
