"""Сущности учёта: договоры, работы, платежи. Деньги — только Decimal."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal("0.00")

WORK_STATUSES = {"planned", "done", "cancelled"}
PAYMENT_STATUSES = {"paid", "queued", "refunded"}


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str  # статус на момент выгрузки
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str  # статус на момент выгрузки
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Dataset:
    contracts: dict[str, Contract]
    works: dict[str, Work]
    payments: dict[str, Payment]
    warnings: tuple[str, ...] = ()


def natural_key(value: str) -> tuple:
    """W-2 < W-10: сортировка номеров с учётом чисел внутри."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value))


def money(value: Decimal) -> str:
    """100000.5 -> '100 000,50'. Форматирование Decimal без перехода во float."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")
