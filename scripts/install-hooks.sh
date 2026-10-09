#!/usr/bin/env bash
# Install the git pre-commit hook: secrets check + ruff before every commit.
#   bash scripts/install-hooks.sh
set -euo pipefail
cd "$(dirname "$0")/.."
cat > .git/hooks/pre-commit <<'EOF'
#!/usr/bin/env bash
set -e
bash scripts/check_no_secrets.sh
if command -v ruff >/dev/null 2>&1; then ruff check nikatel tests; elif [ -x /opt/nikatel/venv/bin/ruff ]; then /opt/nikatel/venv/bin/ruff check nikatel tests; fi
EOF
chmod +x .git/hooks/pre-commit
echo "pre-commit hook installed"
