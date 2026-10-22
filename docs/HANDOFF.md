# 当前工作实例交接

本仓库由2026-10-21已经运行并得到用户飞书客户端 DONE 验收的系统抽取。其他 Agent 接手时先检查实际线上状态，不要用此文档中的历史数字冒充即时运行结果。

## 当时验证过的事实

- 自建 RSSHub 经海外 SSH 出口尝试抓取46位X中文博主。
- 2026-10-20 完整验收曾抓到46/46成功、849条RSS记录；这是一次测试结果，不承诺每轮都成功。
- 2026-10-21 RSS→飞书专用Webhook配置与工作日报WebHook分离，群机器人关键词 RSS情报。用户已确认测试和生产卡片可见。
- SQLite 首次初始化分别保留了旧帖、收取新增、幂等队列。发送结果未知时保留 ambiguous 状态，不盲重发。
- Card v4 最终视觉效果获得 DONE。主题聚合，单篇条目编号、作者+时间与可点击的原文独立两行，紧凑，无箭头，不加重要度/AI摘要。
- 云端使用 systemd 每小时执行，无需用户Mac或Chrome在线。Feedbro Chrome扩展后台曾有兼容性问题，不能将其说成已经完全修复。
- 2026-10-21 编写本Git版本时，线上来源和队列仍在变化。本地移植源码是原线上正式程序的可配置路径版本，不应未经上线流程直接覆盖生产。

## 目录映射

- Git 源码 src/rss_feishu_push.py -> 目标服务 /opt/rss-feishu-push/push.py
- Git 公开账号配置 config/feeds.json -> /opt/rss-feishu-push/feeds.json
- Git systemd 模板 -> /etc/systemd/system/rss-feishu-push.{service,timer}
- Git RSSHub Docker 模板 -> 干净主机的 /opt/rsshub-intelligence/compose.yaml
- 运行专属Webhook -> 目标机 /etc/rss-feishu-push.env（保密，禁止提交）
- X 授权 -> 目标机 /etc/rsshub-intelligence.env 或已有实例自己的 root-only 配置（保密，禁止提交）
- SQLite -> /opt/rss-feishu-push/state/push.sqlite3（禁止提交）
- 海外出口 SSH key 与 known_hosts -> 服务器专用系统账号下的私有文件（禁止提交）
- 公众号/小红书推广稿和截图在 内容生成 的另一个目录，未搬进工程Git，以免夹带隐私截图与大文件。

## 新的 Agent 下一步

只需要在用户明确授权新主机/新机器人部署时按 AGENTS.md 与 docs/DEPLOYMENT.md 从零执行。若目的是升级已在线的现有实例，先用 read-only 观测对比版本、复制SQLite在线备份、给出升级计划，获得批准后再操作。不要在现有机器上重复启动一个与当前容器同名、同端口的RSSHub。

## 需长期观察

- X cookie过期/单账号503/响应不完整，不能用简单健康检查掩盖内容过期。
- 如果新发现文章持续超过每小时12篇，待发队列可能增长，用户可决定调整过滤和限额；不得擅自扩大Webhook发送频率。
- 日报群与RSS情报话题群必须保持独立。
- 本 Git仓库初次创建只建立本地提交，不做任何远端发布。

## 代码来源核对

第一次纳入仓库前，本地源 push.py 与 aliyunVBS 服务端 sha256 对照完全一致（65625663ff502193be3fca459d693f486ef4b1942b0655db03e334b8a99b6916）。之后在 Git 内做了环境变量可移植化、严格的离线配置回归与部署脚本；这份改版尚未替换在线运行版本。

## 统一订阅 v2 项目扩展（2026-10-22）

清单从原46个X用户增加Feedbro导出的20个普通RSS，合计66源。安全原子同步、源健康状态、OPML导入导出、RSS/Atom抓取属于v2。不能从文档推断现网已经部署：以实际systemd状态、源数量与Git版本验证为准。
