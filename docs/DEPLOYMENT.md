# Agent 从零部署到上线

本指南从一台新的 Ubuntu/Debian systemd Linux 主机开始。凡标为“用户操作”的步骤，需要账号所有者本人或已获授权的密钥管理系统完成。Agent 不得把凭据复制进聊天或源码。先看 AGENTS.md 的停止条件。

## 前提与验收口径

- 一台经授权的 Linux 服务器，2 vCPU，至少约1.5 GiB RAM、可用磁盘空间；网络或海外出口可访问 X；拥有 root/sudo 操作权限。
- Python >= 3.9，docker 与 docker compose v2、systemd、curl、ssh、git。
- 已授权的 X 账号有效 Cookie，飞书群自定义机器人专用 Webhook，关键词必须允许 RSS情报。
- 可选择全直连 direct 或受限 SOCKS5 proxy 模式，后者先完成 docs/EGRESS_PROXY.md。
- 需已有 Docker Image 的外网拉取能力；如中国大陆服务器不能拉 Docker Hub，先在经授权的机器拉取并安全转移镜像 tar，再 docker load。模板支持通过服务器私有 .env 中 RSSHUB_IMAGE 指定已预加载镜像。
- 首次运行期望46位公开博主。请不要用流量过高的账号模拟测试，避免被 X 限流。

## 0. 在本地仓库确认安全与代码

    cd /Users/chenglong/github/rss-feishu-intelligence
    python3 tests/test_push.py
    RSS_FEEDS_FILE=config/feeds.json python3 src/rss_feishu_push.py self-test
    python3 scripts/scan_secrets.py
    bash scripts/install-rsshub.sh --mode=direct --dry-run
    bash scripts/install-collector.sh --dry-run

远程新主机可仅传递 Git 已提交文件。例如 git archive HEAD 生成源码包（不能含未提交源码）；避免 rsync 全目录把 .env 等未跟踪文件意外拷走。

    git archive --format=tar HEAD > /tmp/rss-feishu-intelligence-src.tar
    scp /tmp/rss-feishu-intelligence-src.tar <AUTHORIZED_SSH_HOST>:/tmp/
    ssh <AUTHORIZED_SSH_HOST>
    mkdir -p ~/rss-feishu-build
    tar -xf /tmp/rss-feishu-intelligence-src.tar -C ~/rss-feishu-build

复制地址由用户提供的安全 SSH 配置或已有 alias 提供。不写真实 IP 到 Git。

## 1. 敏感文件仅在目标服务器准备（用户操作）

在服务器内执行，避免通过 history 带入秘钥字符串。

    sudo install -o root -g root -m 0600 /dev/null /etc/rsshub-intelligence.env
    sudo install -o root -g root -m 0600 /dev/null /etc/rss-feishu-push.env
    sudoedit /etc/rsshub-intelligence.env
    sudoedit /etc/rss-feishu-push.env
    sudo chmod 600 /etc/rsshub-intelligence.env /etc/rss-feishu-push.env

第一份写入真实 TWITTER_AUTH_TOKEN=...；选代理模式额外设置 RSS_PROXY_URI=socks5h://<PRIVATE_DOCKER_GATEWAY>:10809；可选 RSSHUB_IMAGE 指向能拉取或已导入的镜像。

第二份只写 FEISHU_WEBHOOK_URL_RSS=...。必须是新建 RSS 话题群的独立自定义机器人，安全配置关键词为 RSS情报。不能复制已有工作日报 Webhook。不要在任何 shell 命令行 echo 密钥；编辑器要确保不产生世界可读的备份文件。

只输出文件存在、权限与格式的布尔值，不打印密钥或 curl -v 请求详情。

## 2. 安装 RSSHub

direct 模式优先在可以直接访问 X 的环境中使用。

    cd ~/rss-feishu-build
    sudo bash scripts/install-rsshub.sh --mode=direct --dry-run
    sudo bash scripts/install-rsshub.sh --mode=direct --apply

必须经受限 SSH SOCKS5 出口的情况，先照 docs/EGRESS_PROXY.md 检查 Docker 私有网桥和对应服务，再执行。

    sudo bash scripts/install-rsshub.sh --mode=proxy --dry-run
    sudo bash scripts/install-rsshub.sh --mode=proxy --apply

脚本只将 RSSHub 绑定到 127.0.0.1:1200。若端口占用或同名容器已存在，要检查现有服务并停止本次安装，**不能直接 kill / 删除其他进程**。如果已有通过验收的 RSSHub，应复用该实例，不运行第二个 installer。

## 3. 真正验收 X 内容新鲜度

    curl -fsS http://127.0.0.1:1200/healthz
    curl -fsS --max-time 25 http://127.0.0.1:1200/twitter/user/dotey > /tmp/rss-probe.xml
    python3 - <<'PY'
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    root=ET.parse('/tmp/rss-probe.xml').getroot()
    items=root.findall('.//item')
    print('ITEM_COUNT',len(items))
    for item in items[:3]:
        dt=parsedate_to_datetime(item.findtext('pubDate'))
        print('PUBLISHED',dt.isoformat())
    assert items,'EMPTY_RSS_CANNOT_PASS'
    PY

同时与 X 原站可见的近期帖子对照。HTTP 200 或 XML 解析成功不足以通过。针对全部46个账号以低并发抽样/轮询，若有 503/超时标注，不猜账号是否停更。不能为追求验收把失败源源源不断快速重试。

## 4. 安装独立 Collector（不会发送消息）

    cd ~/rss-feishu-build
    sudo bash scripts/install-collector.sh --dry-run
    sudo bash scripts/install-collector.sh --apply
    sudo python3 -B /opt/rss-feishu-push/push.py validate-rss-webhook
    sudo python3 -B /opt/rss-feishu-push/push.py self-test
    systemctl is-enabled rss-feishu-push.timer || true

注意后两条命令只是配置/离线校验，没有发送飞书。安装结束后 timer 应当仍是 disabled/inactive。其他 Agent 不得自动跳过这个检查。

## 5. 初始化旧帖基线

    sudo bash scripts/bootstrap.sh --dry-run
    sudo bash scripts/bootstrap.sh --apply
    sudo python3 -B /opt/rss-feishu-push/push.py status

默认要求全部46位来源至少首次成功。若个别源确因 X 接口故障无法初始化，经过用户明确认可，可按最低允许成功数执行 sudo bash scripts/bootstrap.sh --apply --min-success=45。失败源会在首次成功时只建立自己的历史基线，不会将旧帖当成新帖推送。首次基线后 pending_articles 应为0；否则暂停流程，人工核查数据库/历史，而非强制清空。

## 6. 验收新群机器人（需要外部消息授权）

使用 Python 进程读取服务器私有配置，按操作指引调用 send-test 前，应征得用户同意；它会向真正的飞书群发送一条明确【排版测试】消息。注意老版本的发送标记可能导致重复测试被拒绝，不可通过删除 SQLite 记录强制重发。

    sudo python3 -B /opt/rss-feishu-push/push.py send-test

这是独立于定时发送的一次明确效果验证，测试消息不应该与真实新文章混合。API 返回 success 后仍由用户在飞书客户端确认接收。错误和超时不可盲目重复发送。确认 RSS 群已收到且日报群没有收到。

## 7. 显式上线

    sudo bash scripts/activate.sh --dry-run
    sudo bash scripts/activate.sh --apply
    systemctl list-timers --all rss-feishu-push.timer
    sudo bash scripts/healthcheck.sh

定时器使用 OnCalendar=hourly、随机延迟最多90秒，按服务器时区整点附近运行；首次启用 systemd Persistent 可能立刻补跑此前时间。每轮会检查46个来源，新帖经去重后每批最多12条。观察首轮日志及下一小时；出现旧帖洪泛必须暂停并核查基线。

    sudo journalctl -u rss-feishu-push.service --since '2 hours ago' --no-pager

不要打印包含 Webhook 的环境变量或请求头。完成用户验收后，交付 docs/HANDOFF.md 相关信息和 commit 哈希。

## 8. 原地升级现有 Collector（不是从零）

- 先审阅 git diff，离线测试和扫描 secrets。
- 登录目标机核查 timer/job 状态；禁用仅本项目的 timer，等待 service 完成。
- 备份 /opt/rss-feishu-push/state/push.sqlite3（SQLite online backup）和 push.py。不要覆盖数据库。
- 运行 installer --dry-run，明确执行 --apply --upgrade；复跑 self-test、validate-rss-webhook，检查 state 与旧 version。
- 若原 timer 正在运行，拒绝热升级。必须由被授权的 Agent 显式停启。
- 修改订阅46账号、schema、主题分类/消息格式时先审查待发批次，不回放已发送数据。兼容性不确定必须进入用户验收。
- 仅升级 Collector 无需重装 RSSHub、海外出口，也不应影响日报业务。

## 9. 回滚

    sudo systemctl disable --now rss-feishu-push.timer
    # 程序版本回退为经核实的 push.py.previous-* 备份
    # 保留 SQLite 数据，先使用 docs/OPERATIONS.md 的一致性备份流程
    sudo systemctl daemon-reload

回滚 RSSHub、出口涉及真实用户进程操作，须经过独立授权，不能把已运行的日报或其他容器一起停掉。
