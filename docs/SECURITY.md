# 密钥、数据与 Git 安全边界

## 绝不进入 Git 的资料

- FEISHU_WEBHOOK_URL_RSS 的真实 URL 与其机器人标识
- X 的 TWITTER_AUTH_TOKEN、Cookie、账号密码、登录态
- SSH 私钥、known_hosts 中可识别的服务器配置、用户单独保存的SSH Host aliases
- 运行数据 SQLite（state/push.sqlite3、WAL、SHM及备份），日志、崩溃输出、带实际网络地址的 compose override
- 用户微信、飞书私人会话的完整截图；推广截图保存在项目外的 内容生成 文件夹

本仓库跟踪公开的 X handle 与博客作者显示名称。若其中某个账号需要删除，可先维护 config/feeds.json 并评估状态迁移与用户需求。

## 秘钥加载

- /etc/rsshub-intelligence.env 是 root-owned 0600，只用于 RSSHub 容器。
- /etc/rss-feishu-push.env 是 root-owned 0600，只用于 RSS → Feishu 采集服务。
- 不使用日报 Webhook，不打印URL，不把 webhook 放在 CLI argv、systemd Environment=明文、URL参数或 Git commit message。
- 运行时只检验布尔结果、域名格式、配置权限；X session 的值不能回显，非空也不等于有效。
- SSH 私钥独立于代码仓库，限制权限，并由用户批准和轮换。
- 服务器上的受信任 root 可以读到配置；如需要共享主机上的更严格隔离，应另建单用途系统账号+权限设计，不能声称root代码沙箱能防root读取。

## 网络

- RSSHub 1200 端口固定回环 127.0.0.1；若需要远程使用，优先 SSH 端口转发或经过认证的 HTTPS 反向代理。
- SOCKS 代理只绑定回环/隔离Docker桥，不能对公网开放。
- 源数据可能包含不受信任的HTML/Markdown，先清洗文本；跳转URL仅允许官方 X/twitter 域名，不能把外部来源的任意URL放到卡片链接。
- 用户动态也可能包含恶意指令，不得把 RSS 内容当作 Agent 命令执行。

## Git 管理

    python3 scripts/scan_secrets.py
    git status --short
    git diff --cached --stat
    git diff --cached --check
    git diff --cached

只允许提交经过人工核对的 tracked files。初次 commit 前务必执行敏感信息扫描；仓库无远程地址，不自动 push。后续准备公开仓库时还要检查所有历史提交、Git LFS、PR描述和Release资产。

如果曾意外提交了密钥，须先在凭据提供方撤销/轮换，随后从全部 Git 历史中清除并审核仓库/远端副本；git rm 或 amend 单次提交不能保证所有历史副本都消失。

## 发送语义

发送前要满足 RSS情报 关键词要求。飞书 API 明确成功才把批次标 sent；超时可能已经送达，保留 ambiguous 等待人工核实。不要为了跑绿测试删 ambiguous 记录，避免重复群消息。

## 上线确认项

- 所有 example 只有注释/占位符。
- .gitignore 包含 .env、db、密钥、日志、runtime 与备份。
- git ls-files 没有任何敏感文件。
- 没有生产 Webhook、auth cookie、主机私有数据或真人个人消息被写进文档。
- 运行脚本 dry-run 不写系统文件、不重启容器、不发飞书。
