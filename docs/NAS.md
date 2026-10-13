# NAS 读写

把 NAS 的共享目录登记为一个 SSH 工作区，已有 Agent 就可以通过同一个 Environment 入口读写 NAS。工具在 NAS 上执行，操作的文件与 SMB、网页文件管理器使用的共享目录一致。中心 Host 直接连接 NAS，使用 Agent 的电脑离线后，这条连接仍可使用。

NAS 复用固定版本的 Pi `read`、`write`、`edit`、`bash`、`grep`、`find`、`ls`，沿用普通工作区的工具参数与结果。共享目录决定相对路径的默认位置，实际文件权限由 NAS 登录用户决定。

## 准备连接

NAS 需要 Python 3.11+、Node.js 22.19.0+、npm 和可用的 SSH 密钥登录。ARM64 NAS 使用 ARM64 运行时。首次工具部署需要访问 npm；预先准备 ripgrep 和 fd 可避免第一次搜索时下载依赖。

Host 可以直接访问 NAS 时，使用已有 SSH 别名即可。NAS 通过另一台服务器的 VPN 联网时，在 Host 的 `~/.ssh/config` 中配置 OpenSSH 中继：

```sshconfig
Host nas-relay
  HostName relay.example.com
  User relay-user
  IdentityFile ~/.ssh/nas-relay
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking yes

Host nas
  HostName 10.8.0.20
  User pi
  ProxyJump nas-relay
  IdentityFile ~/.ssh/nas-user
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking yes
```

先核对 NAS 和中继的主机公钥，登记到 Host 的 `known_hosts`。NAS 用户与中继分别使用专用密钥；中继密钥可以只允许转发 NAS 的 SSH 端口，拒绝执行其他命令。私钥保留在 Host。

在 Host 验证完整路径：

```bash
ssh nas 'hostname; node --version; python3 --version; test -d /srv/nas/data'
```

中继认证较慢时，可以在 Host 上用常驻 SSH 转发复用这条连接。下面的用户服务仅监听 Host 的 loopback；将示例 VPN 地址改为 NAS 的实际地址：

```ini
[Unit]
Description=Private NAS SSH forwarding
After=network-online.target

[Service]
ExecStart=/usr/bin/ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -L 127.0.0.1:31922:10.8.0.20:22 nas-relay
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

将服务保存为 `~/.config/systemd/user/dsh-nas-relay.service`，运行 `systemctl --user daemon-reload` 和 `systemctl --user enable --now dsh-nas-relay.service`。随后把 `nas` 别名的 `HostName` 改为 `127.0.0.1`、`Port` 改为 `31922`，移除该别名的 `ProxyJump`，并在下一节的目标配置中设置 `port: 31922`。将已经核对的 NAS 公钥登记为 `[127.0.0.1]:31922` 的主机密钥。

## 登记共享目录

在现有目标配置的 `targets` 中加入 `nas`，保留其他设备的配置：

```json
{
  "targets": {
    "nas": {
      "host": "nas",
      "label": "NAS",
      "workspace": "/srv/nas/data",
      "python": "python3",
      "node": "node",
      "npm": "npm"
    }
  }
}
```

已有 Host 修改其私有运行时中的 `dsh-targets.json`。Host 在资源发现与工作区选择时重新读取目标配置。Node 或 npm 安装在用户目录时，配置它们的绝对路径；npm 可以使用 `npm-cli.js` 路径。NAS 的数据盘应正确挂载，并让未挂载时的底层目录保持不可写。

## 使用

使用原有 Environment 接入文件即可。先调用 `action=list`，确认机器 `nas`；调用 `action=workspace`，传入 `machine=nas` 和共享目录，取得稳定的目标编号。后续调用 `action=context` 获取该目标的实际工具参数，再使用目标编号进行读写。

```json
{
  "action": "workspace",
  "machine": "nas",
  "workspace": "/srv/nas/data"
}
```

可以直接给 Agent 一个任务：

> 使用 Environment 的 NAS 工作区，在 agent-example/hello.txt 写入一段文字，读回核对，再列出这个目录。保持当前会话的默认工作区。

在 DSH Web 中选择 NAS 和共享目录，也可以创建 NAS 会话或切换已有会话的执行位置。通过 `environment` 显式调用 NAS 工具时，当前会话的默认工作区保持原来的位置。

验证时使用单独的临时目录，通过 Environment 写入、编辑并读回文件，再从 SMB 或 NAS SSH 独立核对内容。完成后删除自己的测试目录。NAS 离线会返回该目标的错误；响应丢失后先检查文件的实际状态，再决定是否重复写入。
