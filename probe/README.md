# Probe

The probe runs on a server inside Iran. For every config it keeps one Xray instance alive (a local
SOCKS port each), tests reachability every 30 s, measures speed daily, and answers the panel's
HTTP API on port 18080 with a bearer token.

| file | role |
|---|---|
| `speed-api.py` | the HTTP API (`/api/config/list`, `/api/configs`, `/api/config/add|update|delete|bulk|uri`, `/api/speed`, `/api/filter/check`, `/api/tunnel/test`) |
| `manager.py` | the CLI `nikatel-probe` (add / remove / enable / test / speed / restart) and `bulk_apply` |
| `run-all.py` | starts one Xray per config and keeps them running (`nikatel-probe-manager.service`) |
| `monitor-all.py` | the health checks with retries (`nikatel-probe-monitor.service`) |
| `daily-speed.py` | the daily speed run (`nikatel-daily-speed.timer`) |
| `parser.py` | link → Xray outbound |
| `bulk-import.py` | helper for mass imports |

Layout on the probe server: code in `/opt/nikatel-probe-manager`, Xray in `/opt/nikatel-probe/xray/`,
token in `/opt/nikatel-probe-manager/speed-api.token`, state in `configs.json`, `health-state/`,
`speed-state/`, `logs/`.

The panel installs all of this on a chosen Iran server from Settings → Probe → "Install on a
server" (`nikatel.services.probe_install`), with the Xray binary from the panel's engine cache.
