"""Отчёт по оплате работ на дату.

    python report.py --date 2026-11-10
    python report.py --date 10.11.2026 --csv out
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from advance.allocate import build_report
from advance.loader import DataError, load
from advance.render import render_text, write_csv


def parse_report_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"дата должна быть ГГГГ-ММ-ДД или ДД.ММ.ГГГГ: {raw!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт: сколько выплачено и сколько осталось по работам и договорам на дату.")
    parser.add_argument("--date", required=True, type=parse_report_date,
                        help="дата отчёта включительно: 2026-11-10 или 10.11.2026")
    parser.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"),
                        help="папка с contracts.csv, works.csv, payments.csv (по умолчанию data/ рядом со скриптом)")
    parser.add_argument("--csv", metavar="DIR", help="дополнительно сохранить отчёт в CSV-файлы для Excel в эту папку")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        dataset = load(args.data)
    except DataError as e:
        print(e, file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"Нет файла данных: {e.filename}", file=sys.stderr)
        return 2

    report = build_report(dataset, args.date)
    print(render_text(report), end="")
    if args.csv:
        for path in write_csv(report, args.csv):
            print(f"Сохранено: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
