# Repository Steward 面试讲解手册

这份文档用于面试时介绍 Repository Steward 的核心架构，重点回答三个问题：

1. 我是怎么做的；
2. 为什么这么做；
3. 有没有其他实现方式。

## 一句话介绍项目

Repository Steward 是一个面向本地 Git 仓库的持久化代码 Agent。它基于 FastAPI、LangChain `create_agent` 和 LangGraph 构建，支持仓库绑定、多轮工具调用、长期记忆、上下文压缩、Checkpoint 恢复、故障恢复、临时 Worker 委派，以及可主动中断的 Run 生命周期。

这个项目的重点不是简单调用大模型 API，而是解决代码 Agent 长时间运行时的状态管理、上下文增长、仓库隔离和执行可控性问题。

## 一、一次请求是怎么执行的

### 我是怎么做的

用户先选择一个 Repository。第一次真正发送消息时，系统创建 Session，并将 Session 永久绑定到一个 `workspace_id`。

每次用户发送消息，系统执行以下流程：

```text
用户发送消息
  ↓
创建一次独立 Run，状态设为 running
  ↓
获取当前 Session 对应的 Agent Graph
  ↓
以 session_id 作为 LangGraph thread_id 恢复 Checkpoint
  ↓
按当前请求选择相关长期 Memory
  ↓
注入当前 HumanMessage 和动态 Memory Context
  ↓
在模型调用前执行上下文预算与压缩
  ↓
LLM 判断直接回答还是调用工具
  ↓
LLM ↔ Tool 循环，直到模型不再产生 tool_call
  ↓
返回最终 AIMessage
  ↓
Run 更新为 completed 或 failed
  ↓
Session 保存完整聊天记录
Checkpoint 保存 LangGraph 活动状态
Memory Extractor 提取长期信息
```

模型是否继续调用工具由模型输出决定。当模型返回包含 `tool_calls` 的 `AIMessage` 时，LangGraph 进入工具节点；工具返回 `ToolMessage` 后再次调用模型。当模型返回不含 `tool_calls` 的最终 `AIMessage` 时，本轮 Agent invocation 结束。

### 为什么这么做

这样可以复用 LangChain 和 LangGraph 已有的 Agent Loop，不需要自己实现“模型调用—工具执行—再次调用模型”的状态机。

同时，每次用户请求都有独立 Run，因此可以单独记录本轮是否运行、完成、失败或被用户中断，而不会把整个 Session 标记成完成或失败。

### 有没有其他方式

可以自己实现 Agent Loop，例如手动编写：

```python
while True:
    response = llm.invoke(messages)
    if not response.tool_calls:
        break
    tool_results = execute_tools(response.tool_calls)
    messages.extend(tool_results)
```

这种方式控制力更强，但需要自己处理工具调用配对、流式输出、异常恢复、状态持久化和中断一致性。当前项目优先复用 LangGraph，减少重复实现协议层逻辑。

## 二、Session、Run 和 Checkpoint 是什么关系

### 我是怎么做的

我把状态拆成三层：

| 层次 | 保存内容 | 主要用途 |
|---|---|---|
| Session | 用户消息、最终回答、完整工具记录、标题、绑定仓库 | 前端展示和完整对话归档 |
| Run | 一次用户请求的状态、时间、reason/error | 执行生命周期和主动中断 |
| Checkpoint | HumanMessage、AIMessage、ToolMessage、tool call、Graph State | Agent 下一轮恢复活动上下文 |

另外还有独立的 Memory，用于跨 Session 保存少量长期有效信息。

它们之间的关系是：

```text
一个 Session
├── Run 1：completed
├── Run 2：failed
├── Run 3：interrupted
├── Run 4：completed
└── 一条以 session_id 为 thread_id 的 Checkpoint 状态链
```

### 为什么这么做

Session 是对话，不应该有“执行完成”的含义。用户完成一次请求后仍然可以继续聊天，因此执行状态必须放在 Run 中。

Checkpoint 是模型和 LangGraph 使用的内部状态，可能经过上下文压缩；Session 是用户看到的完整历史，不能因为模型上下文压缩而丢失旧消息。

如果把三者混成一份数据，会出现这些问题：

- 一次请求失败时，不知道应该把整段 Session 标记成失败还是只标记本轮；
- Checkpoint 压缩历史后，前端也会丢失完整聊天记录；
- 每轮重新注入完整 Session 历史，会和 Checkpoint 中已有消息重复；
- 用户停止当前任务时，无法区分“停止 Run”和“删除 Session”。

### 有没有其他方式

最简单的方案是只保存 Session，每次请求都把完整 Session 历史重新发给模型。这适合普通聊天机器人，但会造成 Token 持续增长，无法保留压缩后的活动上下文，也不利于工具状态恢复。

另一种方式是只使用 Checkpoint，同时从 Checkpoint 渲染前端历史。问题是 Checkpoint 属于内部执行状态，包含压缩、删除消息和工具协议，不适合作为稳定的用户档案。

因此我选择完整 Session、请求级 Run、活动 Checkpoint 分开保存。

## 三、关闭页面后重新打开对话会发生什么

### 我是怎么做的

前端重新打开页面时，只通过 Session API 加载 `backend/sessions/<session_id>.json`，用于展示完整历史。此时不会执行 Agent，也不依赖 Checkpoint 渲染页面。

用户在旧 Session 中发送新消息后，后端执行 `load_checkpoint_seed(session_id)`：

```text
Checkpoint 已存在
  → 不再注入完整 Session 历史
  → LangGraph 根据 thread_id=session_id 自动恢复状态
  → 只追加新的 HumanMessage

Checkpoint 不存在
  → 从 Session JSON 读取历史
  → 转换为 LangChain Message
  → 作为 seed 注入一次
  → 建立新的 Checkpoint
```

### 为什么这么做

如果 Checkpoint 已经存在，又重新注入全部 Session 历史，同一段对话会重复出现。

另外，Checkpoint 可能已经保存经过压缩的活动上下文。如果每次都从完整 Session 重新构建，之前的上下文压缩会失效，Token 会再次膨胀。

Session 同时作为 Checkpoint 丢失时的恢复种子。后端重启不会影响 SQLite Checkpoint；即使 Checkpoint 文件丢失，只要 Session JSON 还在，下一轮仍能重建可继续对话的状态。

### 有没有其他方式

可以永远以 Session 为准，每次重新构建模型上下文，架构更简单，但长会话成本高。

也可以只依赖 Checkpoint，不保留 Session JSON，但前端历史、审计和数据迁移会变得困难。

还可以将 Session 和 Checkpoint 放在同一个事务数据库中，提高一致性，但当前项目是单机原型，JSON 加 SQLite 的实现成本更低。

## 四、Agent Graph 为什么复用，而不是每次重新创建

### 我是怎么做的

当前项目按 Session 缓存 `Agent Graph`。第一次使用 Session 时，系统完成以下装配：

- 创建模型客户端；
- 生成 System Prompt；
- 创建绑定目标 Repository 的工具；
- 注册上下文压缩、Memory 和故障恢复 Middleware；
- 绑定 LangGraph Checkpointer；
- 调用 `create_agent` 创建 Graph。

后续请求复用这个 Graph，但每次都会创建新的 Run 和新的 Agent invocation。

需要区分：

```text
Agent Graph = 可复用的执行结构
Agent invocation = 一次请求触发的执行
Run = 对这次 invocation 的生命周期记录
```

一次 invocation 结束后，执行协程结束，但缓存的 Agent Graph 仍可以用于下一次请求。

### 为什么这么做

同一 Session 的模型、仓库工具、System Prompt 和中间件通常不会变化。缓存 Graph 可以避免每轮重复装配相同执行结构。

真正的对话状态不保存在 Graph Python 对象里，而是保存在 Checkpoint 中。因此即使后端重启、Graph 缓存消失，也可以重新创建 Graph 后根据 `session_id` 恢复状态。

### 有没有其他方式

#### 方案一：每次请求创建 Graph

优点是生命周期简单、不会出现缓存过期、适合多进程；缺点是每次重复装配模型、工具和中间件。

考虑到 LLM 网络调用通常远慢于本地 `create_agent`，这也是一个合理方案，尤其适合追求简单性的系统。

#### 方案二：全局共享一个 Graph

所有 Session 使用同一个 Graph，通过不同 `thread_id` 隔离 Checkpoint。这更符合“Graph 是无状态执行结构”的理念，内存占用最低。

但当前项目的工具在创建时绑定了 Repository 路径，`delegate_task` 还绑定了 `session_id` 和 `workspace_id`，暂时不能安全地让所有仓库共享一个 Graph。

#### 方案三：每个 Repository 一个 Graph

这是后续更推荐的方向。同一仓库的多个 Session 共享 Graph，通过不同 `thread_id` 隔离状态。需要先把 `session_id`、`run_id` 从工具对象移入每次请求的 Runtime Context。

## 五、System Prompt、Checkpoint 和 Memory 如何注入

### 我是怎么做的

固定 System Prompt 在 Agent Graph 创建时构建，包含 Steward 身份、行为规则、工具边界和内部能力说明。

Checkpoint 不是简单拼接到 Prompt 字符串中，而是 LangGraph 根据：

```python
config={"configurable": {"thread_id": session_id}}
```

自动恢复 Graph State 和历史 Messages。

Memory 每轮动态选择。Selector 根据当前用户请求，从“全局用户 Memory + 当前 Repository Memory”中选择相关项，再由 Middleware 临时追加到本轮 System Message。

因此可以概括为：

```text
固定 System Prompt：Graph 创建时配置
Checkpoint：每次 invocation 按 thread_id 自动恢复
当前问题：每次创建新的 HumanMessage
Memory：每次按当前问题选择并临时注入
```

### 为什么这么做

固定规则没有必要每轮从数据库选择；Checkpoint 需要保存完整工具协议和 Graph State，不能退化成一段 Prompt 文本；Memory 则只应按相关性临时注入，避免所有长期信息无限占用上下文。

Memory 不写入 Session 和 Checkpoint，可以避免用户偏好在聊天历史中重复出现，也防止不同 Repository 的项目记忆串线。

### 有没有其他方式

可以把全部 Memory 固定拼进 System Prompt，实现简单，但 Memory 增长后会浪费 Token，并引入无关信息。

可以将 Memory 直接写入 Session，但用户没有说过的内部记忆会出现在聊天档案中，也难以区分原始证据和系统补充信息。

可以使用向量数据库做召回。当前项目先使用模型 Selector 加本地关键词兜底，便于控制依赖和验证 Memory 边界；数据规模增大后可以替换为 Embedding 检索加重排。

## 六、上下文压缩是怎么做的

### 我是怎么做的

每次主模型调用前执行四层上下文管理：

```text
L3 大工具输出落盘
  ↓
L1 裁剪消息中段
  ↓
L2 压缩较旧工具结果
  ↓
L4 必要时生成工作摘要
```

L3 将超大工具结果保存到 `.task_outputs/tool-results/<session_id>/`，上下文只保留路径和预览。

L1 保留开头约束和最近工作现场，裁剪中间历史，同时避免拆散 `AIMessage(tool_calls)` 和对应 `ToolMessage`。

L2 保留最近工具结果的完整正文，将更早结果替换为占位文本，但保留 `tool_call_id` 和消息结构。

L4 在前三层处理后仍超预算时，调用总结模型生成可继续工作的摘要，并和最近消息一起写回 Checkpoint。

### 为什么这么做

代码 Agent 的主要上下文压力往往来自源码、日志和测试输出，而不是普通聊天文本。因此先处理大工具输出，再裁剪消息和压缩历史工具结果，最后才调用有损的 LLM Summary。

这个顺序尽量通过确定性手段降低上下文，只有必要时才使用有损摘要。

### 有没有其他方式

可以只做滑动窗口，只保留最近 N 条消息。实现简单，但容易丢失长期目标、用户约束和关键决策。

可以每轮都总结全部历史，但成本较高，而且摘要会反复压缩，误差持续累积。

可以使用超长上下文模型完全不压缩，但成本、延迟和 Provider 上限仍然存在，工具输出也可能快速占满窗口。

还可以把源码、日志全部放入外部检索系统，按需召回。这适合更大规模的代码库，但仍不能替代对当前任务状态的摘要。

## 七、长期记忆是怎么做的

### 我是怎么做的

Memory 分为：

| 类型 | Scope |
|---|---|
| `user` | 全局用户级 |
| `project` | 当前 Repository |
| `feedback` | 当前 Repository |
| `reference` | 当前 Repository |

每条 Memory 支持 `active`、`superseded` 和 `archived` 状态。

请求前，Selector 只读取 Memory 元数据并选择最多若干条相关项，再加载正文临时注入。

请求成功后，Extractor 从压缩前保存的本轮原始用户信息中提取长期信息。候选项必须能够引用用户原文作为证据，拒绝保存临时任务步骤、模型自行推断、敏感信息和没有新增价值的重复内容。

### 为什么这么做

聊天历史适合回答“刚才说了什么”，但不适合保存跨 Session 的稳定信息。如果把全部历史长期带入，会同时增加 Token 和噪声。

用户偏好可以跨仓库复用，但项目技术约束不应该跨仓库传播，因此我为 Memory 增加了 Repository Scope。

Extractor 是旁路调用，提取失败不会让已经完成的主回答变成失败，避免辅助能力影响核心任务可用性。

### 有没有其他方式

可以完全依赖完整聊天历史，不单独做 Memory，适合短会话。

可以让用户手动维护 `USER.md`，可控性强但体验较差，而且和自动记忆概念重叠。

可以使用向量数据库自动保存所有对话片段，实现方便但容易召回临时信息、错误结论和敏感数据。

当前方案更强调“少量、稳定、有用户证据、可演进”的 Memory。

## 八、故障恢复是怎么做的

### 我是怎么做的

系统先分类模型错误，再执行对应策略：

| 故障 | 恢复方式 |
|---|---|
| 429 限流 | `Retry-After` 或指数退避加随机抖动 |
| 529 / 过载 | 退避重试，必要时切换备用模型 |
| 5xx | 有上限重试 |
| Timeout / Connection | 有上限重试 |
| Prompt Too Long | 强制执行一次 L4，再重试 |
| Authentication / Bad Request | 不进行无意义重试 |
| 输出截断 | 保留已有内容，内部续写并合并 |

恢复过程通过 SSE `recovery` 事件反馈给前端，但不会写入永久 Session 历史。

### 为什么这么做

不同错误需要不同策略。认证错误重复重试没有意义；限流应该等待；上下文超限需要改变输入；输出截断需要继续生成。如果统一重试，会增加延迟，也可能让错误变得更严重。

### 有没有其他方式

可以只在 API 外层做通用重试，实现简单，但无法根据 LangGraph 当前上下文执行强制压缩或自动续写。

可以交给网关统一处理 429 和 5xx，这适合生产环境，但 Prompt Too Long、输出截断等语义错误仍需要 Agent 内部恢复。

更成熟的方案还可以引入熔断器、Provider 路由、请求队列和可观测指标。当前项目先完成单机最小闭环。

## 九、主动中断是怎么做的

### 我是怎么做的

每次用户请求先创建 Run，并将实际执行协程注册到 `run_id`。

用户点击停止后，前端调用：

```http
POST /api/runs/{run_id}/cancel
```

后端执行：

1. 将 Run 状态修改为 `interrupted`；
2. 向实际 Agent 执行协程发送取消信号；
3. 阻止后续模型和工具步骤；
4. 丢弃模型生成到一半的 token；
5. 跳过本轮 Memory Extraction；
6. Session 保存用户消息和“本次任务已中断”；
7. 已完整配对的 tool call 与 ToolMessage 可以保留；
8. 删除可能存在非法悬空 tool call 的活动 Checkpoint；
9. 下一轮从清洗后的 Session 历史重新建立 Checkpoint。

### 为什么这么做

只关闭 SSE 连接只能停止前端接收数据，后台 Agent 可能继续调用模型和工具，无法算真正中断。

直接保留中断时的 Checkpoint 也有风险：模型可能已经生成 `tool_call`，但工具还没有返回 `ToolMessage`，下一轮恢复时会得到不合法的消息序列。

第一版不追求复杂断点恢复，因此采用“保留完整工具调用组、清理活动 Checkpoint、下轮由合法 Session 重建”的策略，优先保证状态正确和 Session 可继续使用。

### 有没有其他方式

可以使用 LangGraph 原生 interrupt/resume，更适合人工审批和可恢复暂停，但用户主动取消与未来恢复并不是完全相同的语义。

可以精确修改 Checkpoint，删除悬空 tool call 或补充取消 ToolMessage，但这会耦合 LangGraph 内部 State Schema，复杂度和升级风险更高。

可以完全保留中断前 Checkpoint，下一轮继续执行，但需要解决外部工具副作用、幂等性和恢复语义，不符合当前最小闭环目标。

可以为每个 Session 建立后台 Actor 和消息队列，中断控制更强，但需要任务调度、Actor 回收和分布式协调，当前阶段过重。

## 十、为什么采用单 Steward 加临时 Worker

### 我是怎么做的

系统只保留一个长期 Steward。它拥有 Session、Checkpoint、Memory 和上下文压缩。

复杂任务通过 `delegate_task` 串行委派给 Backend、Frontend、Test 或 General Worker。Worker 只收到 TaskSpec、Reviewer feedback 和 Repository 工具，不继承 Steward 完整历史、Checkpoint 和 Memory。

Worker 返回统一的 `WorkerResult`：

```text
status
summary
changed_files
tests_run
issues
```

只读 Reviewer 根据验收条件和系统计算出的真实任务 Diff 检查结果；失败时最多返修两轮。

### 为什么这么做

如果存在多个长期 Agent 并共享对话、Checkpoint 和 Memory，会增加状态归属、消息同步和冲突处理的复杂度。

当前设计让 Steward 成为唯一对话负责人，Worker 只是一次性受限执行器。Worker 工具轨迹不会持续膨胀 Steward 上下文，权限由统一的 `PermissionScope` 控制。

### 有没有其他方式

可以使用多个长期 Agent 互相协作，适合角色稳定、任务并行度高的系统，但需要解决共享状态、冲突、调度和最终责任归属。

可以完全不使用 Worker，由 Steward 完成所有任务，架构简单，但复杂任务会让主上下文积累大量工具轨迹。

可以使用并行 DAG 调度多个 Worker，效率更高，但需要 Worktree、写冲突检测、任务依赖和联动取消。当前项目暂时采用串行委派保证可控性。

## 十一、如何限制 Worker 的工作范围

### 问题是怎么发现的

第一版实现给 `TaskSpec` 增加了 `allowed_paths`，例如 Backend Worker 只能修改：

```text
backend/**
```

系统在 `write_file` 前检查目标路径，越界写入会被拒绝。这个方案表面上能够限制 Worker，但我进一步检查后发现，Worker 同时拥有接收任意字符串的 `terminal`。

因此它虽然不能执行：

```text
write_file("frontend/app.ts")
```

却可能通过下面的命令实现同样的效果：

```bash
echo "changed" > frontend/app.ts
```

这说明只保护一个写文件工具是不完整的。Agent 的副作用通道可能包括文件工具、Terminal、Git、测试脚本和构建脚本；只要存在未受控制的第二条通道，`allowed_paths` 就可能被绕过。

### 我是怎么做的

我把权限抽象成统一的 `PermissionScope`，让读取、写入和命令执行共享同一份确定性权限对象。

最终权限来自三层规则的交集：

```text
Repository Policy
        ∩
Role Policy
        ∩
Task Policy
        ↓
Effective PermissionScope
```

三层分别负责：

| 层次 | 职责 | 示例 |
|---|---|---|
| Repository Policy | 系统级边界和敏感文件拒绝规则 | 禁止 `.git`、`.env*`、`*.pem`、`*.key` |
| Role Policy | Worker 角色的最大权限 | Backend 最多写 `backend/**` |
| Task Policy | 当前任务申请的最小范围 | 本次只申请 `backend/auth/**` |

权限不是用 Task 参数覆盖 Role，而是每次访问时同时满足三层规则。例如：

```text
Backend Role：backend/**
Task 申请：backend/auth/**
最终可写：backend/auth/**
```

如果 Steward 错误地申请：

```text
allowed_paths=["**"]
```

最终权限仍不能超过 Backend Role 的 `backend/**`。也就是说，上层 Agent 只能申请或缩小权限，不能改变系统设置的权限上限。General Worker 如果没有显式提供 `allowed_paths`，则默认没有写权限。

`TaskSpec` 同时增加了 `allowed_commands`。Worker 不再拿到通用 `terminal(command: str)`，而是只能调用结构化命令工具：

```text
run_command(
    command="pytest",
    targets=["backend/tests/test_auth.py::test_login"]
)
```

支持的命令标识固定为：

```text
pytest
ruff_check
mypy
npm_test
npm_lint
npm_build
```

系统把命令标识转换为固定参数数组，通过 `subprocess.run(..., shell=False)` 执行。目标参数只能是仓库相对路径，不能传入 Shell 运算符、重定向或任意命令行 flags。命令还必须同时属于 Role Policy 和当前 Task 的 `allowed_commands`。

最后，我没有继续相信 Worker 自己返回的 `changed_files`。模型输出只能作为执行摘要，不能作为安全事实。系统在 Worker 开始前通过 Git 记录 tracked 和 untracked 文件的内容指纹，执行后再次采集，计算相对于任务起点的真实新增、修改和删除文件：

```text
执行前 Git 文件状态
        ↓
Worker 执行
        ↓
执行后 Git 文件状态
        ↓
计算真实 changed_files
        ↓
PermissionScope 越界检查
        ↓
通过后才进入 Reviewer
```

这样即使 Worker 自报只修改了 `backend/auth.py`，系统检测到它还修改了 `frontend/app.ts`，任务仍会在权限层直接失败。用户在 Worker 启动前已有的未提交修改保存在基线中，不会被错误归到 Worker 名下。

Reviewer 的职责也因此更清晰：

```text
PermissionScope + GitChangeTracker：有没有越权
Reviewer：代码是否满足验收标准
```

Reviewer 不再承担安全判断，也不能用“模型审核通过”代替确定性权限检查。

### 为什么这么做

Prompt 只能告诉模型“应该做什么”，不能保证模型“只能做什么”。模型可能误解任务，也可能通过另一种工具完成同一个副作用。因此安全边界必须放在模型外部，由普通代码强制执行。

这套设计体现了三个原则：

1. 最小权限：任务只获得完成当前工作所需的路径和命令；
2. 不信任自报：修改范围来自真实文件状态，而不是模型生成文本；
3. 职责分离：权限系统判断越权，Reviewer 判断质量。

### 有没有其他方式

最简单的方案是只在 Prompt 中要求 Worker 不要越界。实现成本最低，但它属于行为引导，不是可靠的权限边界。

可以继续使用任意 Shell，并尝试维护危险命令黑名单。但 Shell 语法、重定向、脚本和子进程组合非常多，黑名单很难穷举，因此当前项目选择完全移除 Worker 的字符串 Shell 入口。

更强的方案是为每个 Worker 创建独立 Git worktree、容器或 microVM。这样即使命令或测试程序本身产生副作用，也能限制在隔离环境中，并在验收通过后再合并补丁。这种方案隔离更强，但需要处理环境镜像、依赖缓存、文件同步、冲突合并和资源回收，当前版本暂未引入。

当前实现仍直接修改真实 Workspace。发现越界后会将任务判定为失败，但不会自动回滚，因为直接恢复文件可能覆盖用户同时进行的修改。后续如果引入 worktree，就可以在失败时直接丢弃临时工作区，实现更安全的回滚。

### 面试时可以怎么说

> 项目最初通过 `allowed_paths` 限制 Worker 的写文件范围，但我发现它只保护了 `write_file`，Worker 仍然可以通过 Terminal 重定向或脚本绕过。这让我意识到 Prompt 和单工具校验不能构成完整安全边界。
>
> 后来我把权限抽象成统一的 `PermissionScope`，把 Repository、Role 和 Task 三层策略取交集。上层 Agent 只能申请和缩小权限，不能突破角色上限。Terminal 也改成了结构化命令，只支持 pytest、ruff、mypy 和 npm 检查，并用 `shell=False` 执行。
>
> Worker 完成后，系统不相信它自报的 `changed_files`，而是比较任务前后的 Git 文件指纹，得到真实修改列表，再做越界检查。只有权限检查通过才进入 Reviewer；权限层判断能不能改，Reviewer 判断改得对不对。更高安全等级下还可以继续增加 worktree 或容器隔离。

## 十二、当前方案的不足与演进方向

面试时不要把项目描述成生产级系统，应主动说明边界：

- Agent Graph 当前按 Session 缓存，Session 增长后会占用更多内存；
- Graph 创建后修改 Prompt、模型或工具配置不会自动刷新；
- Run 的活动任务映射保存在进程内，不支持多实例协调；
- JSON 和 SQLite 缺少跨存储事务，极端宕机时可能短暂不一致；
- Token 数量是本地估算，可能与 Provider 计算不同；
- Memory 暂无用户审核和管理界面；
- 中断不会自动回滚已经执行的文件修改和命令副作用；
- Worker 串行执行，尚未实现 Worktree 和并行写入隔离；
- L4 摘要是有损压缩，关键事实仍需通过仓库工具重新确认。

推荐的后续演进顺序：

1. 为 Agent Graph 缓存增加 TTL/LRU 和 Session 删除清理；
2. 将 `session_id`、`run_id` 和 Workspace 信息移入 Runtime Context；
3. 将 Graph 缓存粒度从 Session 调整为 Repository；
4. 将 Run 取消信号放入 Redis 等共享存储，支持多实例；
5. 为 Session、Run 和 Checkpoint 增加一致性补偿与启动恢复；
6. 增加 Token、延迟、恢复成功率和任务完成率评测；
7. 根据需求再考虑 Worktree、并行 Worker 和可恢复执行。

## 十三、面试口述版本

### 30 秒版本

> 我做了一个面向本地 Git 仓库的持久化代码 Agent。核心不是简单调用模型，而是把 Workspace、Session、Run 和 LangGraph Checkpoint 分层：Session 保存用户看到的完整历史，Run 表示一次请求的执行生命周期，Checkpoint 保存模型和工具调用的内部活动状态。系统还实现了分层上下文压缩、按仓库隔离的长期记忆、模型故障恢复和主动中断，因此长会话可以恢复，用户也能停止当前任务后继续使用原 Session。

### 2 分钟版本

> Repository Steward 是一个基于 FastAPI、LangChain 和 LangGraph 的代码仓库 Agent。用户先绑定 Repository，每个 Session 永久绑定一个仓库，所有文件和终端工具都限制在仓库根目录内。
>
> 我重点解决了 Agent 状态管理问题。Session 负责完整聊天归档和前端展示；每次用户请求创建独立 Run，记录 running、completed、interrupted 或 failed；LangGraph Checkpoint 以 session_id 作为 thread_id，保存 HumanMessage、AIMessage、ToolMessage 和 Graph State。用户重新打开旧对话时，前端从 Session 加载历史；再次发送消息时，Agent 从 Checkpoint 恢复活动上下文。如果 Checkpoint 不存在，才从 Session 注入一次历史重建。
>
> 长会话方面，我实现了 L3 工具输出落盘、L1 消息裁剪、L2 旧工具结果压缩和 L4 工作摘要。长期记忆则区分全局用户记忆和 Repository 级项目记忆，每轮按相关性选择，成功后从用户原始表达中提取。
>
> 稳定性方面，我针对限流、过载、上下文超限和输出截断分别做退避、备用模型、强制压缩和自动续写。用户点击停止时，后端会真正取消 Agent 执行，而不只是断开 SSE；半截输出不会保存，可能存在悬空 tool call 的 Checkpoint 会被清理，下一轮从合法 Session 重新建立状态。
>
> 当前是单机工程化原型，使用 JSON 和 SQLite，后续可以将 Graph 缓存调整为 Repository 粒度，并用共享任务存储支持多实例。

## 十四、高频追问速答

### 为什么不只使用 Session？

因为 Session 是完整用户档案，而模型实际需要的是经过压缩的活动上下文。只使用 Session 会导致每轮重复加载全部历史，Token 持续增长，也无法保存 LangGraph 内部 Graph State。

### 为什么不只使用 Checkpoint？

Checkpoint 会被压缩和修改，包含内部工具协议，不适合作为稳定的前端聊天档案。Session 必须独立保存完整历史。

### Checkpoint 是怎么注入的？

它不是拼进 Prompt，而是通过 `thread_id=session_id` 由 LangGraph Checkpointer自动恢复。只有 Checkpoint 不存在时，系统才从 Session 注入一次历史 seed。

### 每次请求是否创建一个新 Agent？

每次创建的是新 Run 和新 Agent invocation。当前 Agent Graph 按 Session 缓存并复用；后端重启后会重新创建 Graph，但可以从 SQLite Checkpoint 恢复状态。

### 模型怎么知道什么时候停止调用工具？

模型输出带 `tool_calls` 的 AIMessage 时继续进入工具节点；工具结果返回后再次调用模型。当模型输出不带 `tool_calls` 的最终 AIMessage 时，LangGraph 结束本次 invocation。

### 为什么 Memory 不写入 Checkpoint？

Memory 是按当前请求选择的外部长期信息，不应该永久污染对话状态，也不能跨仓库泄漏。它每轮临时注入，使用后不进入 Session 和 Checkpoint。

### 中断时为什么删除 Checkpoint？

因为可能存在已经生成 tool call、但工具尚未返回 ToolMessage 的非法状态。第一版不做复杂断点恢复，因此从清洗后的 Session 重建 Checkpoint，更容易保证消息配对和后续会话可用。

### 已经执行的工具会回滚吗？

不会。取消只能阻止后续执行，已经产生的文件修改或命令副作用不会自动回滚。自动回滚需要 Worktree、事务或 Undo 机制，不属于当前最小闭环。

### 为什么不是多个长期 Agent？

多个长期 Agent 会带来共享 Session、Checkpoint、Memory 和写冲突问题。当前只让 Steward 长期存在，Worker 是短生命周期、受权限约束的执行器，状态边界更清楚。

### 当前架构最大的改进点是什么？

将 Agent Graph 从按 Session 缓存改为按 Repository 缓存，并将 session/run 信息从工具构造参数移入 Runtime Context；这样既能复用 Graph，又能减少大量 Session 的内存占用。

## 十五、面试表达注意事项

推荐说法：

- “持久化、可中断的代码 Agent 原型”；
- “将 Session、Run 和 Checkpoint 分层管理”；
- “针对工具调用配对设计了中断清理策略”；
- “按 Repository Scope 隔离长期记忆”；
- “使用确定性压缩与 LLM 摘要组合控制上下文”。

避免夸大：

- 不要说“自研大模型”；
- 不要说“完全自主的软件开发系统”；
- 不要说“生产级分布式多 Agent 平台”；
- 没有评测数据前，不要声称准确率或 Token 成本提升了具体百分比；
- 不要掩盖中断无法回滚副作用、多实例不共享取消状态等边界。

最好的表达方式是先讲问题，再讲设计取舍，最后主动说明替代方案和当前限制。这样能体现的不只是“会调用框架”，而是对 Agent 状态、生命周期和工程边界有完整理解。
