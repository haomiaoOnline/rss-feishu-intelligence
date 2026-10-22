# RSS 多群组 Web 管理台：安装、配置、验收与回滚

版本 v3；需求基线见 [WEB-CONSOLE-PRD.md](WEB-CONSOLE-PRD.md)。

## 1. 功能与技术边界

本地网页支持信源新增、名称与类别修改、启用/停用、确认删除；支持新建多个推送分组、复选各组信源、启停组、各组独立的 Feishu 机器人 Webhook、发送间隔和批次上限、单次人工【测试】消息、分组独立队列与投递记录。

历史 `articles`、`batches`、`meta` 保留原有值，旧飞书 RSS 话题群始终通过 `rss-feishu-push.service` 发送；新分组通过独立 `rss-group-dispatch.service` 推送，两个通道不会双重发送旧批次。

新入库的非基线、非过期文章才把 `eligible_for_groups=1`；数据表迁移仅新增列、表和索引。每次加入分组，记录加入时刻；加入前发现的文章不会补发到此组。不同组对于同篇文章以 `(group_id, article_id)` 独立去重，互不干扰。

**用户可见入口仅** `http://127.0.0.1:8765`。无需 Docker、无需 Nginx 或域名。服务器启动 Web 后，Mac 通过 SSH 隧道访问：

```bash
ssh -N -L 8765:127.0.0.1:8765 aliyunVBS
```

然后在 Mac 浏览器打开 `http://127.0.0.1:8765`。管理端不会监听公网地址，不需要修改云服务器安全组开放管理端口。如果本机 8765 被占用，改用本机另一个端口，例如 `ssh -N -L 18765:127.0.0.1:8765 aliyunVBS` 然后打开 `http://127.0.0.1:18765`。

## 2. 首次安装/现有服务器升级

**先决条件**：确认管理员有目标主机的 sudo 权限，且用户允许暂停本项目的 RSS 定时任务；不能自动停掉日报服务。最新版仓库已经通过 `make test`、`make dry-run`、`scripts/scan_secrets.py`。

1. 确认 RSS 服务没有正在运行的 Job、记录 SQLite source/record/batch counts、systemd 现有 unit 配置。
2. 暂停 **仅以下两个服务的定时器**，待正在运行的一轮 `rss-feishu-push.service` 退出后再继续：
```bash
sudo systemctl disable --now rss-feishu-push.timer
sudo systemctl disable --now rss-feeds-sync.timer
```
3. 把已验收 Git 提交中的 `src/`、`scripts/install-web-console.sh`、`infra/systemd/` 文件安全地复制到目标服务器一个临时工作区。**不要**复制本机的 `.env`、浏览器历史或本地 SQLite。
4. 执行安装脚本（脚本默认预演）：
```bash
sudo bash scripts/install-web-console.sh --dry-run
sudo bash scripts/install-web-console.sh --apply
```
5. 安装脚本在线备份 SQLite 于 `/opt/rss-feishu-push/state/pre-web-console-YYYYmmddHHMMSS.sqlite3`，程序版本留在 `/opt/rss-feishu-push/push.py.before-web-console-*`。脚本不重放任何旧批次，不会自动发送测试消息，也不自动启用新定时器。
6. 再次检查 SQLite 旧表的计数、`python3 -B /opt/rss-feishu-push/rss_groups.py init`、`python3 -B /opt/rss-feishu-push/rss_web_admin.py check`。
7. 经用户授权再启用三个独立服务：
```bash
sudo systemctl enable --now rss-feishu-push.timer
sudo systemctl enable --now rss-group-dispatch.timer
sudo systemctl enable --now rss-web-admin.service
```
8. 不要重新启用 `rss-feeds-sync.timer`。管理网页的 `feeds.json` 成为云端唯一写入源；若老 Git timer 仍启用，每次会覆盖用户页面改动。

管理员 token 首次安装自动安全生成于服务器 `/etc/rss-feishu-admin.token`（root:root 0600）；**不写入 Git、服务日志和任何聊天**。管理员本人通过有权限的 SSH 会话查看/使用该文件内的访问口令，并在本机登录表单输入。不得将访问口令通过 URL 传输。忘记密码时须按变更流程安全重置文件并重启 Web 服务，原 token 立即失效。

不同组的新群 webhook 保存在服务器 `/etc/rss-feishu-groups/<group_id>.webhook`（0600），不在页面、状态页或 SQLite 中回显原值。现有 `/etc/rss-feishu-push.env` 仍仅服务旧 RSS 群，不做修改。**飞书自定义群机器人关键词需允许“RSS情报”**。用户需自己建立目标飞书群的机器人并把 webhook 粘贴到页面的对应分组。

## 3. 网页操作顺序

打开左侧「信源管理」：新建 X handle 或 HTTPS RSS，填好名称和类别；需要暂停源就点「停用」，保留已有文章；修改名称和类别用「保存」；彻底删除需勾选确认，已经发送的 SQLite 历史不删除。 信源列表支持按作者、用户名、分类进行搜索，不需要在页面滚动全部66个来源。

点击「推送分组」→「创建分组」，例如“Agent 开发”“出海获客”。编辑页勾选本组所需信源，可以两组都选择同一位博主。分组不再使用物理删除：使用“归档分组”，会停用该组并保留发送历史及审计数据；待发批次会取消且不会自动重发。填本组专用飞书机器人 Webhook；填写后不会显示该值，只显示已配置。确认信源和机器人就绪以后，再勾选“启用定时发送”保存。

点击「测试机器人投递」前要勾选“确认向此群发送 1 条【测试】消息”。这是一次**真实外部飞书消息**，需要用户同意；只有看到实际群里接收了带【测试】的卡片才算人工验收。平台 API 成功不能替代客户端验收。

点击「投递记录」看新分组最近100个批次的状态和篇数。 `ambiguous` 代表结果不明，平台不自动重新发送；请先核实该群里是否真的收到该消息。

## 4. 真实验收命令与验收门槛

```bash
systemctl is-active rss-feishu-push.timer rss-group-dispatch.timer rss-web-admin.service
systemctl is-enabled rss-feeds-sync.timer  # 应为 disabled
curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/health
curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/  # 无 cookie 应为303跳转登录
sudo python3 -B /opt/rss-feishu-push/rss_groups.py status
sudo python3 -B /opt/rss-feishu-push/push.py status
journalctl -u rss-group-dispatch.service --since '2 hours ago' --no-pager
```

- [ ] Web 只监听 127.0.0.1，服务端未新增公网放行规则。
- [ ] 未登录访问重定向登录页；无 cookie 的写请求 401；非法 CSRF 和跨来源请求拒绝。
- [ ] 管理页面 CRUD、分组多选、分组 Webhook 只显示配置状态。
- [ ] 创建两组交叉来源，用 **mock webhook** 验证同一篇文章两个独立投递、同组不会重复。
- [ ] 新信源首次抓取不发送历史文章；已 sent/ambiguous 的旧群批次不变。
- [ ] 新组人工测试消息由本人在目标群看到且旧工作日报群未收到。
- [ ] Web 后台重启后配置仍保留；Git timer 禁用且不能覆盖页面修改。
- [ ] 旧 RSS 群照常运行，日报服务未修改，备份可校验恢复。
- [ ] 代码无敏感凭据，Git 日志提交 SHA 与 GitHub main 一致。

## 5. 回滚原则

出现任何旧 RSS 群重复推送、程序崩溃、管理页面越权或来源丢失时：

```bash
sudo systemctl disable --now rss-group-dispatch.timer
sudo systemctl disable --now rss-web-admin.service
sudo systemctl disable --now rss-feishu-push.timer
```

待原 RSS 服务确定已停止后，用 `push.py.before-web-console-*` 的已核实备份恢复程序并重新加载 systemd。SQLite **不应直接用旧快照覆盖正在运行的业务库**，迁移是加法式；若恢复 SQLite，可能会丢失部署后已经发送的批次记录，必须另行取得用户确认。

若要恢复以前 GitHub 订阅文件自动同步，应先关闭 Web 管理台对信源的写入、确认已经合并页面新增的订阅；否则重新启用 `rss-feeds-sync.timer` 会覆盖页面变更。

## 6. 当前缺口和阶段门

P0 云端可运行后台默认仅为本地/SSH 管理员使用。未执行单独审批时不得发布公网管理入口。飞书机器人真实 Group Webhook、域名、多用户登录、OAuth、组内高级时间窗口属于后续工作或需用户提供外部权限。本页内的成功测试仅确认编码逻辑，不代表所有未来 Feishu 群已经完成本人验收。

### Legacy collector module alias

The existing production executable remains /opt/rss-feishu-push/push.py.
V3 modules import the local module as rss_feishu_push, so the installer
creates /opt/rss-feishu-push/rss_feishu_push.py -> push.py (relative symlink).
The same actual implementation is reused. Unexpected existing alias files
are rejected rather than overwritten. Verify this alias and run
rss_groups.py init and rss_web_admin.py check before enabling timers.


### Subscription update and pause safety

If one pending packet contains several sources and an operator removes one
source, cancel the unsent batch and release only its unsent dedup claims,
so articles from sources still selected can be queued again. Sent or
ambiguous batches remain immutable. Pausing a group similarly cancels any
unsent packet; re-enabling the group uses a fresh enabled_since timestamp to
avoid stale or duplicate delivery. Archiving retains the group's sent
history, with no physical deletion. Regression tests:
tests/test_groups.py and tests/test_web_admin.py.


### Mac 一键打开管理页面（2026-10-22 已真实验证）

管理台只监听云服务器 \`127.0.0.1:8765\`，远程浏览器不能直接访问服务器地址。这不是服务故障。项目在 Mac 上提供可持续运行的 LaunchAgent SSH 隧道：

\`\`\`bash
cd /Users/chenglong/github/rss-feishu-intelligence
python3 scripts/mac_admin_access.py install
python3 scripts/mac_admin_access.py status
python3 scripts/mac_admin_access.py open
\`\`\`

LaunchAgent ID 为 \`com.haomiao.rss-feishu-admin-tunnel\`，安装文件为 \`~/Library/LaunchAgents/com.haomiao.rss-feishu-admin-tunnel.plist\`（0600）。它通过已有 SSH 配置 \`aliyunVBS\` 将 \`127.0.0.1:8765\` 转发到服务器的同一回环端口。开机登录后自动启动，断开自动重试，不需要每次手工保持一个 SSH 命令终端。该隧道独立于已有 RSSHub 1200 端口隧道。

2026-10-22 验收：Mac 端 \`/login\` 返回 HTTP 200，\`/\` 返回 HTTP 303 跳转登录，\`/health\` 返回 HTTP 200，\`open -a Google Chrome\` 成功打开登录地址。管理员需用自己通过受信任 SSH 会话查看的服务端私有 token 完成登录；脚本不读取、不复制、不打印 token。

如果换 Mac 或迁移 SSH alias，应先检查 \`ssh -G aliyunVBS\` 与目标主机密钥，不能使用 \`StrictHostKeyChecking=no\` 绕过授权。卸载隧道：\`python3 scripts/mac_admin_access.py uninstall\`，不会停止云端 RSS 推送、网页服务或既有 RSSHub 端口隧道。

### Mac 浏览器登录报「请求来源无效」的修复与验证（2026-10-22）

原 Handler.same_origin 要求 Origin 字符串必须与 HTTP Host 原样完全相等；
Mac 上的 localhost/127.0.0.1 地址别名在某些浏览器/代理规范化情况下
不完全一致，登录 POST 会返回 403「请求来源无效」。

当前管理程序仅接受：
- Host 必须是绑定端口对应的 127.0.0.1:<port> 或 localhost:<port>。
- 如果存在 Origin，必须是 http 协议、相同绑定端口、
  主机为 127.0.0.1 或 localhost，且无额外路径/参数/账号信息。
- 其他来源（公网域名、错误端口、Origin: null）继续拒绝。
- 管理员口令/HttpOnly Session/所有写操作 CSRF 检查不变。

已在 Mac 本地通过 SSH 隧道执行带有效服务端凭据的完整登录模拟：
POST /login -> HTTP 303，带 cookie 的 GET /groups -> HTTP 200，
并验证 localhost 别名同样成功；凭据未打印或写入 Git。
如果仍报错，直接从浏览器地址栏打开
http://127.0.0.1:8765/login 并重新加载，不要用网页嵌入框或别的网站
跨来源提交表单。用 python3 scripts/mac_admin_access.py status 确认隧道。

### 2026-10-22 再次修复 Chrome 登录「请求来源无效」

前一版本仅允许 Origin 严格等于本机域名别名，在用户浏览器环境仍出现
“请求来源无效”。新的校验职责是：
- HTTP Host 必须严格匹配正在监听的本机 127.0.0.1:<port> 或
  localhost:<port>，服务仅绑定本机网卡，不新增公网入口；
- POST /login 必须通过服务器私有管理员访问口令、登录失败速率限制；
- 所有其他写入请求仍必须持有有效的 HttpOnly、SameSite Session 和
  与 Session HMAC 绑定的 CSRF hidden form token；无 token / 错 token 403；
- 不再将不可靠的浏览器 Origin 字符串本身作为合法本机请求的通行条件。

验收：Mac 本机真实 Chrome 对线上 /login 提交错误的测试口令时，进入
“凭据错误”分支（而非“请求来源无效”）；独立临时 Web 服务器上使用
合成管理员口令执行 Chrome 实际表单登录、访问分组页成功，并确认
错误 CSRF 请求仍 403。测试程序没有读取、打印、复制生产口令。
此次变更只更新 /opt/rss-feishu-push/rss_web_admin.py，不修改飞书推送、
来源数据或其他业务服务。
