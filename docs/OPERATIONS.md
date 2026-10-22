# 日常运维、故障处理与回滚

本文件适用于已获授权服务器的运维 Agent。先查状态，随后按故障类型最小化处理。所有不确定发送结果必须由用户在飞书客户端确认，不盲重发。

## 日常观察（安全只读）

    sudo bash scripts/healthcheck.sh
    systemctl list-timers --all rss-feishu-push.timer
    systemctl show rss-feishu-push.service -p Result -p ExecMainStatus -p ActiveState
    sudo journalctl -u rss-feishu-push.service -n 60 --no-pager
    sudo python3 -B /opt/rss-feishu-push/push.py status
    curl -fsS http://127.0.0.1:1200/healthz

正常情况下会看到 timer active， service oneshot 在两次执行之间 inactive，最近一次 Result=success，数据库 articles/batches 状态连续增加。注意 Result=success 代表脚本按预期退出，不等于所有46个来源都成功。看 rss_polled.source_failed。

## 来源 503/超时或消息滞后

1. 看失败账号列表的类别/次数，不要用高频重试放大 X 限流。
2. 检查 RSSHub /healthz 与海外 SOCKS 出口状态。RSSHub 正常但来源 503，关注 X token、账户登录状态、接口路由是否改变。
3. X auth 非空但日志可能仍提示 No valid Twitter token，需要用户安全刷新；不要读取其真实值。
4. 对近期几条 RSS 原帖核对 pubDate，防止 HTTP 200 返回旧文章。
5. 失败源会在下一小时重新尝试；首次成功时建立自己的历史基线，旧帖不会补发。

## 发送失败与去重状态

- batches.state=retry 表示明确拒收后的退避，允许到期重试。
- batches.state=ambiguous 代表发送结果不明。人工核实飞书群后才允许对该批数据实施进一步操作；绝不能一键将其全改为pending。
- pending_articles 不是零可能很正常。每小时最多12篇，在高峰期可能积压，不保证零延迟。
- 飞书群消息有 RSS情报 关键词，绑定的是独立 Webhook；不会自动回退到日报地址。
- 如果系统没有新增情报，no_new_articles_to_send 是正常。
- 如果主题分类混杂，维护固定关键词规则并用测试预览；不可偷偷增加重要度或AI摘要。

## 定时任务超时

原先曾有一次超过7分钟的请求超时。正式v4采用3个抓取worker、每源15秒超时，service TimeoutStartSec=420。若再次发生，核查 RSSHub 代理与请求时延，而不是无条件增大并发使上游X更易限流。

## 安全备份 SQLite

读取新业务库的备份最好调用 SQLite online backup，避免直接复制一个正在写入的WAL数据库产生不完整状态。备份文件权限0600且放在 Git 工作树之外。例如在服务器上已确认 timer 不在执行时：

    sudo python3 - <<'PY'
    import sqlite3, pathlib, datetime
    a=pathlib.Path('/opt/rss-feishu-push/state/push.sqlite3')
    t=pathlib.Path('/opt/rss-feishu-push/state/backup-'+datetime.datetime.now().strftime('%Y%m%d%H%M')+'.sqlite3')
    source=sqlite3.connect(a);dest=sqlite3.connect(t)
    source.backup(dest);dest.close();source.close()
    t.chmod(0o600)
    print('BACKUP_CREATED','state directory')
    PY

这里仅打印备份事件，不在日志中转储数据库内容。

## 暂停、恢复、升级

    sudo systemctl disable --now rss-feishu-push.timer
    sudo systemctl is-active rss-feishu-push.service

等 service 退出，做好备份，再升级程序。检查下载的 repo commit 与实际备份一致，升级后调用 Python self-test 和 config validate。不要直接在运行过程中覆盖服务脚本。

恢复前先阅读部署文档，并确保 pending队列和 ambiguous批次可接受；升级场景的 pending 不一定为0，不能套用首次激活脚本自动清库。若确需恢复，请由授权Agent执行 systemctl enable --now rss-feishu-push.timer，观察下一次执行。

## 回滚

只暂停本项目的 timer；用已经备份的源码还原 /opt/rss-feishu-push/push.py 并确认权限；不要覆盖SQLite或覆盖别的日报业务。若恢复数据库需明确的迁移/丢失评估与用户批准。

## 交接证据

交付 git commit SHA、程序实际版本、每次系统执行返回码、最近来源成功数/失败分类、待发文章/批次状态，以及用户客户端实际收到的独立RSS群消息。脱敏后归档，绝不粘贴Webhook/Token/私钥。

## 统一订阅状态（v2）

排查订阅请执行 source-health，检查同步定时器 rss-feeds-sync.timer 和相关service journalctl。同步仅影响 feeds.json，不清库、不重放已发历史。新源首次成功时建立历史基线。详细见 docs/SUBSCRIPTIONS.md。
