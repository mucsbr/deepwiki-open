# Ask Agent 部署与验收

## 架构与范围

本实现采用 A 路线：Next.js Ask → 既有 FastAPI `/api/agent/*` → Deep Agents SDK → 每轮更新的索引与源码。

没有引入官方 Agent Server、官方 UI 的 LangGraph HTTP 协议或 LangSmith 服务依赖。界面在原项目样式、登录上下文和 Markdown/Mermaid 上实现，包含历史会话、连续提问、工具进度、停止/继续、文档预览和下载。

新 agent 适用于已配置 GitLab SSO 的平台与**已经索引且本地 clone 可用**的项目。Global Ask 严格使用选择的仓库，不自动追加其他索引项目。会话保留仓库选区；每次新提问先检查所选仓库的 GitLab 默认分支，有新 commit 时拉取并更新索引，再按本轮代码版本分析。恢复同一轮任务仍使用该轮版本。改变仓库选区时需新建会话。GitHub、Bitbucket 等原有 Ask 保留经典问答入口。

## 安装与启动

使用 Python 3.11+ 和项目 Dockerfile 对齐的 Node.js 20。Python 依赖及传递依赖已记录在 `api/poetry.lock`。

```bash
poetry install -C api
# 在 Poetry 环境中，从仓库根目录启动：
poetry --project api run python -m api.main
npm install
npm run dev
```

生产可按现有 Docker 构建方式部署。新增数据默认写入 `~/.adalflow/agent/`，已经位于原 Compose 的 `~/.adalflow` 挂载中；使用自定义 `AGENT_DATA_DIR` 时需挂载相应目录。

### 中国大陆环境的构建源

Docker 构建默认使用清华 PyPI 和 Debian 镜像，npm 沿用 npmmirror。Python 下载超时为 120 秒、重试次数为 5，APT 也设置了超时与重试。大陆服务器可直接运行：

```bash
docker compose build deepwiki
```

其他网络环境可覆盖源地址，例如使用官方源（保留发行版和签名校验不变）：

```bash
docker compose build \
  --build-arg PYPI_INDEX_URL=https://pypi.org/simple \
  --build-arg DEBIAN_MIRROR=https://deb.debian.org \
  deepwiki
```

该参数同时用于安装 Poetry 构建工具和项目运行依赖。构建从已提交的 `poetry.lock` 导出依赖，校验下载哈希，不在镜像内重新执行 `poetry lock`。构建工具独立缓存，包下载使用 BuildKit cache，失败重试可以复用已下载内容。可额外用 `--build-arg PIP_DEFAULT_TIMEOUT=180` 或 `--build-arg PIP_RETRIES=8` 调整网络容忍度。

镜像还预置 `cl100k_base`、`o200k_base` 分词数据，避免 AdalFlow/分词器首次导入时再联网下载。构建时按 tiktoken 内置 SHA-256 校验，默认从数据的官方 Azure 地址下载；如有内部镜像，可用 `--build-arg TIKTOKEN_ENCODINGS_BASE_URL=https://your-mirror/encodings` 覆盖。运行时缓存固定在 `/opt/tiktoken-cache`，不需要下载这些文件。NodeSource 的 Node.js 20 软件仓库目前保留官方地址，与 Debian 系统源分开。

构建成功后，再执行 `docker compose up -d deepwiki` 才会让服务使用新镜像；只有 `git pull` 或重启旧容器不会应用新代码。服务器上的模型配置和数据挂载定制应保留。

| 配置 | 默认值 | 作用 |
|---|---|---|
| `AGENT_ENABLED` | `true` | 关闭时新接口返回 503，经典问答与 Wiki 可继续使用 |
| `AGENT_PROVIDER` / `AGENT_MODEL` | 现有配置 | 新会话模型默认值；界面可以选择其他模型 |
| `AGENT_DATA_DIR` | `~/.adalflow/agent` | 会话数据库、checkpoint 数据库、进程锁 |
| `AGENT_MAX_CONCURRENT_RUNS` | `4` | 当前进程最多同时运行的分析数 |
| `AGENT_RUN_TIMEOUT_SECONDS` | `900` | 单次执行时间预算，超时后可继续 |
| `AGENT_CONTEXT_TOKENS` | `32000` | 上下文整理预算；应不大于实际模型输入限制，已知更小的模型限制优先 |

模型适配覆盖 OpenAI-compatible、OpenRouter、DashScope、Google、Ollama、Azure 和 Bedrock。使用已有环境变量保存地址与密钥，浏览器不提交模型服务地址。框架能接入这些 provider 不代表所有模型都支持工具调用；须在实际代理上验证 `tools`、工具参数、`tool_calls` 与 `tool` 消息往返。没有正确工具调用能力的模型不能靠更改提示词补足。

## 运行与恢复

- 当前执行器适配单 Uvicorn worker（Linux/macOS）。同一数据目录的第二个 worker 会因进程锁启动失败，不可直接水平扩容。多实例需后续接共享队列与共享持久化存储。
- POST 发起任务后，浏览器通过带 Bearer token 的 fetch SSE 订阅；连接断开不取消任务。事件写入数据库，重连通过 `after` 游标补取。
- 每轮刷新在后台任务中执行，并通过 SSE 报告检查和更新进度；GitLab 查询或索引更新失败时，本轮停止，不使用旧代码冒充最新代码。当前用户的 GitLab 会话用于访问校验和分支检查；本地拉取与索引沿用平台服务令牌。GitLab Git 凭据只通过单次子进程配置传递，不写入 clone 的 `origin`；两种 token 均不写入会话或 checkpoint。
- 索引任务先写 `indexing`，确认本地 commit、非空向量与索引文件后才写 `indexed`；拉取失败写 `error`，保留旧 clone 供排查。完整批量扫描后，对不再可访问的历史项目标记 `unavailable`，不删除数据。GitLab 的 403/404 不能单独证明仓库已删除，也可能是服务令牌权限变化。
- 服务正常关闭或异常重启后，未完成记录变为 interrupted；用户从历史会话点击继续，服务端重新校验权限并从 checkpoint 继续。不会持久化用户 GitLab token，也不会在后台自动复用过期身份。
- 取消可以保留已经生成的部分结果；继续时恢复原轮次及该轮源码版本，不重复添加同一条用户消息。尚未完成刷新就中断的轮次会在恢复时重新检查 GitLab。若模型调用失败，可在界面更换模型后继续。
- checkpoint 恢复时，尚未完成的读取或模型调用可能重试。源码工具只读，文档按会话和文件名 upsert，避免重试生成重复文档。
- 会话和每轮的源码版本记录在 `sessions.sqlite3`，LangGraph 状态在 `checkpoints.sqlite3`；旧轮次版本会在数据库迁移时保留。持久化状态含源码片段，应与原始仓库采用相同访问和备份策略。

## 工具与证据

提问开始前的刷新可更新本地 clone 与代码索引；`search_index` 工具本身只加载现有代码/Wiki PKL。索引结果是候选；`read_source` 用本轮固定 commit 的 Git blob 返回实际行号、内容 hash 与 GitLab 引用链接。先前轮次的源码片段可能属于旧版本，必须重新读取后才能用于当前结论。

`search_source` 支持跨选定仓库的精确文本搜索；`list_source_files` 支持目录/文件模式定位。符号链接、路径越界、环境密钥文件、大文件和二进制文件不作为源码读出。每次源码/索引工具调用检查仓库权限，沿用现有 GitLab 权限缓存 TTL。

`load_flow_guide` 按需提供入口定位、项目约定识别、上下游追踪、业务规则、失败分支与未解问题的分析方法。简单定位不强制生成完整流程。工具读取成功不能证明结论正确，输出必须区分源码支持、推断与未知。

内置文件搜索工具不向模型开放；源码查询使用按仓库授权的专用工具。没有主机 shell 或默认子 agent 执行权限。请求文档时通过 `save_document` 保存可下载产物，不能修改远端源仓库。

## API

- `GET /api/agent/config`：agent 默认模型。
- `GET/POST /api/agent/sessions`：分页历史、创建固定仓库选区的会话。
- `GET /api/agent/sessions/{id}`：会话、执行记录与文档。
- `POST /api/agent/sessions/{id}/runs`：发起一轮后台检查与分析；`request_id` 提供幂等性。
- `GET /api/agent/runs/{id}/events?after=...`：重放及订阅执行事件。
- `POST /api/agent/runs/{id}/cancel`、`/resume`：停止与继续。

所有接口要求 GitLab JWT。访问历史、文档、事件和恢复任务均检查会话归属与仓库权限。反向代理应关闭 SSE 缓冲并允许长连接；Next.js rewrite 已加入。

## 工程验证与线上验收

离线工程测试：

```bash
# -c 绕过仓库原 pytest.ini 的错误节名；测试不请求真实模型或 GitLab。
python -m pytest -c /dev/null -o cache_dir=/tmp/deepwiki-agent-pytest tests/agent/test_agent.py -q
npm run build
```

测试覆盖按轮版本、GitLab 更新检查、索引失败状态和缺失项目审计、并发元数据写入、路径/权限隔离、已有索引过滤、幂等请求、Deep Agents 工具循环、跨轮上下文、取消与重启恢复，以及模拟 OpenAI-compatible 流式工具协议。

`tests/agent/preview.py` 是仅供本机 UI 联调的测试服务：它使用临时 Git 仓库与确定性模型，不接真实代码、GitLab 或模型服务，不能挂入生产 API。浏览器联调应检查连续提问、工具进度、文档预览/下载、刷新恢复历史和手机布局。

可复现的浏览器冒烟检查（需本机 Chrome 和可解析的 `playwright` 包）：

```bash
# 终端一：在安装了项目依赖及 pytest 的 Python 环境运行
python -m uvicorn tests.agent.preview:app --host 127.0.0.1 --port 8125
# 终端二：从仓库根目录运行
SERVER_BASE_URL=http://127.0.0.1:8125 npx next dev --turbopack --port 3102
# 终端三：PLAYWRIGHT_PACKAGE_PATH 可指定隔离安装的 playwright 包绝对路径
node tests/agent/ui-smoke.cjs
```

此测试只连接本机 fixture，使用专用测试身份。截图写入系统临时目录。Node 25 的实验性 Web Storage 与当前 Next.js 15 开发环境存在兼容问题，优先使用项目的 Node 20；本机调试可用 `NODE_OPTIONS=--no-experimental-webstorage` 禁用该实验功能。

线上仍需验收：真实模型代理的工具调用、真实索引规模下的时延与内存、不同用户仓库权限、断线后继续，以及真实业务入口的流程覆盖和引用质量。离线测试证明工程机制可用，不证明业务流程已被正确还原。
