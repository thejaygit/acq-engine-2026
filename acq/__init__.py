"""acq: standalone acquisition engine (stdlib only). Every input carries provenance."""
from .engine import Input, Deal, run, readiness, solve_price, debt_sizing, spread_test  # noqa: F401
