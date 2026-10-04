# 文京区「『文の京』施設予約ねっと」空き状況取得 実装案

作成: 4（空き施設調査方法検討担当） / 調査日時: 2026-10-05 03:06–03:27 JST
凡例: **【確認】**=実地アクセス・一次資料で確認済み / **【推測】**=根拠はあるが未検証 / **【不明】**=調べていない・確認できなかった

---

## 0. 要約（実装担当向け 10 行）

1. 予約システムは `https://www.shisetsu.city.bunkyo.lg.jp/user/`。ASP.NET Core と Vue2（BootstrapVue）の SPA で、**世田谷けやきネットとは別物**のため setagaya.py は流用できない（Playwright を使う方針と「最大10件選んで時間帯別へ」という流れは同じ）。【確認】
2. 空き照会にログインは不要。CAPTCHA は出ない。ただし **robots.txt が `Disallow: /*`（許可は `*.html` と `/*/Home` のみ）** で、照会画面は「クロール禁止」に当たる → **ユーザー判断が必要**。【確認】
3. 流れ: `/user/Home` →[利用目的から探す: 体操・ダンス → ダンス(40) → 検索]→ `AvailabilityCheckApplySelectFacility`（施設選択）→[次へ進む]→ `AvailabilityCheckApplySelectDays`（日ごとのグリッド）→[セルを最大10件選んで次へ進む]→ `AvailabilityCheckApplySelectTime`（時間帯別）→[前に戻る]→ グリッドへ戻る（期間と選択状態は保持される）。【確認】
4. データは **DOM を読まず JSON / Vue の状態から取る**。日ごとのグリッドは XHR（`GetAvailability` / `AfterPeriod` / `SearchCondition`）の JSON、時間帯別は `#app.__vue__.$data.model.AvailabilityTime`。どちらも `TimeFrom:900, TimeTo:1230, Status:"vacant"` のような構造化データ。【確認】
5. 時間帯（コマ）は 午前 9:00–12:30 / 午後 13:00–17:00 / 夜間 17:30–21:30（地域活動センター・ふれあい館で確認）。時刻は施設ごとに違う可能性があるので、データの TimeFrom/TimeTo をそのまま使う。【確認／他施設は推測】
6. 制約: 時間帯別へ進めるのは**最大10セル**（サーバー側エラー E-203-000018）。**14施設×1ヶ月の照会は約30秒後に 502 → /user/Error** になった → 1セッションの施設は **3件まで・表示は2週間** にする。【確認】
7. 自動セッションping（`/user/api/Header/*`）は止められない（遮断するとアプリが Error 画面へ遷移する）。`page.goto` で離脱すると beforeunload ダイアログが出るので accept する。【確認】
8. shard の単位は施設（コードでソートして `shard_items`）。1 shard の中では施設を3件ずつのグループに分け、グループごとに Home からやり直す。【設計】
9. 所要時間の見積もり（待機3秒）: 推奨A案（9施設24室）で約25分、B案（14施設44室）で約45分。最悪ケースでも A 約70分、B 約2時間。→ **shards=1 で 330分の予算に十分収まる**。【推測（実測値から算出）】
10. ユーザーの承認が必要なこと: (a) robots.txt を前提に運用してよいか、(b) 利用目的（推奨: ダンス40のみ）、(c) 施設の範囲（推奨: A案）、(d) 「洋室Ａ＋Ｂ」のような合体室を部屋として含めるか。

---

## 1. 予約システムの特定・到達性・規約

| 項目 | 内容 | 出典・確度 |
|---|---|---|
| システム名 | 「文の京」施設予約ねっと（文京区インターネット施設予約システム） | 区公式 https://www.city.bunkyo.lg.jp/b011/p006767.html 【確認】 |
| URL | ポータル `https://www.shisetsu.city.bunkyo.lg.jp/`（静的。中身は `/user/` へのリンク）→ 本体 `https://www.shisetsu.city.bunkyo.lg.jp/user/Home` | 上記ページのリンク、および実地アクセス 【確認】 |
| 資料 | 利用案内PDF https://www.city.bunkyo.lg.jp/documents/6498/20260316.pdf（令和8年3月 第8版）、操作マニュアルPDF https://www.city.bunkyo.lg.jp/documents/6498/sousamanual.pdf（第1.6版。第3章が空き照会） | 【確認】 |
| 到達性 | HTTP 200（HTTP/2, nginx）。システムは年末年始を除き24時間利用可（年末年始もログインはできないが空き確認はできる） | 実地アクセス・Home の掲示 【確認】 |
| 技術構成 | ASP.NET Core（偽造防止トークン `__RequestVerificationToken` が `CfDJ8…` の形式）と Vue2 / BootstrapVue / axios の SPA。画面ごとに URL が変わり、データは XHR（multipart POST）で取る。サーバー側セッション（Cookie）に「選んだ施設・表示期間」を保持する | 実地アクセス 【確認】 |
| ログイン | **空き照会は不要**。予約申込み・抽選にはログインが必要（窓口での利用登録が前提） | 実地＋マニュアル p.54 【確認】 |
| CAPTCHA | 照会の経路（Home→施設選択→施設別→時間帯別）には出ない | 実地 【確認】 |
| robots.txt | 下記の通り。**照会画面（`/user/AvailabilityCheckApply…`）は Disallow に当たる** | `https://www.shisetsu.city.bunkyo.lg.jp/robots.txt`（Last-Modified 2022-03-24）【確認】 |
| 利用規約 | システム内に規約ページへのリンクは見当たらない（Home の href は区公式の案内ページ p006767 と区トップだけ。「ご利用案内」「FAQ/よくあるご質問」ボタンは JS で動き、リンク先は未確認）。利用案内PDF・操作マニュアルにも、自動取得を禁止・許可する記載はない。マニュアル p.8 に「過度なアクセス集中防止」の記述があるが、これは同じIDでの複数同時ログインを防ぐ機能の説明 | 【確認（読んだ範囲）】／区公式FAQ p007799 は【不明（未閲覧）】 |
| 同時ログイン | 同じIDで複数同時にログインすると先の画面が無効になる（今回はログインしないので関係なし） | マニュアル p.8 【確認】 |

robots.txt の全文:
```
User-agent: *
Disallow: /*
Allow: /*.html
Allow: /*/Home
```
→ 許可されるのは `*.html` と `/user/Home` だけ。施設選択・施設別・時間帯別の各画面とそこで呼ぶ XHR は **Disallow**。
（比較のため世田谷 `setagaya.keyakinet.net/robots.txt` も取得しようとしたが、この環境からは 403 で確認できなかった。【不明】）

参考【推測】: JS ファイル名に `oec-` が付く（`oec-partial-component.js` など）ことから、株式会社オーイーシー系の製品かもしれない。同じ製品を使う区があればコードを共通化できる可能性がある。

---

## 2. 絞り込み条件の案（★ユーザー承認待ち）

### 2-1. 利用目的（Home「利用目的から探す」）【確認】
利用目的の分類（ラジオ、`HomeModel.SelectedPurposeCategory`）と利用目的（チェックボックス、`HomeModel.SelectedPurpose` の value）。ダンスに関係するものだけ抜き出すと:

| 分類（value） | 利用目的（value: 名称） |
|---|---|
| 体操・ダンス（3） | **40: ダンス** / 41: バレエ / 37: 体操・ストレッチ / 39: ヨガ・ピラティス |
| 文化活動（6） | 63: 民踊・日舞 / 64: 剣舞 |

- 複数の目的を選ぶと「どれか1つでも合う施設」（OR）が出る。絞り込み（AND）ではない（マニュアル p.43）【確認】。分類はラジオなので、一度に選べるのは1分類の中の目的だけ【確認（UI）】。
- 検索結果のグリッドには、**選んだ目的が使える部屋だけ**が出る（例: 区民会議室は「４階ホール」だけが表示された）【確認】。
- **推奨: 40「ダンス」のみ**。世田谷の「その他ダンス」に一番近い、汎用のダンスの項目。バレエ（41）や民踊・日舞（63、分類が別なので別に検索が必要）を足すと施設・部屋が増えるかどうかは【不明（照会していない）】。

### 2-2. 対象とする施設の種別
ダンス(40) で検索すると24施設が出た【確認】。区公式の分類（p006767 の表）に当てはめると次の通り。

| 区の分類 | ダンス(40)で出た施設（施設コード／ダンス可の部屋数） |
|---|---|
| 集会施設（地域の施設） | 区民会議室(1/1室: ４階ホール)、大原地域活動センター(7/3)、大塚地域活動センター(8/3)、向丘地域活動センター(11/1)、汐見地域活動センター(12/6)、駒込地域活動センター(13/3)、元町多目的室(81/1)、目白台交流館(22/2)、不忍通りふれあい館(24/4) |
| 集会施設（目的が決まった施設） | シルバーセンター(3/1)、男女平等センター(5/2)、福祉センター江戸川橋(25/10)、松聲閣集会室(26/3)、勤労福祉会館(28/4) |
| アカデミー施設 | アカデミー文京(33)、アカデミー音羽(36)、アカデミー茗台(38)、アカデミー向丘(39) |
| 文化施設 | シビックホール大ホール(29)、小ホール(30)、その他施設(31) |
| スポーツ施設 | 総合体育館(45)、スポーツセンター(46)、江戸川橋体育館(47) |

ダンスでは出てこなかった集会施設: 区民センター、礫川・音羽・湯島地域活動センター、区民会館のうち元町多目的室以外（白山東・かるた記念大塚・駕籠町・大塚北・本郷・動坂）、白山・千駄木・根津交流館、障害者会館、大塚公園集会所。【確認（検索結果に無い）／理由は、施設側の利用目的の設定でダンスが不可になっているためと推測】

案:
- **A案（推奨・世田谷の対象に相当）**: 地域の集会施設 = 地域活動センター／区民会館（元町多目的室）／交流館／不忍通りふれあい館／区民会議室。**9施設・24室**。世田谷の「区民センター・地区会館・区民集会所」と同じく、地域住民が一般に使う貸室だけに絞る。
- B案: A案 ＋ 目的が決まった集会施設（シルバーセンター・男女平等センター・福祉センター江戸川橋・松聲閣集会室・勤労福祉会館）。**14施設・44室**。ただし設置目的に沿った利用要件がある（例: 男女平等センターは一般団体だと利用月の1か月前から申込み、シルバーセンターは高齢福祉の施設）ので、一般のダンス団体には使いにくい可能性がある。【確認（各施設の注意事項）】
- C案: B案 ＋ アカデミー施設・シビックホール（スポーツ施設は集会施設ではないので除外）。費用・規模の面で趣旨から外れやすい。

施設を絞るときは、**施設名の許可リスト**（下の §4-1 `BUNKYO_TARGET_FACILITIES`）で決めるのを推奨する。文京区の施設名は「〇〇地域活動センター」「元町多目的室」「区民会議室」のように揃っておらず、キーワードで判定すると漏れや誤判定が出やすいため。検索結果に許可リストにない施設が出たら、ログに出して気付けるようにする。

### 2-3. 合体室の扱い（要確認・優先度低）
「洋室Ａ＋Ｂ」「多目的室Ａ＋Ｂ」「ホール＋スタジオ」のように、部屋をつなげた単位が別の行として出る【確認】。予約の単位としては実在するので、既定では**別の部屋として含める**。画面上の重複が気になるなら、名前に「＋」を含む行を除外する設定も用意できる。

---

## 3. 画面遷移の実地確認結果（セレクタ・通信・データ構造）

ベース URL: `BASE = "https://www.shisetsu.city.bunkyo.lg.jp/user/"`。どの画面も下部の固定ボタン（`.fixed-bottom ul.buttons`）で移動する: `li.next button`=次へ進む、`li.prev button`=前に戻る。ブラウザの戻る・進むは使わないこと（使うと /user/Error になる、と画面に表示される）。【確認】

### 3-1. Home（`/user/Home`）【確認】
- タブ「利用目的から探す」: `page.get_by_text("利用目的から探す", exact=True)`（`a.nav-link` の中の `li.tab-name`）。タブの切替だけでは通信しない。
- 有効なタブの中身: `div.tab-pane.active`
- 分類: `div.tab-pane.active label.custom-control-label:has-text("体操・ダンス")`（ラジオ value=3）
- 目的: `input[name='HomeModel.SelectedPurpose'][value='40']` の id を取り、`label[for='<id>']` をクリックする（**input の id はページを開くたびに変わる UUID**なので、id を決め打ちしないこと）
- 検索ボタン: `div.tab-pane.active button.btn-secondary:visible` で、テキストが「検索」の1つ目（同じ見た目のボタンが非表示の折りたたみ部分にもあるため `:visible` と first が必要）
- 通信: `POST /user/Home/SearchByPurpose`（multipart）→ `GET /user/AvailabilityCheckApplySelectFacility` へ遷移
- 選択肢の全リストは Vue の状態 `#app.__vue__.$data.model.HomeModel.PurposeList`（PurposeCategoryCode / PurposeCode / PurposeName）にある。

### 3-2. 施設選択（`/user/AvailabilityCheckApplySelectFacility`）【確認】
- 表: `table.facilities`。チェックボックス: `table.facilities input[type=checkbox][value='<施設コード>']`（name は `SelectFacilities.Facilities[n].SelectedFacility.Value`）。クリックするのは `label[for='<id>']`。
- 24行すべてが最初から DOM にあるが、11行目以降は「さらに読み込む」（`button:has-text('さらに読み込む')`）を押すまで表示されない。**このボタンは画面上の表示を増やすだけで、通信しない**。非表示の label はクリックできないので、ボタンが消えるまで押してから選ぶ。
- 施設の一覧は Vue の状態から取れる: `model.SelectFacilities.Facilities[]` → `{SelectedFacility:{Value:"7", Text:"大原地域活動センター"}, ObjectCodeList:[…], FacilityGuidUrl, Address, …}`
- 選んだ件数の上限: 14件はエラーにならなかった（それ以上は試していない）。
- 次へ: `.fixed-bottom li.next button` → `POST …/AvailabilityCheckApplySelectFacility/Next`（`SelectFacilities.Selected[]` など）→ `GET /user/AvailabilityCheckApplySelectDays` → `POST …/AvailabilityCheckApplySelectDays/GetAvailability`（JSON）

### 3-3. 施設別空き状況（`/user/AvailabilityCheckApplySelectDays`）【確認】
- 表示条件: `.disp-condition`
  - 開始日 `#SearchCondition_StartDate`（type=date、値は `YYYY-MM-DD`。空なら今日）
  - 表示期間のラジオ `.disp-condition label.custom-control-label` のテキストが「1日／1週間／2週間／1ヶ月」で、隠しフィールド `#SearchCondition_DisplayTerm` の値はそれぞれ 1/2/3/4。**既定は 1週間(2)**。
  - 「その他の条件で絞り込む」（`#otherCondition`）: 表示形式（横表示/カレンダー表示）、表示時間帯（選べるのは「全日」だけ）、表示曜日
  - 表示ボタン: `button.btn:visible` でテキストが「表示」のもの → `POST …/AvailabilityCheckApplySelectDays/SearchCondition`
- 施設ごとに1つ `table.table-schedule`（順番は応答 JSON の `AvailabilitySelectDays[]` と同じ）。行=部屋、列=日付。
- セル: `label.btn.btn-toggle.<状態>`。中に `span.sr-only`（読み上げ用の文字）と、隠しフィールド `AvailabilitySelectDays[i].Rows[j].Cells[k].IsChecked` がある。同じ td の中に `…Cells[k].{FacilityCode, DisplayGroupCode, UseDate, Status, ObjectCode[0]}` の隠しフィールドもある。
- 状態のクラスと画面の表示:

| class | 画面（凡例） | 取得対象 |
|---|---|---|
| `vacant` | 空き（○、すべてのコマが空き） | 時間帯別で確認 |
| `some` | 一部空き（△） | 時間帯別で確認 |
| `full` | 空きなし（×） | 対象外 |
| `time-over` | 申込期間外（－） | 対象外 |
| `disabled closed` | 休館（span.sr-only は空、label の文字が「休館」） | 対象外 |
| 抽選／抽選申込可能／公開対象外（＊） | 凡例にはあるが今回のデータには出ず、class 名は【不明】 | 対象外（vacant/some 以外は見ない） |

- セルを選ぶ: label をクリックすると選択／解除が切り替わる（`.active` が付き、IsChecked が `true` になる）。53セル選んでも画面上の警告は出なかった。
- 「次の期間」: `button.btn-gray:visible:has-text('次の期間')` の1つ目 → `POST …/AvailabilityCheckApplySelectDays/AfterPeriod`。**どの施設のボタンを押しても、全施設がまとめて1期間進む**。
- 次へ進む: `.fixed-bottom li.next button` → `POST …/AvailabilityCheckApplySelectDays/Next`
  - 11セル以上選んでいると、応答は `{"Result":"Error","Information":{"MessageId":"E-203-000018","Param":"10"}}` になり、モーダル `.modal.show` に「選択可能数は、最大10件までです。」と出る（閉じるのは `.modal.show button:has-text('閉じる')`）。
  - 10セル以下なら `GET /user/AvailabilityCheckApplySelectTime` へ遷移する。
- 応答 JSON（グリッド）:
  - `GetAvailability` / `AfterPeriod` の応答: `[ {AvailabilitySelectDays:[…], AvailabilitySelectDaysCalendar:[…]}, "<html>", ("<html>"), null ]`
  - `SearchCondition` の応答: `[ {Result:"Ok", Information:""}, {AvailabilitySelectDays:[…]}, "<html>", "<html>", null ]`
  - → **配列の中から `AvailabilitySelectDays` を持つ dict を探す**ように実装する。
  - `AvailabilitySelectDays[]`: `FacilityCode, FacilityName, Dates[{Item1:"2026-10-12T00:00:00", Item2:祝日フラグ}], Rows[{ObjectName:"多目的室Ａ", ObjectDescription, Capacity, DisplayGroupCode, ObjectCodeList, Cells[{UseDate, Status:"vacant|some|full|time-over|closed…", ClosedDateName, Disabled, IsChecked}]}]`

### 3-4. 時間帯別空き状況（`/user/AvailabilityCheckApplySelectTime`）【確認】
- データは文書に埋め込まれていて、Vue の状態 `#app.__vue__.$data.model.AvailabilityTime.FacilityList[]` から取れる（この画面のための追加 XHR はない）。
  - `FacilityList[i] = {FacilityCode, FacilityName, Tables:[{UseDate, IsHoliday, TimelineFrom:900, TimelineTo:2130, Places:[{ObjectCode, ObjectName:"多目的室Ａ", ObjectDescription, Capacity, Cells:[{TimeFrom:900, TimeTo:1230, FrameNo:1, FrameName:"午前    ", Status:"vacant"|"full"|…}]}]}]}`
- DOM で読む場合（予備）: `li.btn-group-toggle.<状態>` の中の `label.btn-toggle` に `span`（コマ名）と、空きのときだけ `span.sr-only`（「9時から12時30分まで」）がある。隠しフィールド `AvailabilityTime.FacilityList[i].Tables[j].Places[k].Cells[l].{UseDate, TimeFrom, TimeTo, Status}` もある。部屋名は `.room-name.get-name > div`。
- 凡例: 空きあり(○) / 施設に問合せ(△) / 空きなし(×) / 抽選 / 抽選申込可能 / 申込期間外(－) / 利用時間外。今回のデータに出た Status は `vacant` と `full` だけ。△などの class 名は【不明】→ **`Status == "vacant"` だけを Slot にする**。
- 確認したコマ: 午前 900–1230、午後 1300–1700、夜間 1730–2130（大原・汐見地域活動センター、不忍通りふれあい館の３階会議室）。他の施設・部屋の時刻は【不明】。
- 前に戻る: `.fixed-bottom li.prev button` → `POST …/AvailabilityCheckApplySelectTime/Previous` → `GET …/AvailabilityCheckApplySelectDays` → `POST …/GetAvailability`。**表示期間（開始日）と10セルの選択状態は残ったまま戻る**ので、次のバッチの前に選択を解除する必要がある。

### 3-5. 実測値【確認】

| 操作 | 条件 | 所要時間（クリックからデータ受信まで） | 応答サイズ |
|---|---|---|---|
| 施設選択 → 次へ（Next＋文書＋GetAvailability） | 3施設×1週間 | 6.2 秒 | 470 KB |
| 施設選択 → 次へ | 14施設×1週間 | 未計測（正常に表示された） | 1.8 MB |
| 表示期間を1ヶ月にして表示（SearchCondition） | 14施設×1ヶ月 | **約30秒後に 502 → /user/Error** | – |
| 表示期間を2週間にして表示（SearchCondition） | 3施設×2週間 | 6.5 秒 | 873 KB |
| 次の期間（AfterPeriod） | 3施設×1週間 | 4.3 秒 | 466 KB |
| 10セル → 時間帯別（Next＋文書） | – | 1〜3 秒 | 404 KB |
| 時間帯別 → 前に戻る（Previous＋文書＋GetAvailability） | 3施設×1週間 | 6.5 秒 | 約470 KB |

- 空き／一部空きのセルの割合: 14施設×1週目は 101/308（今日の分は申込期間外）。3施設×2〜3週目は 112/182。→ 見積もりでは「受付期間中のセルの約5割を時間帯別で確認する」とした。
- セッション: `GetSessionInterval` の応答が `{"Interval":15,"Warning":3}` → 15分操作しないと切れ、3分前に警告が出るものと推測。【推測】
- 自動ping: `GetSiteClosing`（応答 `{"isClosing":false}`）、`ResetSessionInterval`、`GetSessionInterval` が、画面を読み込むたび・操作するたび（およそ数秒〜1分に1回）に送られる。**ブラウザ内で遮断するとアプリが /user/Error に遷移した**ので、止めてはいけない。【確認】
- `page.goto()` で途中の画面から離れると beforeunload ダイアログが出る。dismiss すると遷移が中止される（ERR_ABORTED）ので、accept する必要がある。【確認】
- GA / GTM（google-analytics.com / googletagmanager.com）は遮断しても動作に影響はなかった。【確認】

---

## 4. 実装案

### 4-1. 追加する設定（`scraper/config.py`）
```python
# 文京区：「利用目的から探す」の分類と目的（HomeModel の value）
# 分類 3:体操・ダンス / 目的 40:ダンス（41:バレエ 37:体操・ストレッチ 39:ヨガ・ピラティス）
BUNKYO_PURPOSE_CATEGORY = os.environ.get("BUNKYO_PURPOSE_CATEGORY", "3")
BUNKYO_PURPOSES = os.environ.get("BUNKYO_PURPOSES", "40").split(",")
# 取得対象の施設名（許可リスト）。★ユーザー承認後に確定（下は A案）
BUNKYO_TARGET_FACILITIES = [
    "区民会議室", "大原地域活動センター", "大塚地域活動センター", "向丘地域活動センター",
    "汐見地域活動センター", "駒込地域活動センター", "元町多目的室", "目白台交流館",
    "不忍通りふれあい館",
    # B案で追加: "シルバーセンター", "男女平等センター", "福祉センター江戸川橋", "松聲閣集会室", "勤労福祉会館",
]
BUNKYO_EXCLUDE_COMBINED_ROOMS = os.environ.get("BUNKYO_EXCLUDE_COMBINED_ROOMS", "") == "1"  # 「＋」を含む部屋を除外
BUNKYO_FACILITIES_PER_SESSION = int(os.environ.get("BUNKYO_FACILITIES_PER_SESSION", "3"))  # 502 対策
BUNKYO_DISPLAY_TERM = os.environ.get("BUNKYO_DISPLAY_TERM", "3")  # 1:1日 2:1週間 3:2週間 4:1ヶ月(使わない)
BUNKYO_REQUEST_DELAY_SEC = float(os.environ.get("BUNKYO_REQUEST_DELAY_SEC", "3.0"))  # 操作ごとの待機
```

### 4-2. クラスの骨格（`scraper/scrapers/bunkyo.py`）
```python
BASE = "https://www.shisetsu.city.bunkyo.lg.jp/user/"
MAX_CELLS = 10           # 時間帯別へ進めるセル数の上限（サーバー仕様 E-203-000018）
NAV_TIMEOUT = 90_000
DRILL = ("vacant", "some")

class BunkyoScraper(WardScraper):
    key = "bunkyo"; ward_name = "文京区"; supports_shard = True

    def __init__(self, max_facilities=None, max_periods=None,
                 shard_index=0, shard_count=1, deadline=None):
        super().__init__(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
        ...

    def scrape(self, date_from, date_to) -> list[Slot]:
        slots = []
        if self.out_of_time("取得開始前"): return slots
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.HEADLESS)
            ctx = browser.new_context(locale="ja-JP", user_agent=UA)
            ctx.route(lambda u: "google-analytics.com" in u or "googletagmanager.com" in u,
                      lambda r: r.abort())             # /user/api/Header/* は絶対に止めない
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())   # beforeunload 対策
            try:
                facs = self._search(page)              # [(code:int, name:str)]（施設選択画面にいる状態）
                facs = self._filter_targets(facs)      # 許可リストで絞り、未知の施設はログに出す
                facs = self.shard_items(sorted(facs))  # コード順に並べてから shard で分ける
                groups = chunks(facs, config.BUNKYO_FACILITIES_PER_SESSION)
                for gi, group in enumerate(groups):
                    if self.out_of_time(f"グループ{gi+1}の開始前"): break
                    if gi > 0: self._search(page)      # グループごとに Home からやり直す（確認済みの経路）
                    slots += self._scrape_group(page, group, date_from, date_to)  # 失敗したら2回まで再試行
            finally:
                ctx.close(); browser.close()
        return dedupe(slots)
```

### 4-3. 画面遷移の手順（擬似コード）
```text
_search(page):
  goto BASE+"Home" (domcontentloaded); wait "text=利用目的から探す"
  click get_by_text("利用目的から探す", exact)
  panel = div.tab-pane.active
  click panel label:has-text("体操・ダンス")                      # config.BUNKYO_PURPOSE_CATEGORY に対応
  for v in BUNKYO_PURPOSES: ensure_checked(panel input[name='HomeModel.SelectedPurpose'][value=v])  # label[for=id] をクリック
  pause()
  click panel button.btn-secondary:visible:has-text("検索") >> nth=0
  wait_for_url("**/AvailabilityCheckApplySelectFacility"); wait table.facilities
  return evaluate(model.SelectFacilities.Facilities) → [(int(Value), Text.strip())]

_scrape_group(page, group):
  while button:visible:has-text("さらに読み込む"): click            # 通信なし
  for each checkbox in table.facilities: その施設が group に入っていれば選択、入っていなければ解除（状態を見てからクリック）
  pause()
  with expect_response("**/AvailabilityCheckApplySelectDays/GetAvailability") as r:
      click .fixed-bottom li.next button
  grid = find_grid(r.value.json()); wait table.table-schedule
  if #SearchCondition_DisplayTerm != BUNKYO_DISPLAY_TERM or 開始日 != date_from:
      開始日が今日でなければ fill #SearchCondition_StartDate = date_from.isoformat()
      click .disp-condition label:has-text("2週間"); pause()
      with expect_response("**/SearchCondition") as r: click button.btn:visible:has-text("表示")（テキストが「表示」と完全一致）
      grid = find_grid(r.value.json())   # data[0].Result == "Ok" を確認
  loop:                                   # 期間（2週間ずつ）
      if out_of_time(): break
      cells = [(i,j,k,cell) for i,fac in enumerate(grid) for j,row in enumerate(fac.Rows)
               for k,cell in enumerate(row.Cells)
               if cell.Status in DRILL and date_from <= cell.UseDate[:10] <= date_to
               and not (EXCLUDE_COMBINED and "＋" in row.ObjectName)]
      for batch in chunks(cells, 10):
          if out_of_time(): break
          for (i,j,k,_) in batch:
              page.locator(f'input[name="AvailabilitySelectDays[{i}].Rows[{j}].Cells[{k}].IsChecked"]').locator("xpath=..").click()
          assert count(label.btn-toggle.active) == len(batch)
          pause()
          click .fixed-bottom li.next button
          wait_for_url("**/AvailabilityCheckApplySelectTime")   # 失敗したら .modal.show の文言をログに出し、閉じて例外にする
          slots += parse_time(page.evaluate(VUE_TIME_MODEL))
          pause()
          with expect_response("**/GetAvailability"): click .fixed-bottom li.prev button
          wait table.table-schedule
          for lab in label.btn-toggle.active: click               # 戻っても選択が残っているので解除する
      last = max(d.Item1 for d in grid[0].Dates)
      if last[:10] >= date_to: break
      pause()
      with expect_response("**/AvailabilityCheckApplySelectDays/AfterPeriod") as r:
          click button.btn-gray:visible:has-text("次の期間") >> nth=0
      grid = find_grid(r.value.json()); wait table.table-schedule

find_grid(data): data（配列）の中で "AvailabilitySelectDays" を持つ dict を探し、その値を返す
VUE_TIME_MODEL = "() => JSON.parse(JSON.stringify(document.querySelector('#app').__vue__.$data.model.AvailabilityTime.FacilityList))"
異常時: page.url が "/user/Error" で終わる、または XHR の status が 500 以上 → 30秒待ってから、そのグループを Home からやり直す（最大2回。2回目は 1週間表示にする）
```
- 待ち方: `networkidle` は使わない（ping が続くので終わらない）。`expect_response` でデータの XHR を待ち、その後 `wait_for_selector`。
- `pause()` は `BUNKYO_REQUEST_DELAY_SEC`。操作と操作の間に必ず入れる。

### 4-4. Slot への変換
- `ward="文京区"`
- `facility = FacilityName`（例「大原地域活動センター」。≪説明≫ は別のフィールドなので取り除く処理は不要）
- `room = Places[].ObjectName`（例「多目的室Ａ」「洋室Ａ＋Ｂ」。全角のまま。説明は `ObjectDescription` にあるので含めない）
- `date = Cells[].UseDate[:10]`（"2026-10-13T00:00:00" → "2026-10-13"）
- `start/end = f"{TimeFrom//100:02d}:{TimeFrom%100:02d}"`（900 → "09:00"、1230 → "12:30"、2130 → "21:30"）
- `Status == "vacant"` のコマだけを Slot にする（△「施設に問合せ」・抽選・申込期間外は含めない。世田谷が○だけを取っているのと同じ方針）
- `facilities.json` に文京区の行を手で追加する（ward と facility の組み合わせで突き合わせるので、名前は FacilityName と完全に一致させる）。参考 URL（施設選択画面のデータ `FacilityGuidUrl` より。区公式の旧 URL なので 301 で新ページへ転送される）:
  - 区民会議室 `https://www.city.bunkyo.lg.jp/shisetsu/kumin/shukai/kuminkaigisitsu.html`
  - 各地域活動センター `https://www.city.bunkyo.lg.jp/shisetsu/kumin/chiiki.html`
  - 元町多目的室 `…/shisetsu/kumin/shukai/kuminkaikan.html`、目白台交流館 `…/shukai/kouryu.html`、不忍通りふれあい館 `…/shukai/shinobazu.html`
  - （B案）シルバーセンター `…/hoken/koresha/koresha/silvercenter.html`、男女平等センター `https://www.bunkyo-danjo.jp/index.aspx`、福祉センター江戸川橋 `…/hoken/koresha/koresha/edogawabasi.html`、松聲閣 `https://higo-hosokawa-bunkyo.jp/meeting-rooms/`、勤労福祉会館 `…/shisetsu/kumin/shukai/kinpuku.html`

### 4-5. shard の分け方
- 単位は**施設**。`(コード, 名前)` をコード順に並べて `self.shard_items()` で分ける（世田谷と同じ考え方）。どの shard も最初に同じ検索をして同じ施設一覧を得るので、分け方は全 shard で一致する。
- shard の中では `BUNKYO_FACILITIES_PER_SESSION=3` 件ずつのグループにして、グループごとに Home からやり直す。グリッドの再読み込み（バッチごとに1回 GetAvailability が走る）の重さは選んだ施設数に比例するので、小さいグループのほうが全体として軽い。
- 推奨: `scrape.yml` に `- { ward: bunkyo, shards: 1, shard: 0 }` を足す。同時に開くセッションを1つにして相手の負荷を下げる。時間に余裕を持たせたいなら shards=2。

### 4-6. 所要時間の見積もり
計算式（待機 d 秒、施設グループ数 G、期間数 P、バッチ数 B）:
- グループの準備（Home＋検索＋選択＋次へ＋2週間表示）: 約 4×(d+4) 秒 ≒ **28秒**（d=3）
- 期間送り（AfterPeriod）: (d+5) ≒ **8秒** × (G × P)。取得期間は今日〜2か月後の月末で約88日なので P ≒ 7。
- 1バッチ（10セル）: クリック1秒＋(d＋次へ3秒)＋(d＋前に戻る7秒) ≒ **17秒**（d=3）／ **13秒**（d=1）
- バッチ数 B ≒ 受付中のセル数 × 0.5 ÷ 10 ＋ 端数バッチ（G×P のうち空きがある期間の数）

受付中の日数【確認（利用案内PDF 5-3）＋推測】: 地域活動センター・区民会館・交流館の随時予約は「使用日の属する月の1か月前の月の8日から」なので、ある時点で受付中なのは平均約38日。区民会議室・駒込のホール・ふれあい館のホールは「2か月前の月の8日から」なので平均約60日。受付前の日はグリッドで 抽選／期間外 になるので、時間帯別では確認しない。

| 案 | 部屋数 | 時間帯別で見るセル（推定） | バッチ | 準備＋期間送り | 合計（d=3） | 合計（d=1） | 最悪ケース（88日すべてが要確認） |
|---|---|---|---|---|---|---|---|
| A（9施設・3グループ） | 24 | 約520 | 約65（端数込み） | 1.5分＋3分 | **約25分** | 約18分 | 約2,100セル → 212バッチ → 約65分 |
| B（14施設・5グループ） | 44 | 約920 | 約117 | 2.5分＋5分 | **約40〜45分** | 約30分 | 約3,900セル → 388バッチ → 約2時間 |

→ どの場合も 1 shard で TIME_BUDGET_MIN=330 に収まる。1回の実行で発生する通信（A案・通常）: 操作系 約370回（1バッチ5回＝Next・文書・Previous・文書・GetAvailability）＋自動ping 約200回。これを1日2回。

### 4-7. 実装時の確認手順（相手への配慮として最小回数で）
1. `max_facilities=1, max_periods=1` で1施設×2週間だけ取得する。ログに施設一覧・グリッドのセル数・Slot 数を出し、施設名・部屋名・時刻を目で確認する。
2. 時間帯別の Status に `vacant`/`full` 以外が出たら、class 名と画面の文字をログに出す（△などの class 名を確定させるため）。
3. グリッドの Status に未知の値（抽選など）が出たらログに出す。
4. ローカル（このサンドボックス）で動かす場合の注意: Python の playwright 1.55 が想定する chromium-1187 は入っておらず、/opt/pw-browsers には 1194 しかない → `executable_path="/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"` を指定する。また、ローカルのプロキシCAを信頼させるために `args=["--ignore-certificate-errors-spki-list=<agent-proxy-ca の SPKI>"]` が必要だった。**CI（GitHub Actions）では `playwright install --with-deps chromium` を使うので、どちらも不要**。

---

## 5. リスクと代替案

| # | リスク | 影響 | 対策・代替案 |
|---|---|---|---|
| R1 | **robots.txt で照会画面がクロール禁止** | 運用してよいかの判断が必要（規約違反と明記されてはいないが、運営者の意思表示ではある） | ★ユーザーの判断。続ける場合は、頻度を1日1〜2回に抑え、待機3秒以上、shards=1、Home から順に画面を操作する（XHR を直接叩かない）。可能なら区（施設予約システムの担当）に事前に問い合わせる。代替: 区公式のPDF等で公開されている情報だけを使う（空き状況は出ないので実質的に不可） |
| R2 | 重い照会で 502（約30秒でゲートウェイタイムアウト） | 相手の負荷・セッション喪失 | 3施設×2週間を上限にする。502や /user/Error が出たら30秒待ち、Home からやり直す。2回目は1週間表示。3回失敗したらそのグループは飛ばす |
| R3 | 10セルの上限（サーバー仕様） | バッチ数が増える | 仕様なので変えられない。2週間表示にすると端数バッチが減る |
| R4 | サーバー側のセッション状態 | 戻る操作や長時間の放置で /user/Error | アプリ内のボタンだけを使う。15分以上操作が空かないようにする（通常は空かない）。Error になったら Home からやり直す |
| R5 | 自動ping を止められない | 通信が増える（1バッチあたり約2〜3回） | 仕様として受け入れる。GA/GTM だけ遮断する |
| R6 | 戻ると選択が残る | 解除し忘れると次の「次へ」が11件以上でエラー | 戻るたびに `label.btn-toggle.active` を全部解除する。解除後に0件であることを確認する |
| R7 | 抽選・公開対象外・△ の class 名が不明 | 取り漏れ・誤分類 | vacant/some だけ時間帯別で確認し、vacant のコマだけを Slot にする（安全側）。未知の値はログに出す |
| R8 | 合体室（Ａ＋Ｂ）が重複して見える | 画面の見え方 | 設定で除外できるようにする（§2-3） |
| R9 | 施設名・コード・目的コードの変更 | 取得0件 | 許可リストにない施設、許可リストにあるのに検索結果に無い施設、目的40が無い場合を、警告としてログに出す |
| R10 | 年末年始など | ログインできないだけで照会はできる | 影響なし |
| R11 | ローカルのブラウザ版のずれ・プロキシCA | ローカルで動かない | §4-7 の4を参照（CIでは問題ない） |

代替案（どれも未検証）:
- **A2: HTTP クライアントで XHR を直接呼ぶ**（httpx。Cookie と `__RequestVerificationToken` を維持しながら SearchByPurpose → SelectFacility/Next → GetAvailability → SelectDays/Next（IsChecked=true にしたフォーム）→ SelectTime の HTML に埋め込まれたモデルを読む → Previous）。静的ファイルと ping が無くなり、数倍速くなる見込み。ただしフォームの項目が多く壊れやすいこと、ブラウザらしくない通信になることから、R1 の観点では勧めない。
- **A3: 「空き」（全コマ空き）の日は時間帯別を見ずに済ませる**（同じ部屋のコマの時刻を覚えておき、それで Slot を作る）。vacant は全体の数%しかなく効果が小さいうえ、曜日や祝日でコマが違う可能性もあるので、最初は使わない。

---

## 6. 未確認事項（【不明】のまとめ）
- 世田谷けやきネットの robots.txt（この環境から 403）
- 区公式FAQ（p007799）など、規約に関わる区側の記載の全体
- 目的 41/63 などを足したときに施設・部屋が増えるか
- 抽選／抽選申込可能／公開対象外、時間帯別の △・利用時間外 の class 名と Status の値
- 地域活動センター・ふれあい館以外の施設（区民会議室、男女平等センター、福祉センター、松聲閣、勤労福祉会館など）のコマの時刻
- 施設選択の上限件数（14件までは問題なし）、2週間×3施設より大きい照会の限界（1ヶ月×14施設は失敗）
- 施設別画面の「前に戻る」で施設選択画面に戻れるか（今回は Home からやり直す経路だけ確認した）
- Home からやり直したときに表示期間（2週間など）が残るかどうか。施設選択については、14件選んだ後に Home から再検索したところ、未選択に戻っていた【確認】。ただし実装では、どちらも状態を見てから操作する

## 7. 証跡（scratchpad）
- アクセスログ: `plans/bunkyo_access_log.tsv`（整形版）、`plans/bunkyo_access_log_raw.tsv`（記録したまま）
- HTML・画面の保存: `bunkyo/html/`（`home.html`、`facility_list.html`、`days.html`（14施設）、`days3.html`、`days_2w.html`、`time.html`、`*.png`）
- XHR の記録（リクエストと応答の本文）: `bunkyo/out/api/*.json`。Vue の状態: `bunkyo/out/home_model.json`、`facility_model.json`、`time_model.json`
- 区の資料: `bunkyo/sousamanual.pdf/.txt`、`bunkyo/riyouannai.pdf/.txt`、`bunkyo/p006767.html`
- 調査に使ったスクリプト: `bunkyo/driver.py`（常駐ドライバ。プロキシCAの指定入り）、`bunkyo/run.sh`
