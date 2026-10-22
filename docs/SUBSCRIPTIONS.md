# 统一订阅管理：Git、Feedbro、云端 RSS

## 单一配置源

config/feeds.json 是唯一配置源。已包括原46个X博主，以及从用户已有Feedbro OPML合并的20个普通HTTPS RSS，合计66源。Feedbro本地更改不会自动同步到服务器，须手工从Feedbro导出OPML、合并至Git、提交并push；随后服务器定时从Git main拉取单个配置文件并校验更新。

## 常用操作（在仓库根目录）

校验与查看：

    python3 scripts/subscriptions.py validate
    python3 scripts/subscriptions.py list

Feedbro OPML合并，默认只预演：

    python3 scripts/subscriptions.py import-opml --opml ~/Downloads/subscriptions.opml
    python3 scripts/subscriptions.py import-opml --opml ~/Downloads/subscriptions.opml --apply

要全量替换，用 --mode replace --allow-remove；不可未经确认盲目删除所有源。手动编辑 config/feeds.json 后也要 validate。

生成供Feedbro手动导入的OPML：

    python3 scripts/subscriptions.py export-opml --apply

输出 exports/feedbro-subscriptions.opml（忽略版本管理）。其中X订阅默认使用本地RSSHub 127.0.0.1:1200，需要SSH端口转发。可指定 --rsshub-base HTTPS_URL。Feedbro不支持的RSS源不要假称导入成功。

## JSON支持

X博主（兼容旧数据库）：

    {"group":"AI Agent","handle":"dotey","name":"宝玉"}

普通RSS/Atom：

    {"type":"rss","group":"AI技术博客","name":"Example News","url":"https://www.example.org/feed.xml"}

仅支持无密钥、无登录、公开HTTPS域名（默认443端口），拒绝私有IP、内网、用户名密码、任意http和危险跳转。普通RSS身份是规范化URL派生的稳定SHA256，编辑URL等于替换来源，会重新初始化历史基线。X身份依然以原handle计算，不会改变旧SQLite已发记录。

上限72源，这是现有3工作线程×每源最多15秒，与系统420秒超时的安全限额，不代表无限扩容。

## Git提交后服务器自动同步

    make test
    python3 scripts/scan_secrets.py
    git diff config/feeds.json
    git add config/feeds.json
    git commit -m "chore: update RSS feed list"
    git push origin main

云端 rss-feeds-sync.timer 在每小时约40分只fetch Git远端main的配置文件，先验证，再获取共享flock锁、写入临时文件、fsync、原子替换并保留之前的feeds备份；永不自动执行拉取的新代码或清空SQLite。删除数量过多会拒绝。本地或云端可先用 --dry-run（默认）查看变化。

源状态检查：

    sudo python3 -B /opt/rss-feishu-push/push.py source-health
    sudo python3 -B /opt/rss-feishu-push/push.py status
    systemctl list-timers --all rss-feeds-sync.timer
    journalctl -u rss-feeds-sync.service -n 30 --no-pager

源已抓取成功但长期不发文章不一定坏；区分最近来源错误、连续失败和最近文章时间。GitHub不可达时同步失败而不是覆盖旧配置。线上保留既有已发记录、37+待发历史队列，新增订阅首次成功只建立历史基线，不倒灌旧文章。备份位于服务器 /opt/rss-feishu-push/.feed-backups。

## Aliyun GitHub connectivity (production implementation 2026-10-22)

The sync timer uses the *public GitHub REST Contents API* to fetch exactly
\`main:config/feeds.json\` over HTTPS. AliyunVBS could access api.github.com
but raw.githubusercontent.com TLS failed and full git clone hung. The sync
service therefore uses \`sync_feeds.py --api --apply --allow-removals\`.
It **does not execute downloaded code**; it only validates and atomically
replaces the JSON file under an exclusive lock. For read-only preview:

    sudo python3 -B /opt/rss-feishu-push/sync_feeds.py --api

Expected unchanged output: current=66, proposed=66, add=0, remove=0,
changed=false, dry_run=true. Use the systemd timer for ongoing checks.
GitHub REST rate limits and network outages leave existing feeds.json intact.
