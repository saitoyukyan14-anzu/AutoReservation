# 新宿区「新宿区立地域センター受付システム」空き状況取得 実装案

作成: 4（空き施設調査方法検討担当） / 調査日時: 2026-10-05 03:44–03:56 JST（予約システムへのアクセスは 03:47:56–03:56:26）
凡例: **【確認】**=実地アクセス・一次資料で確認済み / **【推測】**=根拠はあるが未検証 / **【不明】**=調べていない・確認できなかった

---

## 0. 要約（実装担当向け 10 行）

1. 予約システムは `https://www.shinjuku.eprs.jp/chiiki/web/`。富士通系「e-Pares」製品（JS のヘッダに `e-Pares (予約) … Fujitsu Oita Software Laboratories`）。JSP（Struts の `*.do`）でサーバー側が HTML を生成し、空き状況だけ jQuery の AJAX（JSON）で取る。文字コードは **Windows-31J**（HTML も JSON も）。世田谷・文京とは別製品なので既存コードは流用できない。【確認】
2. **robots.txt は存在しない（404）**。区公式サイト・システム内「ご利用ガイド」・操作マニュアルに、自動取得を禁止・制限する記載は見当たらない。空き照会はログイン不要で、CAPTCHA は出ない。【確認（読んだ範囲）】
3. 流れ: トップ画面 →[どこで=新宿区立地域センター(すべて)・何をする=ダンス（軽スポーツ）で検索]→「空き状況画面（施設ごと）」→ 部屋ごとに **週表示 AJAX**（`rsvWOpeInstSrchVacantAjaxAction.do`）を1回呼ぶと **1部屋×7日×5コマ** の空きが JSON で返る。世田谷・文京のような「時間帯別画面へのドリルダウン」や「10件選択」の制約はない。【確認】
4. 全館で検索すると、結果画面の「施設」プルダウン `#facility-select` に **ダンス（軽スポーツ）で使える全25室（10館すべて）** が `data-bldcd` 付きで一度に出る（1リクエストで対象部屋の全体が分かる）。【確認】
5. コマは全室共通: 午前 9:00–12:00 / 午後１ 13:00–15:00 / 午後２ 15:15–17:15 / 夜間１ 17:30–19:30 / 夜間２ 19:45–21:45。JSON の各セルに `startTime`/`endTime`（例 1515/1715）があるので、時刻はそれを使う。【確認（JSON と区公式の利用方法ページで一致）】
6. 週表示の状態コード: `0`=空き / `210`=予約あり / `1`=保守日 / `2`=休館日 / `700`=受付期間外。**`status==0` のコマだけを Slot にする**。現時点（10/05）で 12/31 までは受付中の表示、2027/1/1 以降は受付期間外。【確認】
7. **推奨は HTTP クライアント方式（A案）**: トップ取得1回＋検索1回＋「部屋×週」ごとに AJAX 1回。静的ファイルが `Cache-Control: no-store` なので、ブラウザ方式（B案）では画面を開くたびに静的ファイル17〜20件を取り直す。A案は CI でもローカルでも TLS の特別な設定が要らない。B案（Playwright）でも実装できるよう、セレクタも §4-9 に記した。【設計】
8. shard の単位は**館**（10館、館コード順に `shard_items`）。取得は「週ごと（外側）×部屋（内側）」の順にし、1週分を全担当部屋で取り終えるたびに `mark_completed_until(その週の最終日)` を呼ぶ。担当施設名は館名（Slot.facility と同じ）で `set_assigned_facilities` に渡す。【設計】
9. 所要時間の見積もり（待機3秒）: 25室×最大14週 ≒ 330〜350リクエスト、**約23〜25分**。→ **shards=1 で 330分の予算に十分収まる**。1回の実行の通信量は約2.5MB。【推測（実測の応答時間 平均1.07秒から算出）】
10. ユーザーの承認が必要なこと: (a) 対象範囲（推奨: 検索に出る全25室＝A案。葬儀兼用ホール5室を除く B案もあり）、(b) 取得方式（推奨: HTTP クライアント）、(c) 「一般の受付開始前でも登録団体は予約できる日」の空きをそのまま出してよいか（§2-4）。

---

## 1. 予約システムの特定・到達性・規約

| 項目 | 内容 | 出典・確度 |
|---|---|---|
| システム名 | 新宿区立地域センター受付システム（画面上のロゴ表記は「しせつよやく」） | 区公式 https://www.city.shinjuku.lg.jp/chiiki/community01_002070.html 、https://www.city.shinjuku.lg.jp/reserve_facilities.html 【確認】 |
| URL | `https://www.shinjuku.eprs.jp/chiiki/web/`（2025-02-13 に URL 変更） | 同上 【確認】 |
| 資料 | 操作マニュアル https://www.shinjuku.eprs.jp/manual/chiiki/chiiki_manual.pdf（0.1版 2024年12月。第2章が空き照会）、区の FAQ https://www.city.shinjuku.lg.jp/kanko/community01_001039.html 、料金表 https://www.city.shinjuku.lg.jp/content/000423351.pdf | 【確認】 |
| 技術構成 | e-Pares（富士通大分ソフトウェアラボラトリ）。JSP/Struts（`*.do`）、jQuery 4.0。画面遷移は `doAction(form, action)` で form1 を POST、空き状況は `doAjax()`（jQuery `$.ajax`、POST、dataType json、`traditional:true`）。サーバーは nginx、Cookie `JSESSIONID`（`Path=/chiiki/web; HttpOnly; SameSite=Lax`） | 実地（hdr/002.hdr、js/paw_doaction.js、js/paw_doajax.js、js/prwrc2000.js）【確認】 |
| 文字コード | HTML `text/html;charset=Windows-31J`、AJAX `application/json;charset=Windows-31J`（JSON 内に cp932 の生の日本語が入る。一部は `\uXXXX`） | 応答ヘッダ 【確認】 |
| 到達性 | HTTP 200。利用は 24 時間（区公式「インターネットによる受付は年末年始も行います」） | 実地・区公式 【確認】 |
| ログイン | **空き照会は不要**（マニュアル第2章「利用者登録されていない方も検索することができます」）。予約・抽選はログインが必要 | 【確認】 |
| CAPTCHA | 照会の経路（トップ→検索→施設ごと→週表示 AJAX）には出ない | 実地 【確認】 |
| robots.txt | `https://www.shinjuku.eprs.jp/robots.txt` は **404 Not Found**（`Server: nginx`、`X-Content-Type-Options`/`X-XSS-Protection` など予約システム本体の応答と同じヘッダ構成。本文は Apache 形式の 404 ページ）。発生源は予約システムのサーバーと判断（同じヘッダ構成のため）。ただし本文の形式が nginx 既定と違う点の理由は【不明】 | hdr/001.hdr 【確認】 |
| 利用規約 | システム内に規約ページへのリンクはない（トップ・ご利用ガイドのリンク先はログイン・抽選・ご利用ガイド・館施設一覧・サイトマップのみ。フッターは空）。「ご利用ガイド」画面の本文は利用者登録と予約の説明だけで、自動取得の記載なし。マニュアルの「利用規約」は**予約申込み時に一部の館で表示される施設利用の規約**（ログイン後の画面。未閲覧）。区公式「本ホームページについて」は区ホームページの著作権・リンクの方針で、予約システムの自動取得には触れていない | html/u02_guide.html、manual.txt、city/about.html 【確認（読んだ範囲）】／申込み時の利用規約の本文は【不明】 |
| 同じホストの別システム | `https://www.shinjuku.eprs.jp/regasu/web/`（レガス新宿 施設予約システム。生涯学習館・区民ホール・スポーツセンター等）。今回は**一切アクセスしていない** | 区公式 reserve_facilities.html 【確認（存在のみ）】 |

### 調査方法と TLS について【確認】
- 予約システムへのアクセスは、ブラウザではなく **curl**（`scratchpad/shinjuku/sjfetch.py` 経由）で行った。curl はサンドボックスのプロキシ CA を含む CA バンドルで**通常どおり証明書を検証**しており、検証を無効にするオプションは使っていない。
- 先に、TLS 関連の引数なしの Chromium（headless_shell-1194）で第三者ホスト example.com に接続したところ `ERR_CERT_AUTHORITY_INVALID` になった（OS/NSS の信頼ストアだけではプロキシ CA を信頼しない）。そのため `--ignore-certificate-errors-spki-list` を使う代わりに curl を選んだ。
- 参考（ローカルで B案を動かす場合）: `--ignore-certificate-errors-spki-list=<SPKI>` は「**指定した公開鍵（ここではプロキシ CA の鍵）を含む証明書チェーンについては、証明書エラー（ホスト名の不一致なども含む）を無視する**」という Chromium の設定であり、CA を信頼ストアに追加するのとは別物（検証を部分的に無効化する）。GitHub Actions ではプロキシがないので不要。

---

## 2. 対象範囲の案（★ユーザー承認待ち）

### 2-1. 利用目的【確認・承認済み】
- 「何をする」`#purpose` の **`40_20` = ダンス（軽スポーツ）**（分類「ダンス・スポーツ」）。送信時は隠しフィールド `selectPpsClPpscd=40_20`。
- 目的は1つしか選べない（select）。検索結果の部屋は**選んだ目的が使える部屋だけ**になる（四谷は10室中「多目的ホール」だけ）。
- 参考: 同じ分類に エアロビクス(40_30)、社交ダンス(40_50)、バレエ(40_60)、フラダンス(40_70)、日舞(40_80)、民族舞踊(40_90) などがある。これらを足すと部屋が増えるかは【不明（照会していない）】。

### 2-2. 対象施設（館）【確認】
- このシステムで照会できるのは **新宿区立地域センター10館だけ**（「どこで」の選択肢、「館・施設一覧」とも10館）。地域センター以外の集会施設はこのシステムにはない。
- 生涯学習館・区民ホールなど地域センター以外の集会施設は、同じホストの**別システム「レガス新宿 施設予約システム」**（`/regasu/web/`）にある。対象に加えるなら別途の調査とユーザー承認が必要（今回は未調査）。

### 2-3. ダンス（軽スポーツ）で使える部屋（全館検索の結果）【確認】
館コード・施設コードは検索結果画面の値。部屋名は画面の表示から定員「（ＮＮ）」を除いたもの。

| 館（館コード） | 部屋（施設コード、定員） |
|---|---|
| 四谷地域センター（1000） | 多目的ホール（10000010、100） |
| 牛込箪笥地域センター（1010） | コンドル（10100010、100）、バラＡ（10100030、30）、バラＢ（10100040、30）、**菊**（10100090、40）、**百合**（10100100、40） |
| 榎町地域センター（1020） | 多目的ホール（10200010、120）、軽音楽室（10200070、18）、小会議室（10200090、12）、**地下ホールＡ**（10200100、54） |
| 若松地域センター（1030） | ３Ｆホール（10300030、130）、第１集会室（10300040、70）、**Ｂ１ホール**（10300090、40） |
| 大久保地域センター（1040） | 多目的ホール（10400010、120） |
| 戸塚地域センター（1050） | 多目的ホール（10500010、180）、**集会室１**（10500110、42） |
| 落合第一地域センター（1060） | ４Ｆホール（10600010、150）、第二集会室（10600060、24） |
| 落合第二地域センター（1070） | 多目的ホール（10700010、100）、大会議室Ａ（10700080、24）、大会議室Ｂ（10700090、24） |
| 柏木地域センター（1080） | 会議室２（10800010、30）、多目的ホール（10800040、120）、会議室１Ａ（10800070、24） |
| 角筈地域センター（1090） | レクリエーションホール（10900060、90） |

計 **10館・25室**。太字は区の料金表で「葬儀ができる多目的ホール」とされている部屋（牛込箪笥の菊・百合は名称で、榎町の地下ホールＡ（料金表「ホールA 54名」）・若松のＢ１ホール（「B1ホール 40名」）・戸塚の集会室１（「集会室1」。定員は料金表39名・システム42名で表記が違う）は名称と定員の対応で判断）【確認（料金表）／対応付けの一部は推測】。区の案内では葬儀の利用が優先され、システムのお知らせにも「葬儀が入った場合は…キャンセル」とある（落合第一の地下ホールについての記載）。

案:
- **A案（推奨）: 検索に出る全25室**。区のシステム自身が「ダンス（軽スポーツ）で使える」としている部屋をそのまま対象にする。世田谷で「その他ダンス」で検索した結果を使っているのと同じ考え方。
- B案: A案から葬儀兼用ホール5室（菊・百合・地下ホールＡ・Ｂ１ホール・集会室１）を除いた **20室**。予約が葬儀で取り消される可能性を嫌う場合。
- 対象外にしたもの（参考）: 榎町の大会議室Ａ・Ｂは、ダンスで2室一体で使う場合は目的「その他（一体利用）」(50_45) で予約する決まり（システムのお知らせ 2025/02/14）。ダンス（軽スポーツ）の検索には出ない。対象にするなら目的 50_45 での追加検索が必要（【不明】未照会）。

部屋の絞り込みは、検索結果の `#facility-select` をそのまま使い、除外したいときだけ除外リスト（施設コード）を設定で持つのを推奨（§4-1）。検索結果に知らない部屋が出たり、想定の部屋が消えたりしたらログに出す。

### 2-4. 「空き」の意味についての注意（★ユーザー判断）【確認＋推測】
- 区の受付ルール: 登録団体（コミュニティ活動）は「利用月の2か月前の第1土曜日（窓口）、インターネットはその翌日9時から」、一般（目的外利用）は「利用月の前月10日（窓口）、インターネットはその翌日9時から」。若松・落合第一・角筈は一般向けに「前月2〜9日のオンライン抽選」がある（区公式 community01_002070.html、community01_000104.html）。
- 実データ: 10/05 時点で 12月のコマも「空き」と出ている（月表示で一部空き・全て空き、週表示 status 0）。若松の11月（一般は抽選申込期間中）も週表示は普通の「空き」で、抽選専用の状態は出なかった。【確認】
- つまり、システムの「空き」には**登録団体しか今は申し込めない日**（一般は受付開始前・抽選中）が含まれる【推測（受付ルールからの推定）】。既定ではシステムの表示どおり「空き」を出す案とし、必要なら「一般の受付期間内の日だけに絞る」設定を後から足す。README か画面の注意書きで「登録団体と一般で申込開始日が違う」旨を示すことを推奨。

---

## 3. 画面遷移・通信の実地確認結果（セレクタ・通信・データ構造）

ベース URL: `BASE = "https://www.shinjuku.eprs.jp/chiiki/web/"`。画面遷移はすべて form1（`<form name="form1" method="post" action="index.jsp" onsubmit="return false;">`）を JS で `action` を差し替えて submit する方式。ブラウザの戻る・進むは使わない。

### 3-1. トップ画面（`GET /chiiki/web/`、`index.jsp` でも同じ。画面ID pawab2000）【確認】
- 空き状況検索のフォーム（`#free-search`）:
  - いつ: ラジオ `input[name=date]`（1 今日 / 2 明日 / **3 1週間（既定）** / 4 1か月）、開始日 `#daystart`（`input[type=date][name=daystart]`、既定は今日 `YYYY-MM-DD`）、期間 `select#days[name=days]`（1/2/3/7/31。既定7）、曜日 `input[name=dayofweek]`（1〜7, 9=祝）、時間帯 `input[name=timezone]`（9 終日 / 1 午前 / 2 午後 / 3 夜間）
  - どこで: `select#bname`（**name 属性なし**）。値は `1000_0`=新宿区立地域センター(すべて)、`1000_1000`=四谷 … `1000_1090`=角筈。選ぶと `filterInst()` が `rsvWOpeUnreservedSearchBuildAjaxAction.do` で `#iname`（施設）の選択肢を取る（「すべて」のときは通信せず `#iname` を空にする）
  - 何をする: `select#purpose`（**name 属性なし**）。ダンス（軽スポーツ）は `40_20`
  - 検索ボタン: `#btn-go`（`onclick="doSearch(document.form1, gRsvWOpeInstSrchVacantAction)"`）
- `doSearch()`（js/prwre1000.js）の処理: `#bname`→隠し `#selectAreaBcd`、`#iname`→`#selectIcd`、`#purpose`→`#selectPpsClPpscd` に値を写す。曜日が未選択なら `dayofweekClearFlg=1`、時間帯が未選択なら `timezoneClearFlg=1`。`storeCookie()`（検索条件をブラウザの Cookie に保存。実装は未読）のあと form1 を `rsvWOpeInstSrchVacantAction.do` へ POST。
- 入力エラー時は `showAlert()` のモーダル（館未指定 e430000、目的未指定 e430010、日付誤り e430020）。

### 3-2. 検索（`POST /chiiki/web/rsvWOpeInstSrchVacantAction.do`）【確認】
- 送った本文（form1 の全項目。Windows-31J で URL エンコード。主な項目）:
  `date=3&daystart=2026-10-05&days=7&dayofweekClearFlg=1&timezoneClearFlg=1&selectAreaBcd=1000_0&selectIcd=&selectPpsClPpscd=40_20&…(e430000 などの文言の隠し項目)…&displayNo=pawab2000&displayNoFrm=pawab2000`
  （実際の送信内容: `shinjuku/forms/u09_search_all.txt`。form1 の項目はトップ画面の HTML から `formbuild.py` と同じ規則で組み立てた）
- 応答: 「空き状況画面（施設ごと）」（`<main id="prwrc1000">`、hidden `displayNo=prwrc2000`）。約35KB。
- **注意: 結果画面（日付順タブ）から条件を変えて「再検索」した POST（form1 の displayNo=prwrc1000）は、条件が反映されず前回と同じ画面が返った**（u07。原因は【不明】）。→ 条件を変えるときは**トップ画面から検索し直す**（u08→u09 で確認済み）。

### 3-3. 空き状況画面（施設ごと）【確認】
- 館: `select#mansion-select[name=iniBCd]`（value=館コード、text=館名）。全館検索で10館。
- 施設（部屋）: `select#facility-select[name=iniICd]`。先頭は `value="0"`「選択してください。」、以降 `<option data-bldcd="1030" value="10300030">３Ｆホール（１３０）</option>` のように**全館の対象部屋**が並ぶ（サーバーが生成した HTML の時点）。
- 隠し項目: `selectBldCd`（初期=先頭の館）、`selectInstCd`（初期=先頭の部屋）、`useDay`（=開始日 `YYYYMMDD`）、`selectPpsClsCd=40`、`selectPpsCd=20`、`viewDay1〜7`。
- 読み込み時に JS が自動で次の2つの AJAX を送る（ブラウザの場合）:
  1. `selectBuild2()` → `POST rsvWOpeInstSrchVacantBuildAjaxAction.do`（`displayNo=prwre1000&bldCd=<館>`）→ `{"results":[{"icd":"10000010","iname":"多目的ホール（１００）"}]}`。**検索の目的で絞られた、その館の部屋だけ**が返り、`#facility-select` がその館の部屋だけに置き換わる。
  2. `changeIname2()` → 週表示 AJAX（§3-4、`transVacantMode=11`）。
- 画面の部品: 週表示の表 `table#week-info`（AJAX で生成）、ボタン `#last-week`（前週 `getWeekInfoAjax(3,0,0)`）、`#yesterday`（前日 12）、`#tomorrow2`（翌日 13）、`#next-week`（翌週 4）、月表示の開閉 `button[data-target="#monthly"]`、`#last-month`/`#next-month`（月表示の前月 1 / 翌月 2）、タブ「日付順」（`doAction(form1, gRsvWOpeUnreservedDailyAction)`）。読み込み中は `#loadingweek`。
- 凡例（ポップオーバーの中身、通信なし）: 予約可能＝空き／選択中・数字＝面数、予約不可能＝一般開放／予約あり／受付期間外／時間外／休館／保守／雨天。
- 該当なしのとき: `#aki-notfound`「指定条件に該当する空き施設が見つかりませんでした。」が表示される（JS で `#facility-select` の値が 0 のとき）。

### 3-4. 週表示 AJAX（`POST /chiiki/web/rsvWOpeInstSrchVacantAjaxAction.do`）【確認】
- 本文（application/x-www-form-urlencoded、ASCII のみ）:
  `displayNo=prwrc2000&useDay=20261005&bldCd=1030&instCd=10300030&transVacantMode=11&clearFlag=0`
  - `transVacantMode`: **11=useDay から7日**（部屋の選択時・月表示の日付クリック時）、**4=翌週**（useDay=いま表示中の週の初日）【確認】、3=前週、12=前日、13=翌日【未確認（JS の onclick より）】
  - ブラウザ（jQuery）は `X-Requested-With: XMLHttpRequest` を付ける。今回もそれに合わせた。
- 応答（JSON、Windows-31J、約7KB）:
```
{ "title":"時間帯", "dayNum":7, "lendType":1, "imstRsvMediaFlg":3, "equipFg":0,
  "beforeStartDay":20261004, "nextStartDay":20261006, "beforeWeekStartDay":20260928, "nextWeekStartDay":20261012,
  "weekDay":[ {"useDay":20261005,"dispMonth":"10月","dispDay":"5日","dispWeekDay":"月曜","holiday":"月曜","holidayFlg":1}, …7件 ],
  "result":[ {"tzoneNo":10,"tzoneName":"午前",
              "timeResult":[ {"useDay":20261005,"status":210,"alt":"予約あり","imgURL":"image/calendar/svg/calendar_full_outline.svg",
                              "startTime":900,"endTime":1200,"rsvNum":0,"selectNum":0}, …7件 ]},
             {"tzoneNo":20,"tzoneName":"午後１",…}, {"tzoneNo":30,"tzoneName":"午後２",…},
             {"tzoneNo":40,"tzoneName":"夜間１",…}, {"tzoneNo":50,"tzoneName":"夜間２",…} ] }
```
  - `holidayFlg`: 1=平日 / 2=土 / 3=日 / 4=祝日（`holiday` に「祝日」）
  - `lendType`: 1=時間帯貸し（週表示を取った3室＝四谷 多目的ホール・若松 ３Ｆホール・榎町 軽音楽室はすべて 1）。2/3/4（時間貸し・面数貸し）の部屋は対象25室には無いと推測【推測（3室で確認）】
  - 次の週がないときは `nextWeekStartDay` が `"0"`（JS がボタンを隠す条件）。12/28 の週でも 20270104 が返った（受付期間外の週にも移動できる）
  - エラー時は `{"ErrManager":{"message":"…"}}`（js/paw_doajax.js の分岐。実際には出ていない）【未確認】
- 状態コード（`status` と `alt`）:

| status | alt（画面の表示） | 扱い | 出現 |
|---|---|---|---|
| 0 | 空き | **Slot にする** | 【確認】 |
| 210 | 予約あり | 対象外 | 【確認】 |
| 1 | 保守日 | 対象外 | 【確認】（同じ日の中で一部のコマだけ保守日のこともある） |
| 2 | 休館日 | 対象外 | 【確認】（年末 12/29–31、若松の11月第3日曜） |
| 700 | 受付期間外 | 対象外 | 【確認】（過去日、2027/1/1 以降） |
| ? | 一般開放／時間外／雨天 | 対象外 | 凡例にあるが今回は出ず、コードは【不明】 |

- 部屋の選択は `#facility-select` の変更（`selectInst(2)` → `getWeekInfoAjax(11,1,0)`）。**このとき useDay は表示中の週のまま**（JS の実装）。
- 館 AJAX（§3-3 の1）を省いて、別の館の部屋（若松 ３Ｆホール、榎町 軽音楽室）の週表示を直接要求しても正常に返った（U10, U16）。【確認（全館検索後のセッションで）】

### 3-5. 月表示 AJAX（`POST /chiiki/web/rsvWOpeInstSrchMonthVacantAjaxAction.do`）【確認】
- 本文: `displayNo=prwrc2000&useDay=20261005&bldCd=1030&instCd=10300030&transVacantMode=11`（11=useDay の月、1=前月、2=翌月。前月・翌月は**サーバー側のセッションが覚えている月**から動く）
- 応答: `{"title":"2026年10月","dayNum":31,"startWeek":5,"beforeMonthStartDay":…,"nextMonthStartDay":…,"weekDay":[曜日見出し],"result":[{"dayYMD":20261001,"status":700,"alt":"受付期間外","day":1,"week":5,"holiday":"","holidayFlg":1,"imgURL":…}, …]}`
- 日ごとの状態: `0`=全て空き / `100`=一部空き / `200`=予約あり / `2`=休館日 / `700`=受付期間外。
- 実装では使わない（週表示だけで足りる）。「空きのある週だけ週表示を取る」絞り込みに使えるが、空きのない週は少なく効果が小さい。

### 3-6. 日付順タブ（`POST rsvWOpeUnreservedDailyAction.do` → 一覧は AJAX `rsvWOpeUnreservedSearchAjaxAction.do`）【確認】
- 画面「空き状況画面（日付順）」は枠だけで、読み込み時に `loadNext()` が AJAX を1回送る。本文は画面に埋め込まれた `formData`（`{"date":3,…,"offset":0,"limit":5,"days":7,"selectAreaBcd":"1000_1000",…}`）。
- 応答: `{"all":3,"next":4,"hitcnt":3,"results":[{"useYmd":20261005,"bcd":"1000","bcdNm":"四谷地域センター","icd":"10000010","icdNm":"多目的ホール（１００）","sJTime2":"15:15","eJTime2":"19:30","timeZoneNos":"30|40","sTimeZones":"1515|1730","eTimeZones":"1715|1930",…}]}`。**同じ部屋・同じ日の連続する空きコマを1件にまとめた**形式で、内容は週表示と一致した。
- ただし1回に `limit=5` 件ずつ、画面の上限 `listMax=100` 件（それ以上は「さらに表示」できない）。全館×数か月の空きは数百件になるので、**取得には使わない**。

### 3-7. セッション・文字コード・その他【確認／一部推測】
- セッション: Cookie `JSESSIONID`（初回 GET で発行。以後の応答でも同じ値を再発行）。照会の途中でセッションが切れた例はなかった（約9分間の調査で）。タイムアウトの長さは【不明】。トップ画面の `startInitTimer()` の中身（時間切れの警告や定期通信をするか）は JS を読んでいないので【不明】（ブラウザ方式でのみ関係）。
- form の送信は Windows-31J で URL エンコードする（ブラウザの既定動作。隠し項目に日本語の文言がある）。AJAX は jQuery が UTF-8 でエンコードするが、送る値は ASCII だけ。
- 静的ファイル（JS など）は `Cache-Control: private, no-store, no-cache, must-revalidate, max-age=0`。ブラウザは画面を開くたびに取り直す。各画面の静的ファイル数（HTML から数えた。CSS が読むフォントは含まない）: トップ17、施設ごと20、日付順17。
- 画面のモーダル: 「ご利用環境」（`#envModal`）、「お知らせ」（`#modal-news`）はクリックしない限り出ない。照会の経路では自動で出るモーダル・確認ダイアログはなかった。

### 3-8. 実測値【確認】

| 操作 | 応答時間（curl、サンドボックスのプロキシ経由） | 応答サイズ |
|---|---|---|
| 画面（トップ・検索結果・日付順など）8件 | 1.23〜1.59 秒（平均 1.43） | 27〜36 KB |
| 週表示 AJAX 6件 | 0.96〜1.36 秒（平均 1.07） | 7.0〜7.2 KB |
| 月表示 AJAX 3件 | 0.91〜1.26 秒 | 5.2〜5.4 KB |
| 館 AJAX 1件 / 日付順 AJAX 1件 | 1.03 秒 / 1.12 秒 | 65 B / 1.6 KB |

- どの照会でも 5xx・タイムアウトは出なかった。照会は「1館（1部屋）×1週間」→「全館の検索＋1部屋×1週間ずつ」の順に広げ、1回の要求で取るデータ量は常に「1部屋×7日」（月表示は1部屋×1か月）に留めた。

---

## 4. 実装案

### 4-0. 方式の比較と推奨
| | **A案（推奨）HTTP クライアント** | B案 Playwright（画面を操作） |
|---|---|---|
| 送る通信 | トップ GET 1 ＋ 検索 POST 1 ＋ 週表示 AJAX（部屋×週） | 左に加えて、画面2つ分の静的ファイル約37件（no-store のため毎回）、館を切り替えるたびの館 AJAX、`startInitTimer` 等の自動通信（あれば） |
| 1回の実行（25室×13週） | 約 327 件 | 週ごとに館を切り替える順序だと約 495 件（館 AJAX 10件×13週が増える）。`page.evaluate` で隠し項目を書き換えてアプリの `getWeekInfoAjax(11,1,0)` を呼ぶなら約 365 件 |
| 速さ | 速い（描画なし） | 遅め |
| TLS | CI・ローカルとも通常の検証で動く | CI は問題なし。ローカルのサンドボックスでは SPKI フラグが必要（§1） |
| 壊れやすさ | フォーム項目名・JSON のキーが変わると壊れる（トップ画面の form1 を毎回 HTML から組み立てれば、項目の追加には追従できる） | 画面の JS が変わっても動きやすい。JSON は応答から読むのでデータ部分の壊れやすさは同じ |
| 依存 | `requests` を追加（または標準の `urllib`＋`http.cookiejar`） | 既存の playwright のみ |

推奨理由: データはすべて「サーバー生成の HTML」と「JSON の AJAX」で取れ、ブラウザでしか得られない情報がない。A案は通信が最少で、相手への負荷が一番小さい。robots.txt がなく、規約にも自動取得の禁止がないので、ブラウザ以外の方式を避ける理由も見当たらない（文京区の A2 案を勧めなかったのは robots.txt の Disallow が理由だった）。**どちらにするかは 1／ユーザーの判断事項**とし、以下は A案で書き、B案のセレクタは §4-9 にまとめる。

### 4-1. 追加する設定（`scraper/config.py`）
```python
# 新宿区：地域センター受付システムの「何をする」（#purpose の value）。40_20 = ダンス（軽スポーツ）
SHINJUKU_PURPOSE = os.environ.get("SHINJUKU_PURPOSE", "40_20")
# 「どこで」。1000_0 = 新宿区立地域センター(すべて)
SHINJUKU_AREA = os.environ.get("SHINJUKU_AREA", "1000_0")
# 除外する部屋（施設コード）。★B案にするなら葬儀兼用ホール5室を入れる
#   10100090 菊, 10100100 百合（牛込箪笥）, 10200100 地下ホールＡ（榎町）, 10300090 Ｂ１ホール（若松）, 10500110 集会室１（戸塚）
SHINJUKU_EXCLUDE_ROOMS = [c for c in os.environ.get("SHINJUKU_EXCLUDE_ROOMS", "").split(",") if c]
# 想定している部屋数（これと違えば警告。A案=25）
SHINJUKU_EXPECTED_ROOMS = int(os.environ.get("SHINJUKU_EXPECTED_ROOMS", "25"))
SHINJUKU_REQUEST_DELAY_SEC = float(os.environ.get("SHINJUKU_REQUEST_DELAY_SEC", "3.0"))  # 全リクエスト間の待機
```

### 4-2. クラスの骨格（`scraper/scrapers/shinjuku.py`、A案）
```python
BASE = "https://www.shinjuku.eprs.jp/chiiki/web/"
TOP = BASE                                  # GET（index.jsp と同じ）
SEARCH = BASE + "rsvWOpeInstSrchVacantAction.do"
WEEK = BASE + "rsvWOpeInstSrchVacantAjaxAction.do"
ENC = "cp932"                               # Windows-31J
AVAILABLE = 0                               # 週表示の status
OUT_OF_PERIOD = 700                         # 受付期間外
UA = "Mozilla/5.0 (...) Chrome/1xx Safari/537.36"   # 世田谷と同じくブラウザの UA

class ShinjukuScraper(WardScraper):
    key = "shinjuku"; ward_name = "新宿区"; supports_shard = True

    def __init__(self, max_rooms=None, max_weeks=None, shard_index=0, shard_count=1, deadline=None):
        super().__init__(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
        self._max_rooms, self._max_weeks = max_rooms, max_weeks   # テスト用
        self._last = 0.0

    def scrape(self, date_from, date_to) -> list[Slot]:
        if self.out_of_time("取得開始前"): return []
        s = requests.Session(); s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
        blds, rooms = self._search(s, date_from)          # 館[(code,name)]、部屋[(bld,inst,label)]
        blds = self.shard_items(sorted(blds))             # 館コード順に並べて shard で分ける
        mine = {code for code, _ in blds}
        self.set_assigned_facilities(name for _, name in blds)
        rooms = [r for r in rooms if r.bld in mine and r.inst not in config.SHINJUKU_EXCLUDE_ROOMS]
        ...（§4-3 の週ループ）
```

### 4-3. 手順（擬似コード、A案）
```text
_pause(): 前回のリクエストから SHINJUKU_REQUEST_DELAY_SEC 秒たつまで待つ（全リクエスト共通）

_search(s, date_from):
  _pause(); r = s.get(TOP, timeout=60)                      # JSESSIONID が発行される
  fields = parse_form1(r.content.decode(ENC))               # form1 の全項目（文書順・checked のみ・select は selected）
  set fields: daystart=date_from(YYYY-MM-DD), date=3, days=7,
              selectAreaBcd=SHINJUKU_AREA, selectIcd="", selectPpsClPpscd=SHINJUKU_PURPOSE,
              dayofweekClearFlg=1, timezoneClearFlg=1       # displayNo=pawab2000 は HTML の値のまま
  body = urlencode(fields, encoding=ENC)
  _pause(); r = s.post(SEARCH, data=body, headers={Content-Type: application/x-www-form-urlencoded,
                                                     Referer: TOP, Origin: https://www.shinjuku.eprs.jp})
  html = r.content.decode(ENC); assert "空き状況画面（施設ごと）" in <title>
  blds  = [(value, text) for option in select#mansion-select]
  rooms = [(data-bldcd, value, text) for option in select#facility-select if value != "0"]
  len(rooms) != SHINJUKU_EXPECTED_ROOMS なら警告（部屋の増減・目的コードの変更に気付くため）
  return blds, rooms

週ループ（週ごと×部屋。completed_until を進めるため）:
  done_rooms = set()                                      # 受付期間外だけの週に達した部屋
  w = date_from
  while w <= date_to:
      if out_of_time(f"{w} の週の開始前"): break
      for room in rooms (館コード→施設コード順):
          if room in done_rooms: continue
          if out_of_time(...): break
          j = _week(s, room, w)                             # §3-4。失敗時は下の「異常時」
          slots += parse_week(j, room, date_from, date_to)
          if all(c.status == OUT_OF_PERIOD for c in cells of j if date(c) >= today): done_rooms.add(room)
      if timed_out: break
      week_end = min(w + 6日, date_to); mark_completed_until(week_end)   # 全担当部屋でこの週を取り終えた
      if done_rooms == set(rooms): mark_completed_until(date_to); break  # 以後はすべて受付期間外
      w += 7日

_week(s, room, w):
  body = f"displayNo=prwrc2000&useDay={w:%Y%m%d}&bldCd={room.bld}&instCd={room.inst}&transVacantMode=11&clearFlag=0"
  _pause(); r = s.post(WEEK, data=body, headers={X-Requested-With: XMLHttpRequest, Referer: SEARCH,
                                                   Accept: "application/json, text/javascript, */*; q=0.01"})
  r.status_code >= 500 → 例外 ServerError（その回の取得を打ち切り、取得済み分を返す）
  j = json.loads(r.content.decode(ENC))
  "ErrManager" in j or "result" not in j → セッション切れ等とみなし _search() をやり直して1回だけ再試行
  j["weekDay"][0]["useDay"] != int(w:%Y%m%d) なら警告（7日の開始が useDay と一致する前提の確認）
  return j

異常時: 通信エラー・タイムアウトは 30 秒待って1回だけ再試行。5xx は再試行せず、その実行を打ち切る
（取得済みの Slot と completed_until はそのまま返す。combine が残りを前回データで補う）。
```
- `transVacantMode=4`（翌週）で辿る方式でもよいが、`11` で週の初日を直接指定するほうが部屋の切り替えと週の進め方を独立に書ける（どちらも画面の操作で送られる要求）。
- 1日の始まりの扱い: `date_from`（今日）を初日にする。今日のコマも「空き」なら Slot にする（インターネットの申請は「利用当日まで」）。

### 4-4. Slot への変換
- `ward = "新宿区"`
- `facility = 館名`（`#mansion-select` の表示。例「若松地域センター」）
- `room = 部屋名`（`#facility-select` の表示から末尾の定員を除く: `re.sub(r"（[０-９0-9]+）$", "", label)`。例「３Ｆホール（１３０）」→「３Ｆホール」。全角のまま（世田谷・文京と同じ）。同じ部屋名（多目的ホール）が複数の館にあるが、facility で区別される）
- `date = f"{useDay//10000:04d}-{useDay//100%100:02d}-{useDay%100:02d}"`（20261116 → "2026-11-16"）
- `start = f"{startTime//100:02d}:{startTime%100:02d}"`、`end` も同様（900 → "09:00"、1515 → "15:15"、2145 → "21:45"）
- **`status == 0` のコマだけ**を Slot にする。`date_from <= date <= date_to` の範囲外は捨てる。
- コマごとに1件（午後２と夜間１が続けて空いていても2件）。フロントは `MERGE_GAP_MINUTES = 30` で15分の休憩をはさむコマも1行にまとめて表示する（web/src/lib/data.ts）ので、こちらでまとめる必要はない。
- `facilities.json` に新宿区の10行を手で追加する（ward と facility で突き合わせるので、名前は館名と完全に一致させる）。URL は「館・施設一覧」画面の「館情報」ボタンのリンク先（指定管理者のサイト）、または区の地域センター総合案内:
  - 四谷 `http://ycc.tokyo`、牛込箪笥 `https://www.ushigometansu.com/`、榎町 `https://enoki-chiiki.tokyo/`、若松 `https://wakamatsucenter.com/index`、大久保 `https://ookubocc.tokyo/`、戸塚 `https://www.tcc-tokyo.net`、落合第一 `https://www.ochiai1center.com/`、落合第二 `https://www.ochiai2center.com/`、柏木 `http://kashiwagicc.com/`、角筈 `https://tsunohazucc.com/`
  - 区の総合案内（共通）: `https://www.city.shinjuku.lg.jp/seikatsu/community01_000104.html`

### 4-5. 担当施設名と completed_until の記録（コミット 606d2ea の基盤を使う）
- 担当施設 = この shard の**館**。`set_assigned_facilities(館名の一覧)` を、shard で分けた直後（検索直後）に呼ぶ。館名は Slot.facility と同じ表記にする（正規化は不要。画面の表記どおり）。
- 部屋単位で shard を分けると、同じ館が複数の part に「担当」として出てしまい、ある shard が失敗しても combine がその館の欠けた部屋を補えない。→ **shard の単位は館**にする。
- completed_until: 週ループの1週を全担当部屋で取り終えるたびに `mark_completed_until(min(週の初日+6日, date_to))`。途中で時間切れになった週は記録しない（combine がその週以降を前回データで補う）。全部屋が受付期間外だけになったら `mark_completed_until(date_to)` して終了する。
- 606d2ea が作業ブランチに取り込まれていない場合は、この2つの呼び出しだけ後から足せばよい（呼ばなくても動く。その場合は補完されない）。

### 4-6. shard の分け方
- 単位は館（10館）。`blds` を館コード順にソートして `self.shard_items()` で分ける。どの shard も同じ検索をするので、館の一覧と順序は全 shard で一致する。
- **推奨: `scrape.yml` に `- { ward: shinjuku, shards: 1, shard: 0 }` の1行**。所要時間が短く（§4-7）、同時に開くセッションを1つにして相手の負荷を抑えられる。shards=2 にすると、それぞれがトップ取得＋検索をするうえ同時に2セッションで照会することになる。

### 4-7. 所要時間の見積もり
- 1リクエストあたり: 応答 約1.1秒（実測平均 1.07秒）＋待機 d 秒。
- リクエスト数: 2（トップ＋検索）＋ 部屋数 R × 週数 W。取得期間は今日〜2か月後の月末（57〜92日）なので W = 9〜14。受付期間外だけの週に達した部屋は以後取らない。

| 案 | R | W=13（10/5 実行の例。12/28 の週まで） | 待機3秒 | 待機2秒 | 待機1秒 |
|---|---|---|---|---|---|
| A案 | 25 | 327 件 | **約22分** | 約17分 | 約11分 |
| B案 | 20 | 262 件 | 約18分 | 約14分 | 約9分 |
| A案の最悪（W=14、全週取得） | 25 | 352 件 | 約24分 | 約18分 | 約12分 |

→ どの場合も 1 shard で TIME_BUDGET_MIN=330 に十分収まる。1回の実行の通信量は約 2.5 MB（週表示 7KB × 350 ＋ 画面 2 つ）。1日2回で約 650〜700 リクエスト。

### 4-8. 負荷への配慮
- 1セッションで順番に照会し、並列にしない。全リクエストの間隔を3秒以上空ける（既定）。
- 静的ファイルを取らない（A案）。日付順一覧（5件ずつ）や月表示は使わない。
- 受付期間外だけの週に達した部屋は以後照会しない。
- 5xx が出たらその実行を打ち切る（再試行で負荷を重ねない）。
- 実行時刻: 現在の定期実行は JST 10:00 / 22:00。毎月11日（一般のインターネット受付開始 9:00）と、第1土曜日の翌日（登録団体のインターネット受付開始 9:00）は、10:00 前後に申込みが集中する可能性がある【推測】。気になるなら新宿区だけ実行を 11:00 以降にずらすか、その日の朝の回を飛ばす。

### 4-9. B案（Playwright）にする場合のセレクタと手順
```text
page.goto(BASE, wait_until="domcontentloaded")
page.select_option("#bname", "1000_0")          # 「すべて」は通信しない
page.select_option("#purpose", "40_20")
（開始日を変えるなら #daystart に YYYY-MM-DD を fill。既定は今日）
with page.expect_response(lambda r: "rsvWOpeInstSrchVacantAjaxAction.do" in r.url) as first:
    page.click("#btn-go")                        # 画面遷移後、館 AJAX → 週表示 AJAX が自動で走る
部屋の全一覧: 遷移した文書の HTML（page.on("response") で rsvWOpeInstSrchVacantAction.do の本文を保存）の
             #facility-select を読む（館 AJAX のあと DOM は先頭の館の部屋だけに置き換わるため）
館の切替: with expect_response(WEEK): page.select_option("#mansion-select", bldCd)
          → 館 AJAX → その館の先頭の部屋の週表示（表示中の週のまま）
部屋の切替: with expect_response(WEEK): page.select_option("#facility-select", instCd)
翌週: with expect_response(WEEK): page.click("#next-week")
データ: json.loads(resp.body().decode("cp932"))  ※DOM の表（table#week-info td.available など）は読まない
完了待ち: page.wait_for_selector("#loadingweek", state="hidden")（JS が isProcessing で連続操作を捨てるため）
```
- JS は操作のあと `setTimeout(gWaitTime)` を置いてから AJAX を送り、処理中の操作は無視する（`isProcessing3/4/5`）。1操作ずつ応答を待ってから次へ進むこと。
- 館 AJAX を省くなら: `page.evaluate("(a)=>{document.form1.selectBldCd.value=a.b; document.form1.selectInstCd.value=a.i; document.form1.useDay.value=a.d; getWeekInfoAjax(11,1,0);}", {...})`（アプリ自身の関数を呼ぶ。世田谷で `__doPostBack` を呼んでいるのと同じ考え方）。
- ローカルのサンドボックスで動かす場合は `executable_path="/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"` と SPKI フラグが必要（§1。CI では不要）。

### 4-10. 実装時の確認手順（相手への配慮として最小回数で）
1. `max_rooms=1, max_weeks=1` で「1部屋×1週」だけ取得し、館名・部屋名・時刻・件数をログで確認する（2＋1 リクエスト）。
2. 次に `max_rooms=3, max_weeks=2`。全件はその後。
3. 週表示の `status` に 0/1/2/210/700 以外が出たら、値と `alt` をログに出す（一般開放・時間外・雨天のコードを確定させるため）。
4. `weekDay[0].useDay` が要求した useDay と違ったら警告を出す（月曜以外を初日にしたときの挙動は未確認のため）。
5. 検索結果の部屋数が 25 でなければ警告（部屋の追加・目的の設定変更に気付くため）。

---

## 5. リスクと代替案

| # | リスク | 影響 | 対策・代替案 |
|---|---|---|---|
| R1 | 規約: 申込み時に一部の館で出る「利用規約」の本文は未確認（ログインが必要な画面） | 自動取得についての記載があるかは不明 | 照会はログイン前の公開画面だけを使う。気になるなら区（地域コミュニティ課 03-5273-4127）へ事前に問い合わせる |
| R2 | 「空き」に、一般は受付前・抽選中で登録団体だけが申し込める日が含まれる | 一般の利用者が申し込めない枠を「空き」と誤解する | §2-4。注意書きを出す。必要なら「一般の受付期間内だけ」に絞る設定を追加 |
| R3 | 葬儀兼用ホール（5室）は葬儀が優先され、予約が取り消されることがある | 取った予約が消える可能性 | B案で除外できるよう設定を用意（§4-1）。facilities.json の note に注意を書く手もある |
| R4 | 結果画面からの再検索で条件が反映されない（原因不明） | 条件を変えた再検索が効かない | 検索は必ずトップ画面から（確認済みの経路） |
| R5 | セッション切れ・エラー JSON | 途中から取れない | `ErrManager` や JSON でない応答ならトップ＋検索をやり直して1回だけ再試行 |
| R6 | 5xx・タイムアウト | 相手の負荷・取得の欠け | 5xx は再試行せず打ち切り。completed_until により combine が残りを前回データで補う |
| R7 | フォーム項目名・JSON のキー・状態コードの変更 | 取得0件・誤分類 | form1 は毎回 HTML から組み立てる。部屋数・未知の status をログに出す。`status==0` だけを空きとする（安全側） |
| R8 | 月曜以外を初日にした週表示の挙動が未確認（useDay に指定した 10/05・11/16・12/28 はすべて月曜） | 週の境目で日付が重なる・抜ける | 応答の `weekDay` の日付で判断する（`useDay` を信じない）。重複は `_dedupe` で除く。抜けが出たら `transVacantMode=4`（翌週）で辿る方式に切り替える |
| R9 | 実行時刻と受付開始の集中が重なる | 相手の混雑時に負荷を足す | §4-8 の時刻調整 |
| R10 | B案のときの静的ファイル（no-store）と自動通信 | 通信が増える | A案を採る。B案なら evaluate で館 AJAX を省く |

代替案（どれも未検証）:
- **月表示で絞る**: 部屋ごとに月表示（1か月=1リクエスト）を取り、「全て空き／一部空き」の日を含む週だけ週表示を取る。空きのない週はほとんどないので、減るリクエストは少ない。
- **日付順一覧**: 1館×1週ずつなら上限100件に収まるが、5件ずつしか取れず、リクエスト数はかえって増える。

---

## 6. 未確認事項（【不明】のまとめ）
- 予約申込み時に一部の館で出る「利用規約」の本文（ログインが必要）
- 状態コード: 一般開放・時間外・雨天（週表示）、`ErrManager` の実例
- `transVacantMode` の 3（前週）・12（前日）・13（翌日）、月曜以外の useDay を初日にしたときの週表示
- 結果画面（日付順タブ）からの再検索で条件が反映されない理由（js/prwrc1000.js は未読）
- セッションのタイムアウトの長さ、`startInitTimer()` の動作（js/paw_common.js 等は未読）
- `storeCookie()` が保存する Cookie をサーバーが使うか（今回は Cookie を作らずに検索して正常に結果が出たので、少なくとも必須ではない）
- 1館だけで検索したセッションで、他の館の部屋の週表示を要求したときの挙動（全館検索後のセッションでは正常）
- ダンス以外の目的（バレエ・社交ダンスなど）や「その他（一体利用）」を足したときに部屋が増えるか
- 1月分の受付期間外が解ける日（登録団体: 11月の第1土曜の翌日 9時と推測）
- レガス新宿 施設予約システム（/regasu/web/）の内容（未アクセス）

## 7. 証跡（scratchpad）
- アクセスログ: `plans/shinjuku_access_log.tsv`（予約システム 25 件＝seq 1〜25、ユーザー操作 U1〜U16。区公式サイト 8 件、TLS テスト 1 件。末尾に集計）
- 応答ヘッダ: `shinjuku/hdr/001.hdr`〜`025.hdr`（seq と同じ番号）
- 画面の HTML: `shinjuku/html/`（`u01_top`、`u02_guide`、`u03_instlist`、`u04_result1`（四谷×1週）、`u06_daily1`（日付順）、`u07_result_all`（反映されなかった再検索）、`u08_home`、`u09_result_all`（全館×1週）。それぞれ `*.html`（原文 Windows-31J）と `*.u8.html`（UTF-8 変換）、一部 `*.pretty.txt`）
- AJAX の応答: `shinjuku/api/`（`a01_build_1000.json`、`a02_week_*`、`a03_daily_*`、`u05_week_next.json`、`u10`〜`u16` の週表示・月表示。原文 Windows-31J）
- 送信した本文: `shinjuku/forms/`（`u04_search.txt`、`u09_search_all.txt`、`u05`〜`u16`、`a01`〜`a03`）
- 取得した JS: `shinjuku/out/paw_doaction.js`、`prwre1000.js`、`prwrc2000.js`（＋UTF-8 版）、`paw_doajax.js`、`prwrf1000.js`（＋UTF-8 版）、`robots.txt`（404 本文）
- 対象部屋の一覧: `shinjuku/out/rooms_dance_40_20.json`（全館×ダンス（軽スポーツ）の 25 室）
- 区の資料: `shinjuku/city/`（FAQ、利用方法、総合案内、施設利用予約、サイトポリシー、料金表 PDF/TXT）、マニュアル `manual.pdf`/`manual.txt`（前回取得分）
- スクリプト: `shinjuku/sjfetch.py`（予約システム用の送信・計数・記録ラッパー。上限・間隔・5xx 停止を強制）、`shinjuku/formbuild.py`（form1 の送信内容の組み立て）、`shinjuku/cityfetch.py`（区公式サイト用）、`shinjuku/tls_test_noflag.py`（TLS テスト）、`shinjuku/weeksum.py`・`monthsum.py`・`extract.py`（読むための要約）
