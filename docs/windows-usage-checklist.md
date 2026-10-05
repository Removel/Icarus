# Icarus Windows 使用清单

适用电脑：当前 Windows / PowerShell 环境。仓库位置：`F:\project\Icarus`。
核对日期：2026-10-02。

## 每次使用先看这里

- [ ] 启动 Docker Desktop，等待引擎就绪。
- [ ] 启动 Mem0 和 OpenKB。
- [ ] 启动 Gateway，并保留它所在的终端。
- [ ] 选择 TUI 或 WebUI，开始对话。
- [ ] 用完后按需退出界面、停止 Gateway 和容器。

Python、Node.js、pnpm、三个 Python 虚拟环境、WebUI 依赖和 Docker 镜像已经安装。正常使用不需要重新安装。

这份清单使用当前 Windows 原生环境，命令在 **PowerShell** 中执行。根 README 中的 `./bin/icarus`、`icarus start` 是 Bash 入口；当前 PowerShell 的 PATH 中没有 `icarus`，请使用下面的命令。

**服务已经在运行时，跳过对应启动步骤。** 本次助手已启动 Gateway 和 WebUI 后台进程；先按第 5 节检查，正常就直接使用。电脑重启后再按顺序启动。

## 1. 启动 Docker Desktop

从开始菜单打开 Docker Desktop，或者在 PowerShell 中执行：

```powershell
Start-Process -FilePath "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe" -WindowStyle Hidden
```

等待 Docker Desktop 显示引擎运行，再检查：

```powershell
docker info --format '{{.ServerVersion}}'
```

能输出服务器版本号即可继续。出现 `failed to connect to the docker API`，通常表示引擎尚未启动完成。

## 2. 启动记忆服务和知识库

在 PowerShell 中执行：

```powershell
Set-Location F:\project\Icarus
$env:PYTHONUTF8 = '1'
python apps/mem0/scripts/icarus_compose.py up -d --no-build
python apps/openkb/scripts/icarus_compose.py up -d --no-build
```

这两个命令读取仓库根 `.env`，使用已有镜像，在后台运行服务。命令执行完后可以继续使用这个终端。已经启动的容器通常会被复用。

容器启动后还需要一点初始化时间；第 5 节可以检查接口是否就绪。

## 3. 启动 Gateway：终端 A

先检查是否已经运行：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

如果显示 `status: ready`，直接进入第 4 节。若连接失败，再在 **终端 A** 执行：

```powershell
Set-Location F:\project\Icarus
$env:PYTHONUTF8 = '1'
$env:PATH = 'D:\Git\bin;' + $env:PATH
.\apps\gateway\.venv\Scripts\python.exe -m apps.gateway.src.main --host 127.0.0.1 --port 8765
```

看到 `Application startup complete` 和 `Uvicorn running on http://127.0.0.1:8765` 后，保留这个终端。

Git Bash 路径供 Agent 的 Bash 工具使用。Agent 运行在 Gateway 内，不需要另外启动 Agent 进程。

## 4A. 使用终端 TUI：终端 B

另开一个 PowerShell，在 **终端 B** 执行：

```powershell
Set-Location F:\project\Icarus
$env:PYTHONUTF8 = '1'
.\apps\tui\.venv\Scripts\python.exe -m apps.tui.src.main --session-id my-session
```

进入界面后直接输入任务。下次使用相同工作区和 `my-session`，可以恢复这段对话。也可以把 `my-session` 改成别的会话 ID，例如 `daily` 或 `project-work`。

| 操作 | 方法 |
| --- | --- |
| 发送消息 | `Enter` |
| 换行 | `Ctrl+J`；支持的终端也可用 `Shift+Enter` |
| 新建对话 | `/clear` |
| 选择历史会话 | `/resume` |
| 退出 TUI | `/exit` |
| 取消当前任务 | 输入框和待发送队列都为空时按 `Ctrl+C` |

`/clear` 和 `/resume` 需要当前会话空闲。`Ctrl+C` 会优先清空草稿，其次撤回最新排队消息，再取消任务；完全空闲时会退出。

退出 TUI 后，Gateway 和 Docker 服务仍然运行。

### 在其他项目目录使用 TUI

TUI 启动时所在的目录就是工作区。切换到其他项目时，需要让 Python 仍能找到 Icarus 源码。下面的项目路径请替换成真实存在的目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = 'F:\project\Icarus;' + $env:PYTHONPATH
Set-Location 'D:\你的项目目录'
& 'F:\project\Icarus\apps\tui\.venv\Scripts\python.exe' -m apps.tui.src.main --session-id project-work
```

不同工作区的会话彼此隔离；恢复时应使用原工作区和原 Session ID。

## 4B. 使用浏览器 WebUI：终端 C

TUI 和 WebUI 可以按需选择，也可以同时打开。仅使用 TUI 时，无需启动 WebUI。

先打开 [WebUI](http://127.0.0.1:5173/)。如果无法访问，再另开 PowerShell，在 **终端 C** 执行：

```powershell
Set-Location F:\project\Icarus\apps\webui
pnpm.cmd dev
```

保留这个终端，然后打开 [Icarus 对话页面](http://127.0.0.1:5173/#/chat?workspace=F%3A%5Cproject%5CIcarus)。

1. 进入「对话」，填写工作区 `F:\project\Icarus`，点击「连接工作区」。上面的链接已携带工作区参数。
2. 显示「已连接」后，选择已有会话，或点击会话旁边的 **＋**。
3. 输入任务，点击「发送」，或按 `Ctrl+Enter`。

可以在相同工作区选择 TUI 已有的会话。浏览器中也能查看和管理「记忆」「知识库」。

这里运行的是 Windows Gateway，工作区使用 Windows 路径。`/mnt/f/...` 是 WSL 路径，不用于当前启动方式。

## 5. 检查服务是否正常

另开 PowerShell，在仓库根目录执行：

```powershell
Set-Location F:\project\Icarus
node --env-file=.env apps/webui/server/check-services.mjs
```

正常输出：

```text
Mem0: ready
OpenKB: ready
Gateway: ready
```

此检查读取服务接口，并检查服务凭据是否能访问接口；不会调用模型，也不会新增记忆或知识。它不代表模型 API Key、额度和模型名称已经验证可用。

查看 Docker 容器状态：

```powershell
docker ps --filter 'label=com.docker.compose.project=mem0-dev' --format '{{.Names}} | {{.Status}}'
docker ps --filter 'label=com.docker.compose.project=openkb-dev' --format '{{.Names}} | {{.Status}}'
```

| 服务 | 默认地址 |
| --- | --- |
| WebUI | http://127.0.0.1:5173/ |
| Gateway 健康检查 | http://127.0.0.1:8765/health |
| Mem0 接口文档 | http://127.0.0.1:8888/docs |
| OpenKB 接口描述 | http://127.0.0.1:7566/openapi.json |

## 6. 用完以后怎么停止

1. TUI 输入 `/exit`。
2. 在运行 `pnpm.cmd dev` 的终端 C 按 `Ctrl+C`，停止 WebUI。
3. 确认任务已结束后，在运行 Gateway 的终端 A 按 `Ctrl+C`。
4. 如需停止记忆服务和知识库，在 PowerShell 执行：

```powershell
Set-Location F:\project\Icarus
python apps/openkb/scripts/icarus_compose.py stop
python apps/mem0/scripts/icarus_compose.py stop
```

`stop` 保留容器和数据。下次仍用第 2 节的 `up -d --no-build` 启动。全部停妥后，可以按需退出 Docker Desktop。

### 已经在后台运行，但找不到终端

本次助手启动的 Gateway/WebUI 属于这种情况。先查询监听端口与进程命令行：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8765,5173 -ErrorAction SilentlyContinue |
    ForEach-Object {
        $taskConnection = $_
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskConnection.OwningProcess)"
        [pscustomobject]@{
            Port = $taskConnection.LocalPort
            ProcessId = $taskProcess.ProcessId
            CommandLine = $taskProcess.CommandLine
        }
    } | Format-List
```

Gateway 命令行应包含 `apps.gateway.src.main`；WebUI 命令行应对应本仓库的 Vite。确认无任务运行后，可在任务管理器「详细信息」中按查询到的 PID 结束对应进程。不要批量结束所有 Python 或 Node 进程。

本次后台启动日志位于 `F:\project\Icarus\.icarus-data\local-start\`。以后按本清单以前台方式启动时，日志直接显示在各自终端。

## 7. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| `icarus` 不是可识别的命令 | 使用本清单里的 Python 模块入口；当前没有配置统一命令的 PATH。 |
| Docker API 连接失败 | 打开 Docker Desktop，等待 `docker info` 能输出服务器版本。 |
| `8765` 或 `5173` 端口已占用 | 先检查已有服务是否正常；正常就直接使用。异常时按第 6 节确认进程后处理。 |
| TUI 提示 Gateway 不可用 | 检查 `http://127.0.0.1:8765/health`，再按第 3 节启动。 |
| 中文显示或编码异常 | 在启动 Python 的 PowerShell 中先设置 `$env:PYTHONUTF8 = '1'`。 |
| Agent 找不到 Bash | 启动 Gateway 的终端中添加 `D:\Git\bin` 到 PATH，再启动 Gateway。 |
| `No module named apps` | 在仓库根目录执行模块命令；其他工作区按第 4A 节设置 PYTHONPATH。 |
| 页面打开但记忆/知识库报错 | 运行第 5 节检查，确认 Docker 容器和根 `.env` 中的服务凭据。 |
| 界面正常，但发送后模型报错 | 检查根 `.env` 的模型 Key、服务额度，以及 `apps/agent/settings.json` 的协议、地址和模型名称。 |
| Docker 提示缺少镜像 | 执行下面的构建命令，再回到第 2 节启动。 |

仅在镜像缺失，或修改相应服务源码后需要重建时执行：

```powershell
Set-Location F:\project\Icarus
python apps/mem0/scripts/icarus_compose.py build
python apps/openkb/scripts/icarus_compose.py build
```

## 8. 配置和数据在哪里

- 当前有效环境配置：`F:\project\Icarus\.env`。日常使用无需复制或覆盖它。
- 模型、Plugin 和 MCP 配置：`F:\project\Icarus\apps\agent\settings.json`。
- 持久数据目录：由根 `.env` 的 `ICARUS_DATA_DIR` 指定。
- 会话数据库：`ICARUS_DATA_DIR` 下的 `icarus.db`。
- 记忆和知识库服务数据：同一数据目录下的 `services\mem0`、`services\openkb`。

不要为了普通重启更换或清空数据目录，否则旧会话、记忆和知识库可能无法继续使用。修改根 `.env` 后，Gateway 和 WebUI 需要重启才能可靠应用；服务容器配置有变化时，重新执行第 2 节的 `up` 命令。

这份清单只记录本机操作步骤，不包含任何密钥。
