PYTHON ?= python3

.PHONY: test security dry-run
test:
	$(PYTHON) -m py_compile src/rss_feishu_push.py tests/test_push.py tests/test_config.py
	$(PYTHON) tests/test_push.py
	$(PYTHON) tests/test_config.py
	RSS_FEEDS_FILE=config/feeds.json $(PYTHON) src/rss_feishu_push.py self-test

security:
	$(PYTHON) scripts/scan_secrets.py
	git diff --cached --check

dry-run:
	bash scripts/install-rsshub.sh --mode=direct --dry-run
	bash scripts/install-rsshub.sh --mode=proxy --dry-run
	bash scripts/install-collector.sh --dry-run
	bash scripts/bootstrap.sh --dry-run
	bash scripts/activate.sh --dry-run
