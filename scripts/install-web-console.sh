#!/usr/bin/env bash
set -euo pipefail
# Install the localhost-only web console and group worker. No bot tests sent.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ACTION=dry-run
for arg in "$@"; do
  case "$arg" in
    --dry-run) ACTION=dry-run;;
    --apply) ACTION=apply;;
    *) echo "Unexpected option: $arg" >&2; exit 2;;
  esac
done
echo "Web console: 127.0.0.1:8765 (SSH tunnel required)"
echo "Feishu group worker: one batch per active group, hourly at :15"
echo "Disables legacy Git feeds-sync.timer to prevent overwriting UI source edits"
echo "Preserves legacy RSS delivery records and daily report services"
if [[ "$ACTION" != apply ]]; then
  echo "DRY_RUN_ONLY"
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo "root required" >&2; exit 2; }
test -x /usr/bin/flock || { echo "flock required" >&2; exit 2; }
test -f /opt/rss-feishu-push/push.py || { echo "existing collector missing" >&2; exit 2; }
test -f /opt/rss-feishu-push/state/push.sqlite3 || { echo "existing DB missing" >&2; exit 2; }
systemctl is-active --quiet rss-feishu-push.service && { echo "Collector actively running" >&2; exit 3; }
systemctl is-active --quiet rss-group-dispatch.service && { echo "Group dispatch actively running" >&2; exit 3; }
systemctl is-active --quiet rss-web-admin.service && { echo "Web admin active: stop first" >&2; exit 3; }
systemctl is-active --quiet rss-feishu-push.timer && { echo "Stop RSS timer for safe upgrade first" >&2; exit 3; }
systemctl is-active --quiet rss-feeds-sync.timer && { echo "Stop old Git feed sync timer first" >&2; exit 3; }

python3 -m py_compile "$REPO/src/rss_feishu_push.py" "$REPO/src/rss_groups.py" "$REPO/src/rss_web_admin.py"
RSS_FEEDS_FILE="$REPO/config/feeds.json" python3 -B "$REPO/src/rss_feishu_push.py" self-test

TARGET=/opt/rss-feishu-push
BACKUP="$TARGET/state/pre-web-console-$(date +%Y%m%d%H%M%S).sqlite3"
python3 - "$TARGET/state/push.sqlite3" "$BACKUP" <<'PY'
import os,sqlite3,sys
src=sqlite3.connect(sys.argv[1])
dst=sqlite3.connect(sys.argv[2])
src.backup(dst)
src.close();dst.close()
os.chmod(sys.argv[2],0o600)
print("SQLite online backup saved outside Git")
PY
install -d -o root -g root -m 0700 /etc/rss-feishu-groups
# Only the user or authorized root should view the generated token.
if [[ ! -e /etc/rss-feishu-admin.token ]]; then
  python3 - <<'PY'
import os,secrets
p='/etc/rss-feishu-admin.token'
fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as out:
  out.write(secrets.token_urlsafe(40)+'\n')
print("Admin token generated at /etc/rss-feishu-admin.token (0600); value omitted")
PY
fi
test "$(stat -c '%a' /etc/rss-feishu-admin.token)" = 600 || {
  echo "Admin token file must be 0600" >&2; exit 4;
}
STAMP="$(date +%Y%m%d%H%M%S)"
cp -a "$TARGET/push.py" "$TARGET/push.py.before-web-console-$STAMP"
install -o root -g root -m 0750 "$REPO/src/rss_feishu_push.py" "$TARGET/push.py"
install -o root -g root -m 0750 "$REPO/src/rss_groups.py" "$TARGET/rss_groups.py"
install -o root -g root -m 0750 "$REPO/src/rss_web_admin.py" "$TARGET/rss_web_admin.py"
# Older production entrypoint is push.py; new modules import rss_feishu_push.
# Relative alias avoids divergence between two deployed collector copies.
if [[ ! -e "$TARGET/rss_feishu_push.py" && ! -L "$TARGET/rss_feishu_push.py" ]]; then
  ln -s push.py "$TARGET/rss_feishu_push.py"
elif [[ ! -L "$TARGET/rss_feishu_push.py" || "$(readlink "$TARGET/rss_feishu_push.py")" != push.py ]]; then
  echo "Unexpected collector module alias; stop for manual review" >&2
  exit 4
fi
install -o root -g root -m 0644 "$REPO/infra/systemd/rss-feishu-push.service" /etc/systemd/system/rss-feishu-push.service
for name in rss-web-admin.service rss-group-dispatch.service rss-group-dispatch.timer; do
  install -o root -g root -m 0644 "$REPO/infra/systemd/$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
python3 -B "$TARGET/push.py" self-test
python3 -B "$TARGET/rss_groups.py" init
python3 -B "$TARGET/rss_web_admin.py" check
echo "INSTALL_COMPLETE_NO_SCHEDULE_STARTED"
echo "Operator next: enable --now rss-feishu-push.timer rss-group-dispatch.timer rss-web-admin.service."
echo "Do NOT reenable rss-feeds-sync.timer: web UI owns the feed manifest now."
