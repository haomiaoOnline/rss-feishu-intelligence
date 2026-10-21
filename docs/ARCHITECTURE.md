# 组件、状态与数据边界

## 数据流

X 作者公开帖子 -> RSSHub /twitter/user/<handle> -> 本机HTTP 127.0.0.1:1200 -> Python Collector（46账号、3个抓取worker、每源15秒限时） -> SQLite -> 飞书独立自定义机器人（RSS情报关键词） -> RSS专属话题群。

直连困难时增加海外网络出口，数据路径是国内 RSSHub -> 限制在本地私有Docker桥上的 SOCKS5 服务 -> SSH 隧道 -> 获授权海外服务器 -> X。RSSHub 1200端口仅回环访问，绝不将SOCKS代理端口暴露公网。

## 系统组件

- src/rss_feishu_push.py 使用 Python 标准库，无外部 pip 依赖，要求 Python3.9+。
- config/feeds.json 保存公开handle、作者显示名和原有分组；不存私密读者名单。
- SQLite schema 创建于源码 CREATE_SQL。state/push.sqlite3 在服务器路径，包含 feeds/articles/batches/meta 4张表。真实DB不得进Git。
- systemd timer 每小时执行一次 service（Type=oneshot、420秒上限、160M内存限制、CPU配额），故障后下一小时再运行。
- card renderer 使用 lark_md；按固定正则关键词归类，编号、作者与时间独立一行、可点击原文第二行；不生成AI摘要或重要度。
- Webhook 只读取 /etc/rss-feishu-push.env 里的 FEISHU_WEBHOOK_URL_RSS。代码拒绝错误格式；不会读取/回退日报 Webhook。

## 首次基线与幂等

每个来源第一次成功时记录 initialized_at，把当时抓取到的文章标记 delivered=1，不进入发送队列。未成功的源不改变 initialized 状态，下次首次成功依旧建立历史基线。

以后每条文章用 handle 与稳定帖子 URL/GUID 派生 SHA-256 ID，SQLite INSERT OR IGNORE 去重；超过72小时的文章和未来异常时间戳视为过期，不推送。发布时间可能来自第三方 RSS，仍需要抽检是否新鲜。

## Batch/Outbox 状态

待发文章 delivered=0；封装一个最多12篇的批次后 articles.batch_id 关联 batches。

- pending 表示待送。
- sending 表示正在调用飞书。
- retry 表示飞书明确拒收后退避，达到 next_try 才重试。
- sent 表示飞书 API 明确返回成功，随后标记 articles.delivered=1。
- ambiguous 表示网络超时、崩溃或结果不明，不能自动重发，需要先在飞书客户端确认；不会阻塞其他新批次。

Webhook请求带关键词 RSS情报，回执成功表示飞书平台接口接受，仍须用户验证客户端显示。系统每次成功发送间隔至少55分钟（MIN_SEND_SECONDS）。没有新帖不推空卡片。

## 已知取舍

- 此设计偏向避免旧帖刷屏和避免重复，某次发送结果未知时优先人工核查，不追求 exactly-once 不可证明语义。
- 上游 X 登录态、接口/代理健康会变化；定时器 active 不等于抓取内容永远有效。
- 单卡最多12篇、归类规则固定；分类命中可能不准或出现其他动态，不能宣称AI理解文章。
- Feedbro 仍可作为本机阅读器试验，但整个后端不需要 Chrome 开着。
