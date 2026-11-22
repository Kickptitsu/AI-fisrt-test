"""Разнесение выплат по работам на дату отчёта.

Правило (см. DECISIONS.md):
1. Платёж, привязанный к работе, сначала гасит цену этой работы.
   Излишек сверх цены и деньги, привязанные к отменённой работе, уходят в общий пул договора.
2. Общий пул договора (непривязанные платежи + излишки) гасит работы по очереди (FIFO):
   сначала принятые — по дате приёмки, затем непринятые — по номеру работы.
   Деньги из пула берутся в порядке дат оплаты.
3. Что осталось в пуле после закрытия всех работ — переплата по договору.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import ZERO, Contract, Dataset, Payment, Work, money, natural_key
from .state import CANCELLED, DONE, PAID, QUEUED, REFUNDED, payment_state, work_state


@dataclass
class WorkLine:
    work: Work
    state: str
    paid: Decimal = ZERO
    sources: list[tuple[str, Decimal]] = field(default_factory=list)  # (payment_id, сумма)
    notes: list[str] = field(default_factory=list)

    @property
    def price_due(self) -> Decimal:
        """Сколько по работе надо заплатить всего: отменённая — ноль."""
        return ZERO if self.state == CANCELLED else self.work.price

    @property
    def remaining(self) -> Decimal:
        return self.price_due - self.paid

    def take(self, payment_id: str, amount: Decimal) -> None:
        self.paid += amount
        for i, (pid, sum_) in enumerate(self.sources):
            if pid == payment_id:
                self.sources[i] = (pid, sum_ + amount)
                return
        self.sources.append((payment_id, amount))


@dataclass
class PaymentLine:
    payment: Payment
    state: str | None
    note: str = ""


@dataclass
class ContractReport:
    contract: Contract
    works: list[WorkLine]
    payments: list[PaymentLine]
    overpaid: Decimal = ZERO
    overpaid_sources: list[tuple[str, Decimal]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _sum(self, values) -> Decimal:
        return sum(values, ZERO)

    @property
    def works_total(self) -> Decimal:
        """Сумма цен всех работ, включая отменённые."""
        return self._sum(w.work.price for w in self.works)

    @property
    def due(self) -> Decimal:
        """К оплате: цены неотменённых работ."""
        return self._sum(w.price_due for w in self.works)

    @property
    def paid(self) -> Decimal:
        return self._sum(p.payment.amount for p in self.payments if p.state == PAID)

    @property
    def paid_to_works(self) -> Decimal:
        return self._sum(w.paid for w in self.works)

    @property
    def remaining(self) -> Decimal:
        return self._sum(w.remaining for w in self.works)

    @property
    def queued(self) -> Decimal:
        return self._sum(p.payment.amount for p in self.payments if p.state == QUEUED)

    @property
    def refunded(self) -> Decimal:
        return self._sum(p.payment.amount for p in self.payments if p.state == REFUNDED)

    @property
    def accepted(self) -> Decimal:
        """Принято работ на сумму."""
        return self._sum(w.work.price for w in self.works if w.state == DONE)

    @property
    def debt_for_accepted(self) -> Decimal:
        """Работы приняты, но не оплачены."""
        return self._sum(w.remaining for w in self.works if w.state == DONE)

    @property
    def advance_unearned(self) -> Decimal:
        """Выплачено вперёд: деньги на непринятых работах плюс переплата."""
        return self._sum(w.paid for w in self.works if w.state != DONE) + self.overpaid


@dataclass
class Report:
    on: date
    contracts: list[ContractReport]
    warnings: list[str]


def _fifo_order(lines: list[WorkLine]) -> list[WorkLine]:
    done = sorted(
        (l for l in lines if l.state == DONE),
        key=lambda l: (l.work.completed_at, natural_key(l.work.work_id)),
    )
    planned = sorted(
        (l for l in lines if l.state not in (DONE, CANCELLED)),
        key=lambda l: natural_key(l.work.work_id),
    )
    return done + planned


def _payment_note(p: Payment, state: str | None) -> str:
    fmt = lambda d: d.strftime("%d.%m.%Y")
    if state == PAID:
        return ""
    if state == REFUNDED:
        return f"возвращён {fmt(p.refunded_at)} — в выплаты не входит"
    if state == QUEUED:
        if p.status == "queued":
            return "в очереди на оплату — в выплаты не входит"
        if p.paid_at:
            return f"оплачен {fmt(p.paid_at)}, после даты отчёта — в выплаты не входит"
        return "на дату отчёта ещё не оплачен"
    # NOT_YET
    when = f" (оплачен {fmt(p.paid_at)})" if p.paid_at else ""
    return f"заведён {fmt(p.created_at)}, после даты отчёта{when} — не учитывается"


def build_contract(ds: Dataset, contract: Contract, on: date) -> ContractReport:
    fmt = lambda d: d.strftime("%d.%m.%Y")
    lines = [
        WorkLine(w, work_state(w, on))
        for w in sorted(ds.works.values(), key=lambda w: natural_key(w.work_id))
        if w.contract_id == contract.contract_id
    ]
    by_id = {l.work.work_id: l for l in lines}

    for l in lines:
        w = l.work
        if w.status == "done" and l.state != DONE:
            l.notes.append(f"принята {fmt(w.completed_at)}, после даты отчёта")
        if w.status == "cancelled" and l.state != CANCELLED:
            l.notes.append(f"отменена {fmt(w.cancelled_at)}, после даты отчёта")

    pay_lines = [
        PaymentLine(p, payment_state(p, on))
        for p in sorted(ds.payments.values(), key=lambda p: natural_key(p.payment_id))
        if p.contract_id == contract.contract_id
    ]
    for pl in pay_lines:
        pl.note = _payment_note(pl.payment, pl.state)

    report = ContractReport(contract, lines, pay_lines)

    paid = sorted(
        (pl.payment for pl in pay_lines if pl.state == PAID),
        key=lambda p: (p.paid_at, natural_key(p.payment_id)),
    )

    # 1. Привязанные платежи — на свою работу.
    pool: list[list] = []  # [payment_id, остаток суммы]; порядок = порядок оплаты
    for p in paid:
        free = p.amount
        if p.work_id is not None:
            line = by_id[p.work_id]
            if line.state == CANCELLED:
                report.warnings.append(
                    f"{p.payment_id} привязан к отменённой работе {p.work_id}: "
                    f"{money(p.amount)} зачтены в общий пул договора"
                )
            else:
                take = min(free, max(line.remaining, ZERO))
                if take > 0:
                    line.take(p.payment_id, take)
                    free -= take
                if free > 0:
                    report.warnings.append(
                        f"{p.payment_id} больше остатка по работе {p.work_id}: "
                        f"излишек {money(free)} зачтён в общий пул договора"
                    )
        if free > 0:
            pool.append([p.payment_id, free])

    # 2. Общий пул — по работам в порядке FIFO.
    for line in _fifo_order(lines):
        while line.remaining > 0 and pool:
            pid, avail = pool[0]
            take = min(avail, line.remaining)
            line.take(pid, take)
            if take == avail:
                pool.pop(0)
            else:
                pool[0][1] = avail - take

    # 3. Остаток пула — переплата.
    for pid, avail in pool:
        report.overpaid += avail
        report.overpaid_sources.append((pid, avail))
    if report.overpaid > 0:
        report.warnings.append(
            f"Переплата по договору: {money(report.overpaid)} сверх цены всех неотменённых работ"
        )

    if report.works_total != contract.amount:
        report.warnings.append(
            f"Сумма договора {money(contract.amount)} не равна сумме цен работ {money(report.works_total)}"
        )
    if report.due != report.works_total:
        report.warnings.append(
            f"Есть отменённые работы: к оплате {money(report.due)} вместо суммы договора {money(contract.amount)}"
        )

    # Самопроверка: всё выплаченное разнесено ровно один раз.
    if report.paid != report.paid_to_works + report.overpaid or any(l.remaining < 0 for l in lines):
        raise RuntimeError(f"{contract.contract_id}: разнесение не сходится — ошибка в расчёте")
    return report


def build_report(ds: Dataset, on: date) -> Report:
    contracts = [
        build_contract(ds, c, on)
        for c in sorted(ds.contracts.values(), key=lambda c: natural_key(c.contract_id))
        if c.signed_at <= on
    ]
    warnings = list(ds.warnings)
    skipped = [c.contract_id for c in ds.contracts.values() if c.signed_at > on]
    if skipped:
        warnings.append(f"Договоры подписаны после даты отчёта и не показаны: {', '.join(skipped)}")
    return Report(on, contracts, warnings)
