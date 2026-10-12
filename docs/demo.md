# The demo

A read-only copy of the panel filled with **invented** data, to show it to people and collect opinions. Nothing in it is real: the server names are made up, the addresses come from the documentation ranges (RFC 5737), there is no SSH key and no way to reach a machine.

What it does:

- no sign-in (the panel shows itself); a small "Demo" pill is shown at the top
- every change (POST, PUT, DELETE under `/api/`) is refused with a clear message
- no job touches a machine; one small job keeps the numbers moving
- the database is filled once, only when it has no servers (`nikatel demo-seed`)

Run one next to a real install (own data directory, own port; the demo is served over plain HTTP):

```bash
NIKATEL_DEMO=true NIKATEL_DATA_DIR=/var/lib/nikatel-demo/data NIKATEL_LOG_DIR=/var/lib/nikatel-demo/logs \
NIKATEL_LISTEN_HOST=0.0.0.0 NIKATEL_LISTEN_PORT=8088 \
/opt/nikatel/venv/bin/python -m nikatel.main serve
```

Never switch it on for a real install: it removes the sign-in.