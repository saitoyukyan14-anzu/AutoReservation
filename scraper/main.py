"""スクレイパーのエントリポイント。

  python scraper/main.py                          # 全区を取得して availability.json を更新（ローカル用）
  python scraper/main.py --months 2               # 2ヶ月先の末日まで
  python scraper/main.py --ward setagaya          # 1区だけ取得 → part ファイルを出力
  python scraper/main.py --combine                # part ファイルを結合 → availability.json
  # 並列実行（GitHub Actions マトリクス用。「区 × shard」ごとに1ジョブ）:
  python scraper/main.py --ward setagaya --shard-index 0 --shard-count 5
      # → web/public/data/availability.part.setagaya.0.json
  # 時間予算（分）。過ぎたらそこまでの取得分で打ち切って書き出す（0 以下で無制限）:
  python scraper/main.py --ward setagaya --time-budget-min 330

part ファイル名は `availability.part.<区キー>.<shard番号>.json`。主なフィールド:
  slots            取得した枠
  timed_out        時間切れ（またはリクエスト上限・エラーの連続など）で打ち切ったか
  stop_reason      時間切れ以外で打ち切った場合の理由（ログ用）。null は時間切れ／打ち切りなし
  facilities       この shard の担当施設名一覧（正規化後）。null は「不明」
  completed_until  全担当施設について取得を完了した最終日。null は「完了した日なし」
  facility_completed_until  施設ごとの完了日 {施設名: 日付 or null}。null（キーなし）は「未記録」
                   （打ち切り時の補完は、これがあれば施設単位、無ければ completed_until で行う）
  carry_over_facilities  設計上今回は取得しない施設（輪番など）。combine が前回データをそのまま引き継ぐ
facilities キーが無い part は旧形式として扱う（combine での補完なし＝従来どおり）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

import config
from scrapers import ALL_SCRAPERS
from scrapers.base import WardScraper

#: 時間予算の起点（プロセス開始時刻）
_STARTED_AT = time.monotonic()

PART_GLOB = "availability.part.*.json"
_PART_NAME_RE = re.compile(r"^availability\.part\.(?P<key>[a-z0-9_]+)\.(?P<shard>\d+)\.json$")
_KEY_RE = re.compile(r"^[a-z0-9_]+$")


def month_end_ahead(d: dt.date, months: int) -> dt.date:
    """d から months ヶ月先の「月末日」を返す。例) 2026-06-10 +2 → 2026-08-31"""
    total = (d.month - 1) + months
    year = d.year + total // 12
    month = total % 12 + 1
    first_of_next = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return first_of_next - dt.timedelta(days=1)


def _warn(tag: str, msg: str) -> None:
    """警告ログ。GitHub Actions 上ではアノテーション（実行サマリーに表示）にする。"""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title={tag}::{msg}")
    else:
        print(f"[{tag}] 警告: {msg}")


def _registry() -> dict[str, type[WardScraper]]:
    """区キー → スクレイパークラス。キーの形式と重複を検査する。"""
    registry: dict[str, type[WardScraper]] = {}
    for cls in ALL_SCRAPERS:
        if not _KEY_RE.match(cls.key or ""):
            raise ValueError(f"{cls.__name__} の key が不正です（英小文字・数字・_ のみ）: {cls.key!r}")
        if cls.key in registry:
            raise ValueError(f"区キー {cls.key!r} が重複しています: {registry[cls.key].__name__}, {cls.__name__}")
        registry[cls.key] = cls
    return registry


def _make_scraper(scraper_cls, shard_index: int, shard_count: int, deadline: float | None):
    """スクレイパーを生成する。shard 非対応の区を shard_count>1 で生成すると例外になる。"""
    scraper = scraper_cls(shard_index=shard_index, shard_count=shard_count, deadline=deadline)
    # __init__ を独自定義して super().__init__ を呼び忘れると shard が効かず重複取得になるので検査
    if getattr(scraper, "shard_count", None) != shard_count or getattr(scraper, "shard_index", None) != shard_index:
        raise ValueError(
            f"{scraper_cls.__name__} が shard 指定を保持していません"
            "（__init__ で super().__init__(shard_index=..., shard_count=..., deadline=...) を呼んでください）"
        )
    return scraper


def part_path(ward_key: str, shard_index: int) -> Path:
    return config.DATA_DIR / f"availability.part.{ward_key}.{shard_index}.json"


def _dump(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_scrape(
    months_ahead: int,
    ward_key: str | None,
    shard_index: int,
    shard_count: int,
    deadline: float | None,
) -> int:
    """取得して区ごとの part ファイルを書き出す。

    --ward 指定時・shard 実行時は part ファイルの出力まで（結合は --combine）。
    --ward なし・分割なし（ローカルの全区一括実行）なら、その場で結合して availability.json を更新する。
    """
    today = dt.date.today()
    date_from = today
    date_to = month_end_ahead(today, months_ahead)

    try:
        registry = _registry()
        if ward_key is not None and ward_key not in registry:
            raise ValueError(f"未登録の区キーです: {ward_key!r}（登録済み: {', '.join(registry)}）")
        classes = [registry[ward_key]] if ward_key else list(registry.values())
        # 取得を始める前に全区を生成して検査する（shard 非対応の区が混ざっていたら即エラー）
        scrapers = [_make_scraper(c, shard_index, shard_count, deadline) for c in classes]
    except (ValueError, TypeError) as e:
        print(f"[main] エラー: {e}")
        return 2

    label = f"{shard_index + 1}/{shard_count}" if shard_count > 1 else "分割なし"
    budget = "無制限" if deadline is None else f"残り {(deadline - time.monotonic()) / 60:.1f} 分"
    target = ward_key or "全区"
    print(f"[main] 取得期間: {date_from} 〜 {date_to} / 対象: {target} / shard {label} / 時間予算 {budget}")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    errors = 0
    for scraper in scrapers:
        out = part_path(scraper.key, shard_index)
        print(f"[main] {scraper.ward_name}（{scraper.key}）を取得中...")
        try:
            slots = scraper.scrape(date_from, date_to)
        except Exception:
            errors += 1
            print(f"[main]   ! {scraper.ward_name} の取得に失敗:")
            traceback.print_exc()
            # 失敗時は part を出さない（combine で「欠落」と判定させ、既存データを引き継がせる）
            if out.exists():
                out.unlink()
            continue
        print(f"[main]   → {len(slots)} 件")
        facilities = scraper.assigned_facilities
        completed_until = scraper.completed_until
        if completed_until is None and not scraper.timed_out:
            # 記録しないスクレイパーでも、最後まで取得できたなら期間の最終日まで完了している
            completed_until = date_to
        stop_reason = getattr(scraper, "stop_reason", None)
        if scraper.timed_out:
            where = f"の shard {label} " if shard_count > 1 else ""
            done = f"{completed_until} まで取得完了" if completed_until else "取得を完了した日なし"
            why = stop_reason or "時間切れ"
            _warn("main", f"{scraper.ward_name}（{scraper.key}）{where}は{why}で打ち切りました（{len(slots)} 件は取得済み・{done}）。")
        if facilities is None and not (scraper.timed_out and not slots):
            print(f"[main]   ※ {scraper.ward_name} は担当施設を記録していません（combine で欠けた shard・時間切れ分の補完ができません）。")
        _dump(out, {
            "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "ward_key": scraper.key,
            "ward": scraper.ward_name,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "timed_out": scraper.timed_out,
            "stop_reason": stop_reason if scraper.timed_out else None,
            "facilities": facilities,
            "completed_until": completed_until.isoformat() if completed_until else None,
            "facility_completed_until": _facility_until(getattr(scraper, "facility_completed_until", None)),
            "carry_over_facilities": getattr(scraper, "carry_over_facilities", None),
            "slots": [s.to_dict() for s in slots],
        })
        print(f"[main] {len(slots)} 件を {out.name} に書き出しました。")
        written.append(out)

    rc = 1 if errors else 0
    if ward_key is None and shard_count == 1:
        # ローカルの全区一括実行：今回書き出した part だけを結合して availability.json を更新
        rc = run_combine(written) or rc
    return rc


def _facility_until(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {name: (d.isoformat() if d else None) for name, d in sorted(value.items())}


def _load_existing_slots() -> list[dict]:
    if not config.AVAILABILITY_JSON.exists():
        return []
    try:
        return json.loads(config.AVAILABILITY_JSON.read_text(encoding="utf-8")).get("slots", [])
    except (OSError, ValueError) as e:
        _warn("combine", f"既存の {config.AVAILABILITY_JSON.name} を読めませんでした（引き継ぎなし）: {e}")
        return []


def _part_kind(part: dict) -> str:
    """part の種類。

    - "known":   担当施設が記録されている → 欠けた shard・時間切れ分を補完できる
    - "noinfo":  担当施設不明・時間切れ・0 件（取得開始前に期限超過など）→ part が無いものとして扱う
    - "unknown": 担当施設不明（記録しないスクレイパー）→ 補完なし（従来どおり）
    - "legacy":  旧形式（facilities キーが無い）→ 補完なし（従来どおり）
    """
    if "facilities" not in part:
        return "legacy"
    if isinstance(part["facilities"], list):
        return "known"
    if part.get("timed_out") and not part.get("slots"):
        return "noinfo"
    return "unknown"


def _part_coverage(part: dict, tag: str) -> set[str]:
    """known な part が担当した施設名の集合（担当施設一覧 ∪ 枠に出てくる施設名）。

    施設選択画面と結果画面で施設名の表記がずれた場合でも、枠が取れた施設は担当に含める。
    """
    assigned = set(part["facilities"])
    seen = {s["facility"] for s in part["slots"]}
    extra = sorted(seen - assigned)
    if extra:
        _warn("combine", f"{tag} の枠に担当施設一覧に無い施設名があります（担当に含めて扱います）: {_names(extra)}")
    return assigned | seen


def _completed_until(part: dict, tag: str) -> str | None:
    """part の completed_until（YYYY-MM-DD）。無い・不正なら None（完了日なし）。"""
    value = part.get("completed_until")
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        _warn("combine", f"{tag} の completed_until が不正です（完了日なしとして扱います）: {value!r}")
        return None


def _facility_completed_until(part: dict, tag: str) -> dict[str, str | None] | None:
    """part の facility_completed_until（{施設名: YYYY-MM-DD or None}）。無い・不正なら None（未記録）。"""
    value = part.get("facility_completed_until")
    if not isinstance(value, dict):
        return None
    out: dict[str, str | None] = {}
    for fac, d in value.items():
        try:
            out[fac] = dt.date.fromisoformat(d).isoformat() if d is not None else None
        except (TypeError, ValueError):
            _warn("combine", f"{tag} の facility_completed_until[{fac!r}] が不正です（完了日なしとして扱います）: {d!r}")
            out[fac] = None
    return out


def _names(names: list[str], limit: int = 10) -> str:
    shown = "、".join(names[:limit])
    return shown + (f" ほか {len(names) - limit} 施設" if len(names) > limit else "")


def run_combine(part_paths: list[Path] | None = None) -> int:
    """part ファイルを1つの availability.json に結合する。

    区ごとに、今回取得できなかった部分を既存の availability.json（前回データ）から補う
    （いずれも date_from より前の枠は捨てる。同じ枠は今回取得分を優先）:

    - part が1つも無い区（ジョブが全失敗、または matrix 未登録）は、その区の枠をすべて引き継ぐ。
    - shard が欠けた区は、今回どの part の担当施設にも含まれない施設の枠を引き継ぐ。
      取得開始前に時間切れになった part（担当施設不明・0 件）も「欠けた shard」として扱う。
    - 時間切れ（打ち切り）の part は、その担当施設の枠のうち completed_until より後の日付を引き継ぐ
      （completed_until が null なら担当施設の全期間）。part に facility_completed_until があれば
      施設ごとの完了日で同じことを行い、最後まで取れた施設には補わない。いずれも今回取得できた
      （施設, 日付）には補わない。
    - part の carry_over_facilities（設計上今回は取得しない施設。輪番など）は、前回データをそのまま
      引き継ぐ（情報ログのみ）。これらは「担当に無い施設」として捨てる対象にはならない。
    - 旧形式・担当施設不明の part は補完せず警告だけ出す（従来どおり）。その part がある区では
      欠けた shard の補完もしない（どの施設が欠けたのか判断できないため）。
    - 全 shard がそろった区では、どの part の担当にも無い施設（前回データにしか無い施設）は
      引き継がない。

    part_paths を省略すると DATA_DIR の `availability.part.*.json` をすべて結合する。
    """
    parts = sorted(part_paths) if part_paths is not None else sorted(config.DATA_DIR.glob(PART_GLOB))
    if not parts:
        print("[combine] part ファイルが見つかりません。")
        return 1

    seen: set[tuple] = set()
    slots: list[dict] = []

    def add(items: list[dict]) -> int:
        n = 0
        for s in items:
            key = (s["ward"], s["facility"], s["room"], s["date"], s["start"], s["end"])
            if key not in seen:
                seen.add(key)
                slots.append(s)
                n += 1
        return n

    by_ward: dict[str, list[dict]] = defaultdict(list)
    date_from = date_to = None
    for p in parts:
        data = json.loads(p.read_text(encoding="utf-8"))
        m = _PART_NAME_RE.match(p.name)
        ward_key = data.get("ward_key") or (m.group("key") if m else p.name)
        by_ward[ward_key].append(data)
        date_from = min(date_from, data["date_from"]) if date_from else data["date_from"]
        date_to = max(date_to, data["date_to"]) if date_to else data["date_to"]
        add(data["slots"])  # 今回取得分を先に入れる（引き継ぎ分と重なったら今回分が残る）
    print(f"[combine] {len(parts)} ファイル（{', '.join(sorted(by_ward))}）→ {len(slots)} 件")

    registry = _registry()
    src = config.AVAILABILITY_JSON.name
    existing: list[dict] | None = None

    def old_slots(ward_name: str) -> list[dict]:
        """既存データのうち、その区の date_from 以降の枠（過去日付は捨てる）。"""
        nonlocal existing
        if existing is None:
            existing = _load_existing_slots()
        return [s for s in existing if s.get("ward") == ward_name and s.get("date", "") >= date_from]

    for ward_key in sorted(set(registry) | set(by_ward)):
        items = sorted(by_ward.get(ward_key, []), key=lambda d: int(d.get("shard_index", 0)))
        cls = registry.get(ward_key)
        name = (items[0].get("ward") if items else None) or (cls.ward_name if cls else ward_key)
        tag = f"{name}（{ward_key}）"
        if cls is None:
            _warn("combine", f"part の区キー {ward_key!r} はスクレイパーに登録されていません（データはそのまま結合します）。")

        # part が1つも無い区は、その区の枠をすべて引き継ぐ
        if not items:
            n = add(old_slots(name))
            _warn("combine", f"{tag} の part が1つもありません（ジョブが全失敗、または scrape.yml の matrix に未登録）。既存の {src} から {n} 件を引き継ぎます。")
            continue

        expected = max(int(d.get("shard_count", 1)) for d in items)
        kinds = [(int(d.get("shard_index", 0)), d, _part_kind(d)) for d in items]
        got = {i for i, _d, _k in kinds}
        missing = [i for i in range(expected) if i not in got]
        noinfo = [i for i, _d, k in kinds if k == "noinfo"]
        opaque = [i for i, _d, k in kinds if k in ("legacy", "unknown")]
        coverage = {i: _part_coverage(d, f"{tag} shard {i}") for i, d, k in kinds if k == "known"}
        covered: set[str] = set().union(*coverage.values())

        if noinfo:
            _warn("combine", f"{tag}: shard {noinfo} は取得開始前に時間切れになりました（担当施設不明・0 件）。part が無いものとして扱います。")

        # 設計上今回は取得しない施設（輪番など）は、前回データをそのまま引き継ぐ（警告ではなく情報）
        carry: set[str] = set()
        for i, d, k in kinds:
            if k == "known" and isinstance(d.get("carry_over_facilities"), list):
                carry |= set(d["carry_over_facilities"])
        carry -= covered  # 今回取得した施設は引き継がない
        if carry:
            n = add([s for s in old_slots(name) if s["facility"] in carry])
            print(f"[combine] {tag}: 今回は取得しない設計の {len(carry)} 施設（輪番など）は、前回データ {n} 件をそのまま引き継ぎます: {_names(sorted(carry))}")
        covered |= carry

        # 欠けた shard（part なし・取得開始前に時間切れ）の施設を補う
        holes = sorted(missing + noinfo)
        if holes:
            ok = expected - len(holes)
            if opaque:
                _warn("combine", f"{tag}: shard {holes} の結果がありません（{ok}/{expected}）。担当施設がわからない part（shard {opaque}：旧形式など）があるため補完できません。該当 shard の施設は今回の結果に含まれません。")
            else:
                fill = [s for s in old_slots(name) if s["facility"] not in covered]
                n = add(fill)
                facs = sorted({s["facility"] for s in fill})
                which = "区の全施設" if not covered else "今回どの shard の担当にも含まれない施設"
                if facs:
                    _warn("combine", f"{tag}: shard {holes} の結果がありません（{ok}/{expected}）。{which}（{len(facs)} 施設）の枠 {n} 件を既存の {src}（前回データ）から補いました。今回は取得していない古いデータです（shard が欠け続けると補完も続きます）: {_names(facs)}")
                else:
                    _warn("combine", f"{tag}: shard {holes} の結果がありません（{ok}/{expected}）。{which}について既存の {src} に補える前回データはありませんでした。該当 shard の施設は今回の結果に含まれません。")
        elif not opaque:
            # 全 shard がそろった区：前回データにしか無い施設は引き継がない（残り続けないように）
            gone = sorted({s["facility"] for s in old_slots(name)} - covered)
            if not covered:
                _warn("combine", f"{tag}: 全 shard がそろっていますが担当施設が0件でした（サイト変更などで施設を選べなかった可能性があります）。この区の枠は0件になります。")
            elif gone:
                print(f"[combine] {tag}: 今回どの shard の担当にも無い {len(gone)} 施設は前回データから引き継ぎません: {_names(gone)}")

        # 時間切れの part は、担当施設の completed_until より後の日付を補う
        for i, d, kind in kinds:
            if not d.get("timed_out") or kind == "noinfo":
                continue
            why = d.get("stop_reason") or "時間切れ"
            if kind != "known":
                _warn("combine", f"{tag}: shard {i} は{why}で打ち切られています（期間の後半が欠けている可能性があります）。担当施設がわからない part（旧形式など）のため補完できません。")
                continue
            facs = coverage[i]
            until = _completed_until(d, f"{tag} shard {i}")
            by_facility = _facility_completed_until(d, f"{tag} shard {i}")
            # 打ち切られたウィンドウ内でも、今回取得できた（施設, 日付）には前回の枠を混ぜない
            fetched = {(s["facility"], s["date"]) for s in d.get("slots", [])}
            olds = old_slots(name)
            fill: list[dict] = []
            complete: list[str] = []
            for fac in sorted(facs):
                # 施設ごとの完了日があればそれを使う（無い施設・無い part は completed_until）
                fac_until = by_facility[fac] if by_facility is not None and fac in by_facility else until
                if by_facility is not None and fac_until is not None and fac_until >= d["date_to"]:
                    complete.append(fac)  # 最後まで取れた施設には補わない
                    continue
                fill += [
                    s for s in olds
                    if s["facility"] == fac
                    and (fac_until is None or s["date"] > fac_until)
                    and (s["facility"], s["date"]) not in fetched
                ]
            n = add(fill)
            filled = sorted({s["facility"] for s in fill})
            detail = f": {_names(filled)}" if filled else ""
            if by_facility is not None:
                partial = len(facs) - len(complete)
                _warn("combine", f"{tag}: shard {i} は{why}で打ち切られました（施設ごとの完了日で補完。担当 {len(facs)} 施設のうち最後まで取得 {len(complete)} 施設は補わず、残り {partial} 施設の未取得の日付の枠 {n} 件を既存の {src}（前回データ）から補いました。今回取得できた日は補っていません）{detail}")
                continue
            if until is None:
                what = f"取得を完了した日がありません。担当 {len(facs)} 施設の全期間"
            else:
                after = (dt.date.fromisoformat(until) + dt.timedelta(days=1)).isoformat()
                what = f"{until} まで取得完了。担当 {len(facs)} 施設の {after} 以降"
            _warn("combine", f"{tag}: shard {i} は{why}で打ち切られました（{what}の枠 {n} 件を既存の {src}（前回データ）から補いました。今回取得できた日は補っていません）{detail}")

    payload = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_from": date_from,
        "date_to": date_to,
        "slots": slots,
    }
    _dump(config.AVAILABILITY_JSON, payload)
    print(f"[combine] {len(slots)} 件を {config.AVAILABILITY_JSON.name} に書き出しました。")
    for p in parts:  # 中間ファイルは掃除
        p.unlink()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=config.SCRAPE_MONTHS_AHEAD)
    parser.add_argument("--ward", help="この区キーの区だけ取得する（例: setagaya）。part ファイルを出力")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument(
        "--time-budget-min", type=float, default=config.TIME_BUDGET_MIN,
        help="時間予算（分）。過ぎたらそこまでの取得分で打ち切る。0 以下で無制限（既定: %(default)s）",
    )
    parser.add_argument("--combine", action="store_true", help="part ファイルを結合する")
    args = parser.parse_args()

    if args.combine:
        sys.exit(run_combine())
    deadline = _STARTED_AT + args.time_budget_min * 60 if args.time_budget_min > 0 else None
    sys.exit(run_scrape(args.months, args.ward, args.shard_index, args.shard_count, deadline))


if __name__ == "__main__":
    main()
