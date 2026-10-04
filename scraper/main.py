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

part ファイル名は `availability.part.<区キー>.<shard番号>.json`。
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
import facilities as facilities_mod
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
        if scraper.timed_out:
            where = f"の shard {label} " if shard_count > 1 else ""
            _warn("main", f"{scraper.ward_name}（{scraper.key}）{where}は時間切れで打ち切りました（{len(slots)} 件は取得済み）。")
        _dump(out, {
            "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "ward_key": scraper.key,
            "ward": scraper.ward_name,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "timed_out": scraper.timed_out,
            "slots": [s.to_dict() for s in slots],
        })
        print(f"[main] {len(slots)} 件を {out.name} に書き出しました。")
        written.append(out)

    rc = 1 if errors else 0
    if ward_key is None and shard_count == 1:
        # ローカルの全区一括実行：今回書き出した part だけを結合して availability.json を更新
        rc = run_combine(written) or rc
    return rc


def _load_existing_slots() -> list[dict]:
    if not config.AVAILABILITY_JSON.exists():
        return []
    try:
        return json.loads(config.AVAILABILITY_JSON.read_text(encoding="utf-8")).get("slots", [])
    except (OSError, ValueError) as e:
        _warn("combine", f"既存の {config.AVAILABILITY_JSON.name} を読めませんでした（引き継ぎなし）: {e}")
        return []


def run_combine(part_paths: list[Path] | None = None) -> int:
    """part ファイルを1つの availability.json に結合する。

    - part が1つも無い区（その区のジョブが全失敗、または matrix 未登録）は、既存の
      availability.json からその区の slots を引き継ぐ（区のデータが丸ごと消えるのを防ぐ）。
    - shard の一部が欠けている区・時間切れで打ち切られた区は警告を出す（データはあるものだけ使う）。

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
        add(data["slots"])
    print(f"[combine] {len(parts)} ファイル（{', '.join(sorted(by_ward))}）→ {len(slots)} 件")

    registry = _registry()

    # 区ごとの完全性チェック（shard 欠け・時間切れ）
    for ward_key, items in sorted(by_ward.items()):
        name = items[0].get("ward") or ward_key
        expected = max(int(d.get("shard_count", 1)) for d in items)
        got = {int(d.get("shard_index", 0)) for d in items}
        missing = [i for i in range(expected) if i not in got]
        if missing:
            _warn("combine", f"{name}（{ward_key}）: shard {missing} の part がありません（{len(got)}/{expected}）。該当 shard の施設は今回の結果に含まれません。")
        cut = sorted(int(d.get("shard_index", 0)) for d in items if d.get("timed_out"))
        if cut:
            _warn("combine", f"{name}（{ward_key}）: shard {cut} は時間切れで打ち切られています（期間の後半が欠けている可能性があります）。")
        if ward_key not in registry:
            _warn("combine", f"part の区キー {ward_key!r} はスクレイパーに登録されていません（データはそのまま結合します）。")

    # part が1つも無い区は既存データを引き継ぐ（過去日付の枠は除く）
    missing_wards = [cls for key, cls in registry.items() if key not in by_ward]
    if missing_wards:
        existing = _load_existing_slots()
        for cls in missing_wards:
            old = [s for s in existing if s.get("ward") == cls.ward_name and s.get("date", "") >= date_from]
            n = add(old)
            _warn("combine", f"{cls.ward_name}（{cls.key}）の part が1つもありません（ジョブが全失敗、または scrape.yml の matrix に未登録）。既存の {config.AVAILABILITY_JSON.name} から {n} 件を引き継ぎます。")

    payload = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_from": date_from,
        "date_to": date_to,
        "slots": slots,
    }
    _dump(config.AVAILABILITY_JSON, payload)
    print(f"[combine] {len(slots)} 件を {config.AVAILABILITY_JSON.name} に書き出しました。")
    facilities_mod.build_facilities_json()
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
