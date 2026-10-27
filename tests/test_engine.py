import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from acq import Input, Deal, run, readiness, debt_sizing, spread_test, solve_price
from acq.engine import _irr_monthly, _mort_pmt

V = "verified_fact"

def base(**kw):
    d = dict(
        name="t",
        price=Input(100_000_000, V, "test"), noi=Input(5_000_000, V, "test"),
        reserve=Input(0, V, "test"), buyer_costs=Input(0, V, "test"), capex=Input(0, V, "test"),
        growth=Input(0.0, V, "test"), exit_cap=Input(0.05, V, "test"), selling_pct=Input(0.02, V, "assumption"),
        debt=Input(0, V, "test"), rate=Input(0.06, V, "test"),
    )
    d.update(kw)
    return Deal(**d)

class TestMath(unittest.TestCase):
    def test_pmt_matches_textbook(self):
        # $100k, 6%, 30y: standard monthly payment 599.55
        self.assertAlmostEqual(_mort_pmt(100_000, 0.06, 30), 599.55, places=2)

    def test_unlevered_irr_equals_cap_when_flat_and_exit_at_entry_cap(self):
        d = base(exit_cap=Input(0.05, V, "t"), selling_pct=Input(0.0, V, "t"))
        r = run(d, debt=0)
        # flat NOI paid monthly, exit at the same 5% cap, no costs.
        # Expected = effective annual yield of a 5% nominal monthly-paid stream.
        self.assertAlmostEqual(r["unlevered_irr"], (1 + 0.05 / 12) ** 12 - 1, delta=0.00001)

    def test_irr_none_without_sign_change(self):
        self.assertIsNone(_irr_monthly([100.0, 50.0]))

    def test_going_in_cap(self):
        r = run(base(), debt=0)
        self.assertAlmostEqual(r["going_in_cap"], 0.05, places=9)

class TestDebt(unittest.TestCase):
    def test_dscr_binds_when_it_is_the_lowest(self):
        d = base(debt=Input(0, V, "t"), min_dscr=1.25, max_ltv=0.9, min_debt_yield=0.01)
        s = debt_sizing(d)
        self.assertEqual(s["binding"], "dscr")
        # DSCR loan supports DSCR exactly at min when run at that loan
        r = run(d, debt=s["loan"])
        self.assertAlmostEqual(r["dscr_y1"], 1.25, delta=0.001)

    def test_debt_yield_binds(self):
        d = base(min_dscr=1.0, max_ltv=0.9, min_debt_yield=0.10)
        s = debt_sizing(d)
        self.assertEqual(s["binding"], "debt_yield")
        self.assertAlmostEqual(s["loan"], 50_000_000, delta=1)

    def test_ltv_binds(self):
        d = base(min_dscr=0.5, max_ltv=0.40, min_debt_yield=0.01)
        s = debt_sizing(d)
        self.assertEqual(s["binding"], "ltv")
        self.assertAlmostEqual(s["loan"], 40_000_000, delta=1)

class TestSpreadAndPrice(unittest.TestCase):
    def test_spread_gate_and_price_at_gate_consistent(self):
        d = base(spread_treasury=Input(0.0495, V, "fred"), spread_bp=100)
        t = spread_test(d)
        self.assertAlmostEqual(t["gate_cap"], 0.0595, places=6)
        self.assertFalse(t["clears"])  # 5% < 5.95%
        # price at gate: NOI / price = gate cap
        self.assertAlmostEqual(d.noi.value / t["price_at_gate"], t["gate_cap"], places=6)

    def test_tax_reassessment_moves_noi(self):
        from acq.engine import _noi
        d = base(tax_rate=Input(0.0164, V, "tax"), tax_basis_price=110_000_000)
        # at a $100M price, tax is on $110M basis: NOI is higher by rate*(110-100)M
        self.assertAlmostEqual(_noi(d, 100_000_000) - 5_000_000, 0.0164 * 10_000_000, places=2)

    def test_solve_price_returns_hurdle(self):
        d = base(exit_cap=Input(0.06, V, "t"), debt=Input(0, V, "t"), selling_pct=Input(0.0, V, "t"))
        p = solve_price(d, hurdle=0.08, metric="unlevered")
        self.assertIsNotNone(p)
        self.assertAlmostEqual(run(d, price=p)["unlevered_irr"], 0.08, delta=0.0005)

class TestReadiness(unittest.TestCase):
    def test_unresolved_blocks(self):
        d = base(exit_cap=Input(0.05, "unresolved", "none"))
        r = readiness(d)
        self.assertEqual(r["verdict"], "NOT READY")
        self.assertIn("UNRESOLVED:exit_cap", r["blockers"])

    def test_assumption_qualifies(self):
        d = base(buyer_costs=Input(900_000, "assumption", "1% best guess"))
        r = readiness(d)
        self.assertEqual(r["verdict"], "QUALIFIED")
        self.assertIn("ASSUMPTION:buyer_costs", r["qualifications"])

    def test_selling_cost_zero_blocks(self):
        r = readiness(base(selling_pct=Input(0.0, V, "t")))
        self.assertIn("SELLING_COST_ZERO", r["blockers"])

    def test_bad_status_rejected(self):
        with self.assertRaises(ValueError):
            Input(1, "guess", "x")

if __name__ == "__main__":
    unittest.main()
