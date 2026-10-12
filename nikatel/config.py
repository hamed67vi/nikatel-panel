"""Settings. Every value can be overridden by an environment variable NIKATEL_<NAME>
or by /etc/nikatel/nikatel.env. Nothing else in the code reads the environment."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NIKATEL_", env_file="/etc/nikatel/nikatel.env", extra="ignore")

    # where state lives
    data_dir: Path = Path("/var/lib/nikatel/data")
    bootstrap_dir: Path = Path("/etc/nikatel")      # probe.json / panels.json read once on first start
    log_dir: Path = Path("/var/lib/nikatel/logs")

    # http
    listen_host: str = "0.0.0.0"
    listen_port: int = 9443
    tls_cert: Path | None = Path("/etc/nikatel/tls.crt")
    tls_key: Path | None = Path("/etc/nikatel/tls.key")
    session_hours: int = 24

    # ssh to the managed servers
    ssh_key: Path = Path("/root/.ssh/nikatel_v2_node")
    ssh_connect_timeout: int = 10
    # one background connection per server, shared by the many short commands of the jobs (a new SSH handshake for each of them was most of the CPU the panel used)
    ssh_multiplex: bool = True
    # while true, the panel may only run the reviewed read-only scripts (nikatel.ssh.SCRIPTS)
    read_only: bool = True

    # jobs (seconds)
    server_sample_interval: int = 120
    tunnel_check_interval: int = 60
    ssh_workers: int = 8

    log_level: str = "INFO"
    # the license mode: "owner" on the vendor's own installs (every feature on, no token); empty = a token from Settings → About → License or the free tier
    license: str = ""
    # the public demo: invented data, no sign-in, every change refused (see services/demo.py); never on a real install
    demo: bool = False

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'nikatel.db'}"

    @property
    def known_hosts(self) -> Path:
        return self.data_dir / "known_hosts"

    def secret_key(self) -> bytes:
        """A per-install key for session cookies, created once."""
        p = self.data_dir / "secret.key"

        if not p.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            p.write_bytes(secrets.token_bytes(32))
            p.chmod(0o600)

        return p.read_bytes()


@lru_cache
def get_settings() -> Settings:
    return Settings()
