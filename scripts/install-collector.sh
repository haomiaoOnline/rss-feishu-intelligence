#!/usr/bin/env bash
set -euo pipefail
# Safe stage-only installation, timer remains disabled.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET=/opt/rss-feishu-push
MODE=dry-run; UPGRADE=0
for arg in "$@";do case "$arg" in
 --dry-run) MODE=dry-run;;
 --apply) MODE=apply;;
 --upgrade) UPGRADE=1;;
 *) echo "Unknown argument: $arg" >&2;exit 2;;
esac;done
echo "Source=$REPO/src/rss_feishu_push.py Target=$TARGET/push.py"
echo "This command NEVER sends messages or enables the timer."
if [[ "$MODE" != apply ]];then echo DRY_RUN_ONLY;exit 0;fi
[[ $EUID == 0 ]] || { echo "root required" >&2;exit 2; }
command -v python3 >/dev/null;command -v systemctl >/dev/null
python3 -c 'import sys,zoneinfo;assert sys.version_info>=(3,9)'
if [[ -e "$TARGET/push.py" && "$UPGRADE" != 1 ]];then echo "Existing installation: use --upgrade" >&2;exit 3;fi
if systemctl is-active --quiet rss-feishu-push.service;then echo "Existing job active" >&2;exit 4;fi
if systemctl is-active --quiet rss-feishu-push.timer;then
 echo "Timer still active; pause after operator approval before any upgrade" >&2;exit 4
fi
install -d -m 0700 -o root -g root "$TARGET/state"
if [[ -e "$TARGET/push.py" ]];then cp -a "$TARGET/push.py" "$TARGET/push.py.previous-$(date +%Y%m%d%H%M%S)";fi
install -o root -g root -m 0750 "$REPO/src/rss_feishu_push.py" "$TARGET/push.py"
if [[ ! -f "$TARGET/feeds.json" || "$UPGRADE" == 1 ]];then
 install -o root -g root -m 0640 "$REPO/config/feeds.json" "$TARGET/feeds.json"
fi
install -o root -g root -m 0644 "$REPO/infra/systemd/rss-feishu-push.service" /etc/systemd/system/rss-feishu-push.service
install -o root -g root -m 0644 "$REPO/infra/systemd/rss-feishu-push.timer" /etc/systemd/system/rss-feishu-push.timer
systemctl daemon-reload
python3 -B "$TARGET/push.py" self-test
echo INSTALL_OK_TIMER_NOT_STARTED
