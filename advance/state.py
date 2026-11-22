"""Восстановление состояния на дату отчёта.

Статусы в выгрузке — на момент выгрузки, поэтому на дату отчёта считаем по датам.
Дата отчёта включительно: событие в этот день уже произошло.
"""
from __future__ import annotations

from datetime import date

from .models import Payment, Work

# Состояния работы на дату
DONE = "done"
PLANNED = "planned"
CANCELLED = "cancelled"

# Состояния платежа на дату
PAID = "paid"          # деньги ушли и не возвращены — учитывается как выплата
QUEUED = "queued"      # заведён, но деньги ещё не ушли — справочно
REFUNDED = "refunded"  # ушли и вернулись — справочно, в выплату не входит
NOT_YET = None         # на дату отчёта платежа в учёте ещё не было


def work_state(work: Work, on: date) -> str:
    if work.completed_at and work.completed_at <= on:
        return DONE
    if work.cancelled_at and work.cancelled_at <= on:
        return CANCELLED
    return PLANNED


def payment_state(payment: Payment, on: date) -> str | None:
    # Факт движения денег важнее даты заведения в учёт.
    if payment.paid_at and payment.paid_at <= on:
        if payment.refunded_at and payment.refunded_at <= on:
            return REFUNDED
        return PAID
    if payment.created_at > on:
        return NOT_YET
    return QUEUED
