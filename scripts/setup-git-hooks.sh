#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
test -d .git || { echo "git init first" >&2;exit 2; }
HOOK=.git/hooks/pre-commit
if [[ -e "$HOOK" ]];then echo "pre-commit hook exists; review before overwriting" >&2;exit 2;fi
cat > "$HOOK" <<'HOOK_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
python3 scripts/scan_secrets.py
git diff --cached --check
HOOK_SCRIPT
chmod 700 "$HOOK"
echo "Local Git pre-commit secret guard installed"
