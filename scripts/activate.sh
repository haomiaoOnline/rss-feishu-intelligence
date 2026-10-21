#!/usr/bin/env bash
set -euo pipefail
MODE=dry-run
for arg in "$@";do case "$arg" in
 --apply) MODE=apply;;
 --dry-run) MODE=dry-run;;
 *) echo "Unknown arg: $arg" >&2;exit 2;;
esac;done
if [[ "$MODE" != apply ]];then
 echo "DRY_RUN_ONLY: explicit --apply required after bootstrap and consent"
 exit 0
fi
[[ $EUID == 0 ]] || { echo "root required" >&2;exit 2; }
APP=/opt/rss-feishu-push/push.py
python3 -B "$APP" self-test
python3 -B "$APP" validate-rss-webhook
curl -fsS --max-time 8 http://127.0.0.1:1200/healthz >/dev/null
python3 - <<'PY'
import sqlite3
db=sqlite3.connect('/opt/rss-feishu-push/state/push.sqlite3')
ok=db.execute('select count(*) from feeds where initialized_at is not null').fetchone()[0]
q=db.execute('select count(*) from articles where delivered=0').fetchone()[0]
assert ok>0,'BOOTSTRAP_FIRST'
assert q==0,'UNREVIEWED_QUEUE_CANNOT_ACTIVATE'
print('READY_WITH_INITIALIZED_FEEDS',ok)
PY
systemctl enable --now rss-feishu-push.timer
systemctl is-active --quiet rss-feishu-push.timer
systemctl list-timers rss-feishu-push.timer --no-pager
echo TIMER_ENABLED
