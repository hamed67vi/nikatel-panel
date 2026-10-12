# The analyst

The Tunnel Doctor has a second tab, **Analyst**: what the history says, read only. Under it:

- **Tunnels:** for each tunnel the availability, the outages and how long they last, the normal latency and whether it is rising now, the hours of the day it is weak, and a verdict (stable, watch, chronic). Read from the checks of the last 1 to 14 days.
- **At risk:** tunnels that are up but show a rising latency, fail more often than before, or are in one of their weak hours. Also **outages that come together** on tunnels that share a server: that points at the server or its network, not at the tunnels.
- **What fixes what:** every step of every smart repair is kept as data (table `repair_cases`), and the plain restarts of the action log are counted too. For each step and each cause: how often it healed, how fast.
- **Servers:** load in the four busiest hours of the Iran servers, what runs on each, and a verdict: `idle` (almost nothing runs), `redundant` (another server in the same city can take its load, judged by CPU), `busy`, `light`. The panel does not know prices: it says what is unused, not what it costs.

## How the smart repair uses it

Before the smart repair climbs its ladder it asks the analyst (`analyst.hints_for`). A restart or a rebuild that failed three times in a row on this tunnel (and healed it in none of the last 14 days), or that healed under a tenth of at least eight cases with the same cause on all tunnels, is skipped, and the repair story says why. A move or a switch is never skipped, a dead service is always restarted first, and the ladder is never left empty. The analyst only advises: if it cannot read its data, the repair goes on as before.

API: `GET /api/analyst?days=7`. Code: `nikatel/services/analyst.py` (the pure functions are covered by `tests/test_analyst.py`), `nikatel/web/analyst.js`.