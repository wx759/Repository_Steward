# Repository Steward

Repository Steward 是一个面向本地 Git 仓库的 AI 代码维护助手。用户先绑定一个仓库，再以持续会话的方式让 Steward 阅读代码、修改文件、运行命令和测试；复杂任务可以委派给短生命周期 Worker，并由统一 Reviewer 验收。

项目基于 FastAPI、LangChain `create_agent`、LangGraph Checkpoint 和 Next.js 构建，支持阿里云百炼、智谱、DeepSeek、OpenAI 等 OpenAI-compatible 模型服务。

当前设计重点是：

- 一个 Session 始终绑定一个 Repository，工具不能越过仓库边界；
- 完整聊天归档、活动 Checkpoint 和长期 Memory 分开保存；
- 长对话通过分层压缩继续工作，而不是无限堆积上下文；
- 限流、过载、上下文超限和输出截断可以按故障类型恢复；
- Steward 是唯一长期 Agent，Worker 和 Reviewer 只处理一次受限任务。

## 核心能力

- Repository 发现、注册、选择与 Session 固定绑定；
- 基于 LangChain `create_agent` 的多轮工具调用；
- 仓库内 `read_file`、`write_file`、`terminal` 工具；
- 复杂任务的 `delegate_task`、统一 PermissionScope、结构化命令和统一 Review；
- FastAPI + SSE 流式文本、工具轨迹、恢复状态与 Run 状态；
- JSON 完整会话归档和 SQLite LangGraph Checkpoint；
- 按用户与仓库隔离的 SQLite 长期记忆；
- L3 → L1 → L2 → L4 分层上下文压缩；
- 限流退避、服务过载恢复、备用模型、强制压缩与自动续写；
- 每次用户请求独立 Run，以及不依赖 SSE 断开的主动中断；
- Next.js 仓库选择、会话历史、任务进度和 Markdown 消息界面。

## 整体运行流程

```text
选择 Repository
  ↓
第一次真实消息发送时创建 Session，并永久绑定 workspace_id
  ↓
检查该 Session 是否已经存在 LangGraph Checkpoint
  ├─ 不存在：从完整 Session JSON 注入一次历史种子
  └─ 已存在：直接恢复 Checkpoint，不重复注入完整历史
  ↓
按“全局用户 + 当前仓库”范围选择相关长期 Memory
  ↓
在模型调用前执行上下文预算与分层压缩
  ↓
Steward ↔ read_file / write_file / terminal / delegate_task
  ↓
模型异常时进入分类恢复；复杂任务由 Worker 执行并由 Reviewer 验收
  ↓
通过 SSE 返回文本、工具轨迹、恢复状态和 Run 状态
  ↓
保存完整 Session JSON 与活动 Checkpoint
  ↓
从本轮用户原始信息中提取可长期复用的 Memory
```

## Repository 与 Session 绑定

### Workspace Root

`WORKSPACE_ROOT` 是 Repository Steward 允许访问的仓库总目录。例如：

```dotenv
WORKSPACE_ROOT=/Users/your-name/VSCodeProject
DEFAULT_WORKSPACE_RELATIVE_PATH=codeAgent
```

前端可以发现 `WORKSPACE_ROOT` 下最多三层目录中的 Git 仓库，也可以手动填写绝对路径添加仓库。后端最终会把路径转换为相对于 `WORKSPACE_ROOT` 的 `relative_path`，并持久化到：

```text
backend/workspaces.json
```

注册表不保存依赖某台机器的宿主机绝对路径。每次使用时，后端都会基于当前 `WORKSPACE_ROOT` 重新解析并验证真实路径。

### 安全边界

Workspace 必须满足：

1. 路径存在且是目录；
2. 路径位于 `WORKSPACE_ROOT` 内；
3. 前端添加仓库时要求存在 `.git`；
4. Repository Steward 自身源码仓库不会出现在可选列表中，也不能创建目标 Session；
5. Repository 工具以所选仓库为根目录，拒绝 `../` 等路径越界。

Agent 内部的 `skills/`、`workspace/` 等系统资源不属于目标仓库。仓库目录、文件和 Git 状态必须来自针对当前 Repository 的工具输出，不能把内部能力配置描述成用户仓库内容。

### Session 不可换仓库

Session schema 当前为 v3，并保存固定的 `workspace_id`。一个 Session 一旦创建，不能中途切换到另一个仓库；需要处理其他仓库时，应创建新任务。

前端的“新任务”最初只是本地草稿。只有第一次真实消息发送时才创建后端 Session，因此连续点击“新任务”不会生成空白历史；后端列表也会过滤零消息 Session。

## 三种持久化状态

项目刻意不使用一份“万能历史”：

| 数据 | 存储位置 | 用途 | 生命周期 |
|---|---|---|---|
| Session 归档 | `backend/sessions/*.json` | UI 展示、审计、完整消息与工具记录 | 单个 Session |
| LangGraph Checkpoint | `backend/checkpoints.sqlite` | 下一轮模型继续工作的活动状态 | 单个 Session / thread |
| 长期 Memory | `backend/memory.sqlite` | 跨 Session 复用稳定偏好和项目事实 | 用户全局或单个 Repository |
| Run / Task | `backend/runs/*.json` | 委派任务、Worker 和 Review 状态 | 单个 Session 的任务运行 |

Session 归档不会被上下文压缩改写；Checkpoint 可以保存压缩后的活动上下文；Memory 则按当前请求选择后临时注入。

## Checkpoint 恢复与首次注入

每个 Session 使用自己的 `session_id` 作为 LangGraph `thread_id`。后端启动时通过 `AsyncSqliteSaver` 打开 `backend/checkpoints.sqlite`，Agent 每轮执行状态由 LangGraph 自动持久化。

请求进入时会调用 `load_checkpoint_seed(session_id)`：

```text
Checkpoint 已存在
  → 返回空 seed
  → LangGraph 直接恢复活动状态

Checkpoint 不存在
  → 从 backend/sessions/<session_id>.json 读取完整兼容历史
  → 仅注入一次，建立初始 Checkpoint
```

这套机制解决两种场景：

- 正常连续对话不会在每轮重复塞入完整 Session 历史；
- 老 Session、迁移数据或 Checkpoint 丢失时，可以由 Session JSON 重建一次活动状态。

上下文压缩产生的 `RemoveMessage`、摘要和保留的最近工作现场会写入 Checkpoint，但不会覆盖 Session JSON。删除 Session 时，后端会同时删除对应 JSON 和 LangGraph thread，避免残留状态在同一 Session ID 下被再次恢复。

## 上下文压缩

代码任务的上下文不仅包含用户对话，还包含源码、日志、测试输出和工具调用。项目在每次主模型调用前执行同一条分层管线：

```text
L3 大工具输出落盘
  ↓
L1 裁剪消息中段
  ↓
L2 压缩较旧工具结果
  ↓
L4 必要时生成工作摘要
```

### L3：大工具输出落盘

当最近工具结果总大小超过预算时，系统优先把最大的完整输出保存到：

```text
backend/.task_outputs/tool-results/<session_id>/
```

活动上下文只保留文件路径和预览。完整日志仍然可以重新读取，但不会持续占用模型窗口。

### L1：裁剪消息中段

消息数量超过上限时，系统保留开头的约束信息和最近工作现场，用占位消息替换中间历史。裁剪边界会保护 `AIMessage(tool_calls)` 与对应 `ToolMessage` 调用组，避免形成模型无法接受的残缺工具协议。

### L2：压缩旧工具结果

最近若干条工具结果保留完整正文，更早的结果替换为固定占位文本。`tool_call_id` 和消息结构仍然保留；旧证据再次重要时，Agent 应重新执行工具获取。

### L4：生成工作摘要

前三层处理后，如果估算 Token 数加预留空间仍超过阈值，系统才调用总结模型。摘要要求保留：

- 当前目标与用户约束；
- 关键发现和决定；
- 已读取、修改的文件；
- 验证结果与尚未完成的工作；
- 被否决方案及原因。

摘要会和预算允许的最近消息共同构成新的活动上下文，并持久化到 Checkpoint。L4 是有损压缩，因此关键结论仍应通过仓库工具重新核验。

## 长期记忆

长期 Memory 不等于聊天历史，也不等于 Agent 系统提示词。它只保存用户明确表达、未来仍可能有用的信息。

### Memory 类型与状态

| 类型 | Scope | 示例 |
|---|---|---|
| `user` | 全局 | 稳定的沟通偏好、协作方式 |
| `project` | 当前 Repository | 项目长期约束、技术约定 |
| `feedback` | 当前 Repository | 用户对已有做法的明确纠正 |
| `reference` | 当前 Repository | 后续需要持续参考的资料或事实 |

每条 Memory 有 `active`、`superseded`、`archived` 三种状态。信息被更正时，旧记录不会直接消失，而是保留状态演进。

### 请求前：选择与注入

每轮开始前，Selector 只会看到：

- 当前用户请求；
- 全局 `user` Memory；
- 当前 `workspace_id` 下的 `project`、`feedback`、`reference` 元数据。

Selector 默认最多选择 5 条，再读取正文并以 `<long-term-memory>` 数据块临时追加到本轮 System Message。Memory 正文不会写入用户消息、Session JSON 或 LangGraph Checkpoint，也不会泄漏到其他仓库。

选择模型失败时会退回本地关键词匹配；整个选择流程失败时，本轮会在没有 Memory 的情况下继续。

### 请求后：提取与更新

主回答成功后，Extractor 从压缩前保存的本轮原始消息快照中提取长期信息。候选 Memory 必须能引用用户原文作为证据，可以执行创建、替代或归档。

以下内容不会被保存：

- 一次性任务步骤和临时进度；
- Agent 自己推断、但用户没有明确表达的内容；
- API Key、Token 等敏感信息；
- 与现有 Memory 重复且没有新增价值的内容。

Memory 选择与提取使用隔离的旁路模型调用。旁路失败只记日志，不会把已经完成的主任务改成失败。前端提供按 Repository scope、类型和状态筛选的只读审核界面，可查看记忆正文与用户原文证据，并归档错误或过期的 active Memory。

## 故障恢复

模型错误先分类，再决定恢复策略：

| 故障类型 | 处理方式 |
|---|---|
| 429 限流 | 尊重 `Retry-After`，否则指数退避并加入随机抖动 |
| 529 / 服务过载 | 退避重试；达到阈值后可切换备用模型 |
| 5xx 服务错误 | 有上限的退避重试 |
| Timeout / Connection | 有上限的退避重试 |
| Prompt Too Long | 强制执行一次现有 L4，再用压缩状态重试 |
| Authentication / Bad Request | 不做无意义重试，返回明确错误 |
| 输出长度截断 | 保留已有文本，内部续写并合并最终回答 |

恢复过程通过 `recovery` SSE 事件通知前端，例如限流等待、服务过载或上下文压缩；这些临时提示不会写入永久聊天历史。

### 上下文超限

本地 Token 预算只是估算。如果 Provider 仍返回上下文超限，恢复中间件会强制调用同一个 L4 算法一次，再重试原请求。同一轮不会无限重复强制压缩；压缩后仍然超限时，会返回明确错误。

### 输出截断与自动续写

模型返回 `length`、`max_tokens` 或 `max_output_tokens` 时，系统会保留已有输出，加入内部续写指令，并使用更高输出上限继续生成。多段内容最终合并为一条助手消息，内部续写提示不会进入 Session 历史。

默认最多续写 3 次。仍未完成时，已有文本会以 `status: incomplete` 保存，前端提示用户可以继续。

### 备用模型

配置 `LLM_FALLBACK_MODEL` 后，主模型连续过载达到恢复条件时，可切换到同一 Provider、Base URL 和 API Key 下的备用模型。它适合相同兼容接口中的模型降级，不是跨 Provider 路由。

## Run 生命周期与主动中断

每次用户请求都会在 Agent 启动前创建独立 Run，而不再只在发生 Worker 委派时创建。Run 只记录 `run_id`、`session_id`、`workspace_id`、目标、状态、时间和错误原因；聊天消息仍归 Session，LangGraph 内部消息仍归 Checkpoint。

Run 状态包括 `running`、`completed`、`interrupted` 和 `failed`。同一 Session 同时只允许一个 `running` Run。

前端运行期间，发送按钮会变成停止按钮。点击后调用 `POST /api/runs/{run_id}/cancel`，后端将 Run 标记为 `interrupted`，并向实际执行协程发送取消信号，而不是只关闭 SSE。

中断时不会保存模型生成到一半的文本，也不会执行本轮 Memory 提取。Session 保存用户消息、已经完整配对的工具调用/结果，以及固定助手消息“本次任务已中断”。可能含有未配对 tool call 的活动 Checkpoint 会被删除；下一轮从清洗后的 Session 历史重新建立合法 Checkpoint，因此 Session 可以继续聊天。

## Steward、Worker 与 Reviewer

系统只有一个长期存在的 Steward。它拥有 Session、Checkpoint、上下文压缩和长期 Memory，并直接处理普通问答、仓库探索和小型改动。

复杂任务可以通过 `delegate_task` 串行委派给短生命周期 Worker：

```text
Steward 创建 TaskSpec
  ↓
仓库规则 ∩ 角色规则 ∩ TaskSpec 权限申请
  ↓
生成本次 Worker 的 PermissionScope
  ↓
记录 Worker 执行前的 Git 文件状态
  ↓
Backend / Frontend / Test / General Worker
  ↓
计算任务产生的真实变更并检查是否越权
  ├─ 越权：直接判定失败，不进入 Reviewer
  └─ 未越权：Reviewer 只检查质量和验收标准
       ├─ 通过：返回 Steward
       └─ 失败：携带 feedback 返修，最多两轮
```

Worker 只收到 `TaskSpec`、Reviewer feedback 和当前 Repository 的受限工具，不继承 Steward 完整对话、Checkpoint 或 Memory。所有 Worker 工具共享同一个 `PermissionScope`：

- Repository Policy 规定整个系统不可访问的敏感路径，例如 `.git`、`.env*`、`*.pem` 和 `*.key`；
- Role Policy 规定角色上限，例如 Backend Worker 最多写 `backend/**`，Frontend Worker 最多写 `frontend/**`；
- Task Policy 由 `allowed_paths` 和 `allowed_commands` 表达，只能在角色上限内进一步缩小权限；
- General Worker 必须显式申请写入路径，不会因为角色是 `general` 就自动获得 `**`。

最终权限采用交集而不是覆盖。例如 Backend Worker 申请 `backend/auth/**` 时，只能修改该子目录；即使申请 `**` 或 `frontend/**`，也不能扩大 Backend Role 的 `backend/**` 上限。

Worker 不再拥有接收任意字符串的 `terminal`。它只能调用结构化的 `run_command`：命令名限定为 `pytest`、`ruff_check`、`mypy`、`npm_test`、`npm_lint` 或 `npm_build`，并且还必须出现在本次 Task 的 `allowed_commands` 中。命令通过参数数组和 `shell=False` 启动；目标只接受仓库相对路径，不能传 Shell 运算符或任意命令行参数。

系统不会信任模型返回的 `changed_files`。委派开始前，`GitChangeTracker` 记录 tracked 与 untracked 文件的内容指纹；Worker 每轮结束后再次采集，计算相对于任务开始时的真实新增、修改和删除文件。真实变更会覆盖模型自报结果，再交给 `PermissionScope` 检查。这样用户在任务开始前已有的未提交修改不会被错误归到 Worker 名下。

权限检查和质量审核彼此分离：`PermissionScope` 与 `GitChangeTracker` 判断“有没有越权”，Reviewer 只判断“实现是否满足验收标准”。Reviewer 没有写文件工具，检查命令也使用相同的结构化命令入口。

Worker 最终统一返回：

```text
status
summary
changed_files
tests_run
issues
```

Worker 内部消息和工具轨迹不会进入 Steward 的长期上下文，Steward 只接收结构化结果。Run/Task 状态保存到 `backend/runs/*.json`，前端按顺序展示执行与 Review 状态。

## 本地启动

### 环境要求

- Python 3.11 或兼容版本；
- Node.js 20+ 和 npm；
- 一个 OpenAI-compatible 模型 API Key。

### 公共配置

先复制后端配置文件：

```bash
cp backend/config/.env.example backend/config/.env
```

Windows PowerShell 使用：

```powershell
Copy-Item backend/config/.env.example backend/config/.env
```

然后编辑 `backend/config/.env`，填写模型服务配置：

```dotenv
LLM_PROVIDER=bailian
LLM_MODEL=qwen3.7-plus
LLM_API_KEY=请填写你的百炼_API_Key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

`WORKSPACE_ROOT` 应填写所有待维护仓库的共同父目录，`DEFAULT_WORKSPACE_RELATIVE_PATH` 填写当前项目相对于该目录的路径。例如：

macOS：

```dotenv
WORKSPACE_ROOT=/Users/你的用户名/VSCodeProject
DEFAULT_WORKSPACE_RELATIVE_PATH=codeAgent
```

Windows：

```dotenv
WORKSPACE_ROOT=C:\Users\你的用户名\VSCodeProject
DEFAULT_WORKSPACE_RELATIVE_PATH=codeAgent
```

当前前端会隐藏 Repository Steward 自身仓库。

### macOS

推荐使用 Miniconda / Anaconda 创建后端环境：

```bash
conda create -n codeagent python=3.11 -y
conda activate codeagent
pip install -r backend/requirements.txt
```

如果终端提示需要初始化 Conda：

```bash
conda init zsh
exec zsh
conda activate codeagent
```

安装前端依赖：

```bash
cd frontend
npm install
cd ..
```

使用 macOS 服务脚本启动：

```bash
./start_services.sh start
```

其他管理命令：

```bash
./start_services.sh status
./start_services.sh restart
./start_services.sh stop
```

脚本优先使用 `backend/.venv/bin/python`；如果该文件不存在，则使用 Conda 的 `codeagent` 环境。

### Windows

在项目根目录使用 PowerShell 创建后端虚拟环境并安装依赖：

```powershell
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

安装前端依赖：

```powershell
Set-Location frontend
npm.cmd install
Set-Location ..
```

启动前后端服务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_services.ps1
```

其他管理命令：

```powershell
.\start_services.ps1 -Status
.\start_services.ps1 -Restart
.\start_services.ps1 -Stop
```

如果当前 PowerShell 的执行策略不允许直接执行 `.ps1`，可以继续使用带有 `-ExecutionPolicy Bypass` 的完整命令。

### 访问地址

- 前端：<http://127.0.0.1:7788>
- 后端：<http://127.0.0.1:8002>
- 后端健康检查：<http://127.0.0.1:8002/health>

两个服务脚本都会把标准输出、错误日志和进程状态保存在 `.repository_steward/dev_services/`。

首次使用时，在左侧 Workspace 区域选择自动发现的 Git 仓库，或点击 `+` 输入仓库路径。

## Docker 启动（可选）

虽然本地开发推荐 Conda，但项目仍保留 Docker Compose：

```bash
WORKSPACE_HOST_ROOT=/Users/你的用户名/VSCodeProject \
DEFAULT_WORKSPACE_RELATIVE_PATH=codeAgent \
docker compose up --build -d
```

容器内统一使用 `/workspaces`，宿主机目录由 `WORKSPACE_HOST_ROOT` 映射。Workspace 注册表只保存相对路径，因此不会把 macOS/Windows 的宿主机绝对路径写入容器状态。

## 关键配置

完整默认值见 `backend/config/.env.example`。

| 配置 | 作用 |
|---|---|
| `LLM_PROVIDER` | `zhipu`、`bailian`、`deepseek`、`openai` |
| `LLM_MODEL` | 主模型名称 |
| `LLM_API_KEY` | OpenAI-compatible API Key |
| `LLM_BASE_URL` | Provider 兼容接口地址 |
| `LLM_FALLBACK_MODEL` | 同 Provider 下的备用模型 |
| `WORKSPACE_ROOT` | 所有可选 Repository 的父目录 |
| `DEFAULT_WORKSPACE_RELATIVE_PATH` | 默认仓库相对路径 |
| `CONTEXT_*` | Token、消息、工具输出和摘要预算 |
| `MEMORY_*` | Memory 开关、选择数量、超时和提取限制 |
| `RECOVERY_*` | 重试、退避、续写次数和输出 Token 上限 |

关闭长期 Memory：

```dotenv
MEMORY_ENABLED=false
```

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 后端健康检查 |
| `GET` | `/api/workspaces/config` | 查看 Workspace Root |
| `GET` | `/api/workspaces/discover` | 发现 Git 仓库 |
| `GET/POST` | `/api/workspaces` | 查询或注册 Repository |
| `GET/POST` | `/api/sessions` | 查询或创建绑定仓库的 Session |
| `GET` | `/api/sessions/{id}/history` | 获取 UI 展示历史 |
| `PUT/DELETE` | `/api/sessions/{id}` | 重命名或删除 Session |
| `POST` | `/api/chat` | SSE 或非流式聊天 |
| `GET` | `/api/runs?session_id=...` | 查询委派 Run / Task 状态 |
| `POST` | `/api/runs/{run_id}/cancel` | 主动中断正在执行的 Run |

## 项目结构

```text
backend/
├── api/                    # Chat、Session、Workspace、Run API
├── config/                 # 模型与运行参数
├── delegation/             # TaskSpec、Worker、权限、Reviewer、WorkerResult
├── graph/                  # Steward Agent、LLM 与 create_agent 装配
├── memory/                 # Store、Selector、Extractor、注入 Middleware
├── middleware/             # Context Management 与 Error Recovery
├── service/                # Session、Workspace、Run、压缩、恢复等服务
├── tools/                  # Repository 工具和 Skill 扫描
├── workspace/              # Steward 内部 System Prompt 组件
├── sessions/               # 完整 Session JSON（运行时生成）
├── runs/                   # 委派 Run / Task JSON（运行时生成）
├── checkpoints.sqlite      # LangGraph 活动状态（运行时生成）
├── memory.sqlite           # 长期 Memory（运行时生成）
└── app.py                  # FastAPI 入口

frontend/
├── src/app/                # Next.js 页面和全局样式
├── src/components/         # Sidebar、Chat、Run Progress 等组件
└── src/lib/                # API 客户端和前端状态
```

## 测试与构建

后端测试：

```bash
conda run -n codeagent pytest backend/tests -q
```

前端生产构建：

```bash
cd frontend
npm run build
```

测试覆盖 Workspace 隔离、Session schema、Checkpoint 恢复、Memory scope 与证据约束、分层上下文压缩、工具调用组完整性、PermissionScope 权限求交、结构化命令、Git 真实变更检测、Reviewer 返修、异常分类、退避重试、强制 L4 和输出续写。

## 当前边界

- SQLite、JSON 和本地文件面向单机运行，尚未处理多实例并发一致性；
- Token 数量是本地近似估算，Provider 仍可能给出不同的上下文判断；
- Memory Selector 与 Extractor 默认复用同一模型服务，但调用上下文相互隔离；
- 长期 Memory 前端目前只支持查看、筛选和归档，不支持人工创建、任意编辑或物理删除；
- L4 摘要是有损压缩，重要仓库事实必须重新通过工具确认；
- 故障恢复只处理模型调用，不会自动修复任意终端命令或业务错误；
- Worker 当前串行委派，未实现并行 DAG、远程执行和分布式队列；
- Worker 仍直接修改当前 Workspace；Git 越权检测会将任务判为失败，但不会自动回滚文件，以免覆盖用户原有未提交修改；
- 结构化命令消除了任意 Shell 字符串入口，但测试或构建程序本身仍可能产生副作用；执行不可信代码时仍应增加容器或 Git worktree 隔离；
- Steward 处理小任务时仍使用普通 Repository 工具，不经过 Worker PermissionScope；使用前应检查工具调用记录与 `git diff`。
