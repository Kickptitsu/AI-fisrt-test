"""Чтение CSV из data/ и проверка целостности.

Ошибки (данные нельзя посчитать) собираются все сразу и выбрасываются одним
DataError. Подозрительное, но считаемое — в Dataset.warnings.
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    PAYMENT_STATUSES,
    WORK_STATUSES,
    Contract,
    Dataset,
    Payment,
    Work,
)


class DataError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Ошибки в данных:\n" + "\n".join(f"  - {e}" for e in errors))


def parse_money(raw: str) -> Decimal:
    """Строка из CSV -> Decimal с копейками. Float не участвует."""
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"не сумма: {raw!r}")
    if not value.is_finite():
        raise ValueError(f"не сумма: {raw!r}")
    if value.as_tuple().exponent < -2:
        raise ValueError(f"больше двух знаков после запятой: {raw!r}")
    return value.quantize(Decimal("0.01"))


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def load(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    errors: list[str] = []
    warnings: list[str] = []

    def field(where: str, fn, raw):
        try:
            return fn(raw)
        except ValueError as e:
            errors.append(f"{where}: {e}")
            return None

    contracts: dict[str, Contract] = {}
    for row in _read(data_dir / "contracts.csv"):
        cid = row["contract_id"]
        where = f"contracts.csv {cid}"
        if cid in contracts:
            errors.append(f"{where}: повторяющийся номер договора")
            continue
        amount = field(where, parse_money, row["amount"])
        signed = field(where, parse_date, row["signed_at"])
        if not row["signed_at"]:
            errors.append(f"{where}: нет даты подписания")
        contracts[cid] = Contract(cid, row["contractor"], amount, signed)

    works: dict[str, Work] = {}
    for row in _read(data_dir / "works.csv"):
        wid = row["work_id"]
        where = f"works.csv {wid}"
        if wid in works:
            errors.append(f"{where}: повторяющийся номер работы")
            continue
        if row["contract_id"] not in contracts:
            errors.append(f"{where}: неизвестный договор {row['contract_id']}")
        status = row["status"]
        if status not in WORK_STATUSES:
            errors.append(f"{where}: неизвестный статус {status!r}")
        price = field(where, parse_money, row["price"])
        if price is not None and price < 0:
            errors.append(f"{where}: отрицательная цена")
        completed = field(where, parse_date, row["completed_at"])
        cancelled = field(where, parse_date, row["cancelled_at"])
        if status == "done" and completed is None:
            errors.append(f"{where}: статус done без даты приёмки")
        if status == "cancelled" and cancelled is None:
            errors.append(f"{where}: статус cancelled без даты отмены")
        if status == "planned" and (completed or cancelled):
            warnings.append(f"{wid}: статус planned, но заполнена дата приёмки/отмены — дата игнорируется")
            completed = cancelled = None
        if status == "done" and cancelled:
            warnings.append(f"{wid}: работа принята, но заполнена дата отмены — дата отмены игнорируется")
            cancelled = None
        if status == "cancelled" and completed:
            warnings.append(f"{wid}: работа отменена, но заполнена дата приёмки — дата приёмки игнорируется")
            completed = None
        works[wid] = Work(wid, row["contract_id"], row["title"], price, status, completed, cancelled)

    payments: dict[str, Payment] = {}
    for row in _read(data_dir / "payments.csv"):
        pid = row["payment_id"]
        where = f"payments.csv {pid}"
        if pid in payments:
            errors.append(f"{where}: повторяющийся номер платежа")
            continue
        cid = row["contract_id"]
        if cid not in contracts:
            errors.append(f"{where}: неизвестный договор {cid}")
        work_id = row["work_id"] or None
        if work_id is not None:
            if work_id not in works:
                errors.append(f"{where}: неизвестная работа {work_id}")
            elif works[work_id].contract_id != cid:
                errors.append(
                    f"{where}: работа {work_id} относится к договору "
                    f"{works[work_id].contract_id}, а платёж — к {cid}"
                )
        status = row["status"]
        if status not in PAYMENT_STATUSES:
            errors.append(f"{where}: неизвестный статус {status!r}")
        amount = field(where, parse_money, row["amount"])
        if amount is not None and amount <= 0:
            errors.append(f"{where}: сумма должна быть больше нуля")
        created = field(where, parse_date, row["created_at"])
        paid = field(where, parse_date, row["paid_at"])
        refunded = field(where, parse_date, row["refunded_at"])
        if not row["created_at"]:
            errors.append(f"{where}: нет даты заведения платежа")
        if status in ("paid", "refunded") and paid is None:
            errors.append(f"{where}: статус {status} без даты оплаты")
        if status == "refunded" and refunded is None:
            errors.append(f"{where}: статус refunded без даты возврата")
        if paid and refunded and refunded < paid:
            errors.append(f"{where}: возврат раньше оплаты")
        if status == "queued" and (paid or refunded):
            warnings.append(f"{pid}: статус queued, но заполнены даты оплаты/возврата — платёж считается неоплаченным")
            paid = refunded = None
        if status == "paid" and refunded:
            warnings.append(f"{pid}: статус paid, но заполнена дата возврата — дата возврата игнорируется")
            refunded = None
        payments[pid] = Payment(
            pid, cid, work_id, row["purpose"], amount, status, created, paid, refunded
        )

    if errors:
        raise DataError(errors)
    return Dataset(contracts, works, payments, tuple(warnings))
