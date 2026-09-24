# Ask Agent 实施计划（A 路线）

日期：2026-09-22。目标：在现有 DeepWiki 中以同一对话入口支持功能定位、规则解释、流程追踪和文档生成。

## 调研结论与决策

- 使用 Deep Agents Python SDK，直接嵌入 FastAPI；不依赖官方 Agent Server、LangSmith 服务或官方 UI 的接口协议。
- 固定 `deepagents==0.7.17`；在 Python 3.11 隔离环境验证实际安装版本的 API，不依据 upstream main 猜测接口。
- 现有 `/ws/chat` 和 `/chat/completions/stream` 仍服务 Wiki 生成。Ask 使用独立 `/api/agent/*` 路由。
- 使用 SQLite 保存会话、执行记录、事件与文档，使用 LangGraph SQLite checkpointer 保存工具调用上下文。文件位于 `~/.adalflow/agent/`，随既有挂载持久化。
- 首版运行方式对应当前单 Uvicorn worker 部署。后台任务独立于浏览器连接，事件可重放；重启后标记未完成任务为 interrupted，由用户重新鉴权后恢复。进程锁拒绝多个 worker 同时管理同一数据库。
- 远端仓库只读，会话固定仓库范围；后续改为每轮检查并更新本地 clone 与索引，按该轮 commit 使用 Git 对象读取，避免工作区变动造成行号漂移。向量索引只提供候选，引用必须通过源码工具确认。
- GitLab 身份和逐仓库权限在服务端验证，读取会话也校验权限；不把用户 token 存入消息、checkpoint 或事件。不自动把全部索引仓库加入用户所选范围。
- 模型使用 LangChain tool-calling 适配器，复用现有服务端 provider/model 配置。先覆盖当前 OpenAI-compatible 接口及常见 provider；实际模型与代理的工具调用能力需在部署环境验收。
- 前端沿用 Next.js 15 / Tailwind 4、Markdown/Mermaid 与登录上下文。参考官方 UI 的会话/工具进度/文档面板布局，自建适配现有平台的组件与事件协议。

## 实施任务

- [x] 核对现有调用路径、权限、模型配置及官方 SDK/API。
- [x] 验证依赖共存，更新 Python 依赖及锁文件。
- [x] 实现会话/运行/事件/文档存储、单进程任务管理、取消与恢复。
- [x] 实现仓库授权、固定版本源码读取、精确搜索、既有索引检索工具。
- [x] 接入 Deep Agents、多轮上下文、按需流程分析指引、计划和文档产物。
- [x] 实现独立鉴权 API、流式事件重放及 Next.js 代理。
- [x] 升级 Ask：连续对话、历史、进度、取消/恢复、文档查看/下载，兼容单仓库和 Global Ask。
- [x] 验证权限/路径边界、状态恢复、工具循环、前端构建与浏览器交互。
- [x] 补部署配置、已验证范围与线上验收清单。

## 工程验证结果

- 13 项后端测试通过，包括真实 SDK 工具循环、模拟 OpenAI-compatible 流式工具协议、已有 FAISS 索引、固定源码版本、跨用户隔离、幂等请求、取消/重启恢复、空响应重试和上下文预算。
- 完整 FastAPI 生命周期、健康检查与新接口未登录返回 401 的检查通过。
- Chromium 浏览器验证了连续两轮提问、文档生成/下载、刷新后恢复历史和手机宽度布局，无页面异常；可复现脚本位于 `tests/agent/ui-smoke.cjs`。
- TypeScript、新增代码 ESLint/Ruff、Poetry 锁文件一致性、Next.js 生产构建通过。构建仍有原有 Hook 依赖及图片优化警告。
- 为构建移除了两个已有未使用绑定；Mermaid 改为严格模式并移除了错误回显时的原始 HTML 插入。
- 首版实现完成，尚未部署线上。真实代理和真实业务仓库的验收按《Ask-Agent部署与验收》执行。

## 验收边界

本地使用临时 Git 仓库、确定性测试模型和本地模拟服务验证工具、协议和运行机制，不访问或上传真实业务代码，不将这些测试当作业务流程还原效果证明。真实仓库、模型代理、索引性能和业务文档完整性由具有代码访问权限的已部署平台完成验收。

## 参考

- https://github.com/langchain-ai/deepagents
- https://github.com/langchain-ai/deep-agents-ui
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/langsmith/deploy-standalone-server
- https://github.com/mthang1801/flow-trace-genesis
