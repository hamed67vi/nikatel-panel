#!/usr/bin/env bash
# The repository is public: nothing that identifies the real infrastructure may be committed.
# Fails when a tracked file contains a public IPv4 address, a hostname of ours, a token or a key.
# Allowed: loopback, 0.0.0.0, RFC1918, documentation ranges, and the test addresses 1.1.1.1 … 9.9.9.9.
set -euo pipefail
cd "$(dirname "$0")/.."
# tracked AND new (untracked, not ignored) files - a fresh file must not slip through before `git add`
FILES=$( (git ls-files; git ls-files --others --exclude-standard) 2>/dev/null | grep -v '^$' | sort -u || true)
[ -n "$FILES" ] || FILES=$(find . -type f -not -path './.git/*' -not -path '*/__pycache__/*' -not -name '*.pyc' -not -path './*.egg-info/*')
bad=0

# public IPv4 (filter the allowed ones afterwards)
while IFS=: read -r file line match; do
    case "$match" in
        127.*|0.0.0.0|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.0.2.*|198.51.100.*|203.0.113.*|255.*|169.254.*) continue ;;
        1.1.1.1|2.2.2.2|3.3.3.3|4.4.4.4|5.5.5.5|9.9.9.9|8.8.8.8) continue ;;
    esac
    echo "$file:$line: public IPv4 '$match'"; bad=1
done < <(grep -nEo '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' $FILES 2>/dev/null | grep -vE '^(\./)?(pyproject\.toml|CHANGELOG\.md|scripts/check_no_secrets\.sh):' || true)

# our hostnames / providers (every file except this one)
HOST_FILES=$(echo "$FILES" | grep -vE '^(\./)?scripts/check_no_secrets\.sh$' || true)
if [ -n "$HOST_FILES" ] && grep -nEi 'nikashop|nikatel-monitor|penta[0-9]|rapido0|respina0|fanava0|mobin0|zitel0|pnt0[0-9]' $HOST_FILES; then
    bad=1
fi

# tokens / private keys - the masker, its test and its doc legitimately mention the patterns
TOKEN_FILES=$(echo "$FILES" | grep -vE '^(\./)?(nikatel/log\.py|tests/test_ssh_and_log\.py|docs/logging\.md|scripts/check_no_secrets\.sh)$' || true)
if [ -n "$TOKEN_FILES" ] && grep -nE 'BEGIN (RSA|OPENSSH|EC) PRIVATE|[0-9]{8,10}:[A-Za-z0-9_-]{30,}|sk-ant-|ghp_[A-Za-z0-9]{20,}|github_pat_' $TOKEN_FILES; then
    bad=1
fi

if [ "$bad" = 1 ]; then echo "SECRETS CHECK FAILED - remove the lines above before committing"; exit 1; fi
echo "secrets check ok"
