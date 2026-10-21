# X 出口受限环境的 SSH SOCKS5 代理安装

如 RSSHub 所在主机直连 X 不可用，经用户授权后可通过专用 SSH 登录身份转发到海外服务器。本项目原来的正式部署使用双服务器 + Docker 私有网桥 + SSH -D，属于已有服务器场景；这份文档没有硬编码任何真实服务器IP、用户名、SSH key或凭据。

## 必须由用户提供或批准

1. 用户合法控制或获授权的海外出口服务器，目标主机的 SSH alias 以及两端防火墙修改权限。
2. 专用 SSH key 的生成与安装，专用账号的 authorized_keys，准确远端 SSH 主机密钥的带外验真。私钥不可进入聊天/Git。
3. 远端出站 443/80 策略、专用 SOCKS 转发的允许范围；必须遵守账号服务条款与本地政策。
4. 如果会变更已有 sshd_config 或调整 Docker 网桥 CIDR，操作前要由用户批准，不能擅自影响现网。

## 先确定网桥不冲突

在 RSSHub 宿主机检查 docker network ls、docker network inspect、ip addr、路由表；选一个和现有网段没有冲突的私有 RFC1918 子网。下面 172.19.0.0/24 仅是示例，不是服务器实际公网地址。

    docker network create --driver bridge       --subnet 172.19.0.0/24       --gateway 172.19.0.1       rsshub-egress-net

如果 rsshub-egress-net 已存在，则观察已有设置，严禁删除重建以破坏其他容器。

## 安装专用 SSH SOCKS 转发

最小权限建议使用一个独立的本地系统账号与一对专用 SSH key，远端账号没有普通交互 shell，只允许需要的端口转发。服务模板位于 infra/egress/rsshub-us-egress.service.template。

- 在代理宿主机的系统账号的受限家目录中放置私钥和用户核实过的 known_hosts，文件只允许账号本人读取；不要将它们放入工作区。
- 按主机事实将模板里的 <PROXY_SERVICE_USER>、<PRIVATE_KEY_ABSOLUTE_PATH>、<REMOTE_SSH_ALIAS>、<CONTAINER_BRIDGE_GATEWAY_IP> 替换为实际值。生成后的文件存放在 /etc/systemd/system/rsshub-us-egress.service，不回写 Git。
- ssh -D 的 127.0.0.1:10808 只供本机诊断，私网网桥网关:10809 只允许专用 RSSHub 容器访问。检查防火墙确保 10809 不从公网进入或从其他 Docker 网络任意进入。
- 不要在远端放宽 AllowTcpForwarding 到所有用户，不应提供普通 shell 登录权限。远端安全约束可以用 Match User、AllowTcpForwarding local、禁止PTY等措施；改 SSHD 需确认不会断掉现有管理员会话。

    sudo systemctl daemon-reload
    sudo systemctl enable --now rsshub-us-egress.service
    sudo systemctl is-active rsshub-us-egress.service

## 验证

    curl --socks5-hostname 127.0.0.1:10808       --connect-timeout 6 --max-time 25 -I https://x.com
    ss -ltn | grep 10809
    docker network inspect rsshub-egress-net

在目标服务器私有 /etc/rsshub-intelligence.env 内设置 RSS_PROXY_URI=socks5h://172.19.0.1:10809（示例，要按实际Docker网桥地址替换）。使用 proxy compose 部署 RSSHub。Docker 里 socks5h 必须让域名解析在代理出口完成。

RSSHub route 返回 200 后，还必须检查近期 pubDate，原先未认证的 X 路由曾返回过时的 2025 内容。X auth 可能到期，需要用户刷新。不要用 curl -v 把认证 Cookie 发到日志。

## 运维与故障

- systemctl is-active rsshub-us-egress.service、journalctl -u rsshub-us-egress.service。
- 如果 X 站点通过代理能访问而 RSSHub 返回 503，请区分 Cookie过期、账号限流、RSSHub路由变化和某单源暂时异常。
- 不要盲目反复重建海外代理、删除现有容器或开放公网端口。先读 docs/OPERATIONS.md。
- 如果隧道掉线则 systemd 会按模板尝试重新连接；心跳配置不是成功抓取的证明，仍需定时检查RSS内容。

## 拆除

这属于影响真实服务器进程的操作，只有用户授权后才执行。

    sudo systemctl disable --now rsshub-us-egress.service

删除网桥必须确认没有其他容器在用；避免影响系统其他业务。删除远端专用账号/密钥也须独立确认。
