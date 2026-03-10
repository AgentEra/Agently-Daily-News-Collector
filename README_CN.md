# Agently Daily News Collector v4

本项目已经基于 **Agently v4** 完整重写，核心实现改为：

- 使用 `TriggerFlow` 编排整条新闻采集流程
- 使用 Agently v4 内置 `Search` / `Browse` 工具
- 使用结构化输出契约替代旧版 v3 `Workflow` API

> 版本约束：本项目仅适用于 **Agently v4.0.8.3 及以上版本**。当前实现已经使用 `TriggerFlow sub flow` 组织栏目子流程；如果你安装的是更早的 v4 版本，主流程与子流程之间的组合能力将与当前代码不兼容。

原有 Agently v3 项目已经整体归档到 [`./v3`](./v3)。

## 功能说明

- 输入一个主题，自动生成多栏目新闻汇总
- 自动完成搜索、筛选、浏览正文、总结和 Markdown 排版
- 最终报告输出到 `./outputs`
- 提示词保存在 `./prompts`，便于继续调优
- 提供独立的 `./tools` 适配层，方便替换搜索和浏览实现
- 提供独立的 `./workflow` 目录，方便单独调整流程编排

## 使用方式

1. 安装依赖：

```bash
pip install -r requirements.txt
```

如果你是手动安装 Agently，请确认版本至少为：

```bash
pip install "agently>=4.0.8.3"
```

2. 修改 [`SETTINGS.yaml`](./SETTINGS.yaml)：

- 保持模型配置为环境变量占位符
- 在环境变量中提供下面三个值：

```bash
export AGENTLY_NEWS_BASE_URL="https://api.openai.com/v1"
export AGENTLY_NEWS_MODEL="gpt-4.1-mini"
export AGENTLY_NEWS_API_KEY="your_api_key"
```

- 或者写到本地 `.env` 文件中：

```dotenv
AGENTLY_NEWS_BASE_URL=https://api.openai.com/v1
AGENTLY_NEWS_MODEL=gpt-4.1-mini
AGENTLY_NEWS_API_KEY=your_api_key
```

- 按需调整输出语言、搜索参数和并发参数
- 如果你的 OpenAI-compatible 服务本身不需要鉴权，可以不设置 `AGENTLY_NEWS_API_KEY`，项目会自动跳过 `auth`

3. 启动：

```bash
python app.py
```

也可以直接把主题作为命令行参数传入：

```bash
python app.py "AI Agents"
```

## 目录结构

```text
.
├── app.py
├── news_collector/
├── tools/
├── workflow/
├── prompts/
├── outputs/
├── logs/
└── v3/
```

## 重要说明：v3 -> v4 的关键变化

业务主线其实没有变，仍然基本是：

`outline -> search -> pick -> browse + summarize -> write column -> render markdown`

真正变化的是这条链路在工程上的组织方式。

### 从本项目角度看，主要改了什么

- 旧版 v3 主要是 `./workflows` 里的主流程加栏目子流程，再配合项目内自定义的 `search.py` / `browse.py` 和 storage 传值。
- 新版 v4 把职责拆得更清楚：
  - `news_collector/`：app / integration 层
  - `workflow/`：主 flow、栏目 sub flow 与各 chunk 的具体实现
  - `tools/`：搜索与抓取适配层
  - `prompts/`：结构化提示词契约
- 模型配置不再写死在 Python 代码里，而是统一通过 `SETTINGS.yaml` 里的 `${ENV.xxx}` 占位符注入，部署和切换环境更简单。
- 搜索、浏览、日志等依赖不再散落在工作流实现内部，而是通过 TriggerFlow runtime resources 注入，后续替换实现时不需要改业务流程本身。
- 现在工作流规划也更贴近业务边界：
  - 主 flow 负责 `prepare_request -> generate_outline -> for_each(column) -> render_report`
  - 栏目 sub flow 负责 `search -> pick -> summarize -> write_column`
  - 栏目内部的 `summarize` 又继续下沉为一个 summary sub flow，用 TriggerFlow 自己的 `for_each + collect` 做并发收拢，而不是在业务代码里手写 `asyncio.gather`
  - 这样主流程关注“整份日报如何生成”，子流程关注“单个栏目如何产出”
  - `sub flow` 的直接价值是：栏目链路现在可以被当成一个独立、可复用、可单独演进的流程单元来看待，而不是继续埋在父流程的某个大 chunk 里

### 本项目实际用到了 Agently v4 的哪些关键能力

- **TriggerFlow 编排**
  - 用更显式的流程图式写法替代 v3 的旧 Workflow 风格，支持 `to`、`for_each`、`sub flow` 等组合方式。
  - 和旧版 v3 基本串行执行不同，这个 v4 版本会并发处理多个栏目，并在栏目内部并发总结多条入选新闻。
  - 对本项目的意义：新闻采集链路更容易拆 chunk、看依赖、调并发，也更适合后续继续演进；现在“主流程”和“栏目流程”可以直接用 sub flow 建模，而不是继续把整条栏目链路塞进一个大 chunk。
- **Sub Flow 组合能力**
  - 现在可以把“栏目生成”这种天然重复出现的业务子流程，抽成独立的 TriggerFlow，再由父 flow 在 `for_each(column)` 中重复调用。
  - 对本项目的意义：
    - 父 flow 只保留日报级编排，职责更稳定
    - 栏目流程可以单独测试、单独可视化、单独导出配置
    - 后续如果新增“快讯栏目”“深度栏目”“地区栏目”，可以直接复用或派生子流程，而不是继续复制粘贴父流程节点
    - 主流程和子流程之间通过 `capture / write_back` 显式传递输入、状态和资源，边界比闭包式调用清晰得多
- **结构化输出契约**
  - 现在 outline、pick、summarize、write column 都直接在 YAML prompt 里声明输出结构。
  - 对本项目的意义：少写很多手工解析代码，步骤之间的接口更清晰，调 prompt 时更可控。
- **内置 Search / Browse 工具**
  - 默认直接使用 Agently v4 提供的 Search / Browse，而不是沿用 v3 里项目自带的工具实现。
  - 对本项目的意义：减少项目自维护基础设施代码，同时又保留了 `./tools` 层，方便用户自己替换实现。
- **Runtime resources 与 state 命名空间**
  - 通过 TriggerFlow runtime resources 注入 `logger`、`search_tool`、`browse_tool`，通过 runtime state 保存 `request`、`outline`、中间结果。
  - 对本项目的意义：把“依赖注入”和“流程状态”拆开，chunk 代码更薄，也更容易维护。
- **环境变量感知的 settings**
  - 使用 Agently v4 的 `set_settings(..., auto_load_env=True)` 配合 `${ENV.xxx}` 占位符。
  - 对本项目的意义：`base_url`、`model`、`api_key` 都可以按环境切换，不需要改代码，也更适合本地开发和部署。

### 这些改动对项目整体的意义

- 对 v3 用户来说，产品级行为仍然熟悉，但项目结构已经从“单体 workflow 脚本”变成了更清晰的 app / workflow / tools / prompts 分层。
- 更多能力直接复用了 Agently v4 原生机制，而不是继续在项目里堆自定义胶水代码。
- 真正的并发执行现在成为默认能力。v3 版本整体上仍是串行 workflow，而 v4 可以通过 TriggerFlow 并发跑栏目和栏目内摘要，直接改善总耗时。
- 后续无论是替换工具、调整提示词，还是演进工作流步骤，风险都比 v3 结构更低；主流程和栏目流程也终于恢复成了清晰的父子结构。
- 这也意味着工作流演进可以按层进行：日报级逻辑改父 flow，栏目级逻辑改 sub flow，二者不必总是一起变动。

## 说明

- Agently v4 要求 Python `>=3.10`
- 本项目要求 Agently `>=4.0.8.3`
- 模型配置现在使用 Agently v4 的 `auto_load_env=True` 和 `${ENV.xxx}` 占位符
- `tools/` 默认封装 Agently v4 内置工具；如果你要接自己的搜索或抓取方案，只需要替换这里的工厂函数
- `workflow/` 现在按业务边界拆成主 flow、栏目 sub flow、报告级 chunks、栏目级 chunks
- `news_collector/` 现在承担 app/integration 层职责，负责配置、模型装配和 CLI 入口支持
- 当前仓库里的 [`SETTINGS.yaml`](./SETTINGS.yaml) 默认开启 `BROWSE.enable_playwright: true`，因为很多新闻页面只有在真实浏览器环境下才能抓到可用正文
- 如果你不想额外安装 Playwright，可以手动把 `BROWSE.enable_playwright` 改成 `false`，但动态站点、受保护页面和部分媒体站的抓取质量会明显下降
- 新版配置优先读取 `MODEL / SEARCH / BROWSE / WORKFLOW / OUTLINE / OUTPUT` 结构，同时兼容部分旧版 v3 配置键，例如 `MODEL_PROVIDER`、`MODEL_URL`、`MODEL_AUTH`、`MODEL_OPTIONS`、`MAX_COLUMN_NUM`、`USE_CUSTOMIZE_OUTLINE`
