"""スクレイパーのエントリポイント。

  python scraper/main.py                          # 全施設を取得して JSON を更新
  python scraper/main.py --months 2               # 2ヶ月先の末日まで
  # 並列実行（GitHub Actions マトリクス用）:
  python scraper/main.py --shard-index 0 --shard-count 5   # 5分割の0番だけ取得
  python scraper/main.py --combine --shard-count 5         # 分割結果を結合
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback

import config
import facilities as facilities_mod
from scrapers import ALL_SCRAPERS


def month_end_ahead(d: dt.date, months: int) -> dt.date:
    """d から months ヶ月先の「月末日」を返す。例) 2026-06-10 +2 → 2026-08-31"""
    total = (d.month - 1) + months
    year = d.year + total // 12
    month = total % 12 + 1
    first_of_next = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return first_of_next - dt.timedelta(days=1)


def _make_scraper(scraper_cls, shard_index: int, shard_count: int):
    """shard 引数を受け付けるスクレイパーには渡す（受け付けないものは無印で生成）。"""
    try:
        return scraper_cls(shard_index=shard_index, shard_count=shard_count)
    except TypeError:
        return scraper_cls()


def _part_path(shard_index: int):
    return config.DATA_DIR / f"availability.part{shard_index}.json"


def run_scrape(months_ahead: int, shard_index: int, shard_count: int) -> int:
    today = dt.date.today()
    date_from = today
    date_to = month_end_ahead(today, months_ahead)
    label = f"{shard_index + 1}/{shard_count}" if shard_count > 1 else "全件"
    print(f"[main] 取得期間: {date_from} 〜 {date_to} / shard {label}")

    all_slots, errors = [], 0
    for scraper_cls in ALL_SCRAPERS:
        scraper = _make_scraper(scraper_cls, shard_index, shard_count)
        print(f"[main] {scraper.ward_name} を取得中...")
        try:
            slots = scraper.scrape(date_from, date_to)
            print(f"[main]   → {len(slots)} 件")
            all_slots.extend(slots)
        except Exception:
            errors += 1
            print(f"[main]   ! {scraper.ward_name} の取得に失敗:")
            traceback.print_exc()

    payload = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "slots": [s.to_dict() for s in all_slots],
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if shard_count > 1:
        # 並列実行：自分の担当分だけ part ファイルに書き出す（結合は別ステップ）
        out = _part_path(shard_index)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[main] {len(all_slots)} 件を {out.name} に書き出しました。")
    else:
        config.AVAILABILITY_JSON.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[main] {len(all_slots)} 件を {config.AVAILABILITY_JSON.name} に書き出しました。")
        facilities_mod.build_facilities_json()
    return errors


def run_combine() -> int:
    """並列ジョブが生成した availability.part*.json を1つに結合する。"""
    parts = sorted(config.DATA_DIR.glob("availability.part*.json"))
    if not parts:
        print("[combine] part ファイルが見つかりません。")
        return 1

    seen, slots = set(), []
    date_from = date_to = None
    for p in parts:
        data = json.loads(p.read_text(encoding="utf-8"))
        date_from = min(date_from, data["date_from"]) if date_from else data["date_from"]
        date_to = max(date_to, data["date_to"]) if date_to else data["date_to"]
        for s in data["slots"]:
            key = (s["ward"], s["facility"], s["room"], s["date"], s["start"], s["end"])
            if key not in seen:
                seen.add(key)
                slots.append(s)
    print(f"[combine] {len(parts)} ファイル → {len(slots)} 件に結合")

    payload = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_from": date_from,
        "date_to": date_to,
        "slots": slots,
    }
    config.AVAILABILITY_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    facilities_mod.build_facilities_json()
    for p in parts:  # 中間ファイルは掃除
        p.unlink()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=config.SCRAPE_MONTHS_AHEAD)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--combine", action="store_true", help="part ファイルを結合する")
    args = parser.parse_args()

    if args.combine:
        sys.exit(run_combine())
    errors = run_scrape(args.months, args.shard_index, args.shard_count)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
