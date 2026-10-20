"""A database made by the panel at schema 19 (1.69.49) and opened by the code of today has the same tables, columns and indexes as a database made new.

`create_all` only makes MISSING TABLES: a column or an index added to a model without a numbered migration (nikatel/db.py MIGRATIONS) exists on a fresh install and is missing on every
install that is updated - the first query that names it fails ("no such column") on the live panel. This test builds the old database from the frozen lists below, opens it with `init_db()`
and compares it with a fresh one. When a migration 20 adds a column, nothing here changes: the frozen database runs the migration."""
import sqlite3

from nikatel import db
from nikatel.config import get_settings

# the tables of schema 19, column names in order (types do not matter for the comparison)
FROZEN_19 = {
    "action_log": "id ts ref action source dry_run ok story finished_at",
    "alert_states": "id kind ref fails alerted bad_since good_since last_notice flips flap_since quiet_until",
    "api_tokens": "id name scope token_hash prefix created_at created_by last_used_at revoked_at",
    "config_samples": "id config_id ts up ping_ms",
    "configs": "id name slug group port enabled tunnel cdn_port meta status status_since ping_ms down_mbps up_mbps speed_at checked_at last_error created_at",
    "events": "id ts level domain text ref",
    "gateways": "server_id mode ports borrowed installed applied_at version",
    "jobs": "name interval enabled running last_start last_ok last_duration_ms last_error runs failures last_failure last_failure_at",
    "maintenance": "id start end note",
    "outbound_configs": "id uri note enabled created_at",
    "outbound_hubs": "server_id port method password installed applied_at version",
    "panels": "id name type url username secrets verify_tls enabled status status_since fails checked_at latency_ms version stats nodes last_error created_at",
    "profiles": "id kind server_id panel_id node_id tag created_at",
    "repair_cases": "id ts tunnel iran foreign kind cause steps healed secs source",
    "server_samples": "id server_id ts cpu ram disk load1 rx_mbps tx_mbps conn",
    "servers": "id name host role ssh_user ssh_port enabled provider asn country created_at online last_seen_at last_error facts extra",
    "settings": "key value",
    "subscriptions": "id name url nodes excluded created_at last_import",
    "tunnel_checks": "id tunnel_id ts ok rtt_ms rx_mbps tx_mbps error",
    "tunnels": "id name type transport engine iran_server_id foreign_server_id port iran_tunnel_ip foreign_tunnel_ip forwarded_ports record secrets enabled created_at state state_since last_check_at last_error rtt_ms rx_mbps tx_mbps detail",
}
FROZEN_19_INDEXES = [
    "CREATE INDEX ix_action_log_ref_ts ON action_log (ref, ts)",
    "CREATE UNIQUE INDEX ix_alert_states_kind_ref ON alert_states (kind, ref)",
    "CREATE INDEX ix_config_samples_config_ts ON config_samples (config_id, ts)",
    "CREATE INDEX ix_events_ts ON events (ts)",
    "CREATE INDEX ix_profiles_node ON profiles (panel_id, node_id)",
    "CREATE INDEX ix_profiles_server ON profiles (server_id)",
    "CREATE INDEX ix_repair_cases_tunnel_ts ON repair_cases (tunnel, ts)",
    "CREATE INDEX ix_server_samples_server_ts ON server_samples (server_id, ts)",
    "CREATE INDEX ix_tunnel_checks_tunnel_ts ON tunnel_checks (tunnel_id, ts)",
]


def _shape(path):
    c = sqlite3.connect(path)

    try:
        tables = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1")]
        cols = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in tables}
        idx = sorted(n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%'"))
        return cols, idx
    finally:
        c.close()


def _open(monkeypatch, folder):
    folder.mkdir()
    monkeypatch.setattr(get_settings(), "data_dir", folder)
    db.reset_for_tests()
    return folder / "nikatel.db"


def test_a_database_updated_from_schema_19_has_the_columns_and_indexes_of_a_new_one(tmp_path, monkeypatch):
    old = _open(monkeypatch, tmp_path / "old")
    c = sqlite3.connect(old)

    for table, cols in FROZEN_19.items():
        c.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{x}"' for x in cols.split()) + ")")

    for ddl in FROZEN_19_INDEXES:
        c.execute(ddl)

    c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    c.executemany("INSERT INTO schema_version VALUES (?)", [(v,) for v in range(1, 20)])
    c.commit()
    c.close()
    assert db.init_db() == max(v for v, _, _ in db.MIGRATIONS)
    db.reset_for_tests()
    new = _open(monkeypatch, tmp_path / "new")
    db.init_db()
    db.reset_for_tests()
    (old_cols, old_idx), (new_cols, new_idx) = _shape(old), _shape(new)
    missing = {f"{t}.{x}" for t, cols in new_cols.items() for x in cols if x not in old_cols.get(t, [])}
    assert not missing, f"columns of the models that an updated database does not have - add a migration to db.MIGRATIONS: {sorted(missing)}"
    assert sorted(old_cols) == sorted(new_cols)
    assert old_idx == new_idx, f"indexes of the models that an updated database does not have - add a migration: {sorted(set(new_idx) - set(old_idx))}"
