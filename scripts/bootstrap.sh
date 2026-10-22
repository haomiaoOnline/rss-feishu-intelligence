#!/usr/bin/env bash
set -euo pipefail
ACTION=dry-run;MIN_OK=all
for arg in "$@";do case "$arg" in
 --apply) ACTION=apply;;
 --dry-run) ACTION=dry-run;;
 --min-success=*) MIN_OK="$(echo "$arg" | cut -d= -f2)";;
 *) echo "Unknown arg: $arg" >&2;exit 2;;
esac;done
if [[ "$MIN_OK" != all && ! "$MIN_OK" =~ ^[0-9]+$ ]];then
 echo "bad min-success" >&2;exit 2
fi
echo "Reads the unified manifest into SQLite baseline; does not send Feishu or enable scheduler"
if [[ "$ACTION" != apply ]];then echo DRY_RUN_ONLY;exit 0;fi
[[ $EUID == 0 ]] || { echo "root required" >&2;exit 2; }
! systemctl is-active --quiet rss-feishu-push.timer || { echo "Pause timer first";exit 3; }
curl -fsS --max-time 8 http://127.0.0.1:1200/healthz >/dev/null
python3 -B /opt/rss-feishu-push/push.py bootstrap
python3 -B /opt/rss-feishu-push/push.py status
python3 - "$MIN_OK" <<'PY'
import sys,sqlite3,json
db=sqlite3.connect('/opt/rss-feishu-push/state/push.sqlite3')
ok=db.execute('select count(*) from feeds where initialized_at is not null').fetchone()[0]
q=db.execute('select count(*) from articles where delivered=0').fetchone()[0]
print('INITIALIZED',ok,'QUEUE',q)
threshold=len(json.loads(open('/opt/rss-feishu-push/feeds.json').read())['feeds']) if sys.argv[1]=='all' else int(sys.argv[1])
assert ok>=threshold,'INITIALIZATION_BELOW_ACCEPTANCE_THRESHOLD'
assert q==0,'QUEUE_MUST_BE_ZERO_FOR_FRESH_BOOTSTRAP'
PY
echo BASELINE_ACCEPTED
