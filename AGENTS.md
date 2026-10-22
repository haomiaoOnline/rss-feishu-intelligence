# Agent 接手指引

任务目标：任何拿到本仓库及经授权的服务器权限的 Agent，能安全部署独立 RSSHub → Feishu RSS 情报推送、复验并交接；不得碰已有日报项目。

## 不可越过的边界

1. 默认先观测。仅当操作者明确批准时执行服务器创建、重启、变更防火墙、部署、发送飞书测试消息、发布 Git 或外部推送。
2. 不索取 X auth cookie、Webhook、SSH private key 原文到聊天。必须由用户通过目标服务器上的安全交互方式自行设置，或使用获授权的秘密管理服务。永不打印它们。
3. 不允许将任何真实 .env、SQLite、日志、私钥、X cookies、飞书 Webhook、个人 IP/会话截图提交 Git。Git 是持久历史，误提交以后删除工作区文件也不等于清除历史。
4. 不修改 /etc/daily-work-report.env、日报的 systemd 服务、日报群 Webhook 或其他 Docker 业务容器。
5. 不以 HTTP 200 判断 X RSS 数据新鲜度。抽查 pubDate 是否是实际近期 X 帖文。
6. 初次部署先调用 bootstrap 建历史基线；确认无待发旧帖后才允许启用 timer，且用户要验收真实群消息。
7. 已经发送的批次一律不重发；ambiguous 批次必须人工核实；不得清空/覆盖线上 SQLite。
8. 修改代码前看 Git status 和最近提交。部署前备份目标程序与 SQLite；升级不应在服务正在执行时热替换。

## 上手的确定顺序

A. 从 README.md 确认实际功能，读取 docs/ARCHITECTURE.md，浏览 src/rss_feishu_push.py 的命令行入口。
B. 执行 Python 离线测试与 secrets scan、shell lint；禁止因测试结果失败而跳过。
C. 读取 docs/DEPLOYMENT.md，根据环境选择 direct 或 proxy；如果需要海外出口，先按 docs/EGRESS_PROXY.md 办理用户授权的 SSH / Docker 网络。不得擅自变更 SSHD 或开放 SOCKS 端口。
D. 配置敏感凭据仅在服务器 /etc 的 mode 0600 文件中；Agent 自己只确认存在性、格式和进程加载状态，不读取或输出原值。
E. 首次部署顺序为 RSSHub → 验证单源的内容新鲜度 → collector → bootstrap → 验证接收群 → activate。
F. 检查 systemctl 的定时器和 journalctl 的运行结果，SQLite status 和用户飞书接收结果；故障时按 docs/OPERATIONS.md 处理。
G. 提交 Git 仅允许跟踪代码/文档/示例/测试。先执行 secrets scanner，git diff --cached 审核，然后 commit。用户未授权前不得 push。

## 重要文件

- src/rss_feishu_push.py 是正式 v4 源码（旧现网 push.py 的可移植版本）。
- config/feeds.json 是唯一订阅配置，包含原46位X及20个普通HTTPS RSS。变更前后必须通过校验、测试、安全同步dry-run。
- infra/rsshub/ 是 direct/proxy 二选一的 Compose 模板；只能绑定 127.0.0.1 端口。
- infra/systemd/ 是后台每小时 timer 模板。首次验收前 timer 必须停用。
- scripts/install-rsshub.sh、scripts/install-collector.sh 不会隐式发送消息或启用 timer。
- scripts/bootstrap.sh 只写首次基线，不发送群消息。
- scripts/activate.sh 才会显式启用真实推送，默认 dry-run。
- scripts/scan_secrets.py 应在 Git 暂存之后再次运行。
- docs/HANDOFF.md 记录验收边界与已知问题，不要误写无限稳定或永久 X 授权。

## 完成定义

- 离线测试全部通过，示例安装 dry-run 正常。
- 服务器 RSSHub 内容最近发布时间核对通过，46个源首次成功率按用户接受阈值达标。
- 首次不发旧消息；SQLite 中批次状态与队列合理。
- 独立飞书 RSS 群收到明确【测试】消息（用户确认），再在下一运行周期收到真正新增的主题卡片；日报群没有 RSS 消息。
- 提交内无凭据或用户运行数据，Git status 清洁；提供当前路径、HEAD commit、回滚命令与所有未解决故障。

## 必须停下来找用户的情况

需要本人登录、设置/轮换 Cookie/Webhook/SSH 密钥或防火墙授权；配置验收出现产品取舍；首次上线及大变更达 READY_FOR_USER_ACCEPTANCE 等待用户回复 DONE。

## 统一订阅同步 v2

增加/删除/合并OPML之前读 docs/SUBSCRIPTIONS.md；必须先dry-run再apply。云端只跟踪Git main里的feeds.json，不执行远程代码。同步和推送必须共用flock锁。无需给Feedbro浏览器安装修改插件。不要把实际RSS登录令牌、Webhook、内部服务器URL提交Git。
