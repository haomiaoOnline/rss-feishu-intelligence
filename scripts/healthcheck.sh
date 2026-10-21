#!/usr/bin/env bash
set -euo pipefail
echo RSSHUB
curl -sS --connect-timeout 3 --max-time 8 -o /dev/null -w 'http=%{http_code}\n' http://127.0.0.1:1200/healthz
echo SCHEDULER
systemctl is-active rss-feishu-push.timer || true
systemctl list-timers rss-feishu-push.timer --no-pager || true
echo SQLITE_STATUS
python3 -B /opt/rss-feishu-push/push.py status
echo LAST_JOB_RESULT
systemctl show rss-feishu-push.service -p Result -p ExecMainStatus -p ActiveState --no-pager
