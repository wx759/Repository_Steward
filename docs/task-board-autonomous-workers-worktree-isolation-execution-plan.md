# Task Board + Autonomous Workers + Worktree Isolation：现状分析与最小实施方案

> 状态：已确认并实施  
> 编写日期：2026-08-17  
> 参考：[s17_autonomous_agents](https://github.com/wx759/learn-claude-code/tree/main/s17_autonomous_agents)、[s18_worktree_isolation](https://github.com/wx759/learn-claude-code/tree/main/s18_worktree_isolation)

## 1. 目标与首版边界

本次在现有 Repository Steward 上增加一条小型的多 Agent 编排路径：用户仍只与现有 Agent（下文称 Leader）对话；Leader 通过工具创建 Run、拆分有依赖关系的 Task、启动 Run、等待并读取结果；每个 Run 最多启动两个长期存活到 Run 结束的 Worker。Worker 不由 Leader 逐项分配任务，而是在空闲时自行扫描 Task Board、原子认领可执行 Task、完成后继续认领下一项。

首版包含：

- 持久化 Run、Task、WorkerContext；
- Task 依赖检查与并发安全的 claim；
- 每个 Run 最多两个后台 Worker；
- 每个 Worker 一个独立 branch 和 git worktree；
- Worker 的 `read_file`、`terminal` 全部固定在自己的 `workspace_root`；
- Worker 每完成一个 Task 后继续扫描；
- Worker 可在自己的分支提交 commit；
- Run 结束后 Leader 能读取 Task、branch、commit 和失败信息并汇总；
- 后端重启后恢复尚未结束的 Run；
- 覆盖用户列出的五项最小自动化测试。

首版明确不做：

- 自动 merge、rebase、cherry-pick 或冲突解决；
- Worker 间消息总线、聊天或协商协议；
- 动态扩缩 Worker 数量；
- Task 优先级、重试策略、租约、超时抢占；
- Task Board 前端页面；
- 跨机器或多后端实例调度；
- 自动删除含工作成果的 worktree/branch。

## 2. 当前项目结构与真实调用链

与本次改造直接相关的当前结构：

```text
backend/
├── app.py                         # FastAPI lifespan；创建 SQLite checkpointer
├── api/chat.py                    # 用户与 Leader 的 Chat/SSE 入口
├── graph/
│   ├── agent.py                   # 现有 Leader AgentManager
│   ├── agent_factory.py           # create_agent(...) 的统一构造点
│   └── llm.py                     # LLM 构造
├── middleware/
│   ├── context_management.py      # Context Compact
│   └── error_recovery.py          # 模型错误/截断恢复
├── memory/                        # 现有长期 Memory
├── service/
│   ├── session_manager.py         # sessions/*.json 聊天持久化
│   ├── prompt_builder.py          # Leader system prompt
│   └── context_manager.py         # Compact 的纯逻辑
├── tools/
│   ├── read_file_tool.py          # 构造时绑定 root_dir
│   └── terminal_tool.py           # subprocess cwd 绑定 root_dir
├── tests/                         # pytest 测试
├── checkpoints.sqlite            # Leader LangGraph checkpoint
└── memory.sqlite                 # Memory 数据
```

当前一次用户请求的主链为：

```text
POST /api/chat
  -> api/chat.py 读取 Session JSON 与 checkpoint seed
  -> AgentManager.astream(...)
  -> AgentManager._build_agent()
  -> agent_factory.create_agent_from_config(...)
  -> LangGraph: LLM -> tool -> LLM -> ...
  -> SSE token/tool/done
  -> SessionManager 保存标准 LangChain messages
```

需要保持不变的边界：

- `sessions/*.json` 继续只负责完整聊天历史；
- `checkpoints.sqlite` 继续只负责 Leader 的 LangGraph 活跃上下文；
- `memory.sqlite` 和 Memory middleware/extractor 继续服务 Leader；
- Context Compact、Error Recovery 和现有 SSE 事件语义不改变；
- 前端聊天和 Session API 不需要为首版改造。

## 3. 对参考实现的取舍

### 3.1 复用 s17 的部分

s17 的核心语义适合本项目：Worker 完成一项工作后进入 IDLE，扫描 `pending + owner 为空 + 依赖全部 completed` 的 Task，认领后回到 WORK，完成后再次扫描。

不直接照搬的部分：s17 教学版使用 JSON 文件且没有文件锁，只检查 owner，存在两个 Worker 同时读到同一个 pending Task 的竞争窗口。本项目的验收明确要求“两个 Worker 不会 claim 同一个 Task”，因此首版使用 SQLite 写事务，在同一事务内重新读取条件并更新 owner/status。

### 3.2 复用 s18 的部分

s18 证明工具隔离必须落实到真实 cwd/root，而不能只在 prompt 中提醒 Worker。本项目现有 `ReadFileTool(root_dir=...)` 与 `TerminalTool(root_dir=...)` 已经天然支持这一点，可以直接复用工具类，只为每个 Worker 传入不同的根目录。

与 s18 的区别：s18 教学版把 worktree 绑定到 Task；本需求明确给出 `WorkerContext(run_id, worker_id, workspace_root, branch_name)`，所以首版采用“一名 Worker 一个 worktree”。同一 Worker 连续认领多个 Task 时始终在自己的 branch 上工作，并可形成多个 Task commit。

## 4. 建议的最小架构

```text
用户
  -> 现有 Chat/SSE
  -> Leader（保留 Memory / Compact / Recovery）
       ├── create_run / create_task / list_tasks / get_task
       ├── start_run / get_run / wait_for_run
       └── 读取最终 Task + branch + commit，生成汇总

RunCoordinator（应用级后台编排器）
  ├── CoordinationStore（orchestration.sqlite）
  ├── WorktreeManager（只负责 git branch/worktree）
  ├── worker_01 loop
  │    ├── Worker Agent 独立 checkpoint thread
  │    ├── workspace_root = .worktrees/<run>_worker_01
  │    └── scan -> atomic claim -> work -> complete -> scan
  └── worker_02 loop
       ├── Worker Agent 独立 checkpoint thread
       ├── workspace_root = .worktrees/<run>_worker_02
       └── scan -> atomic claim -> work -> complete -> scan
```

职责边界：

- `CoordinationStore` 只处理数据规则、事务和状态聚合，不调用 LLM/Git；
- `WorktreeManager` 只执行经过参数校验的 `git worktree`/`git branch` 命令；
- `WorkerAgentFactory` 复用现有 LLM、`create_agent_from_config`、checkpoint、`ReadFileTool`、`TerminalTool`；
- `RunCoordinator` 负责启动/恢复两个 asyncio Worker loop，并在 Task 状态变化后计算 Run 是否结束；
- Leader 只拥有管理工具；Worker 只拥有执行工具，二者不共享管理权限。

## 5. 持久化模型

新增独立的 `backend/orchestration.sqlite`。它复用项目已经使用的 SQLite 技术，但不把业务表写进 LangGraph 的 checkpoint 数据库，避免两个生命周期耦合。

### 5.1 Run

```python
Run:
    id
    goal
    status          # draft | running | completed | failed
    base_commit
    created_at
    started_at
    finished_at
    summary
    error
```

`draft` 阶段允许 Leader 创建 Task；`start_run` 成功创建两个 worktree 后进入 `running`。全部 Task 为 `completed` 时 Run 为 `completed`；只要出现 `failed` 且已无进行中的 Task，Run 为 `failed`。

### 5.2 Task

用户要求的字段全部保留：

```python
Task:
    id
    run_id
    subject
    description
    status          # pending | in_progress | completed | failed
    owner           # null | worker_01 | worker_02
    blocked_by      # API 层返回 Task ID 列表
```

首版为可审计性补充：

```python
    result
    error
    commit_sha
    created_at
    claimed_at
    completed_at
```

SQLite 内部使用 `tasks` 和 `task_dependencies` 两张表，API/tool 输出时再组装 `blocked_by`。`create_task` 要求依赖 Task 已存在且属于同一 Run；首版不提供修改依赖的接口，因此依赖图只能指向已经创建的 Task，可自然避免环。

### 5.3 WorkerContext

```python
WorkerContext:
    run_id
    worker_id       # worker_01 | worker_02
    status          # starting | idle | working | shutdown | failed
    workspace_root
    branch_name
    thread_id       # run:{run_id}:worker:{worker_id}
    current_task_id
    last_error
    started_at
    updated_at
    stopped_at
```

Run/Task/Worker 状态写入 `orchestration.sqlite`；Leader checkpoint 保持在 `checkpoints.sqlite`；Worker checkpoint 单独放入 `worker_checkpoints.sqlite`，并按建议格式使用独立 thread ID。这样不仅 thread 逻辑隔离，物理 checkpoint 文件也与 Leader 分开。

## 6. Task Board 操作与并发规则

服务层实现用户要求的五个操作：

```text
create_task
list_tasks
get_task
claim_task
complete_task
```

其中 `claim_task` 必须在一个 SQLite `BEGIN IMMEDIATE` 写事务中完成：

1. 按 `task_id + run_id` 重新读取 Task；
2. 确认 `status == pending`；
3. 确认 `owner IS NULL`；
4. 查询 `task_dependencies`，确认所有依赖 Task 都是 `completed`；
5. 更新 `owner`、`status = in_progress`、`claimed_at`；
6. 提交事务并返回认领结果。

如果两个 Worker 同时尝试认领同一 Task，SQLite 会串行化写事务；第二个 Worker进入事务后会看到最新状态并得到“不可认领”，不会覆盖第一个 owner。

`complete_task` 还要验证：调用方 worker 与 owner 一致、Task 当前为 `in_progress`。失败通过内部 `fail_task` 收口；Worker 不获得任意修改其他 Task 状态的能力。

## 7. Leader 与 Worker 工具权限

### 7.1 Leader 工具

保留现有：

```text
read_file
terminal
```

新增最小管理工具：

```text
create_run
create_task
list_tasks
get_task
start_run
get_run
wait_for_run
```

`wait_for_run` 让同一轮 Leader 调用在 Run 到达 terminal 状态后继续，从而真正做到“监控整体任务状态并最终汇总”；它只等待/读取状态，不执行 Worker 工作。

### 7.2 Worker 工具

每个 Worker 只获得：

```text
read_file           # root_dir = WorkerContext.workspace_root
terminal            # cwd = WorkerContext.workspace_root
list_tasks          # 强制限定当前 run_id
get_task            # 强制限定当前 run_id
claim_task          # owner 强制为当前 worker_id
complete_task       # 只能完成自己拥有的 Task
```

Worker 不获得：

```text
create_run
create_task
start_run
wait_for_run
创建/删除 worktree
merge/rebase/cherry-pick
操作其他 Run/Worker
```

这里的限制由“传给 Worker graph 的工具实例集合”和工具内部绑定的 `run_id/worker_id` 同时保证，不依赖 prompt 自觉。

## 8. Worker 生命周期与恢复

每个 Worker 使用应用内 asyncio task 运行以下外层循环：

```text
STARTING
  -> 验证持久化 WorkerContext 与 worktree
  -> IDLE
       -> 扫描当前 Run 可执行 Task
       -> 原子 claim 成功：WORKING
       -> claim 竞争失败：立即重新扫描
       -> 暂无可执行 Task：短轮询等待
  -> WORKING
       -> 用独立 thread_id 调用 Worker Agent
       -> Worker 使用自己的 read_file/terminal
       -> 可 git add + git commit
       -> complete_task
       -> IDLE，继续下一项
  -> Run completed/failed
       -> SHUTDOWN
```

首版使用 1 秒短轮询，不引入文件监听或消息总线。Worker 不因短暂无任务而退出：只要 Run 仍为 `running`，它就保持 idle；Run terminal 后才 shutdown。这满足“Worker 属于 Run，而不是某轮用户对话”。

Worker 每次 Agent 调用结束后，Coordinator 会重新读取 Task：

- 已 `completed`：继续扫描下一项；
- 已 `failed`：记录失败并继续进入 Run 收口；
- 仍为 `in_progress`：说明 Worker 没有调用 `complete_task`，由 Coordinator 将其标为 `failed`，避免 Run 永久卡住。

应用重启时，lifespan 扫描 `running` Run：

- 验证两个持久化 worktree 路径和 branch；
- 将遗留的 `in_progress` Task 按持久化 owner/current_task 交还给原 Worker，使其继续使用原
  branch、worktree 和 checkpoint；
- 重新创建该 Run 的两个 Worker loop；
- 继续使用原 `thread_id` 和 `worker_checkpoints.sqlite`。

首版恢复以“原 Worker 在原 worktree 中重新进入未确认完成的 Task”为准，不实现租约和
exactly-once 外部副作用保证。

## 9. Worktree Isolation 细节

Run 启动时记录当前 `HEAD` 为 `base_commit`，并创建：

```text
worker_01
  branch: <run_id>/worker_01
  path:   <repo>/.worktrees/<run_id>_worker_01

worker_02
  branch: <run_id>/worker_02
  path:   <repo>/.worktrees/<run_id>_worker_02
```

`WorktreeManager` 使用参数列表调用 Git，不拼接 shell 字符串：

```text
git worktree add <path> -b <branch> <base_commit>
```

安全规则：

- `run_id/worker_id` 由系统生成，不接受任意路径文本；
- `workspace_root.resolve()` 必须位于 `<repo>/.worktrees/` 下；
- Run 启动前要求主工作区干净，保证两个 Worker 都从用户当前可见、可复现的同一个 HEAD 开始；
- `.worktrees/` 加入 `.gitignore`；
- Run 结束默认保留 worktree 和 branch，供人工 review；
- 首版不提供删除 worktree 工具，避免误删未合并成果。

Worker commit 约定：在调用 `complete_task` 前先通过 `terminal` 执行 `git add`/`git commit`。`complete_task` 检查 worktree 没有未提交改动并记录当前 `HEAD` 到 `commit_sha`；允许“检查/分析类 Task”无新增 commit，但仍记录当时 HEAD。最终 Leader 汇总 branch、worktree、Task 与 commit，不做 merge。

## 10. 对现有 Agent 的最小接入方式

不重写 `AgentManager` 或现有聊天循环：

1. 保持 `AgentManager.astream()`、Session、SSE、Memory/Compact/Recovery 原样；
2. 扩展 AgentManager 初始化参数，使应用启动时可把 Leader 编排工具追加到现有两个工具之后；
3. 在现有 system prompt 中增加一个短的 Leader Protocol，说明何时创建 Run/Task、启动、等待和汇总；
4. 新建 `WorkerAgentFactory`，内部继续调用 `build_agent_config/create_agent_from_config`；
5. Worker 使用相同 LLM provider 与 Error Recovery；Context Compact 继续复用现有 middleware 逻辑，但使用 Worker 独立 thread；
6. Worker 不接入 Memory selector/extractor，防止并行 Worker 把任务过程写入 Leader 的长期 Memory；Leader 的 Memory 行为完全不变。

Worker prompt 只包含：Worker 身份、当前 Run/Task、workspace/branch、工具边界、必须验证结果、提交约定和完成协议。它不复用 Leader 的管理身份，也不允许创建 Run/Worker 或合并分支。

## 11. 预计文件改动

| 文件 | 动作 | 作用 |
|---|---|---|
| `backend/orchestration/models.py` | 新增 | Run/Task/WorkerContext 数据模型与状态常量 |
| `backend/orchestration/store.py` | 新增 | SQLite schema、CRUD、依赖判断、原子 claim、状态聚合/恢复 |
| `backend/orchestration/worktree.py` | 新增 | 安全创建、验证 branch/worktree |
| `backend/orchestration/coordinator.py` | 新增 | Run 启动/恢复、两个 Worker loop、shutdown |
| `backend/orchestration/worker_agent.py` | 新增 | 复用 `create_agent` 的 Worker graph 和独立 checkpoint thread |
| `backend/orchestration/__init__.py` | 新增 | 对外导出最小接口 |
| `backend/tools/task_tools.py` | 新增 | Leader/Worker 的 Task Board 工具及上下文权限绑定 |
| `backend/tools/run_tools.py` | 新增 | Leader 专属 Run 管理/等待工具 |
| `backend/tools/__init__.py` | 修改 | 分开组装 core、Leader、Worker 工具集合 |
| `backend/service/prompt_builder.py` | 小改 | 加载短 Leader Protocol |
| `backend/workspace/LEADER.md` | 新增 | Leader 拆解、启动、等待、汇总约定 |
| `backend/graph/agent.py` | 小改 | 初始化时接收 Leader 管理工具；现有聊天链不变 |
| `backend/app.py` | 修改 | 创建 store/coordinator 与第二个 checkpointer；启动恢复、关闭 worker tasks |
| `backend/config/config.py` | 小改 | Worker 数量（上限固定 2）、轮询间隔、数据库/worktree 路径配置 |
| `backend/tests/test_task_board.py` | 新增 | 依赖规则与并发 claim |
| `backend/tests/test_worker_lifecycle.py` | 新增 | 自动 claim、连续认领、terminal 状态与恢复 |
| `backend/tests/test_worktree_isolation.py` | 新增 | 临时 Git repo 中验证不同 path/branch/cwd |
| `backend/tests/test_core.py` | 小改 | Leader/Worker 工具权限与现有核心能力回归 |
| `.gitignore` | 小改 | 忽略 worktree 与新增 SQLite 运行文件 |
| `README.md` | 实施后小改 | 补充演示流程、状态文件和人工 review/merge 说明 |

文件名可能在实施中做很小的归并，但模块职责和边界不变。不会修改前端文件。

## 12. 实施顺序

### 第一步：锁定数据规则

先实现纯 store 和模型测试：Run/Task 创建、同 Run 依赖校验、可执行条件、complete/fail、Run terminal 聚合。

验收：不接 LLM/Git 即可验证 Task dependency 的所有状态转换。

### 第二步：完成原子 claim

在 SQLite 写事务内实现 claim，并用两个并发线程/协程对同一 Task 发起认领。

验收：恰好一个成功；另一个得到明确失败；最终只有一个 owner。

### 第三步：实现 worktree 管理

在 pytest 临时目录初始化小型 Git repo，创建两个 Worker branch/worktree，验证路径、branch 与工具 cwd。

验收：两个 Worker 路径和 branch 不同，在各自目录创建文件不会出现在对方 worktree。

### 第四步：实现 Worker 工具与 Agent

按 WorkerContext 构造 `read_file`、`terminal` 和上下文绑定的 Task tools；复用现有 `create_agent`、LLM 和 checkpoint saver。

验收：Worker 工具无法越出 workspace_root，无法操作其他 Run/owner，也不包含管理/merge 工具。

### 第五步：实现自治生命周期

增加 RunCoordinator 与两个 Worker loop。生命周期测试使用 fake task executor/fake graph，不调用真实模型，稳定验证“claim -> complete -> 再 claim”。

验收：Worker 完成第一项后同一 loop 自动取得下一项；有依赖的 Task 在前置完成后解锁；Run 结束后两个 Worker 都 shutdown。

### 第六步：接入 Leader

向现有 Leader 追加 Run/Task 工具和 Leader Protocol；在 FastAPI lifespan 初始化/恢复 Coordinator 与 Worker checkpointer。

验收：用户只通过 Chat 请求即可让 Leader 创建 Run/Task、启动最多两个 Worker、等待 terminal 状态并汇总 branch/commit。

### 第七步：回归和文档

运行全部后端测试，确认 Session、Memory、Compact、Recovery、checkpoint、聊天 API 与原工具继续通过；补充 README 的演示命令和人工 review 流程。

建议验证命令：

```powershell
uv run --project backend python -m pytest backend/tests -p no:cacheprovider
git diff --check
```

真实 LLM smoke test 作为本地 API key 可用时的补充验证，不作为自动测试前提。

## 13. 最小测试清单

必须覆盖：

1. **Task dependency**：B blocked_by A；A 未完成时 B 不可 claim，A 完成后 B 可 claim。
2. **Worker 自动 claim**：启动 Worker loop 后，无 Leader assign 调用也会把可执行 Task 从 pending 推进到 in_progress/completed。
3. **不会重复 claim**：两个 Worker 并发争抢唯一 Task，只有一个成功且 owner 不被覆盖。
4. **连续认领**：同一 Worker 完成第一项后不退出，自动认领并完成第二项。
5. **不同 worktree**：两个 Worker 的 `workspace_root`、branch、`git rev-parse --show-toplevel` 不同，文件修改互不覆盖。

同时增加回归断言：

- 每个 Run 固定且最多两个 Worker；
- Worker tool list 不含管理/merge 能力；
- Worker 只能 list/get/claim/complete 当前 Run 的 Task；
- `read_file` 路径穿越仍被拒绝，`terminal` cwd 是 Worker worktree；
- Run terminal 后 Worker 状态持久化为 shutdown；
- Leader 与 Worker checkpoint thread/file 隔离；
- 原有 Session/Memory/Compact/Recovery 测试继续通过。

## 14. 需要用户决策的点

以下四点会影响实现行为；括号内是建议默认值。

### 决策 1：首版是否需要 Task Board 前端页面

- **建议：不做。** Leader 通过工具操作看板，用户通过聊天看到进度和最终汇总；SQLite 是真实持久化来源。
- 如果需要 UI，则要新增 Run/Task 查询 API、SSE/polling 和前端页面，明显扩大首版范围。

### 决策 2：主工作区有未提交修改时能否启动 Run

- **建议：拒绝启动并提示先提交。** Git worktree 基于 commit，主目录未提交内容不会自动进入 Worker，允许启动容易让 Worker 基于过期代码工作。
- 可选方案是允许启动但明确警告；首版不建议自动 stash/临时 commit，因为这会修改用户 Git 状态。

### 决策 3：Task 完成时的 commit 规则

- **建议：Worker 在 complete 前提交；complete_task 拒绝遗留未提交修改。** 分析/检查类无改动 Task 可完成并记录当前 HEAD。
- 可选方案是允许 completed + dirty worktree，但最终结果不稳定，也难以按 Task 追踪成果。

### 决策 4：Run 等待与最终汇总方式

- **建议：后台 Worker + Leader 的 `wait_for_run` 工具。** Worker 生命周期不依赖 HTTP 回合；同一轮对话中 Leader 可以等待完成并立即汇总，用户之后也可重新询问状态。
- 可选方案是 `start_run` 立即返回、用户稍后主动问状态；实现更短，但不完全满足“Leader 监控并最终汇总”的目标体验。

若你没有额外偏好，建议直接按以上四个默认方案实施。

## 15. 通过后的最终验收形态

一个最小演示应能完成：

```text
User -> Leader: 完成一个包含后端改动和测试的复杂任务
Leader:
  1. create_run
  2. create_task(A)
  3. create_task(B, blocked_by=[A])
  4. create_task(C)
  5. start_run

worker_01 -> 在独立 worktree claim A -> 修改/验证/commit -> complete A
worker_02 -> 在独立 worktree claim C -> 修改/验证/commit -> complete C
worker_01 或 worker_02 -> B 解锁 -> claim B -> 修改/验证/commit -> complete B

Run -> completed -> workers shutdown
Leader -> 汇总每个 Task 的 owner/result/commit，以及两个 branch/worktree，提示人工 review/merge
```

这条链路不会替换现有 Agent，而是在现有 Leader 的工具边界外增加一个小型、可持久化、可测试的自治执行层。

## 16. 实施结果

本方案已按第 14 节的四个建议默认值完成实施。最终落地包括独立的
`orchestration.sqlite`、`worker_checkpoints.sqlite`、两个持久化 Worker loop、事务化
Task claim、Worker 级 branch/worktree、Leader/Worker 权限隔离、重启恢复和人工 review
所需的 branch/commit 汇总。首版 Worktree Run 面向在完整 Git checkout 中启动的本地后端；
现有只包含 `backend/` 的容器仍可用于聊天，但不负责创建宿主机 worktree。

自动测试覆盖 Task dependency、并发 claim、连续认领、固定两个 Worker、不同 worktree/
branch/tool root、权限边界以及现有 Session、Memory、Context Compact、Recovery 和 API 回归。
实施完成时的全量结果为 `50 passed`。
