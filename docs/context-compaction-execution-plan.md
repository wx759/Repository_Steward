# 分层上下文压缩：现状分析与最小实施方案

> 状态：已确认并实施  
> 参考：[wx759/learn-claude-code - s08_context_compact](https://github.com/wx759/learn-claude-code/tree/main/s08_context_compact)

## 1. 本次目标与边界

目标是在现有 Repository Steward Agent Demo 中加入一条每次调用主 Agent LLM 前都会执行的上下文管理管线：

```text
当前 messages
  -> L3 大 tool_result 落盘
  -> L1 裁剪中间历史
  -> L2 清理旧 tool_result
  -> 估算剩余上下文
       -> 未超预算：调用主 LLM
       -> 仍超预算：L4 调用 LLM 总结并重建 messages，再调用主 LLM
```

编号表达策略层级，实际执行顺序必须是 `L3 -> L1 -> L2 -> L4`。L3 必须先于 L2，否则旧的大结果会先被 placeholder 覆盖，之后无法保存完整内容。

本次坚持个人 Demo 范围：不更换 LangChain/LangGraph，不引入外部数据库或 tokenizer 服务（仅使用本地 SQLite Checkpointer），不增加手动 compact API/UI，不实现 Claude Code 的缓存恢复、context collapse、session memory 等生产级机制。

## 2. 当前项目结构与真实调用链

与本改造直接相关的结构如下：

```text
backend/
├── api/chat.py                  # 读取 session、启动 SSE、保存本轮 UI 消息
├── graph/
│   ├── agent.py                # 组装本轮 messages，调用 graph.astream
│   ├── agent_factory.py        # create_agent(...) 的唯一注册点
│   └── llm.py                  # ChatOpenAI / OpenAI-compatible 模型构造
├── service/
│   ├── session_manager.py      # JSON session 与 agent history 转换
│   └── prompt_builder.py       # system prompt 组装
├── tools/
│   ├── read_file_tool.py       # 当前输出最多 10,000 字符
│   └── terminal_tool.py        # 当前输出最多 5,000 字符
├── sessions/                   # 运行期 JSON 会话
└── tests/test_core.py          # session、agent 入参、工具与路由测试
```

一次请求的调用链是：

```text
POST /api/chat
  -> SessionManager.load_session_for_agent(session_id)
  -> AgentManager.astream(user_message, history)
  -> AgentManager._build_messages(history) + 当前 user message
  -> create_agent(...).astream({"messages": turn_messages})
  -> LangGraph 内部循环：LLM -> tool -> LLM -> ...
  -> API 将 token/tool 事件转成 SSE
  -> SessionManager.save_message(...) 保存 user/assistant/UI tool trace
```

最合适的接入点是 `create_agent` 的 middleware：它位于 LangGraph 内部循环中，因此不仅一轮 HTTP 请求的第一次 LLM 调用会执行 Context Management，工具返回后的第二次、第三次 LLM 调用也会执行。

## 3. 已有上下文管理能力排查结果

### 3.1 当前仓库内不存在重复的压缩实现

全仓搜索 `context / compact / summarization / middleware / token / tool_result` 后确认：

- 没有 context compactor、summary service、compact middleware 或手动 compact API。
- `backend/graph/agent_factory.py` 调用 `create_agent(...)` 时没有传入 `middleware`。
- `target.md` 明确记录旧的 `SummarizationMiddleware`、compress API 和手动 history summarize/compact 已被删除，为后续重新实现 Claude Code Harness 风格的 compaction 留出位置。
- Git 当前只有初始化提交，没有可直接恢复并复用的历史压缩模块。

因此本次不会叠加第二套机制，可以从一个清晰的新模块开始。

### 3.2 框架已经提供可复用的接入机制

当前虚拟环境使用 LangChain `1.3.14`。该版本的 `create_agent` 已支持 middleware，`AgentMiddleware.abefore_model(...)` 会在每次模型调用前执行；消息层也已提供 `AIMessage`、`ToolMessage`、`RemoveMessage` 和 `tool_call_id`。

LangChain 包内虽然还提供 `ContextEditingMiddleware`、`ClearToolUsesEdit` 和 `SummarizationMiddleware`，但它们目前没有在项目中注册。它们可以作为实现参考，不建议直接拼装成四个 middleware：本需求要求固定的 `L3 -> L1 -> L2 -> L4` 顺序，而且 L3 还包含项目自己的落盘目录规则。一个小型自定义 middleware 更容易保证顺序，也更适合面试解释。

会复用的现有能力是：

- `create_agent(..., middleware=[...])` 注册机制；
- LangChain 标准消息对象及 message id/tool_call_id；
- LangGraph 的 message reducer，通过 `RemoveMessage(REMOVE_ALL_MESSAGES)` 原子替换上下文；
- 当前 `ChatOpenAI` 实例作为 L4 summary model；
- 当前 JSON session 继续作为 UI 完整会话的来源。

### 3.3 两个必须先处理的现状

1. **工具当前提前截断结果。** `read_file` 只返回前 10,000 字符，`terminal` 只返回前 5,000 字符。若保持现状，L3 middleware 只能看到截断后的文本，无法把“完整大结果”落盘。实施时应移除这两个硬切片，把预算控制统一交给 L3。
2. **持久化 session 与 Agent context 不是同一种消息。** session JSON 会保存 assistant 的 `tool_calls` 供 UI 展示，但 `load_session_for_agent()` 下一轮只恢复非空的 user/assistant 文本，旧 tool use/result 不会重新进入 Agent。因此：
   - L1/L2 的 tool pair 保护主要作用于当前 LangGraph 调用中的真实 `AIMessage + ToolMessage`；
   - UI 的完整历史不应被压缩覆盖；
   - L4 的结果需要由 Checkpointer 持久化，否则 Agent Loop 内部的压缩 state 会在下一个 HTTP 回合丢失。

## 4. 最小设计

### 4.1 新增一个纯逻辑模块

新增 `backend/service/context_manager.py`，集中放置：

- `ContextPolicy`：所有 Demo 阈值；
- `estimate_tokens(messages)`：使用 LangChain 的近似 token counter，不引入新依赖；
- `apply_tool_result_budget(...)`：L3；
- `snip_middle_messages(...)`：L1；
- `compact_old_tool_results(...)`：L2；
- `summarize_and_rebuild(...)`：L4；
- tool call/result 分组与安全切点辅助函数；
- tool output 的落盘辅助函数。

这些函数尽量保持“输入 messages，返回新 messages”，不原地修改调用方对象，便于独立单测和面试演示。

建议的初始 Demo 参数：

| 参数 | 初始值 | 说明 |
|---|---:|---|
| `max_context_tokens` | 50,000 | 触发 L4 的近似输入预算，后续可按模型调整 |
| `max_messages` | 50 | L1 触发数量 |
| `keep_head_messages` | 3 | 保留最早目标/约束 |
| `keep_recent_tool_results` | 3 | L2 保留最近完整结果数 |
| `tool_results_budget_bytes` | 200,000 | 单轮全部 tool results 总预算 |
| `persist_result_min_bytes` | 30,000 | 单条结果满足该值才落盘 |
| `preview_chars` | 2,000 | L3 上下文 preview |
| `summary_max_tokens` | 2,000 | L4 summary 输出上限 |

阈值放入现有 `Settings`，允许 `.env` 覆盖；不单独建立复杂配置系统。

### 4.2 新增一个编排 middleware

新增 `backend/middleware/context_management.py`，只定义一个 `ContextManagementMiddleware(AgentMiddleware)`。

它的 `abefore_model(state, runtime)` 每次按以下步骤运行：

1. 复制 `state["messages"]`；
2. 执行 L3，检查最近一批 ToolMessage 的合计大小，将最大的结果依次落盘；
3. 执行 L1，保留头部和尾部，并按 tool_call_id 调整切点；
4. 执行 L2，仅保留最近 N 个 ToolMessage 的完整 content；
5. 重新估算 token；
6. 若仍超预算，使用非 streaming 的现有模型生成结构化 summary；
7. 返回 `RemoveMessage(REMOVE_ALL_MESSAGES) + rebuilt_messages`，让压缩结果进入 LangGraph state，而不只影响单次 request 副本。

L4 summary prompt 固定要求保留：

- 当前目标；
- 关键发现与决策（包括被否决方案及原因）；
- 已读取/修改文件；
- 剩余任务；
- 用户约束。

summary 调用不绑定 tools，并对输入做长度上限，避免总结模型自己再次超上下文。Demo 首版不实现自动重试和 reactive compact；如果验证时供应商仍返回 prompt-too-long，再作为单独的小版本补一条最多一次的应急路径。

### 4.3 L1 的 pair-safe 规则

LangChain 中的一组工具交互通常是：

```text
AIMessage(tool_calls=[id-a, id-b])
ToolMessage(tool_call_id=id-a)
ToolMessage(tool_call_id=id-b)
```

L1 不按单条消息盲切，而先把上述消息看成一个不可拆分组。若头部或尾部切点落在 ToolMessage 上，则回退到发起对应 tool call 的 AIMessage；如果无法找到对应消息，则跳过整段连续 ToolMessage。被裁掉的中间只插入一条普通 HumanMessage placeholder。

### 4.4 L3 的文件格式与路径

建议目录：

```text
backend/.task_outputs/tool-results/<session-or-run>/<tool_call_id>.txt
```

ToolMessage 中替换成：

```text
<persisted-output>
Full output: .task_outputs/tool-results/.../call-id.txt
Preview:
前 2000 字符
</persisted-output>
```

文件名只使用清洗后的 session/run id 和 tool_call_id；目录加入 `.gitignore`。不做去重、压缩、TTL 和清理任务。

### 4.5 保持 UI 历史完整，使用 LangGraph Checkpointer 保存活跃上下文

Session JSON 保留完整 `human / ai / tool` 档案；SQLite Checkpointer 以 Session ID 作为 `thread_id`，保存 L1/L2/L3/L4 处理后的 LangGraph `state["messages"]`。

首次运行且 thread 尚无 Checkpoint 时，从 Session JSON 恢复完整历史进行播种。后续用户回合只提交当前 `HumanMessage`，由 LangGraph 自动恢复上一轮压缩后的 state 并追加新消息。这样 middleware 在 Agent Loop 内触发的 L4 也能跨回合、跨 API 重启延续。

## 5. 预计修改文件

| 文件 | 动作 | 最小改动 |
|---|---|---|
| `backend/service/context_manager.py` | 新增 | 四层纯逻辑、摘要 prompt、落盘 |
| `backend/middleware/__init__.py` | 新增 | 导出 middleware |
| `backend/middleware/context_management.py` | 新增 | `abefore_model` 编排与 LangGraph state 替换 |
| `backend/graph/agent_factory.py` | 修改 | 构造并注册一个 middleware |
| `backend/graph/agent.py` | 小改 | 将 session/run 标识传给 context 管理；执行持久化历史预检 |
| `backend/service/session_manager.py` | 小改 | 标准消息档案读写与旧格式迁移 |
| `backend/config/config.py` | 小改 | 增加少量 context 阈值配置 |
| `backend/tools/read_file_tool.py` | 小改 | 去掉 10,000 字符硬截断，让 L3 接管 |
| `backend/tools/terminal_tool.py` | 小改 | 去掉 5,000 字符硬截断，让 L3 接管 |
| `backend/tests/test_context_manager.py` | 新增 | 四层策略与 pair-safe 边界测试 |
| `backend/tests/test_core.py` | 小改 | middleware 注册、session summary 兼容测试 |
| `.gitignore` | 小改 | 忽略 `.task_outputs/` |
| `README.md` | 小改 | 完成后补充机制和运行目录说明 |

不计划修改前端、API 路由形状、SSE 事件协议、LLM provider 选择或现有两个工具的调用参数。

## 6. 实施顺序

### 第一步：先锁定消息行为

新增测试样本覆盖 HumanMessage、普通 AIMessage、带多个 tool_calls 的 AIMessage 和连续 ToolMessage，先写出安全分组、token 估算和 placeholder 的期望结果。

验收：任何 L1/L4 切点都不留下孤立 ToolMessage，也不留下缺少结果的 tool call 组。

### 第二步：实现 L3、L1、L2 纯函数

先实现零 API 的三层，并用临时目录验证 L3 完整落盘、preview 可见、L2 最近 N 条保留、L1 中间裁剪。

验收：执行顺序固定为 `L3 -> L1 -> L2`，输入 messages 不被意外原地污染。

### 第三步：实现 L4 summary

加入 summary prompt、近似阈值判断和 messages 重建。单测使用 fake model，不调用真实 API。

验收：summary 包含五类必需信息；L4 前一定已经跑过前三层；summary 失败时明确抛错，不静默用错误文本替换上下文。

### 第四步：注册 middleware

在 `agent_factory.py` 使用现有 `create_agent(..., middleware=[...])`，不改 Agent Loop。通过 fake graph/model 记录每次 model call 前收到的 messages。

验收：首次调用以及每次 tool result 返回后的模型调用都会经过 Context Management。

### 第五步：接入持久化 Checkpointer

保留 UI 的完整 Session messages，并用 `AsyncSqliteSaver` 保存 Agent 活跃 state。验证旧 Session 首次播种、同一 `thread_id` 后续恢复、API 重启后 summary 仍可复用，以及删除 Session 时同步删除 thread。

验收：前端历史不丢；L4 后的活跃上下文不会在下一 HTTP 回合被完整 Session 覆盖。

### 第六步：把工具硬截断交给 L3

移除两个工具尾部的字符串切片，使用构造的大文件/大命令输出来验证完整内容真正写入 `.task_outputs`，LLM 只看到路径和 preview。

验收：落盘文件等于原始完整输出，ToolMessage 小于预算，路径可由现有 `read_file`/`terminal` 再读取。

### 第七步：全量回归与说明

运行：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests
```

再进行一次 fake model 多轮工具调用集成测试；若本地 API key 可用，再做一轮手工 smoke test，但不把真实 API 作为自动测试前提。

## 7. 测试清单

- 少于阈值时，messages 内容与顺序不变。
- L3：单轮总结果未超预算时不落盘。
- L3：超预算时按结果大小从大到小落盘，直到回到预算内。
- L3：落盘内容完整，placeholder 同时含相对路径和 preview。
- L1：普通历史保留头部和尾部，中间只有一个 placeholder。
- L1：一个 AIMessage 发起多个 tool calls 时，整组保留或整组裁掉。
- L2：只保留最近 N 个 ToolMessage 的完整 content，旧结果保留 id/name/status 等结构字段。
- 顺序：大旧结果在 L2 替换前已经由 L3 落盘。
- L4：前三层后仍超阈值才调用 fake summary model。
- L4：重建后保留 summary 和 pair-safe 的近期尾部，不只剩一条无上下文摘要。
- middleware：一次 Agent 调用内发生两次 LLM 调用时，管理器执行两次。
- session/checkpoint：旧格式 JSON 可读；UI messages 不被压缩覆盖；压缩后的 state 可跨回合与重启加载。
- 回归：现有 session、prompt、工具、SSE 路由测试继续通过。

## 8. 明确不在首版实现的内容

- 精确 tokenizer 或每个 provider 的自动 context-window 探测；
- prompt cache / cache editing；
- read-file state 与 compact 后自动恢复最近文件；
- 自动清理历史输出文件；
- 并发写同一 session 的锁；
- 手动 `/compact` 工具、API 或 UI 按钮；
- 多级 prompt-too-long retry、fallback model 和熔断器。

这些都不影响四层机制的面试展示，首版保持代码短、职责清晰、可测试。

## 9. 建议的确认结论

建议按本文件方案实施，核心选择是：**一个自定义 LangChain middleware 负责每次模型调用前的四层编排，一个纯逻辑 service 负责可测试算法；完整 UI session 与压缩后的 LangGraph checkpoint 分开保存。**

该方案已按确认结果实施；实际文件与验证结果以仓库当前代码和测试为准。

## 10. 实施后补充：Session 消息对齐

后续实施中已消除原方案 3.3 节记录的消息结构差异：

- Session JSON 现在持久化 `human / ai / tool` 标准消息，而不是仅供 UI 展示的 `user / assistant + tool_calls`。
- `AIMessage.tool_calls[].id` 与 `ToolMessage.tool_call_id` 原样关联，下一轮可恢复完整工具调用链。
- SSE 的 `token / tool_start / tool_end / done` 仅负责实时传输，不再用于反向拼装 Session。
- Session API 将标准消息投影成前端原有结构，因此前端无需修改。
- 旧 Session 在读取时自动迁移到 `schema_version: 2`。
- LangGraph SQLite Checkpointer 保存压缩后的活跃上下文；Session JSON 继续保存完整标准消息档案。

当前持久化边界如下：

```text
LangGraph HumanMessage / AIMessage / ToolMessage
  -> message_codec.py
  -> sessions/<id>.json（完整标准消息档案）
       -> get_history()：投影成前端展示消息
  -> checkpoints.sqlite（按 thread_id 保存压缩后的 Agent state）
       -> 首次无 checkpoint 时由 Session 播种
       -> 后续回合由 LangGraph 自动恢复
```
