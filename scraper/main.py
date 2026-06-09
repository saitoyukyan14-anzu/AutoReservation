"""スクレイパーのエントリポイント。

  python scraper/main.py            # 全区を取得して data/*.json を更新
  python scraper/main.py --days 14  # 取得日数を指定

GitHub Actions から定期実行される。
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


def run(days_ahead: int) -> int:
    today = dt.date.today()
    date_from = today
    date_to = today + dt.timedelta(days=days_ahead)
    print(f"[main] 取得期間: {date_from} 〜 {date_to}")

    all_slots = []
    errors = 0
    for scraper_cls in ALL_SCRAPERS:
        scraper = scraper_cls()
        print(f"[main] {scraper.ward_name} を取得中...")
        try:
            slots = scraper.scrape(date_from, date_to)
            print(f"[main]   → {len(slots)} 件")
            all_slots.extend(slots)
        except Exception:  # 1区が失敗しても他区は続行する
            errors += 1
            print(f"[main]   ! {scraper.ward_name} の取得に失敗しました:")
            traceback.print_exc()

    payload = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "slots": [s.to_dict() for s in all_slots],
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.AVAILABILITY_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[main] {len(all_slots)} 件を {config.AVAILABILITY_JSON} に書き出しました。")

    facilities_mod.build_facilities_json()
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=config.SCRAPE_DAYS_AHEAD)
    args = parser.parse_args()
    errors = run(args.days)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
