"""Вывод отчёта: текст в консоль и CSV для Excel."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .allocate import ContractReport, Report
from .models import money
from .state import CANCELLED, DONE, PAID

WORK_STATE_RU = {DONE: "принята", CANCELLED: "отменена"}
PAYMENT_STATE_RU = {
    PAID: "выплачен",
    "queued": "не оплачен",
    "refunded": "возвращён",
    None: "ещё не заведён",
}


def money_csv(value: Decimal) -> str:
    """Для русского Excel: без разделителя тысяч, запятая перед копейками."""
    return f"{value:.2f}".replace(".", ",")


def d(value) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def work_state_ru(state: str) -> str:
    return WORK_STATE_RU.get(state, "не принята")


def sources_str(sources, fmt=money) -> str:
    return "; ".join(f"{pid}: {fmt(s)}" for pid, s in sources)


def table(headers: list[str], rows: list[list[str]], right: set[int]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]

    def fmt(row):
        cells = [
            str(c).rjust(w) if i in right else str(c).ljust(w)
            for i, (c, w) in enumerate(zip(row, widths))
        ]
        return "  ".join(cells).rstrip()

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), sep, *(fmt(r) for r in rows)])


def contract_totals(c: ContractReport) -> list[tuple[str, Decimal]]:
    return [
        ("Сумма договора", c.contract.amount),
        ("К оплате (цены неотменённых работ)", c.due),
        ("Выплачено", c.paid),
        ("Осталось оплатить", c.remaining),
        ("Переплата", c.overpaid),
        ("В очереди на оплату (справочно)", c.queued),
        ("Принято работ на сумму", c.accepted),
        ("  из них не оплачено (долг подрядчику)", c.debt_for_accepted),
        ("Выплачено вперёд (непринятые работы + переплата)", c.advance_unearned),
        ("Возвращено подрядчиком (справочно)", c.refunded),
    ]


def render_text(report: Report) -> str:
    out: list[str] = [f"ОТЧЁТ ПО ОПЛАТЕ РАБОТ НА {d(report.on)}", ""]

    for c in report.contracts:
        k = c.contract
        out.append(f"=== Договор {k.contract_id} — {k.contractor} (подписан {d(k.signed_at)}) ===")
        out.append("")
        rows = [
            [
                l.work.work_id,
                l.work.title,
                work_state_ru(l.state),
                money(l.work.price),
                money(l.paid),
                money(l.remaining),
                sources_str(l.sources),
                "; ".join(l.notes),
            ]
            for l in c.works
        ]
        out.append(table(
            ["Работа", "Название", "На дату", "Цена", "Выплачено", "Осталось", "Из каких платежей", "Примечание"],
            rows,
            right={3, 4, 5},
        ))
        out.append("")
        if any(l.state == CANCELLED for l in c.works):
            out.append("Отменённые работы оплате не подлежат: «Осталось» по ним 0.")
            out.append("")
        out.append("Итого по договору:")
        out.append(table(["", "Сумма"], [[n, money(v)] for n, v in contract_totals(c)], right={1}))
        if c.overpaid_sources:
            out.append(f"Переплата сложилась из: {sources_str(c.overpaid_sources)}")
        out.append("")
        out.append("Платежи по договору:")
        out.append(table(
            ["Платёж", "Работа", "Назначение", "Сумма", "Оплачен", "На дату", "Примечание"],
            [
                [
                    p.payment.payment_id,
                    p.payment.work_id or "—",
                    p.payment.purpose,
                    money(p.payment.amount),
                    d(p.payment.paid_at),
                    PAYMENT_STATE_RU[p.state],
                    p.note,
                ]
                for p in c.payments
            ],
            right={3},
        ))
        if c.warnings:
            out.append("")
            out.append("Внимание:")
            out.extend(f"  ! {w}" for w in c.warnings)
        out.append("")

    out.append("=== Сводка по договорам ===")
    out.append(table(
        ["Договор", "Подрядчик", "Сумма договора", "К оплате", "Выплачено", "Осталось", "Переплата", "В очереди"],
        [
            [
                c.contract.contract_id,
                c.contract.contractor,
                money(c.contract.amount),
                money(c.due),
                money(c.paid),
                money(c.remaining),
                money(c.overpaid),
                money(c.queued),
            ]
            for c in report.contracts
        ],
        right={2, 3, 4, 5, 6, 7},
    ))
    if report.warnings:
        out.append("")
        out.append("Замечания к данным:")
        out.extend(f"  ! {w}" for w in report.warnings)
    return "\n".join(out) + "\n"


def write_csv(report: Report, out_dir: str | Path) -> list[Path]:
    """Три файла для Excel: работы, итоги по договорам, платежи. Разделитель «;», UTF-8 с BOM."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = report.on.isoformat()
    paths = []

    def write(name, headers, rows):
        path = out_dir / f"{name}_{suffix}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(headers)
            w.writerows(rows)
        paths.append(path)

    write(
        "works",
        ["Дата отчёта", "Договор", "Подрядчик", "Работа", "Название", "Статус на дату",
         "Цена", "Выплачено", "Осталось оплатить", "Из каких платежей", "Примечание"],
        [
            [d(report.on), c.contract.contract_id, c.contract.contractor, l.work.work_id,
             l.work.title, work_state_ru(l.state), money_csv(l.work.price), money_csv(l.paid),
             money_csv(l.remaining), sources_str(l.sources, money_csv), "; ".join(l.notes)]
            for c in report.contracts
            for l in c.works
        ],
    )
    totals_names = [n.strip() for n, _ in contract_totals(report.contracts[0])] if report.contracts else []
    write(
        "contracts",
        ["Дата отчёта", "Договор", "Подрядчик", *totals_names, "Замечания"],
        [
            [d(report.on), c.contract.contract_id, c.contract.contractor,
             *(money_csv(v) for _, v in contract_totals(c)), " | ".join(c.warnings)]
            for c in report.contracts
        ],
    )
    write(
        "payments",
        ["Дата отчёта", "Договор", "Платёж", "Работа", "Назначение", "Сумма", "Заведён",
         "Оплачен", "Возвращён", "Статус на дату", "Учтён в выплатах", "Примечание"],
        [
            [d(report.on), c.contract.contract_id, p.payment.payment_id, p.payment.work_id or "",
             p.payment.purpose, money_csv(p.payment.amount), d(p.payment.created_at),
             d(p.payment.paid_at), d(p.payment.refunded_at), PAYMENT_STATE_RU[p.state],
             "да" if p.state == PAID else "нет", p.note]
            for c in report.contracts
            for p in c.payments
        ],
    )
    return paths
