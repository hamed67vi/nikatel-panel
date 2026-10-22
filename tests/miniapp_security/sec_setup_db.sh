#!/bin/bash
# private MariaDB for the Mini App SECURITY run: own datadir + socket, no network, nothing shared with the live panel or the nikabot db.
# usage: sec_setup_db.sh start|stop|reset
set -e
B=${SEC_SCRATCH:-/tmp/miniapp-sec-scratch}
D=$B/db
SCHEMA="$(cd "$(dirname "$0")" && pwd)/nikabot_schema.sql"
case "${1:-start}" in
stop)
  [ -f $B/run/db.pid ] && kill "$(cat $B/run/db.pid)" 2>/dev/null || true
  sleep 2; exit 0;;
esac
mkdir -p $B/run
if [ ! -d $D/mysql ]; then
  mkdir -p $D
  mariadb-install-db --no-defaults --datadir=$D --user=root --auth-root-authentication-method=normal --skip-test-db >$B/install.log 2>&1 || { tail -5 $B/install.log; exit 1; }
fi
if [ ! -S $B/run/db.sock ]; then
  nohup mariadbd --no-defaults --datadir=$D --socket=$B/run/db.sock --pid-file=$B/run/db.pid --skip-networking --user=root \
    --innodb-buffer-pool-size=24M --innodb-log-file-size=8M --key-buffer-size=2M --max-connections=40 --performance-schema=OFF \
    --log-error=$B/db.err >/dev/null 2>&1 &
  for i in $(seq 1 60); do [ -S $B/run/db.sock ] && break; sleep 0.5; done
fi
mariadb --no-defaults -S $B/run/db.sock -uroot -e "DROP DATABASE IF EXISTS nikabot_sec; CREATE DATABASE nikabot_sec CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'mini'@'localhost' IDENTIFIED BY 'mini-test-pw'; GRANT ALL ON nikabot_sec.* TO 'mini'@'localhost';"
mariadb --no-defaults -S $B/run/db.sock -uroot nikabot_sec < $SCHEMA
mariadb --no-defaults -S $B/run/db.sock -uroot nikabot_sec -e "SHOW TABLES" | wc -l
