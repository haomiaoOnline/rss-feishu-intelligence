#!/usr/bin/env bash
set -euo pipefail
# Stage config-sync (not application auto-update). Defaults to dry-run.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ACTION=dry-run
for arg in "$@";do case "$arg" in
 --apply) ACTION=apply;;
 --dry-run) ACTION=dry-run;;
 *) echo "Unknown option $arg" >&2;exit 2;;
esac;done
echo "Will synchronize only config/feeds.json from reviewed Git main."
echo "No secrets are downloaded or written."
if [[ "$ACTION" != apply ]];then echo DRY_RUN_ONLY;exit 0;fi
[[ $EUID == 0 ]] || { echo "root required" >&2;exit 2; }
test -x /usr/bin/flock
test -f /opt/rss-feishu-push/push.py
test -f /opt/rss-feishu-push/feeds.json
test -f "$REPO/scripts/sync_feeds.py"
python3 -B "$REPO/scripts/sync_feeds.py" --source "$REPO/config/feeds.json"  --app /opt/rss-feishu-push/push.py --target /opt/rss-feishu-push/feeds.json
# GitHub contents API avoids a full clone; installation stays restricted
# to this collector and a dedicated timer. Do not expose credentials.
install -o root -g root -m 0750 "$REPO/scripts/sync_feeds.py" /opt/rss-feishu-push/sync_feeds.py
install -o root -g root -m 0644 "$REPO/infra/systemd/rss-feeds-sync.service" /etc/systemd/system/rss-feeds-sync.service
install -o root -g root -m 0644 "$REPO/infra/systemd/rss-feeds-sync.timer" /etc/systemd/system/rss-feeds-sync.timer
systemctl daemon-reload
echo "SYNC_INSTALLED_BUT_NOT_ENABLED"
echo "Operator: test one manual run and only then enable rss-feeds-sync.timer"
