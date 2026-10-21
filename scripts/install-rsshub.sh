#!/usr/bin/env bash
set -euo pipefail
# Installs ONE separate RSSHub instance; no credentials in argv/repository.
MODE=direct;ACTION=dry-run;UPGRADE=0
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/rsshub-intelligence
for arg in "$@";do case "$arg" in
 --mode=direct) MODE=direct;;
 --mode=proxy) MODE=proxy;;
 --apply) ACTION=apply;;
 --dry-run) ACTION=dry-run;;
 --upgrade) UPGRADE=1;;
 *) echo "Unknown argument: $arg" >&2;exit 2;;
esac;done
echo "target=$DIR, mode=$MODE, exposed=127.0.0.1:1200 only"
if [[ "$ACTION" != apply ]];then echo DRY_RUN_ONLY;exit 0;fi
[[ $EUID == 0 ]] || { echo "root required" >&2;exit 2; }
docker compose version >/dev/null
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/rsshub-intelligence.env')
assert p.is_file(),'CREATE_PRIVATE_SERVER_RSSHUB_ENV_FIRST'
assert p.stat().st_mode & 0o077 == 0,'RSSHUB_ENV_MUST_BE_0600'
lines=p.read_text().splitlines()
assert any(x.startswith('TWITTER_AUTH_TOKEN=') and len(x.split('=',1)[1].strip())>16 and
           'REPLACE' not in x.upper() for x in lines),'AUTH_TOKEN_MISSING'
PY
# Refuse to hijack an existing instance that is managed elsewhere.
if docker inspect rsshub-intelligence >/dev/null 2>&1 && [[ ! -e "$DIR/compose.yaml" ]];then
 echo "A same-name container exists but is not managed by this target directory; STOP" >&2
 exit 6
fi
if [[ -e "$DIR/compose.yaml" && "$UPGRADE" != 1 ]];then
 echo "Existing RSSHub; use --upgrade after backup" >&2;exit 3
fi
if [[ "$MODE" == proxy ]];then
 docker network inspect rsshub-egress-net >/dev/null || { echo "Create isolated proxy Docker network first" >&2;exit 4; }
 grep -q '^RSS_PROXY_URI=socks5h://' /etc/rsshub-intelligence.env || { echo "Set RSS_PROXY_URI in private config" >&2;exit 5; }
fi
install -d -m 0700 -o root -g root "$DIR"
if [[ -f "$DIR/compose.yaml" ]];then cp -a "$DIR/compose.yaml" "$DIR/compose.previous-$(date +%Y%m%d%H%M%S).yaml";fi
install -m 0600 -o root -g root /etc/rsshub-intelligence.env "$DIR/.env"
install -m 0644 -o root -g root "$REPO/infra/rsshub/compose.$MODE.yaml" "$DIR/compose.yaml"
cd "$DIR"
docker compose config --quiet
docker compose up -d
curl -fsS --retry 5 --retry-delay 4 --max-time 18 http://127.0.0.1:1200/healthz >/dev/null
echo "RSSHUB_HEALTHY; still verify real X post freshness before baseline."
