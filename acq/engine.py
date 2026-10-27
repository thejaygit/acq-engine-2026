"""Acquisition engine. Annual NOI, monthly debt, exit at hold end. Stdlib only.

Provenance statuses: verified_fact, source_claim, assumption, derived, unresolved, policy.
A value with status 'unresolved' blocks readiness. 'assumption' and 'source_claim' qualify it.
"""
from dataclasses import dataclass, field
from typing import Optional

STATUSES = {"verified_fact", "source_claim", "assumption", "derived", "unresolved", "policy"}


@dataclass
class Input:
    value: float
    status: str
    anchor: str

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"bad status {self.status!r}")


@dataclass
class Deal:
    name: str
    price: Input
    noi: Input                 # Year-1 NOI, before reserve
    reserve: Input             # annual replacement reserve
    buyer_costs: Input         # one-time closing and buyer costs
    capex: Input               # one-time upfront capex
    growth: Input              # annual NOI growth
    exit_cap: Input
    selling_pct: Input         # disposition cost as share of gross sale
    debt: Input                # loan amount
    rate: Input                # annual interest rate
    amort_years: int = 30
    hold_years: int = 5
    max_ltv: float = 0.65
    min_dscr: float = 1.25
    min_debt_yield: float = 0.08
    tax_rate: Optional[Input] = None      # reassessment rate (price-dependent tax)
    tax_basis_price: Optional[float] = None
    spread_treasury: Optional[Input] = None
    spread_bp: float = 100.0


def _mort_pmt(principal, rate, years):
    r = rate / 12
    n = years * 12
    return principal * r / (1 - (1 + r) ** -n) if r else principal / n


def _noi(d: Deal, price: float) -> float:
    base = d.noi.value
    if d.tax_rate is not None and d.tax_basis_price is not None:
        base += d.tax_rate.value * (d.tax_basis_price - price)
    return base


def _irr_monthly(flows):
    """Annualized IRR of monthly flows via bisection on NPV. Returns None if no sign change."""
    def npv(r):
        return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))
    lo, hi = -0.99, 1.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (1 + (lo + hi) / 2) ** 12 - 1


def run(d: Deal, price: Optional[float] = None, debt: Optional[float] = None) -> dict:
    p = d.price.value if price is None else price
    loan = d.debt.value if debt is None else debt
    noi0 = _noi(d, p)
    equity = p + d.buyer_costs.value + d.capex.value - loan
    if equity <= 0:
        raise ValueError("loan must be below total cost")
    pmt = _mort_pmt(loan, d.rate.value, d.amort_years) if loan > 0 else 0.0
    bal = loan
    flows = [-equity]
    months = d.hold_years * 12
    dscr_y1 = None
    for m in range(months):
        yr = m // 12
        noi_m = (noi0 * (1 + d.growth.value) ** yr - d.reserve.value * (1 + d.growth.value) ** yr) / 12
        interest = bal * d.rate.value / 12
        princ = pmt - interest
        bal -= princ
        cf = noi_m - pmt
        if m < 12:
            dscr_y1 = (noi0 - d.reserve.value) / (pmt * 12) if pmt > 0 else None
        flows.append(cf)
    # exit at end of hold: forward NOI over the year after exit
    fwd_noi = noi0 * (1 + d.growth.value) ** d.hold_years - d.reserve.value * (1 + d.growth.value) ** d.hold_years
    gross = fwd_noi / d.exit_cap.value
    net = gross * (1 - d.selling_pct.value) - bal
    flows[-1] += net
    levered = _irr_monthly(flows)
    unlev_flows = [-(p + d.buyer_costs.value + d.capex.value)]
    unlev_flows += [(noi0 * (1 + d.growth.value) ** (m // 12) - d.reserve.value * (1 + d.growth.value) ** (m // 12)) / 12 for m in range(months)]
    unlev_flows[-1] += gross * (1 - d.selling_pct.value)
    unlevered = _irr_monthly(unlev_flows)
    return {
        "price": p, "debt": loan, "noi_y1": noi0, "going_in_cap": noi0 / p,
        "cfads_cap": (noi0 - d.reserve.value) / p, "dscr_y1": dscr_y1,
        "debt_yield": noi0 / loan if loan else None, "ltv": loan / p,
        "equity": equity, "levered_irr": levered, "unlevered_irr": unlevered,
        "exit_gross": gross, "exit_net_equity": net, "ending_balance": bal,
        "equity_multiple": (sum(f for f in flows if f > 0) / -flows[0]) if flows[0] < 0 else None,
    }


def debt_sizing(d: Deal, price: Optional[float] = None) -> dict:
    """Lender-supported loan: min of LTV, DSCR and debt-yield constraints."""
    p = d.price.value if price is None else price
    noi = _noi(d, p) - d.reserve.value
    pmt_max = noi / d.min_dscr / 12
    r = d.rate.value / 12
    n = d.amort_years * 12
    loan_dscr = pmt_max * (1 - (1 + r) ** -n) / r if r else pmt_max * n
    loan_dy = noi / d.min_debt_yield
    loan_ltv = d.max_ltv * p
    loan = min(loan_dscr, loan_dy, loan_ltv)
    binding = {loan_dscr: "dscr", loan_dy: "debt_yield", loan_ltv: "ltv"}[loan]
    return {"loan": loan, "binding": binding, "dscr": loan_dscr, "debt_yield": loan_dy, "ltv": loan_ltv}


def spread_test(d: Deal) -> Optional[dict]:
    if d.spread_treasury is None:
        return None
    p = d.price.value
    cap = _noi(d, p) / p
    gate = d.spread_treasury.value + d.spread_bp / 10000
    return {"going_in_cap": cap, "gate_cap": gate, "spread_bp": (cap - d.spread_treasury.value) * 10000,
            "clears": cap >= gate, "price_at_gate": _price_at_cap(d, gate)}


def _price_at_cap(d: Deal, cap: float) -> float:
    """Solve price where NOI(price)/price = cap, NOI linear in price through tax reassessment."""
    if d.tax_rate is None or d.tax_basis_price is None:
        return d.noi.value / cap
    r = d.tax_rate.value
    return (d.noi.value + r * d.tax_basis_price) / (cap + r)


def solve_price(d: Deal, hurdle: float, metric: str = "levered") -> Optional[float]:
    """Bisection on price for a hurdle IRR. Loan held at the deal's stated amount."""
    def f(price):
        r = run(d, price=price)
        return (r["levered_irr"] if metric == "levered" else r["unlevered_irr"]) - hurdle
    lo, hi = d.debt.value + 1_000_000, d.price.value * 2
    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None or flo * fhi > 0:
        return None
    for _ in range(120):
        mid = (lo + hi) / 2
        fm = f(mid)
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


QUALIFY = ("assumption", "source_claim")


def readiness(d: Deal) -> dict:
    blockers, quals = [], []
    for field_name in ("price", "noi", "reserve", "buyer_costs", "capex", "growth", "exit_cap", "selling_pct", "debt", "rate"):
        inp: Input = getattr(d, field_name)
        if inp.status == "unresolved":
            blockers.append(f"UNRESOLVED:{field_name}")
        elif inp.status in QUALIFY:
            quals.append(f"{inp.status.upper()}:{field_name}")
    if d.selling_pct.value <= 0:
        blockers.append("SELLING_COST_ZERO")
    if d.exit_cap.value <= 0:
        blockers.append("EXIT_CAP_INVALID")
    r = run(d)
    if r["dscr_y1"] is not None and r["dscr_y1"] < d.min_dscr:
        blockers.append("DSCR_BELOW_MIN")
    if r["ltv"] > d.max_ltv:
        blockers.append("LTV_ABOVE_MAX")
    verdict = "NOT READY" if blockers else ("QUALIFIED" if quals else "READY FOR PRINCIPAL REVIEW")
    return {"verdict": verdict, "blockers": blockers, "qualifications": quals}
