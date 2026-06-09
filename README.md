# けやき空き — 東京都の施設空き状況ビューア

けやきネット（世田谷区公共施設予約システム）で **用途「その他ダンス（音量大/小）」が使える
集会施設**（区民センター・地区会館・区民集会所）の空き状況を定期取得し、
**希望の日時・広さを複数候補入力して横断検索**できる静的サイトです。

- **スクレイパー**（Python + Playwright）が GitHub Actions で定期実行 → JSON生成
- **フロント**（Vite + React + TypeScript + Tailwind）が JSON を読み、検索UIを提供
- **GitHub Pages** で配信（サーバー不要・無料）
- **広さ／定員** は Google スプレッドシートで手動管理し、空き状況に結合して表示

```
Googleスプレッドシート(広さ) ─┐
                              ▼
  GitHub Actions（1日2回 10時/22時）: けやきネット取得 → web/public/data/*.json をcommit
                              ▼
  GitHub Pages: 静的サイトが JSON を読み込み、区/日時/広さで絞り込み表示
```

## ディレクトリ構成

```
scraper/                 # Python スクレイパー
  scrapers/
    base.py              # 区スクレイパーの抽象基底（区追加の差込口）
    setagaya.py          # けやきネット（世田谷区）
    __init__.py          # 有効スクレイパーのレジストリ
  config.py              # 設定（対象カテゴリ・出力先・取得日数 等）
  facilities.py          # スプレッドシート(CSV)→ facilities.json
  models.py              # Slot / Facility データモデル
  main.py                # エントリポイント
web/                     # フロント（Vite + React）
  public/data/           # ★ スクレイパーの出力先（availability.json / facilities.json）
  src/                   # UI
.github/workflows/
  scrape.yml             # 定期スクレイピング（1日2回 10:00/22:00 JST）＋公開
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

python scraper/main.py --months 2     # 2ヶ月先末日まで取得 → web/public/data/*.json
HEADFUL=1 python scraper/main.py       # ブラウザを表示してデバッグ
```

> ⚠️ 時間帯までのドリル取得は相手サーバーへのアクセスが多いため、`config.REQUEST_DELAY_SEC`
> で間隔を空けています。低頻度（1日1回程度）の利用にとどめてください。

## 広さデータベース（Google スプレッドシート）

1. スプレッドシートに次の列を用意（日本語ヘッダ可）:
   `区, 施設, 部屋, 面積, 定員, 備考`（= `ward, facility, room, area_sqm, capacity, note`）
2. 「ファイル > 共有 > ウェブに公開」で **CSV** のリンクを取得
3. そのURLを GitHub リポジトリの Secrets に `FACILITIES_SHEET_CSV_URL` として登録
   （未設定なら `web/public/data/facilities.json` を手動編集して使うことも可能）

`区 + 施設 + 部屋` をキーに空き状況と結合されます。定員はけやきネット側にも載るため、
スプレッドシートでは主に **面積（㎡）** を埋めれば十分です。

## デプロイ（GitHub Pages）

1. リポジトリ Settings > Pages > Build and deployment を **GitHub Actions** に設定
2. `main` に push すると `deploy.yml` がビルド・公開
3. `scrape.yml` が1日2回（10時/22時 JST）データを更新し、その後サイトを再公開

> ⚠️ スクレイピングは1回あたり数時間規模になり得ます。GitHub Actions の無料枠の都合上、
> **Public（公開）リポジトリ**での運用を推奨します（Public は Actions 実行時間が無制限）。

## 区を追加するには

1. `scraper/scrapers/<区名>.py` に `WardScraper` を継承したクラスを作成し `scrape()` を実装
2. `scraper/scrapers/__init__.py` の `ALL_SCRAPERS` に追加

出力（`Slot`）の形式は全区共通なので、フロントは無改修で新しい区に対応します。

## 注意

空き状況は参考情報です。実際の予約・最新状況は必ず
[けやきネット](https://setagaya.keyakinet.net/Web/) でご確認ください。
