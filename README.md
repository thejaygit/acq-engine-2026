# acq-engine

Standalone acquisition engine. Python 3 standard library only. No GitHub remote.

Run tests:  python3 -I tests/test_engine.py

Every input is an Input(value, status, anchor). Statuses: verified_fact, source_claim, assumption, derived, unresolved, policy.
Readiness: any unresolved input is a blocker (NOT READY). Assumption and source_claim qualify (QUALIFIED). Otherwise READY FOR PRINCIPAL REVIEW.

Not identical to the repo engine in /home/claude/acqx/experiment. This tool uses an annual exit on forward NOI, a 30-year amortizing loan and a monthly cash flow. Figures can differ from the repo run for the same deal. Do not mix the two without reconciling them.
