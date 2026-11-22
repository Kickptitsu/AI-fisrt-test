import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advance.allocate import build_report  # noqa: E402
from advance.loader import DataError, load, parse_money  # noqa: E402
from report import main  # noqa: E402

DATA = ROOT / "data"
D = Decimal


def by_id(report):
    contracts = {c.contract.contract_id: c for c in report.contracts}
    works = {l.work.work_id: l for c in report.contracts for l in c.works}
    return contracts, works


def write_data(tmp: Path, contracts: str, works: str, payments: str) -> Path:
    (tmp / "contracts.csv").write_text("contract_id,contractor,amount,signed_at\n" + contracts, encoding="utf-8")
    (tmp / "works.csv").write_text(
        "work_id,contract_id,title,price,status,completed_at,cancelled_at\n" + works, encoding="utf-8")
    (tmp / "payments.csv").write_text(
        "payment_id,contract_id,work_id,purpose,amount,status,created_at,paid_at,refunded_at\n" + payments,
        encoding="utf-8")
    return tmp


class ControlDate(unittest.TestCase):
    """Эталон на 10.11.2026 — дата, которую просила бухгалтер."""

    @classmethod
    def setUpClass(cls):
        cls.contracts, cls.works = by_id(build_report(load(DATA), date(2026, 11, 10)))

    def check(self, work_id, state, paid, remaining):
        line = self.works[work_id]
        self.assertEqual((line.state, line.paid, line.remaining), (state, D(paid), D(remaining)), work_id)

    def test_d1_works(self):
        self.check("W-11", "done", "100000", "0")
        self.check("W-12", "done", "100000", "0")
        self.check("W-13", "cancelled", "0", "0")
        self.check("W-14", "done", "100000", "0")
        self.check("W-15", "planned", "15000", "85000")
        self.check("W-16", "planned", "0", "100000")
        self.assertEqual(self.works["W-14"].sources, [("P-3", D("15000")), ("P-1", D("85000"))])

    def test_d1_totals(self):
        c = self.contracts["D-1"]
        self.assertEqual(c.contract.amount, D("600000"))
        self.assertEqual(c.due, D("500000"))
        self.assertEqual(c.paid, D("315000"))
        self.assertEqual(c.remaining, D("185000"))
        self.assertEqual(c.overpaid, D("0"))
        self.assertEqual(c.queued, D("150000"))
        self.assertEqual(c.refunded, D("50000"))
        self.assertEqual(c.debt_for_accepted, D("0"))
        self.assertEqual(c.advance_unearned, D("15000"))

    def test_d2(self):
        # W-23 принята 11.11 — на 10.11 ещё не принята; P-8 от 12.11 не учитывается.
        self.check("W-21", "done", "20000", "0")
        self.check("W-22", "done", "30000", "0")
        self.check("W-23", "planned", "25000", "15000")
        c = self.contracts["D-2"]
        self.assertEqual((c.due, c.paid, c.remaining, c.overpaid), (D("90000"), D("75000"), D("15000"), D("0")))

    def test_sums_match(self):
        for c in self.contracts.values():
            self.assertEqual(sum((l.paid for l in c.works), D(0)) + c.overpaid, c.paid)
            self.assertEqual(sum((l.remaining for l in c.works), D(0)), c.remaining)


class OtherDates(unittest.TestCase):
    def test_before_refund(self):
        # 05.09: ошибочный P-2 ещё не вернули — он в выплатах; D-2 ещё не подписан; W-13 ещё не отменена.
        report = build_report(load(DATA), date(2026, 9, 5))
        contracts, works = by_id(report)
        self.assertNotIn("D-2", contracts)
        self.assertEqual(contracts["D-1"].paid, D("350000"))
        self.assertEqual(works["W-13"].state, "planned")
        self.assertEqual(works["W-13"].paid, D("100000"))
        self.assertEqual(works["W-14"].paid, D("50000"))
        self.assertEqual(contracts["D-1"].queued, D("0"))  # P-4 заведён 01.11

    def test_after_export_overpayment(self):
        report = build_report(load(DATA), date(2026, 11, 15))
        contracts, works = by_id(report)
        c = contracts["D-2"]
        self.assertEqual(works["W-23"].state, "done")
        self.assertEqual((c.paid, c.remaining, c.overpaid), (D("95000"), D("0"), D("5000")))
        self.assertEqual(c.overpaid_sources, [("P-8", D("5000"))])
        self.assertTrue(any("Переплата" in w for w in c.warnings))


class Rules(unittest.TestCase):
    def test_bound_overflow_and_cancelled_go_to_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_data(
                Path(tmp),
                "K,X,300.00,2026-01-01\n",
                "A,K,a,100.00,done,2026-02-01,\nB,K,b,100.00,cancelled,,2026-02-02\nC,K,c,100.00,planned,,\n",
                "P1,K,A,на A,150.00,paid,2026-01-05,2026-01-05,\n"
                "P2,K,B,на B,40.00,paid,2026-01-06,2026-01-06,\n",
            )
            report = build_report(load(tmp), date(2026, 3, 1))
        contracts, works = by_id(report)
        self.assertEqual(works["A"].paid, D("100"))
        self.assertEqual(works["B"].paid, D("0"))
        self.assertEqual(works["C"].sources, [("P1", D("50")), ("P2", D("40"))])
        self.assertEqual(works["C"].remaining, D("10"))
        self.assertEqual(len(contracts["K"].warnings), 3)  # излишек, отменённая, отмена уменьшила сумму

    def test_done_works_paid_first_by_acceptance_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_data(
                Path(tmp),
                "K,X,300.00,2026-01-01\n",
                "W-1,K,a,100.00,planned,,\nW-2,K,b,100.00,done,2026-03-01,\nW-10,K,c,100.00,done,2026-02-01,\n",
                "P1,K,,аванс,150.00,paid,2026-01-05,2026-01-05,\n",
            )
            _, works = by_id(build_report(load(tmp), date(2026, 3, 1)))
        self.assertEqual([works[w].paid for w in ("W-10", "W-2", "W-1")], [D("100"), D("50"), D("0")])

    def test_money_is_exact(self):
        self.assertEqual(parse_money("0.10") + parse_money("0.20"), D("0.30"))
        with self.assertRaises(ValueError):
            parse_money("1.005")


class Validation(unittest.TestCase):
    def test_errors_are_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_data(
                Path(tmp),
                "K,X,100.00,2026-01-01\nL,Y,100.00,2026-01-01\n",
                "A,K,a,100.00,done,,\nB,L,b,100.00,planned,,\n",
                "P1,K,B,чужая работа,10.00,paid,2026-01-01,2026-01-01,\n"
                "P2,K,,без даты,10.00,paid,2026-01-01,,\n"
                "P3,Z,,нет договора,10.001,paid,2026-01-01,2026-01-01,\n",
            )
            with self.assertRaises(DataError) as ctx:
                load(tmp)
        text = str(ctx.exception)
        for fragment in ("done без даты приёмки", "относится к договору L", "без даты оплаты",
                         "неизвестный договор Z", "больше двух знаков"):
            self.assertIn(fragment, text)


class Cli(unittest.TestCase):
    def test_text_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--date", "10.11.2026", "--data", str(DATA), "--csv", tmp])
            self.assertEqual(code, 0)
            self.assertIn("315 000,00", buf.getvalue())
            contracts_csv = (Path(tmp) / "contracts_2026-11-10.csv").read_text(encoding="utf-8-sig")
            self.assertIn("D-1;Студия «Свет»;600000,00;500000,00;315000,00;185000,00", contracts_csv)
            self.assertTrue((Path(tmp) / "works_2026-11-10.csv").exists())
            self.assertTrue((Path(tmp) / "payments_2026-11-10.csv").exists())


if __name__ == "__main__":
    unittest.main()
