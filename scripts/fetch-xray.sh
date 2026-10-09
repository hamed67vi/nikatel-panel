#!/usr/bin/env bash
# Download the latest Xray release into the panel's engine cache (Iran servers often have no internet;
# the panel copies the binary to them over SSH). Re-run to upgrade.
set -euo pipefail
DIR=/var/lib/nikatel/engines
mkdir -p "$DIR" && cd "$DIR"
VER=$(curl -fsSL -m 20 https://api.github.com/repos/XTLS/Xray-core/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
if [ ! -f xray ] || [ ! -f geoip.dat ] || [ ! -f geosite.dat ] || [ "$(cat xray.version 2>/dev/null)" != "$VER" ]; then
    curl -fsSL -m 300 -o xray.zip "https://github.com/XTLS/Xray-core/releases/download/$VER/Xray-linux-64.zip"
    python3 -c "import zipfile; z=zipfile.ZipFile('xray.zip'); [z.extract(n) for n in ('xray','geoip.dat','geosite.dat') if n in z.namelist()]"
    chmod 755 xray && echo "$VER" > xray.version && rm -f xray.zip
fi
echo "xray $(./xray version | head -1 | cut -d' ' -f1-2) cached in $DIR"
