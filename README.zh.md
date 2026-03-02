# DeepWiki-Open

![DeepWiki 横幅](screenshots/Deepwiki.png)

**DeepWiki**可以为任何GitHub、GitLab或BitBucket代码仓库自动创建美观、交互式的Wiki！只需输入仓库名称，DeepWiki将：

1. 分析代码结构
2. 生成全面的文档
3. 创建可视化图表解释一切如何运作
4. 将所有内容整理成易于导航的Wiki

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/sheing)

[![Twitter/X](https://img.shields.io/badge/Twitter-1DA1F2?style=for-the-badge&logo=twitter&logoColor=white)](https://x.com/sashimikun_void)
[![Discord](https://img.shields.io/badge/Discord-7289DA?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/invite/VQMBGR8u5v)

[English](./README.md) | [简体中文](./README.zh.md) | [繁體中文](./README.zh-tw.md) | [日本語](./README.ja.md) | [Español](./README.es.md) | [한국어](./README.kr.md) | [Tiếng Việt](./README.vi.md) | [Português Brasileiro](./README.pt-br.md) | [Français](./README.fr.md) | [Русский](./README.ru.md)

## ✨ 特点

- **即时文档**：几秒钟内将任何GitHub、GitLab或BitBucket仓库转换为Wiki
- **私有仓库支持**：使用个人访问令牌安全访问私有仓库
- **智能分析**：AI驱动的代码结构和关系理解
- **精美图表**：自动生成Mermaid图表可视化架构和数据流
- **简易导航**：简单、直观的界面探索Wiki
- **提问功能**：使用RAG驱动的AI与您的仓库聊天，获取准确答案
- **深度研究**：多轮研究过程，彻底调查复杂主题
- **多模型提供商**：支持Google Gemini、OpenAI、OpenRouter和本地Ollama模型

### 企业功能

- **GitLab SSO**：基于OAuth2的GitLab单点登录
- **管理后台**：批量索引、项目管理、系统监控
- **MCP 服务器**：JWT鉴权的 [Model Context Protocol](https://modelcontextprotocol.io/) 端点——将 Claude Code、Codex 等 MCP 客户端连接到您的代码库
- **产品管理**：将多个仓库分组为逻辑产品，支持跨仓库分析
- **仓库依赖关系**：自动化的仓库间依赖关系图可视化
- **全局问答**：跨所有已索引项目的问答
- **结构化洞察**：通过 LLM 提取模块、API 端点、数据模型、技术栈
- **权限系统**：基于 GitLab 的访问控制，内存缓存（项目级 5 分钟，项目列表 24 小时）

## 🚀 快速开始（超级简单！）

### 选项1：使用Docker

```bash
# 克隆仓库
git clone https://github.com/AsyncFuncAI/deepwiki-open.git
cd deepwiki-open

# 创建包含API密钥的.env文件
echo "GOOGLE_API_KEY=your_google_api_key" > .env
echo "OPENAI_API_KEY=your_openai_api_key" >> .env
# 可选：如果您想使用OpenRouter模型，添加OpenRouter API密钥
echo "OPENROUTER_API_KEY=your_openrouter_api_key" >> .env

# 使用Docker Compose运行
docker-compose up
```

(上述 Docker 命令以及 `docker-compose.yml` 配置会挂载您主机上的 `~/.adalflow` 目录到容器内的 `/root/.adalflow`。此路径用于存储：
- 克隆的仓库 (`~/.adalflow/repos/`)
- 仓库的嵌入和索引 (`~/.adalflow/databases/`)
- 缓存的已生成 Wiki 内容 (`~/.adalflow/wikicache/`)

这确保了即使容器停止或移除，您的数据也能持久保存。)

> 💡 **获取这些密钥的地方：**
> - 从[Google AI Studio](https://makersuite.google.com/app/apikey)获取Google API密钥
> - 从[OpenAI Platform](https://platform.openai.com/api-keys)获取OpenAI API密钥

### 选项2：手动设置（推荐）

#### 步骤1：设置API密钥

在项目根目录创建一个`.env`文件，包含以下密钥：

```
GOOGLE_API_KEY=your_google_api_key
OPENAI_API_KEY=your_openai_api_key
# 可选：如果您想使用OpenRouter模型，添加此项
OPENROUTER_API_KEY=your_openrouter_api_key
```

#### 步骤2：启动后端

```bash
# 安装Python依赖
python -m pip install poetry==2.0.1 && poetry install -C api

# 启动API服务器
python -m api.main
```

#### 步骤3：启动前端

```bash
# 安装JavaScript依赖
npm install
# 或
yarn install

# 启动Web应用
npm run dev
# 或
yarn dev
```

#### 步骤4：使用DeepWiki！

1. 在浏览器中打开[http://localhost:3000](http://localhost:3000)
2. 输入GitHub、GitLab或Bitbucket仓库（如`https://github.com/openai/codex`、`https://github.com/microsoft/autogen`、`https://gitlab.com/gitlab-org/gitlab`或`https://bitbucket.org/redradish/atlassian_app_versions`）
3. 对于私有仓库，点击"+ 添加访问令牌"并输入您的GitHub或GitLab个人访问令牌
4. 点击"生成Wiki"，见证奇迹的发生！

## 🔍 工作原理

DeepWiki使用AI来：

1. 克隆并分析GitHub、GitLab或Bitbucket仓库（包括使用令牌认证的私有仓库）
2. 创建代码嵌入用于智能检索
3. 使用上下文感知AI生成文档（使用Google Gemini、OpenAI、OpenRouter或本地Ollama模型）
4. 创建可视化图表解释代码关系
5. 将所有内容组织成结构化Wiki
6. 通过提问功能实现与仓库的智能问答
7. 通过深度研究功能提供深入研究能力

```mermaid
graph TD
    A[用户输入GitHub/GitLab/Bitbucket仓库] --> AA{私有仓库?}
    AA -->|是| AB[添加访问令牌]
    AA -->|否| B[克隆仓库]
    AB --> B
    B --> C[分析代码结构]
    C --> D[创建代码嵌入]

    D --> M{选择模型提供商}
    M -->|Google Gemini| E1[使用Gemini生成]
    M -->|OpenAI| E2[使用OpenAI生成]
    M -->|OpenRouter| E3[使用OpenRouter生成]
    M -->|本地Ollama| E4[使用Ollama生成]

    E1 --> E[生成文档]
    E2 --> E
    E3 --> E
    E4 --> E

    D --> F[创建可视化图表]
    E --> G[组织为Wiki]
    F --> G
    G --> H[交互式DeepWiki]

    classDef process stroke-width:2px;
    classDef data stroke-width:2px;
    classDef result stroke-width:2px;
    classDef decision stroke-width:2px;

    class A,D data;
    class AA,M decision;
    class B,C,E,F,G,AB,E1,E2,E3,E4 process;
    class H result;
```

## 🛠️ 项目结构

```
deepwiki/
├── api/                        # 后端API服务器
│   ├── main.py                 # 入口点（uvicorn）
│   ├── api.py                  # FastAPI 应用，REST/WebSocket 端点
│   ├── gitlab_auth.py          # GitLab OAuth2 SSO、JWT、MCP Token
│   ├── gitlab_permission.py    # 仓库权限检查 + 缓存
│   ├── admin.py                # 管理API路由
│   ├── batch_indexer.py        # 后台批量索引
│   ├── mcp_server.py           # MCP 服务器（JWT鉴权）
│   ├── metadata_store.py       # 索引元数据存储
│   ├── product_manager.py      # 产品 CRUD
│   ├── repo_relations.py       # 仓库依赖分析
│   ├── insight_extractor.py    # 结构化知识提取
│   ├── wiki_generator.py       # Wiki 生成核心逻辑
│   ├── rag.py                  # 单仓库 RAG
│   ├── multi_rag.py            # 多仓库 RAG
│   ├── data_pipeline.py        # 仓库克隆、嵌入
│   ├── config.py               # 配置加载器、环境变量
│   ├── prompts.py              # LLM 提示词模板
│   ├── config/                 # JSON 配置文件
│   └── *_client.py             # LLM 提供商客户端
│
├── src/                        # 前端 Next.js 应用
│   ├── app/
│   │   ├── page.tsx            # 首页（SSO登录、项目列表）
│   │   ├── [owner]/[repo]/     # Wiki 查看器
│   │   ├── admin/              # 管理后台
│   │   ├── admin/relations/    # 仓库依赖关系图
│   │   ├── ask/                # 全局问答（跨仓库）
│   │   └── auth/callback/      # OAuth 回调
│   ├── components/             # React 组件
│   └── contexts/               # Auth、Language 上下文
│
├── public/                     # 静态资源
├── package.json                # JavaScript 依赖
└── .env                        # 环境变量（需要创建）
```

## 🤖 提问和深度研究功能

### 提问功能

提问功能允许您使用检索增强生成（RAG）与您的仓库聊天：

- **上下文感知响应**：基于仓库中实际代码获取准确答案
- **RAG驱动**：系统检索相关代码片段，提供有根据的响应
- **实时流式传输**：实时查看生成的响应，获得更交互式的体验
- **对话历史**：系统在问题之间保持上下文，实现更连贯的交互

### 深度研究功能

深度研究通过多轮研究过程将仓库分析提升到新水平：

- **深入调查**：通过多次研究迭代彻底探索复杂主题
- **结构化过程**：遵循清晰的研究计划，包含更新和全面结论
- **自动继续**：AI自动继续研究直到达成结论（最多5次迭代）
- **研究阶段**：
  1. **研究计划**：概述方法和初步发现
  2. **研究更新**：在前一轮迭代基础上增加新见解
  3. **最终结论**：基于所有迭代提供全面答案

要使用深度研究，只需在提交问题前在提问界面中切换"深度研究"开关。

## 🏢 企业 GitLab 集成

DeepWiki 支持以 GitLab 作为身份认证和仓库提供商的完整企业部署。

### GitLab SSO 配置

1. 在 GitLab 中创建 OAuth2 应用（管理 > 应用）：
   - **回调 URI**：`http://your-frontend:3000/auth/gitlab/callback`
   - **权限范围**：`read_user`、`read_api`
2. 设置环境变量：
   ```bash
   GITLAB_URL=https://gitlab.example.com
   GITLAB_CLIENT_ID=your_app_id
   GITLAB_CLIENT_SECRET=your_app_secret
   JWT_SECRET_KEY=your_random_secret
   FRONTEND_ORIGIN=http://your-frontend:3000
   ADMIN_USERNAMES=admin_user1,admin_user2
   ```
3. 批量索引和 MCP 服务器需要服务账号令牌（`read_api` 权限）：
   ```bash
   GITLAB_SERVICE_TOKEN=glpat-xxxxxxxxxxxx
   ```

### 管理后台

`ADMIN_USERNAMES` 中的用户可访问 `/admin`：
- **已索引项目**：查看、重新索引或移除已索引的仓库
- **批量索引**：批量选择并索引 GitLab 项目
- **产品管理**：将仓库分组为逻辑产品，支持跨仓库分析
- **系统状态**：缓存大小、索引状态、配置概览

### 仓库依赖关系

访问 `/admin/relations`：
- 通过 LLM 辅助的导入扫描自动检测依赖
- 交互式依赖图（ReactFlow），支持分组/聚焦/全视图模式
- 边过滤和跨仓库依赖可视化

## 🔌 MCP 服务器集成

DeepWiki 在 `/mcp` 暴露经过 JWT 鉴权的 [MCP](https://modelcontextprotocol.io/) 端点，允许外部 AI 代理访问已索引的代码库。

### 可用工具

| 工具 | 说明 |
|------|------|
| `list_products` | 列出所有已定义的产品及其仓库 |
| `get_product_overview` | 获取产品跨所有仓库的聚合概览 |
| `search_product_code` | 跨产品所有仓库的语义代码搜索 |
| `ask_product` | 跨产品所有仓库提问 |
| `list_projects` | 列出所有已索引项目及状态 |
| `get_wiki_summary` | 获取 Wiki 结构和页面标题 |
| `get_wiki_page` | 读取完整 Wiki 页面内容 |
| `search_code` | 单项目语义代码搜索 |
| `get_repo_relations` | 获取依赖关系 |
| `ask_question` | 针对单个项目提问 |
| `get_project_insights` | 获取结构化知识索引 |
| `extract_project_insights` | 通过 LLM 提取洞察 |
| `get_product_insights` | 跨产品的聚合洞察 |

### 连接 Claude Code

1. 通过 GitLab SSO 登录 DeepWiki
2. 点击导航栏中的 🔑 图标获取 MCP Token
3. 运行生成的命令：
   ```bash
   claude mcp add --transport http deepwiki http://your-server:8001/mcp \
     --header "Authorization: Bearer <your-mcp-token>"
   ```
4. Claude Code 现在可以查询您已索引的代码库

## 📱 截图

![DeepWiki主界面](screenshots/Interface.png)
*DeepWiki的主界面*

![私有仓库支持](screenshots/privaterepo.png)
*使用个人访问令牌访问私有仓库*

![深度研究功能](screenshots/DeepResearch.png)
*深度研究为复杂主题进行多轮调查*

### 演示视频

[![DeepWiki演示视频](https://img.youtube.com/vi/zGANs8US8B4/0.jpg)](https://youtu.be/zGANs8US8B4)

*观看DeepWiki实际操作！*

## ❓ 故障排除

### API密钥问题
- **"缺少环境变量"**：确保您的`.env`文件位于项目根目录并包含所需的API密钥
- **"API密钥无效"**：检查您是否正确复制了完整密钥，没有多余空格
- **"OpenRouter API错误"**：验证您的OpenRouter API密钥有效且有足够的额度

### 连接问题
- **"无法连接到API服务器"**：确保API服务器在端口8001上运行
- **"CORS错误"**：API配置为允许所有来源，但如果您遇到问题，请尝试在同一台机器上运行前端和后端

### 生成问题
- **"生成Wiki时出错"**：对于非常大的仓库，请先尝试较小的仓库
- **"无效的仓库格式"**：确保您使用有效的GitHub、GitLab或Bitbucket URL格式
- **"无法获取仓库结构"**：对于私有仓库，确保您输入了具有适当权限的有效个人访问令牌
- **"图表渲染错误"**：应用程序将自动尝试修复损坏的图表

### 常见解决方案
1. **重启两个服务器**：有时简单的重启可以解决大多数问题
2. **检查控制台日志**：打开浏览器开发者工具查看任何JavaScript错误
3. **检查API日志**：查看运行API的终端中的Python错误

## 🤝 贡献

欢迎贡献！随时：
- 为bug或功能请求开issue
- 提交pull request改进代码
- 分享您的反馈和想法

## 📄 许可证

本项目根据MIT许可证授权 - 详情请参阅[LICENSE](LICENSE)文件。

## ⭐ 星标历史

[![星标历史图表](https://api.star-history.com/svg?repos=AsyncFuncAI/deepwiki-open&type=Date)](https://star-history.com/#AsyncFuncAI/deepwiki-open&Date)

## 🤖 基于提供者的模型选择系统

DeepWiki 现在实现了灵活的基于提供者的模型选择系统，支持多种 LLM 提供商：

### 支持的提供商和模型

- **Google**: 默认使用 `gemini-2.5-flash`，还支持 `gemini-2.5-flash-lite`、`gemini-2.5-pro` 等
- **OpenAI**: 默认使用 `gpt-5-nano`，还支持 `gpt-5`, `4o` 等
- **OpenRouter**: 通过统一 API 访问多种模型，包括 Claude、Llama、Mistral 等
- **Ollama**: 支持本地运行的开源模型，如 `llama3`

### 环境变量

每个提供商需要相应的 API 密钥环境变量：

```
# API 密钥
GOOGLE_API_KEY=你的谷歌API密钥        # 使用 Google Gemini 模型必需
OPENAI_API_KEY=你的OpenAI密钥        # 使用 OpenAI 模型必需
OPENROUTER_API_KEY=你的OpenRouter密钥 # 使用 OpenRouter 模型必需

# OpenAI API 基础 URL 配置
OPENAI_BASE_URL=https://自定义API端点.com/v1  # 可选，用于自定义 OpenAI API 端点
```

### 为服务提供者设计的自定义模型选择

自定义模型选择功能专为需要以下功能的服务提供者设计：

- 您可在您的组织内部为用户提供多种 AI 模型选择
- 您无需代码更改即可快速适应快速发展的 LLM 领域
- 您可支持预定义列表中没有的专业或微调模型

使用者可以通过从服务提供者预定义选项中选择或在前端界面中输入自定义模型标识符来实现其模型产品。

### 为企业私有渠道设计的基础 URL 配置

OpenAI 客户端的 base_url 配置主要为拥有私有 API 渠道的企业用户设计。此功能：

- 支持连接到私有或企业特定的 API 端点
- 允许组织使用自己的自托管或自定义部署的 LLM 服务
- 支持与第三方 OpenAI API 兼容服务的集成

**即将推出**：在未来的更新中，DeepWiki 将支持一种模式，用户需要在请求中提供自己的 API 密钥。这将允许拥有私有渠道的企业客户使用其现有的 API 安排，而不是与 DeepWiki 部署共享凭据。

### 环境变量

每个提供商需要其相应的API密钥环境变量：

```
# API密钥
GOOGLE_API_KEY=your_google_api_key        # Google Gemini模型必需
OPENAI_API_KEY=your_openai_api_key        # OpenAI模型必需
OPENROUTER_API_KEY=your_openrouter_api_key # OpenRouter模型必需

# OpenAI API基础URL配置
OPENAI_BASE_URL=https://custom-api-endpoint.com/v1  # 可选，用于自定义OpenAI API端点

# 配置目录
DEEPWIKI_CONFIG_DIR=/path/to/custom/config/dir  # 可选，用于自定义配置文件位置

# 授权模式
DEEPWIKI_AUTH_MODE=true  # 设置为 true 或 1 以启用授权模式
DEEPWIKI_AUTH_CODE=your_secret_code # 当 DEEPWIKI_AUTH_MODE 启用时所需的授权码
```
如果不使用ollama模式，那么需要配置OpenAI API密钥用于embeddings。其他密钥只有配置并使用使用对应提供商的模型时才需要。

## 授权模式

DeepWiki 可以配置为在授权模式下运行，在该模式下，生成 Wiki 需要有效的授权码。如果您想控制谁可以使用生成功能，这将非常有用。
限制使用前端页面生成wiki并保护已生成页面的缓存删除，但无法完全阻止直接访问 API 端点生成wiki。主要目的是为了保护管理员已生成的wiki页面，防止被访问者重新生成。

要启用授权模式，请设置以下环境变量：

- `DEEPWIKI_AUTH_MODE`: 将此设置为 `true` 或 `1`。启用后，前端将显示一个用于输入授权码的字段。
- `DEEPWIKI_AUTH_CODE`: 将此设置为所需的密钥。限制使用前端页面生成wiki并保护已生成页面的缓存删除，但无法完全阻止直接访问 API 端点生成wiki。

如果未设置 `DEEPWIKI_AUTH_MODE` 或将其设置为 `false`（或除 `true`/`1` 之外的任何其他值），则授权功能将被禁用，并且不需要任何代码。

### 配置文件

DeepWiki使用JSON配置文件管理系统的各个方面：

1. **`generator.json`**：文本生成模型配置
   - 定义可用的模型提供商（Google、OpenAI、OpenRouter、Ollama）
   - 指定每个提供商的默认和可用模型
   - 包含特定模型的参数，如temperature和top_p

2. **`embedder.json`**：嵌入模型和文本处理配置
   - 定义用于向量存储的嵌入模型
   - 包含用于RAG的检索器配置
   - 指定文档分块的文本分割器设置

3. **`repo.json`**：仓库处理配置
   - 包含排除特定文件和目录的文件过滤器
   - 定义仓库大小限制和处理规则

默认情况下，这些文件位于`api/config/`目录中。您可以使用`DEEPWIKI_CONFIG_DIR`环境变量自定义它们的位置。

### 面向服务提供商的自定义模型选择

自定义模型选择功能专为需要以下功能的服务提供者设计：

- 您可在您的组织内部为用户提供多种 AI 模型选择
- 您无需代码更改即可快速适应快速发展的 LLM 领域
- 您可支持预定义列表中没有的专业或微调模型

使用者可以通过从服务提供者预定义选项中选择或在前端界面中输入自定义模型标识符来实现其模型产品。

### 为企业私有渠道设计的基础 URL 配置

OpenAI 客户端的 base_url 配置主要为拥有私有 API 渠道的企业用户设计。此功能：

- 支持连接到私有或企业特定的 API 端点
- 允许组织使用自己的自托管或自定义部署的 LLM 服务
- 支持与第三方 OpenAI API 兼容服务的集成

**即将推出**：在未来的更新中，DeepWiki 将支持一种模式，用户需要在请求中提供自己的 API 密钥。这将允许拥有私有渠道的企业客户使用其现有的 API 安排，而不是与 DeepWiki 部署共享凭据。

## 🧩 使用 OpenAI 兼容的 Embedding 模型（如阿里巴巴 Qwen）

如果你希望使用 OpenAI 以外、但兼容 OpenAI 接口的 embedding 模型（如阿里巴巴 Qwen），请参考以下步骤：

1. 用 `api/config/embedder_openai_compatible.json` 的内容替换 `api/config/embedder.json`。
2. 在项目根目录的 `.env` 文件中，配置相应的环境变量，例如：
   ```
   OPENAI_API_KEY=你的_api_key
   OPENAI_BASE_URL=你的_openai_兼容接口地址
   ```
3. 程序会自动用环境变量的值替换 embedder.json 里的占位符。

这样即可无缝切换到 OpenAI 兼容的 embedding 服务，无需修改代码。

