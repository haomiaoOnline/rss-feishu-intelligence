# RSS → 飞书情报自动推送

经真实运行验收的 X/RSSHub/Feishu 自动情报项目。本地 Git 仓库存放的是可复制的程序、46个公开的订阅账号、部署模板、离线测试与 Agent 操作规程。服务器登录凭据、飞书 Webhook、X auth cookie、SSH 私钥、SQLite 内容和运行日志绝不存入本仓库。

## 实际功能（已验收的 v4 基线）

- RSSHub 聚合 46 个 X 中文博主的 RSS；首次每个来源成功时将既有文章标成历史，避免刷屏。
- Python 标准库程序每小时检查一次，使用 SQLite 幂等去重、持久化待发队列、发送记录和指数退避重试。
- 每小时最多汇总12条，每次成功发送后至少等待55分钟。
- 推送到独立的飞书自定义群机器人，仅使用 FEISHU_WEBHOOK_URL_RSS。消息关键词 RSS情报。
- 一条 interactive 飞书卡片按固定关键词聚合 Agent/自动化、模型/开发、产品/工具、创业/商业化、出海/增长、其他。
- 同主题每篇用独立编号、作者时间行和可点击的原帖文字行。无“重要度”、无 AI 摘要、不附多余箭头。标题仅是 RSS 原帖文字截取。
- 异常来源下轮重试；飞书明确拒收退避重试，结果未知的请求人工核验，不盲目重复发送。
- 云端定时执行不依赖 Mac、Chrome 或 Feedbro。

## 统一订阅 v2

Git唯一订阅配置 config/feeds.json：46位X博主 + 20条Feedbro普通HTTPS RSS。支持动态订阅数（安全上限72）、RSS/Atom、OPML导入导出、健康状态、云端订阅配置自动同步。浏览器Feedbro的变更需先导出OPML提交Git，并非直接自动双向同步。具体见 docs/SUBSCRIPTIONS.md。

## 仓库入口

| 需要做什么 | 阅读文件 |
| --- | --- |
| 其他 Agent 首次接手与硬性边界 | AGENTS.md |
| 从全新 Linux 主机部署至上线 | docs/DEPLOYMENT.md |
| SSH 海外出口和 Docker 网络 | docs/EGRESS_PROXY.md |
| 服务架构与状态机 | docs/ARCHITECTURE.md |
| 监控、故障诊断、备份与回滚 | docs/OPERATIONS.md |
| X / 飞书凭据与 Git 信息安全 | docs/SECURITY.md |
| 本地/离线测试及验收清单 | docs/TESTING.md |
| 统一订阅清单管理 | docs/SUBSCRIPTIONS.md |
| 现网交接与后续改进建议 | docs/HANDOFF.md |

## 先在 Mac 运行完全离线的检查

    cd /Users/chenglong/github/rss-feishu-intelligence
    python3 tests/test_push.py
    RSS_FEEDS_FILE=config/feeds.json python3 src/rss_feishu_push.py self-test
    python3 scripts/scan_secrets.py
    bash scripts/install-rsshub.sh --mode=proxy --dry-run
    bash scripts/install-collector.sh --dry-run

以上命令不会连接飞书、不会初始化线上数据库，不会启动定时任务。

## 一次生产部署的最短顺序

1. 根据 docs/DEPLOYMENT.md 核对 Linux、SSH、Docker、Python 版本与资源；确认是否需要受限海外出口。
2. 在目标服务器上离开 Git 仓库，安全创建 /etc/rsshub-intelligence.env 和 /etc/rss-feishu-push.env；不可将密钥写到命令行、工单或日志中。
3. 运行 RSSHub installer 的 dry-run，确认目标路径，随后由 Agent 按经授权的命令安装 RSSHub。必须验证真实 X 帖子的最近发布日期，HTTP 200 不足以验收。
4. 运行 collector installer 的 dry-run，再执行 --apply，检查 timer 此时尚未启用。
5. 运行 bootstrap.sh --apply 建立每个源的历史基线，不发送飞书消息。
6. 在用户授权下，单独验证 RSS 机器人（一次明确标记的测试消息）。然后使用 activate.sh --apply 开启定时器。
7. 观察首轮运行及下一小时，并让用户在飞书客户端确认真实消息与日报群隔离。

完整的命令、回滚和失败处理在 docs/DEPLOYMENT.md。

## 限制与已知问题

- X 登录态会失效，单个账号返回 503/超时并不罕见，不能保证46个账号每轮都成功。
- 单次最多12条，超过到达速率时待发送队列会增长，不具备零延迟 SLA。
- 固定关键词分类不会理解复杂语义；无 AI 模型调用。
- Feedbro 曾出现 Chrome 扩展后台兼容故障，不属于云端推送的关键依赖。
- 该仓库是本地 Git 项目，初次版本仅本地 commit，不默认创建远程 GitHub 仓库或推送。
