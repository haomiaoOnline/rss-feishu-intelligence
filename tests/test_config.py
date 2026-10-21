#!/usr/bin/env python3
"""Offline safety and config regression tests, never performs network I/O."""
from pathlib import Path
import importlib.util
import json
import subprocess
import tempfile
import time
import os

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rsspush_config_test", repo / "src/rss_feishu_push.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory(prefix="rss-feishu-offline-") as name:
    root = Path(name)
    config = root / "webhook.env"
    mod.CONFIG_FILE = config
    # Deliberately synthetic, NEVER a real hook.
    dummy = "https://open.feishu.cn/open-apis/bot/v2/hook/" + ("x" * 28)
    config.write_text("FEISHU_WEBHOOK_URL=" + dummy + "\n")
    try:
        mod.webhook()
        raise AssertionError("fallback to daily hook should never work")
    except RuntimeError as exc:
        assert str(exc) == "RSS_WEBHOOK_MISSING_OR_DUPLICATE"
    config.write_text("FEISHU_WEBHOOK_URL_RSS=" + dummy + "\n")
    assert mod.webhook() == dummy
    config.write_text("FEISHU_WEBHOOK_URL_RSS=" + dummy + "\n" +
                      "FEISHU_WEBHOOK_URL_RSS=" + dummy + "\n")
    try:
        mod.webhook()
        raise AssertionError("duplicate webhook key should be rejected")
    except RuntimeError as exc:
        assert str(exc) == "RSS_WEBHOOK_MISSING_OR_DUPLICATE"
print("PASS_STRICT_RSS_WEBHOOK_NO_DAILY_FALLBACK")

now = int(time.time())
posts = [{
    "handle": "dotey", "name": "Test Person", "title": "Agent MCP weekly practice",
    "published": now, "url": "https://x.com/dotey/status/" + str(1000 + i)
} for i in range(12)]
card = mod.build_post(posts, now)
raw = json.dumps(card, ensure_ascii=False)
assert card["msg_type"] == "interactive"
assert "RSS情报" in card["card"]["header"]["title"]["content"]
assert raw.count("https://x.com/") == 12
assert len(card["card"]["elements"]) < 12
assert "↗" not in raw and "重要度" not in raw and "AI摘要" not in raw
assert "**01 · " in raw and "**12 · " in raw
assert len(raw.encode()) < 19000
print("PASS_V4_TWELVE_ENTRIES_COMPACT_AND_NO_AI")

bad = [{
    "handle": "dotey", "name": "<at id=all>",
    "published": now,
    "title": "Agent " + "<at id=all>" + " [click](https://evil.invalid) **unsafe**",
    "url": "https://evil.invalid/redirect"
}]
safe = json.dumps(mod.build_post(bad, now), ensure_ascii=False)
assert "<at" not in safe and "evil.invalid" not in safe
assert "https://x.com/dotey" in safe
print("PASS_UNTRUSTED_TEXT_AND_URL_NORMALIZATION")

for f in sorted((repo / "scripts").glob("*.sh")):
    check = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
    assert check.returncode == 0, (f.name, check.stderr)
for args in [
    ["bash", "scripts/install-rsshub.sh", "--mode=direct", "--dry-run"],
    ["bash", "scripts/install-rsshub.sh", "--mode=proxy", "--dry-run"],
    ["bash", "scripts/install-collector.sh", "--dry-run"],
    ["bash", "scripts/bootstrap.sh", "--dry-run"],
    ["bash", "scripts/activate.sh", "--dry-run"],
]:
    completed = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=9)
    assert completed.returncode == 0, (args, completed.stdout, completed.stderr)
    assert "DRY_RUN" in completed.stdout, args
print("PASS_ALL_SAFE_DRY_RUNS")

scanner_spec=importlib.util.spec_from_file_location("secret_scan_test",repo/"scripts/scan_secrets.py")
scanner=importlib.util.module_from_spec(scanner_spec)
scanner_spec.loader.exec_module(scanner)
cases=[
    (Path("leak.env"), b"something=value"),
    (Path("normal.txt"), ("TWITTER_AUTH_TOKEN=" + "t"*40).encode()),
    (Path("normal.txt"), ("FEISHU_WEBHOOK_" + "URL_RSS=" + "https://" + "open.feishu.cn/open-apis/bot/v2/hook/" + "x"*36).encode()),
    (Path("normal.txt"), ("8.8."+"8.8").encode()),
]
for path,content in cases:
    assert scanner.scan_file(path,content),(path,content[:15])
print("PASS_SECRET_GUARD_DETECTS_LEAKS")
print("CONFIG_AND_DEPLOYMENT_TEST_SUITE_OK")
