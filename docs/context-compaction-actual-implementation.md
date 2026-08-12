# 分层上下文压缩：实际实施说明

## 1. 文档目的

`context-compaction-execution-plan.md` 记录的是实施前的现状分析和最小改造方案。本文件记录方案确认后，仓库中**实际落地的代码、运行链路和持久化方式**。

当前实现保留了原有的 FastAPI、LangChain `create_agent`、LangGraph Agent Loop、SSE 和 JSON Session 架构，只增加了以下几块：

- 一个四层压缩算法模块；
- 一个在每次模型调用前自动执行的 middleware；
- 一套 LangGraph SQLite Checkpointer；
- 一层 LangChain 消息与 Session JSON 记录之间的转换；
- 对应的配置、测试和运行目录。

## 2. 最终结构

与上下文管理直接相关的文件如下：

```text
backend/
├── app.py
├── api/
│   ├── chat.py
│   └── sessions.py
├── graph/
│   ├── agent.py
│   └── agent_factory.py
├── middleware/
│   ├── __init__.py
│   └── context_management.py
├── service/
│   ├── context_manager.py
│   ├── message_codec.py
│   └── session_manager.py
├── tests/
│   ├── test_context_manager.py
│   ├── test_checkpoint_persistence.py
│   └── test_core.py
├── sessions/                         # 完整会话档案，运行时生成
├── checkpoints.sqlite                # Agent 活跃状态，运行时生成
└── .task_outputs/tool-results/       # L3 大结果原文，运行时生成
```

依赖中增加了：

```text
langgraph-checkpoint-sqlite>=3.0.0,<4.0.0
```

`checkpoints.sqlite*` 和 `.task_outputs/` 均已加入 `.gitignore`。

## 3. 三类数据的实际分工

当前不是用一个文件同时承担全部职责，而是把“完整档案”和“模型工作上下文”分开保存。

| 存储 | 保存内容 | 使用者 | 是否受上下文压缩影响 |
|---|---|---|---|
| `backend/sessions/<session_id>.json` | 完整的 `human / ai / tool` 消息档案 | Session API、前端历史展示、无 Checkpoint 时的首次播种 | 不会被 L1/L2/L3/L4 覆盖 |
| `backend/checkpoints.sqlite` | LangGraph 当前线程的活跃 State，包括压缩后的 `messages` | LangGraph Agent 下一轮续聊 | 会保存 middleware 修改后的上下文 |
| `backend/.task_outputs/tool-results/` | 被 L3 移出上下文的完整工具结果 | Agent 后续按路径重新读取 | 保存原文，Checkpoint 里只保留路径和 preview |

可以简化理解为：

```text
Session JSON       = 给人看的完整档案
SQLite Checkpoint  = 给模型继续工作的上下文
.task_outputs      = L3 移出的工具结果原文
```

这里没有额外保存 transcript。Session JSON 已经承担完整聊天档案的职责，L4 只负责重建 Agent 的活跃上下文。

## 4. Checkpointer 的实际接入

### 4.1 后端启动时创建一个 Checkpointer

`backend/app.py` 在 FastAPI lifespan 中执行：

```python
checkpoint_path = settings.backend_dir / "checkpoints.sqlite"
async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
    await checkpointer.setup()
    agent_manager.initialize(settings.backend_dir, checkpointer=checkpointer)
    yield
```

含义是：

- 整个后端进程共用一个 `AsyncSqliteSaver`；
- SQLite 文件自动创建，不需要单独安装或启动数据库服务；
- `setup()` 自动准备 LangGraph 所需的表；
- FastAPI 关闭时由异步上下文管理器关闭数据库连接。

### 4.2 把 Checkpointer 交给 LangGraph Agent

`backend/graph/agent_factory.py` 最终把它传给 LangChain：

```python
create_agent(
    model=config.llm,
    tools=config.tools,
    middleware=config.middleware,
    context_schema=AgentRunContext,
    checkpointer=config.checkpointer,
)
```

`create_agent` 返回的是编译后的 LangGraph Agent。之后节点状态的保存与恢复由 LangGraph 执行，业务代码不直接读写 SQLite 表。

### 4.3 Session ID 就是 Thread ID

`backend/graph/agent.py` 将前端传来的 `session_id` 转换成：

```python
{"configurable": {"thread_id": session_id}}
```

每次运行 Agent 都传入这个 config。因此：

- 一个 Session 对应一个 LangGraph Thread；
- 一个 Thread 可以有多个 Checkpoint 快照；
- 下一轮根据同一个 `thread_id` 恢复最新 State；
- 不是“一轮创建一个 Checkpointer”，而是一个 Checkpointer 管理所有会话和快照。

### 4.4 首次播种与后续恢复

`AgentManager.load_checkpoint_seed()` 先检查当前 `thread_id` 是否已有 Checkpoint：

```python
checkpoint = await self.checkpointer.aget_tuple(self._thread_config(session_id))
if checkpoint is not None:
    return []
return self.session_manager.load_session_for_agent(session_id)
```

实际行为分为两种：

```text
没有 Checkpoint
  -> 从 sessions/<id>.json 读取完整标准消息
  -> 作为该 Thread 的初始消息
  -> 本轮运行后建立 Checkpoint

已有 Checkpoint
  -> 不再把完整 Session JSON 传入 Agent
  -> 本轮只提交新的 HumanMessage
  -> LangGraph 自动恢复 SQLite 中的最新 State
```

因此 L4 已经生成的 summary 不会在下一次 HTTP 请求时被完整 Session 历史覆盖。

删除会话时，`AgentManager.delete_session()` 会同时删除 Session JSON 和对应的 Checkpoint Thread。

## 5. Middleware 是怎样接入 Agent Loop 的

### 5.1 注册位置

`backend/graph/agent.py` 创建 Agent 时注册：

```python
middleware=[
    ContextManagementMiddleware(
        self.context_manager,
        self._summary_model,
    )
]
```

### 5.2 自动执行位置

`backend/middleware/context_management.py` 实现了 LangChain `AgentMiddleware.abefore_model()`：

```python
async def abefore_model(self, state, runtime):
    result = await self.manager.manage(
        state["messages"],
        summary_model=self.summary_model,
        run_id=runtime.context.session_id,
    )
```

`abefore_model` 是 LangChain Agent Loop 的模型调用前钩子。Agent 每次准备调用模型时都会经过这里，包括一轮内工具执行结束后再次调用模型的情况。

所以实际循环是：

```text
恢复 LangGraph State / 加入本轮消息
  -> abefore_model
  -> L3 -> L1 -> L2 -> 必要时 L4
  -> 调用模型
  -> 如果模型要求调用工具，则执行工具
  -> 工具结果加入 State
  -> 再次进入 abefore_model
  -> 再次调用模型
  -> 直到模型给出最终回答
```

如果压缩改变了消息，middleware 返回：

```python
{
    "messages": [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        *result.messages,
    ]
}
```

这会通过 LangGraph 的 message reducer 原子替换当前 `state["messages"]`。Checkpointer 随后保存的是替换后的 State，而不是只在单次模型请求中使用的临时副本。

## 6. 四层压缩的实际算法

核心代码位于 `backend/service/context_manager.py`，入口为：

```python
ContextManager.manage(messages, summary_model, run_id)
```

实际固定顺序为：

```text
L3 -> L1 -> L2 -> token 检查 -> 必要时 L4
```

### 6.1 L3：当前工具结果批次超预算时落盘

实现函数：`apply_tool_result_budget()`。

它只在当前消息尾部是 `ToolMessage` 时工作，并从尾部向前收集连续的 `ToolMessage`。这组连续消息就是最近一次模型请求工具后，工具节点交回的结果批次。

处理方式：

1. 按 UTF-8 字节数计算该批次所有工具结果的总大小；
2. 未超过 `tool_results_budget_bytes` 时不处理；
3. 超额时按单条结果从大到小选择；
4. 将完整内容写入：

   ```text
   backend/.task_outputs/tool-results/<session_id>/<tool_call_id>.txt
   ```

5. 将原 `ToolMessage.content` 替换成路径和前 `preview_chars` 字符；
6. 直到该批次在上下文中的估算字节数回到预算内，或已无可继续处理的结果。

工具本身原有的 5,000/10,000 字符硬截断已经移除，因此 L3 能看到并保存完整输出。

### 6.2 L1：消息过多时裁剪中间历史

实现函数：`snip_middle_messages()`。

当消息数超过 `max_messages` 时：

- 保留头部 `keep_head_messages` 条；
- 保留预算允许的最近消息；
- 中间替换为一条 `[snipped N messages from conversation middle]`；
- 使用 `_tool_groups()` 和 `_safe_boundary()` 调整裁剪边界。

Pair-safe 规则保护下面的结构不被从中间切开：

```text
AIMessage(tool_calls=[id-a, id-b])
ToolMessage(tool_call_id=id-a)
ToolMessage(tool_call_id=id-b)
```

也就是说，发起工具调用的 AI 消息和紧随其后的工具结果要么一起保留，要么一起被裁掉。

### 6.3 L2：旧工具结果替换为占位符

实现函数：`compact_old_tool_results()`。

它按时间顺序找到所有 `ToolMessage`，保留最近 `keep_recent_tool_results` 条的完整内容，其余旧结果替换成：

```text
[Earlier tool result compacted. Re-run the tool if needed.]
```

工具调用关系没有删除：`ToolMessage` 本身、`tool_call_id` 和相应的 `AIMessage.tool_calls` 都还在，只是旧的结果正文被清空。

### 6.4 L4：前三层之后仍超预算时总结

前三层完成后，通过 LangChain `count_tokens_approximately()` 估算消息 token，并额外预留 `context_token_reserve`。

当结果仍超过 `max_context_tokens` 时才触发 L4：

1. 尽量保留最近若干消息，并再次用 pair-safe 边界避免拆开工具组；
2. 将更早的消息序列化成受 `summary_input_chars` 限制的文本；
3. 使用一个非流式 ChatModel 调用生成 summary；
4. summary 固定要求包含：
   - 当前目标；
   - 关键发现、决策以及被否决方案；
   - 已读取或修改的文件；
   - 剩余任务；
   - 用户约束；
5. 使用下面的消息重建上下文：

   ```text
   HumanMessage("[Compacted context]\n\n<summary>")
   + 最近保留的消息
   ```

L4 使用的是单独创建的非流式 ChatModel 实例，但它仍采用项目当前配置的 provider、model 和 API，不要求配置另一个模型。它不绑定 tools，也不保存 transcript。

## 7. Session 消息怎样保存和展示

### 7.1 Session 内部格式

`backend/service/message_codec.py` 将 LangChain 消息转换为稳定的 JSON 记录：

```json
{"type": "human", "content": "用户问题"}
{"type": "ai", "content": "", "tool_calls": [{"id": "call-1", "name": "read_file", "args": {}}]}
{"type": "tool", "tool_call_id": "call-1", "name": "read_file", "content": "工具结果"}
{"type": "ai", "content": "最终回答"}
```

这样 `AIMessage.tool_calls[].id` 和 `ToolMessage.tool_call_id` 可以完整对应。旧的 `user / assistant + tool_calls` Session 在读取时会自动迁移到 `schema_version: 2`。

### 7.2 本轮消息何时写入 Session

`AgentManager.astream()` 从 LangGraph 的 `updates` 流中收集本轮真实的：

- 当前 `HumanMessage`；
- 发起工具调用的 `AIMessage`；
- `ToolMessage`；
- 最终 `AIMessage`。

Agent 发出 `done` 事件后，`backend/api/chat.py` 调用 `append_agent_messages()`，把本轮完整消息追加到 Session JSON。

SSE 的 `token / tool_start / tool_end / done` 只负责实时传输，不再用于反向拼装持久化消息。

### 7.3 前端展示来源

前端打开历史会话时，Session API 读取 JSON。`records_to_display_messages()` 再把标准记录投影成前端原有的：

```text
user
assistant
assistant.tool_calls[]
```

因此即使 SQLite 中已经发生 L1/L2/L3/L4 压缩，前端仍显示 Session JSON 中的完整历史，不会突然把旧聊天替换成 summary 或 placeholder。

## 8. 一次请求的完整实际调用链

### 8.1 第一次使用某个 Session

```text
POST /api/chat(session_id=A, message=新问题)
  -> chat.py 读取 Session 记录
  -> load_checkpoint_seed(A)
  -> SQLite 中没有 Thread A
  -> 从 sessions/A.json 转为 LangChain messages
  -> 加入本轮 HumanMessage
  -> agent.astream(..., thread_id=A)
  -> middleware.abefore_model()
  -> L3 -> L1 -> L2 -> 必要时 L4
  -> 模型/工具 Agent Loop
  -> LangGraph 将 State 保存到 checkpoints.sqlite
  -> SSE 返回前端
  -> done 后把本轮完整消息追加到 sessions/A.json
```

### 8.2 同一 Session 的后续问题

```text
POST /api/chat(session_id=A, message=追问)
  -> load_checkpoint_seed(A)
  -> SQLite 已存在 Thread A，因此返回空 seed
  -> 只加入本轮 HumanMessage
  -> agent.astream(..., thread_id=A)
  -> LangGraph 自动恢复 Thread A 的最新 State
  -> middleware 再次在模型调用前执行
  -> 模型/工具 Agent Loop
  -> 更新 SQLite Checkpoint
  -> 本轮完整消息追加到 Session JSON
```

“回答后立即追问”和“关闭页面后重新打开历史会话再追问”，只要 `session_id` 相同且 SQLite 文件仍在，对 Agent 来说没有区别。

如果 Session JSON 存在但对应 Checkpoint 不存在，例如旧会话、SQLite 被清理或只复制了 JSON，则下一次提问会用完整 Session 重新播种一个新 Thread。

## 9. 配置项

当前通过 `backend/config/.env` 的可选环境变量调整策略：

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `CONTEXT_MAX_TOKENS` | 50000 | L4 的总上下文触发预算 |
| `CONTEXT_TOKEN_RESERVE` | 8000 | 为模型输出等预留的 token |
| `CONTEXT_MAX_MESSAGES` | 50 | L1 消息数量阈值 |
| `CONTEXT_KEEP_HEAD_MESSAGES` | 3 | L1 保留的头部消息数 |
| `CONTEXT_KEEP_RECENT_TOOL_RESULTS` | 3 | L2 保留的最近完整工具结果数 |
| `CONTEXT_TOOL_RESULTS_BUDGET_BYTES` | 200000 | L3 最近工具结果批次总预算 |
| `CONTEXT_PREVIEW_CHARS` | 2000 | L3 留在上下文中的 preview 长度 |
| `CONTEXT_SUMMARY_MAX_TOKENS` | 2000 | L4 summary 最大输出 token |

SQLite 不需要用户名、密码、端口或额外配置，固定使用 `backend/checkpoints.sqlite`。

## 10. 已完成的验证

当前测试覆盖：

- L3 按整批工具结果预算判断，并优先落盘较大结果；
- L1 保留头尾且不拆分 `tool_use / tool_result` 组；
- L2 只保留最近若干完整工具结果；
- L4 只在前三层处理后仍超预算时调用 summary model；
- L4 summary 与最近消息能够重建上下文；
- 压缩后的 State 能在同一 `thread_id` 的下一轮恢复；
- 关闭并重新打开 SQLite 后仍能恢复压缩 State；
- 删除 Session 时同步删除 Checkpoint Thread；
- Session 标准消息、旧格式迁移、前端投影和 Agent 消息收集。

验证命令及最近结果：

```powershell
uv run --with pytest pytest -q --basetemp .test-tmp-final
```

```text
18 passed
```

同时通过了 Python `compileall` 和 `git diff --check`。

## 11. Demo 范围内保留的简化

这版实现刻意没有扩展成生产级系统：

- token 使用近似估算，不调用模型 tokenizer 服务；
- SQLite 路径固定，没有做多后端存储配置；
- Session JSON 与 SQLite 不是一个数据库事务；
- Checkpointer 会保留历史快照，压缩最新 State 不等于 SQLite 文件立即缩小；
- L3 文件没有实现 TTL、去重和定期清理；
- 没有并发写同一 Session 的锁；
- L4 总结失败时没有复杂重试和降级链；
- 没有手动 compact API 或前端管理界面。

这些取舍不影响当前 Demo 展示的核心目标：**完整聊天档案保持可见，模型使用的活跃上下文可以分层压缩，并且压缩结果能够跨 HTTP 回合和后端重启继续使用。**
