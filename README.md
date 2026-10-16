# Container Cache Guard

[![CI](https://github.com/kzerghost/container-cache-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/kzerghost/container-cache-guard/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kzerghost/container-cache-guard)](https://github.com/kzerghost/container-cache-guard/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

On-demand inspection and backup-first repair for a specific Synology Container Manager name-cache mismatch.

用于检查并修复群晖 Container Manager 的一种名称缓存错乱：Docker 中的真实容器正常，群晖仍保留 `旧ID前12位_容器名`，导致面板提示“容器不存在”或“容器 undefined 不存在”。

工具提供中文网页，手动扫描、逐项确认、先备份后修复。启动或打开网页不会自动扫描、修复，也不会启动、停止或重建业务容器。

## 适用范围

| 项目 | 当前支持 |
| --- | --- |
| Container Manager | **24.0.2-1543**；其他版本只检查，不开放修复 |
| 镜像架构 | `linux/amd64` |
| 修复模式 | 完整容器 ID 一致，缓存名为已识别的旧前缀格式，目标文件不存在 |
| 稳定观察 | 三次采样、至少 60 秒；存在读取错误时不提供修复 |

这是针对特定缓存结构的独立社区工具，不是 Synology 官方产品。它不能解决所有面板错误、容器启动失败或容器内部程序缺失。

## 快速部署

公开镜像：`ghcr.io/kzerghost/container-cache-guard:0.2.2`。支持匿名下载，**无需 GitHub 或 Docker Hub 登录**；`latest` 指向最近发布的稳定版本。建议日常部署固定版本号。

1. 从 [Releases](https://github.com/kzerghost/container-cache-guard/releases/latest) 下载 `compose.yaml` 和 `default.env.example`，放入 NAS 的专用项目目录，将后者改名为 `.env`。从源码部署则复制 `.env.example` 为 `.env`。
2. 编辑 `.env`，填写实际 NAS 内网 IP、端口和密码文件的绝对路径。示例地址仅为占位值。
3. 创建至少 20 字符的独立随机密码，保存为本地文件；密码不要写进 YAML、Git 或 issue。可以在 NAS 的管理员终端执行：

   ```sh
   # 在专用项目目录执行；已有密码文件时保留原内容。
   if [ ! -e ui-password.txt ]; then
     (umask 077; openssl rand -hex 24 > ui-password.txt)
   fi
   chown root:root ui-password.txt
   chmod 600 ui-password.txt
   ```

   密码文件及项目目录应仅允许管理员修改。`.env` 中的 `GUARD_PASSWORD_FILE` 必须指向该文件。

4. 在项目目录运行，或在 Container Manager 中导入 Compose 项目并设置同名变量：

   ```sh
   docker compose config --quiet
   docker compose up -d guard
   ```

5. 打开 `http://你的NAS内网IP:18764/`，用户名 `admin`，密码为上述文件内容。请通过 **Compose 项目** 部署；在“映像”页面直接点运行不会自动带上所需的挂载和密码文件。

### 扫描和修复

等映像更新、项目构建和容器重建结束后，点击“开始扫描”，等待约 60 秒。只有满足条件的“持续错名”项才出现“备份并修复”。核对后逐项执行，完成后刷新群晖面板。

备份和事务记录保存在固定 Docker 卷 `container-cache-guard_backups`。用完可以停止工具，保留备份卷。若页面提示未完成操作，使用“检查并恢复”；发生外部变化时工具会拒绝覆盖，需人工检查备份。不要通过删除备份卷来消除提示。

### 更新

修改 `.env` 中的 `GUARD_IMAGE` 为目标版本，保留原密码和备份卷，然后运行：

```sh
docker compose pull guard
docker compose up -d guard
```

发布新镜像不会自动更新 NAS。已有本地镜像部署也可以按上述方式迁移。

## 配置

| Compose 变量 | 用途 |
| --- | --- |
| `GUARD_IMAGE` | 镜像地址；默认固定为 `0.2.2` |
| `NAS_IP` | 必填，绑定服务的 NAS 内网 IP |
| `UI_PORT` | 网页端口，默认 `18764` |
| `GUARD_PASSWORD_FILE` | 必填，密码文件在 NAS 上的绝对路径 |

Compose 会设置网页访问白名单 `ALLOWED_HOSTS=NAS_IP:UI_PORT`。自定义部署必须显式设置该白名单，不支持通配符；多个实际访问地址可以用逗号分隔。

镜像仅包含程序文件。运行时通过挂载读取 Docker socket、群晖套件信息、缓存和密码，备份写入独立卷；不会把这些数据上传到 GitHub 或镜像仓库。

## 权限与安全

这是高权限管理工具：Docker socket 即使挂载为只读，也不代表 Docker API 只读。程序仅请求固定的容器列表 GET 接口，但进程被控制后仍可能影响宿主机。缓存目录需要写权限，修复只修改匹配记录的文件名及 JSON `name`，保留其他字段并先保存原始字节备份。

默认配置包含密码认证、Host/CSRF 校验、只读根文件系统、移除 capabilities、内存和日志限制，以及指定 NAS 内网 IP 绑定。HTTP Basic 不加密，只适合可信内网按需使用；远程访问应配置 HTTPS 和正确的访问白名单，不要直接暴露公网。

群晖自己的写入程序不使用本工具的锁。操作中会多次检查 ID、哈希、版本和目标冲突，但仍无法完全消除外部并发写入的竞态。操作前请等其他更新任务结束。

安全报告方式见 [SECURITY.md](SECURITY.md)。提交普通问题时请使用脱敏信息，勿上传真实 `.config`、密码文件或完整备份。

## 开发与发布

Python 3.12，运行时仅使用标准库：

```sh
python -m unittest discover -v
```

测试使用合成配置，覆盖稳定观察、冲突拒绝、备份、异常恢复和网页访问控制。真实 NAS 的兼容性仍需单独验证。

GitHub Actions 在主分支和 PR 运行测试并校验 Compose。只有与 `VERSION` 一致的 `vX.Y.Z` 标签通过测试后才构建 `linux/amd64` 镜像，发布固定版本及 `latest` 标签，并创建带部署附件的 GitHub Release。发布使用工作流的 `GITHUB_TOKEN`，不会连接或更新 NAS。

`.gitignore` 排除本地密码、配置、缓存和备份；`.dockerignore` 仅允许四个运行文件及许可证进入构建上下文。变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)。
