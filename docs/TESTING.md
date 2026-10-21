# 测试、验收与上线门槛

## 离线测试（任何 Agent 可以运行）

    python3 -m py_compile src/rss_feishu_push.py tests/test_push.py
    python3 tests/test_push.py
    python3 tests/test_config.py
    RSS_FEEDS_FILE=config/feeds.json python3 src/rss_feishu_push.py self-test
    python3 scripts/scan_secrets.py
    for file in scripts/*.sh; do bash -n "$file"; done
    bash scripts/install-rsshub.sh --mode=direct --dry-run
    bash scripts/install-rsshub.sh --mode=proxy --dry-run
    bash scripts/install-collector.sh --dry-run
    bash scripts/bootstrap.sh --dry-run
    bash scripts/activate.sh --dry-run

以上测试均不可调用真实 Feishu Webhook。test_push.py 使用临时SQLite与假的 RSS 内容测试历史基线和发送状态机。不可将线上 state 目录复制进入测试目录。

## 必要的离线断言

- 全部46位账号唯一，格式合法。
- 首次模拟849条历史全部标记不发送。
- 后续出现3条新文章，batch 是 interactive 卡片、具有 RSS情报 标题及3个官方X原文链接。
- Mock 飞书明确拒收后退避，成功后标 sent；重复轮询不再次入队。
- 未配置专用Webhook时拒绝发送，绝不读取/回退日报WebHook。
- 主题分组、同主题独立序号、作者/时间/原文以及无重要度/AI摘要。
- 禁止外部帖子内HTML/Markdown触发 @all 或跳转到陌生站点。
- Git 跟踪列表和内容中不能出现真实密钥、生产IPv4地址、敏感日志、SQLite与备份。

## 生产验收只能在授权下执行

1. 在 RSSHub server health=200 的前提下，手动检查近期真实 X 帖文 pubDate。
2. 首次执行 bootstrap 时确认 pending_articles=0，失败源后续首次成功只建基线。
3. 独立 RSS 群里一条明确测试卡片（须本人确认收到），以及下一次成功运行的真实新增汇总。
4. 生产日志记录源成功/失败数、飞书回应及 sent batch 数；没有重复、没有往工作日报群推 RSS。
5. 连续两次定时执行成功，观察代理和凭据会话情况。
6. 持续失败源要保留清晰清单，不能以本地离线测试通过冒充生产上线。

## Git 验收

    git status --short
    git ls-files
    git diff --cached --check
    python3 scripts/scan_secrets.py
    git log -1 --format='%h %s'

初始化 Git 之后，只在批准范围内 git add/commit。用户未要求远端 push，默认不设置 origin。
