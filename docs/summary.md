# Repository Steward 十三阶段改造小结

## 阶段一：引入 Workspace

- 新增 `Workspace` 与 JSON 持久化的 `WorkspaceManager`，统一校验仓库存在、目录类型以及必须位于 `WORKSPACE_ROOT` 下。
- Session schema 升级到 v3，新增不可中途切换的 `workspace_id`；API 创建 Session 时必须传入有效 Workspace。
- 新增 `/api/workspaces` 列表与创建接口，并在启动时注册当前项目作为默认 Workspace，旧 Session 会一次性绑定默认 Workspace。
- AgentManager 改为按 Workspace 缓存 Runtime，`read_file`、新增的 `write_file` 和 `terminal` 均以当前 Session 的仓库根目录运行，不再永久绑定 `backend`。
- 增加 Workspace 隔离、越界拒绝和 Session 不可换仓库测试；阶段完成时后端测试为 44 项通过。

## 阶段二：处理 Docker Workspace 映射

- Docker Compose 为后端增加统一 `/workspaces` volume，宿主机根目录通过 `WORKSPACE_HOST_ROOT` 配置。
- 增加 `DEFAULT_WORKSPACE_RELATIVE_PATH`，容器只用相对仓库名解析默认 Workspace，不依赖 Windows/macOS 宿主机绝对路径。
- Workspace 注册表继续只持久化 `relative_path`，每次读取都基于当前 `WORKSPACE_ROOT` 重新解析并校验。
- README 与 `.env.example` 已补充本地和 Docker 两种 Workspace 配置方式。

## 阶段三：调整 Memory 的 Repository Scope

- Memory SQLite schema 新增可空 `workspace_id`，并提供兼容旧数据库的自动迁移与按 scope 的索引。
- User Memory 强制保持全局 scope（`workspace_id = NULL`）；Project、Feedback、Reference 由提取器写入当前 Workspace。
- Steward 召回时只向 Selector 提供“全局 User Memory + 当前 Workspace Memory”，其他仓库的 Project Memory 不会出现在候选目录中。
- Memory 名称唯一性改为按 Workspace scope 判断，允许不同仓库拥有同名项目记忆。
- 新增跨 A/B Workspace 的 Memory 隔离测试；阶段完成时后端测试为 45 项通过。

## 阶段四：保留单 Steward Agent

- 新增明确的 Steward System Prompt 组件，规定它是 Session 唯一长期 Agent，同时负责普通问答、仓库理解、简单修改、复杂规划与委派汇总。
- 明确禁止额外 Router/Leader/General 长期 Agent；简单任务继续直接使用仓库工具，复杂任务才进入 `delegate_task`。
- Session、Checkpoint、Context Compaction 与 Memory 仍只归 Steward 所有，后续 Worker 只作为短生命周期工具执行实例。

## 阶段五：实现 delegate_task

- 新增 LangChain `delegate_task` 工具与强类型 `TaskSpec`，输入包含 role、description、context、allowed_paths 和 acceptance_criteria。
- 工具绑定当前 Repository Workspace，并通过 `DelegationService` 的异步锁拒绝重叠委派，保证一个任务完整返回后 Steward 才能继续调用下一个任务。
- `delegate_task` 已加入每个 Workspace 的 Steward 工具集，简单任务仍可直接使用 read/write/terminal。

## 阶段六：实现 Worker Factory

- 新增 `WorkerFactory`，支持 backend、frontend、test、general 四种角色及独立 System Prompt。
- 每次委派即时创建临时 LangChain Agent，只注入 TaskSpec、Reviewer feedback 与当前 Workspace 工具，不继承 Steward 完整 messages、Checkpoint 或 Memory。
- Worker 调用结束后只保留解析后的结果，临时 Agent 与 messages 不进入 Session。

## 阶段七：增加 Tool Permission Scope

- 为 Worker 增加 `allowed_paths` 写入检查，并提供 backend/frontend/test/general 的默认路径 scope。
- Worker 可读取整个当前仓库，但 `write_file` 越界会被拒绝；所有路径仍受 Workspace 根目录 traversal 防护。
- Reviewer 只拥有 read_file 与测试/检查命令白名单 terminal，不拥有 write_file，shell 组合与修改命令会被拒绝。

## 阶段八：实现统一 Reviewer

- 新增只读 Reviewer，输入 TaskSpec、WorkerResult、git diff 与验收标准，并统一输出 `ReviewResult`。
- Review 失败会把 feedback 传回同角色 Worker 修复，然后重新验收；最多执行两轮 Review，仍失败则把原因返回 Steward。
- 增加返修成功、超过轮次失败以及 Reviewer 禁止修改的自动化测试。

## 阶段九：统一 WorkerResult

- Worker 对 Steward 统一返回 `status`、`summary`、`changed_files`、`tests_run`、`issues` 五个字段。
- 增加严格 JSON 提取与 Pydantic 校验；Worker/Reviewer 输出格式异常会转换成结构化失败，不泄露内部消息轨迹。
- 委派闭环测试验证 Steward 最终只接收结构化 WorkerResult；阶段完成时后端测试为 49 项通过。

## 阶段十：增加轻量 Run / Task

- 新增 JSON 持久化 `RunManager`，记录 run_id、session_id、workspace_id、goal、status 与有序 tasks。
- 每次 delegate_task 自动创建/复用当前 Session Run，记录 Task 的 running/completed/failed、Review pending/passed/failed 以及结构化结果。
- Steward 本轮结束时关闭 Run，失败任务会保留失败状态；新增 `/api/runs?session_id=...` 供前端展示与调试。
- 增加 Run/Task 持久化状态测试；阶段完成时后端测试为 50 项通过。

## 阶段十一：复用现有 Context Compaction

- Steward Runtime 保持现有 L3→L1→L2→L4 ContextManagementMiddleware 与 Checkpoint 持久化，不改变长期会话压缩语义。
- Worker 与 Reviewer 明确不接入完整 Context Compaction：它们每次只处理一个短生命周期 TaskSpec/Review 输入，结束即销毁。
- 委派结果进入 Steward 时只保留 Task 与 WorkerResult，因此 Worker 工具轨迹不会膨胀 Steward 的长期上下文。

## 阶段十二：复用 Error Recovery

- 抽取 AgentManager 的统一 Recovery middleware 构造逻辑，Steward、Worker、Reviewer 均使用相同的 Retry/Backoff、超限恢复、续写与备用模型策略。
- Worker/Reviewer 使用独立短生命周期 `AgentRunContext` 保存本次恢复状态，但不获得 Steward Session history 或长期 Memory。
- 删除各 Agent 自行包裹模型异常的需求；结构化解析失败仍统一转为 WorkerResult/ReviewResult，而模型调用故障由公共中间件处理。

## 阶段十三：前端最小改造

- 新建 Session 区域增加 Repository 下拉选择，创建请求必须携带 workspace_id；历史 Session 继续显示并保持各自绑定仓库。
- 顶部在会话标题下显示 `Repository · branch`，Workspace API 只读获取当前 Git branch。
- 新增轻量 RunProgress，按顺序显示 Backend/Frontend/Test/General Agent 的运行状态与 Review pending/passed/failed。
- 前端通过 `/api/runs` 在选择会话、委派工具结束及整轮结束时刷新状态；保持简单列表，不引入 DAG 或多 Agent 图谱。

## 最终验收

- 后端 pytest：50 项全部通过；Python compileall 与 `git diff --check` 通过。
- 前端 Next.js 生产构建、ESLint 与 TypeScript 检查通过。
- 本地服务重启成功：前端 `127.0.0.1:7788`、后端 `127.0.0.1:8002`。
- Workspace API 返回当前 `codeAgent · main`，创建的验收 Session schema 为 v3 且固定绑定 Workspace。
- 使用阿里云百炼实际调用改造后的 Steward Runtime，成功返回“十三阶段改造验收成功”。

## 后续前端与会话体验改造

- 重新设计为开发工具风格：深色 Repository/Session 侧栏、紧凑顶栏、纯净任务画布与黄绿色状态强调，去除大面积玻璃拟态和冗余卡片。
- `codeAgent` 自身仓库从 Workspace 列表、历史会话和新建 Session API 中隔离，避免 Agent 修改承载自身的项目。
- 增加 Repository 添加弹窗、`WORKSPACE_ROOT` 展示和三层目录内 Git 仓库自动发现，也支持输入合法绝对路径并由后端校验。
- 新任务改为纯前端草稿，第一次真实消息发送时才创建后端 Session；后端列表同时过滤零消息 Session，因此连续点击不会产生空白历史。
- 无仓库时显示明确引导并禁用输入；选择仓库后显示仓库名和分支，再进入真实任务空状态。
- 后端 50 项测试和前端生产构建通过；本地 Workspace/Session 均为空、自身仓库创建 Session 返回 400、首页返回 200。
- 视觉进一步调整为 ChatGPT 风格的克制设计系统：`#171717` 侧栏、白色对话画布、浅灰输入框与边界、`#10a37f` Agent 状态色，并使用 macOS/Windows 系统 UI 字体替代装饰性 Google Font。
