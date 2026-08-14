# SQLite 长期 Memory：现状分析与执行计划

> 状态：待确认，仅完成设计，尚未实现 Memory 代码  
> 参考：[wx759/learn-claude-code - s09_memory](https://github.com/wx759/learn-claude-code/tree/main/s09_memory)

## 1. 目标与边界

本次目标是在现有 Repository Steward 中加入一个简化但相对工程化的跨 Session 长期 Memory：

```text
select -> load -> main agent -> extract -> save
```

核心约束：

- Memory 使用独立 SQLite 数据库持久化，不使用“一条 Memory 一个 Markdown 文件”。
- 存储层与选择、提取策略解耦。
- Selector 和 Extractor 都是独立的无工具 LLM side-call，不进入主 Agent 的 messages、LangGraph state 或 checkpointer。
- 选中的 Memory 只临时影响本轮主模型，不写入 Session JSON 和 `checkpoints.sqlite`。
- 复用现有模型配置、错误恢复边界和 Context Compact，不重写 Agent Runtime。
- 第一版不实现向量检索、embedding、自动 consolidation、多用户隔离和 Memory 管理 UI/API。

参考项目中值得保留的设计思想是：catalog 只暴露 `name + description`，LLM 最多选择 5 条，再按需加载正文；提取发生在一轮真正结束后，并优先使用 compact 前快照；side-call 失败时不阻断主流程。参考项目的文件存储、常驻 `MEMORY.md` 索引和 Dream/consolidation 不照搬。

## 2. 当前项目真实结构

与本次改造直接相关的代码如下：

```text
backend/
├── app.py                              # FastAPI lifespan；初始化 SQLite checkpointer 和 AgentManager
├── api/
│   └── chat.py                         # SSE 请求边界、Session 保存、首轮标题生成
├── config/
│   └── config.py                       # LLM 与 Context/Recovery 配置
├── graph/
│   ├── agent.py                        # AgentManager、主请求编排、事件和当前轮原始消息收集
│   ├── agent_factory.py                # create_agent(...) 唯一装配点
│   └── llm.py                          # OpenAI-compatible ChatOpenAI 构造
├── middleware/
│   ├── context_management.py           # 每次主模型调用前执行 Context Compact
│   └── error_recovery.py               # 主模型错误恢复和续写
├── service/
│   ├── context_manager.py              # L3 -> L1 -> L2 -> L4
│   ├── session_manager.py              # Session JSON 完整历史
│   └── message_codec.py                # LangChain message 与持久化/UI 结构转换
├── sessions/                           # 完整 UI/Agent 会话归档
├── checkpoints.sqlite                  # LangGraph 当前会话运行状态与 compact 结果
└── tests/
    ├── test_core.py
    ├── test_context_manager.py
    └── test_checkpoint_persistence.py
```

## 3. 当前 Agent Loop、State、Checkpointer 和消息流

### 3.1 应用初始化

`backend/app.py` 的 lifespan 当前执行：

```text
refresh skill snapshot
  -> 打开 backend/checkpoints.sqlite
  -> AsyncSqliteSaver.setup()
  -> AgentManager.initialize(backend_dir, checkpointer)
```

`AgentManager.initialize()` 创建：

- `SessionManager`：完整 Session JSON；
- 工具集；
- `ContextManager`；
- LangGraph checkpointer 引用；
- 延迟构建的主 Agent graph 和非流式 summary model。

长期 Memory 应在这里初始化，但使用独立 `memory.sqlite`，不能与 LangGraph 的 checkpoint schema 混在一起。

### 3.2 一次请求的真实调用链

```text
POST /api/chat
  -> SessionManager.load_session_record(session_id)
  -> AgentManager.load_checkpoint_seed(session_id)
       -> checkpoint 已存在：返回 []，由 LangGraph 自己恢复 state
       -> checkpoint 不存在：从 Session JSON 恢复完整 LangChain messages 作为首次 seed
  -> AgentManager.astream(user_message, history, session_id)
       -> _build_messages(history)
       -> 追加当前 HumanMessage
       -> create_agent(...).astream(
            messages,
            thread_id=session_id,
            context=AgentRunContext(...)
          )
       -> LangGraph 内部循环：model -> tools -> model -> ...
       -> 收集 token、AIMessage、ToolMessage
       -> 生成 done 事件和 `_session_messages`
  -> api/chat.py 在 done 时把 `_session_messages` 追加到 Session JSON
  -> 首轮额外生成 Session title
```

### 3.3 当前三层状态职责

| 状态面 | 当前职责 | Memory 改造规则 |
|---|---|---|
| `sessions/*.json` | 用户可见的完整对话和工具轨迹归档 | 不写入临时 Memory 注入；仅保留真实用户/Agent 交互 |
| `checkpoints.sqlite` | LangGraph thread state、tool loop、compact 后 state | 不写入 Memory 正文或 Selector/Extractor 消息 |
| `AgentRunContext` | 单次 graph run 的运行时恢复状态 | 增加只读的本轮 Memory 文本和 compact 前快照，是临时注入的承载点 |

### 3.4 Context Compact 的真实接入点

`ContextManagementMiddleware.abefore_model()` 在主 Agent 每次模型调用前执行：

```text
state["messages"]
  -> ContextManager.manage(...)
  -> L3 大工具结果落盘
  -> L1 中间消息裁剪
  -> L2 旧工具结果压缩
  -> L4 必要时生成摘要并重建 messages
  -> RemoveMessage(REMOVE_ALL_MESSAGES) + rebuilt messages
```

这意味着不能把 Memory 作为普通 `HumanMessage` 或 `SystemMessage` 放进 `graph.astream({"messages": ...})`：普通输入会被 LangGraph reducer/checkpointer 持久化，并参与后续 compact，造成每轮重复膨胀。

本计划改为：Selector/Loader 在进入 graph 前完成，把格式化 Memory 放在 `AgentRunContext`；新增动态 prompt middleware，在每次真正调用主模型时将 Memory 追加到该次 `ModelRequest.system_message`。动态 system message 不是 graph messages，因此不会进入 Session JSON 或 checkpoint。

## 4. 目标架构

### 4.1 新增模块

建议新增一个独立领域包：

```text
backend/memory/
├── __init__.py
├── models.py          # MemoryMetadata / MemoryRecord / MemoryDraft 与类型约束
├── store.py           # MemoryStore，唯一接触 sqlite3 的模块
├── selector.py        # LLM 选择 + 关键词降级
├── extractor.py       # 终局提取、严格解析和去重判断
├── prompts.py         # Selector/Extractor 固定 prompt 与 Memory 注入格式
└── middleware.py      # 将 AgentRunContext 中的 Memory 临时追加到 system prompt
```

职责划分：

```text
MemoryStore       = 如何持久化
MemorySelector    = 本轮需要哪些 Memory
MemoryExtractor   = 本轮产生了哪些新的长期信息
Memory middleware = 如何临时影响主模型
AgentManager      = 只负责编排 select/load/main/extract/save
```

Selector/Extractor 只依赖 `MemoryStore` 的公开方法和模型对象，不导入 `sqlite3`，也不拼 SQL。

### 4.2 SQLite 文件

第一版使用：

```text
backend/memory.sqlite
```

理由：

- 与 `checkpoints.sqlite` 物理隔离，职责清晰；
- 当前 Docker/本地运行都以整个 `backend/` 为持久化目录；
- 无需引入新的数据库依赖，Python 标准库 `sqlite3` 足够；
- 后续替换存储实现时，不影响 Selector/Extractor。

将以下文件加入 `.gitignore`：

```gitignore
backend/memory.sqlite*
```

## 5. Memory Store 设计

### 5.1 表结构

```sql
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (
                    type IN ('user', 'project', 'feedback', 'reference')
                ),
    description TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_name_nocase
ON memories(name COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS ix_memories_updated_at
ON memories(updated_at DESC);
```

约定：

- `id` 使用 UUID hex，由 Store 创建；
- 时间使用 UTC ISO-8601 字符串；
- `name` 大小写不敏感唯一，阻止完全同名重复项；
- `type` 只允许 `user/project/feedback/reference`；
- 所有文本在写入前去除首尾空白，空值拒绝保存。

### 5.2 数据对象

`models.py` 使用轻量 dataclass：

- `MemoryMetadata(id, name, type, description, created_at, updated_at)`；
- `MemoryRecord(..., body)`；
- `MemoryDraft(name, type, description, body)`。

不把 SQLite Row 泄漏到上层。

### 5.3 `MemoryStore` API

```python
class MemoryStore:
    def list_metadata(self) -> list[MemoryMetadata]: ...
    def get_memories(self, ids: Sequence[str]) -> list[MemoryRecord]: ...
    def save_memory(self, draft: MemoryDraft) -> MemoryRecord: ...
    def update_memory(self, memory_id: str, draft: MemoryDraft) -> MemoryRecord: ...
    def delete_memory(self, memory_id: str) -> bool: ...
```

实现约束：

- 构造时只保存数据库路径并执行幂等 schema 初始化；
- 每个公开操作使用短生命周期 connection；
- 启用 `PRAGMA journal_mode=WAL`、`foreign_keys=ON`、`busy_timeout`；
- 写操作使用显式事务；
- `get_memories(ids)` 必须只返回白名单 id，保持 Selector 顺序，并忽略不存在 id；
- SQL 只存在于 `store.py`；
- 异步 Agent 路径通过 `asyncio.to_thread(...)` 调用同步 Store API，避免 SQLite 文件 I/O 阻塞事件循环。

## 6. Memory Selector

### 6.1 输入和输出

每次 `AgentManager.astream()` 进入主 graph 前：

1. `MemoryStore.list_metadata()` 读取全部 catalog；
2. catalog 只格式化 `id + name + description`，不发送 body；
3. 将当前用户原始请求和 catalog 交给非流式 side-model；
4. 模型只允许返回 JSON id 数组；
5. 解析、白名单过滤、去重并截断为最多 5 条。

建议模型输出：

```json
["memory-id-1", "memory-id-2"]
```

Selector prompt 明确要求：

- 只选对当前请求有直接帮助的 Memory；
- 不确定时不选；
- 最多 5 条；
- 不返回名称、解释、Markdown 或代码块；
- 只能返回 catalog 中存在的 id。

### 6.2 独立 LLM 调用

Selector 复用 `AgentManager._build_chat_model()` 生成的 non-streaming、temperature 0 模型，但直接调用 `model.ainvoke(...)`：

- 不通过 `create_agent`；
- 不绑定工具；
- 不传 `thread_id`；
- 不使用 checkpointer；
- 输入和结果都不追加到 `persisted_turn_messages`。

### 6.3 失败降级

以下情况统一降级：

- 模型/API 异常；
- 超时；
- 返回内容不是合法 JSON 数组；
- id 不在 catalog；
- 返回超过 5 条。

关键词降级使用当前用户请求与 `name + description` 做规范化后的词项交集评分，按分数和 `updated_at` 排序，返回前 5 条。中文文本至少保留连续汉字片段和双字片段，英文按小写字母数字词项匹配。无匹配时返回空列表，不把“最新 Memory”强行注入。

## 7. Memory Load 与临时注入

### 7.1 加载

Selector 返回 id 后：

```text
selected ids
  -> MemoryStore.get_memories(ids)
  -> 完整 MemoryRecord（包含完整 body）
  -> 格式化为本轮 memory_context
```

格式建议：

```text
<long-term-memory>
The following records are cross-session context. Use them only when relevant.
Do not mention that memory was loaded unless the user asks.

<memory id="..." type="user" name="...">
description: ...
body:
...
</memory>
</long-term-memory>
```

### 7.2 临时注入机制

扩展 `AgentRunContext`：

```python
@dataclass
class AgentRunContext:
    session_id: str = "default"
    recovery_state: RecoveryState = field(default_factory=RecoveryState)
    memory_context: str = ""
    raw_turn_snapshot: list[AnyMessage] = field(default_factory=list)
```

新增 `MemoryPromptMiddleware`，使用当前 LangChain 版本已有的 `ModelRequest.override(system_message=...)` 或等价 `dynamic_prompt` 机制：

```text
原 static system prompt
  + 本轮 AgentRunContext.memory_context
  -> 仅当前 ModelRequest.system_message
```

关键保证：

- 不返回 `{"messages": ...}`；
- 不创建持久化 HumanMessage；
- 不修改 `state["messages"]`；
- 不写入 Session JSON；
- 不写入 LangGraph checkpoint；
- 同一轮 tool loop 中每次主模型调用都能看到相同 Memory；
- 下一轮重新 select，不沿用上轮选择结果。

Middleware 建议注册顺序：

```text
ContextManagementMiddleware
MemoryPromptMiddleware
ErrorRecoveryMiddleware
ToolErrorMiddleware
```

Context Compact 继续只管理 conversation messages；Memory 作为 system prompt 的临时附加内容，不被 L1/L2/L3/L4 修改。

## 8. Memory Extractor

### 8.1 触发时机

只在主 Agent graph 真正结束、已得到最终回答之后触发一次：

```text
主 graph astream 结束
  -> final_content 确定
  -> persisted_turn_messages/raw_turn_snapshot 确定
  -> extract_memories(...)
  -> save new memories
  -> yield done
```

不在每次 tool call 后提取，不在 Context Compact middleware 内提取，不从 SSE token 增量提取。

Extractor 失败必须被捕获并记录日志，不能把已经成功的主回答改成 error。第一版可以同步等待一次有限超时的提取调用，保证请求结束时 Memory 已落盘；如果后续观察到明显尾延迟，再单独演进为可靠后台队列，而不是直接 fire-and-forget。

### 8.2 compact 前原始快照

当前 `persisted_turn_messages` 在 `AgentManager.astream()` 外层收集本轮原始：

- 当前 HumanMessage；
- 主模型产生的 AI tool-call message；
- 完整 ToolMessage；
- 最终 AIMessage。

它不依赖 `ContextManagementMiddleware` 重建后的 checkpoint state，因此适合作为第一版 compact 前原始快照。实现时应显式创建 `raw_turn_snapshot`，在消息进入任何 Memory 格式化前复制本轮消息，避免后续代码把 compact 后 state 误传给 Extractor。

Extractor 输入不需要发送无限工具输出。格式化规则：

- 始终保留本轮用户原文和最终助手回答；
- 保留工具名和有助于长期项目事实/reference 的有限结果片段；
- 设置输入字符预算，超出时优先保留用户消息、最终回答和最新工具结果；
- 不使用 L4 summary 替代该快照。

### 8.3 提取范围

只允许四类：

| type | 内容 | 示例 |
|---|---|---|
| `user` | 稳定用户偏好 | 用户长期偏好 PowerShell/浅色 UI |
| `project` | 长期项目事实 | 本项目后端工作目录就是 `backend/` |
| `feedback` | 用户对 Agent 长期工作方式的反馈 | 启动脚本应复用成熟项目的交互模式 |
| `reference` | 后续仍有价值的重要入口 | 某类问题应查看的文件、文档或服务边界 |

明确禁止：

- 仅本轮有效的 TODO、临时端口占用、一次错误栈；
- 已完成任务的流水账；
- 大段工具输出或代码正文；
- API key、token、密码等秘密；
- 从模型猜测出来、没有本轮证据支持的事实；
- 已被现有 catalog 覆盖的同义 Memory。

### 8.4 Existing catalog 与输出

提取前重新读取最新 `name + description` catalog，避免 Selector 到 Extractor 之间已有写入导致重复。

Extractor 只返回严格 JSON 数组：

```json
[
  {
    "name": "prefer-light-ui",
    "type": "user",
    "description": "User prefers light UI themes.",
    "body": "Use a restrained light palette for Repository Steward UI work."
  }
]
```

解析后执行：

1. JSON/schema 校验；
2. type 白名单校验；
3. 字段长度与空值校验；
4. name 大小写去重；
5. 与已有 `name + description` 做规范化精确/高重叠检查；
6. `MemoryStore.save_memory()` 保存真正的新 Memory。

第一版 Extractor 只新增，不自动更新或删除已有 Memory。`update_memory/delete_memory` 先作为 Store 完整能力和测试接口保留，避免无 consolidation 时让 LLM 擅自覆盖长期事实。

## 9. 与 `AgentManager` 的最小集成

### 9.1 初始化

`AgentManager` 新增：

```python
self.memory_store
self.memory_selector
self.memory_extractor
self._side_model
```

`initialize()`：

```text
MemoryStore(base_dir / "memory.sqlite")
  -> MemorySelector(store, side_model)
  -> MemoryExtractor(store, side_model)
```

Selector、Extractor、标题和 L4 summary 可以复用相同模型配置，但必须是彼此独立的 `ainvoke` 请求，不能共享消息列表或 graph state。

### 9.2 `astream()` 前后插入点

目标伪代码：

```python
async def astream(message, history, session_id):
    catalog = await to_thread(memory_store.list_metadata)
    selected_ids = await memory_selector.select(message, catalog, limit=5)
    memories = await to_thread(memory_store.get_memories, selected_ids)

    current_user_message = HumanMessage(content=message)
    raw_turn_snapshot = [current_user_message]
    run_context = AgentRunContext(
        session_id=session_id,
        memory_context=format_memory_context(memories),
        raw_turn_snapshot=raw_turn_snapshot,
    )

    async for event in main_agent.astream(..., context=run_context):
        # 保持现有 token/tool/recovery/SSE 行为
        # 同时收集未压缩的本轮 AIMessage/ToolMessage
        ...

    final_content = ...
    await memory_extractor.extract_and_save(raw_turn_snapshot, final_content)
    yield done
```

保持不变：

- `api/chat.py` 的 SSE 协议；
- `_session_messages` 的保存位置；
- `load_checkpoint_seed()`；
- Session 删除和 checkpoint 删除逻辑；
- Context Compact、Recovery、工具注册；
- 前端聊天协议和 UI。

### 9.3 Selector/Extractor 可观测性

第一版不向前端发送 Memory 详情。后端使用结构化日志记录：

- Selector 选中数量和是否 fallback；
- Extractor 候选数量、实际保存数量；
- side-call 失败类别；
- 绝不记录完整 Memory body 或秘密。

## 10. 配置计划

在 `Settings` 和 `.env.example` 中加入少量配置：

```dotenv
MEMORY_ENABLED=true
MEMORY_SELECTOR_MAX_ITEMS=5
MEMORY_SIDE_CALL_TIMEOUT_SECONDS=20
MEMORY_EXTRACT_INPUT_CHARS=12000
MEMORY_EXTRACT_MAX_ITEMS=3
```

规则：

- `MEMORY_ENABLED=false` 时完全跳过 select/load/extract/save，主 Agent 行为回到现状；
- `MEMORY_SELECTOR_MAX_ITEMS` 上限硬限制为 5；
- side-call 超时只影响 Memory，不影响主回答；
- 第一版不增加独立 provider/model 配置，复用当前主 LLM provider/model，避免配置面膨胀。

## 11. 并发、失败与安全边界

### 11.1 并发

- SQLite WAL + 短连接 + busy timeout；
- `save_memory` 依赖数据库唯一索引处理并发同名写入；
- Selector 读取的 catalog 是快照，Extractor 保存前重新读取 catalog；
- 单进程 reload 和未来多 worker 都不共享长生命周期 sqlite connection。

### 11.2 失败策略

| 失败点 | 行为 |
|---|---|
| 数据库初始化失败 | 启动失败并打印明确路径，避免假装 Memory 可用 |
| Selector LLM 失败 | 关键词降级 |
| catalog 为空 | 直接跳过 select/load |
| 某些 id 已删除 | 忽略缺失 id，加载其余项 |
| Memory 注入格式化失败 | 本轮不注入，继续主 Agent |
| Extractor LLM/解析失败 | 记录日志，不影响主回答 |
| 单条 Memory 保存冲突 | 跳过冲突项，继续保存其他项 |

### 11.3 安全

- Extractor prompt 明确禁止 secrets；
- 保存前增加常见 credential 形态过滤，命中时拒绝该候选；
- body 只注入主模型，不经 SSE 发给前端；
- 第一版是单用户、单仓库 Memory，文档中明确不能直接扩展为多租户服务。

## 12. 文件修改清单

### 新增

```text
backend/memory/__init__.py
backend/memory/models.py
backend/memory/store.py
backend/memory/prompts.py
backend/memory/selector.py
backend/memory/extractor.py
backend/memory/middleware.py
backend/tests/test_memory_store.py
backend/tests/test_memory_selector.py
backend/tests/test_memory_extractor.py
backend/tests/test_memory_integration.py
```

### 修改

```text
backend/graph/agent.py                 # initialize、select/load、raw snapshot、extract/save
backend/graph/agent_factory.py         # 注册 MemoryPromptMiddleware
backend/middleware/context_management.py # 扩展 AgentRunContext 的临时字段
backend/config/config.py               # Memory 配置
backend/config/.env.example            # 配置示例
.gitignore                             # 忽略 memory.sqlite*
README.md                              # 说明长期 Memory 与 Session/Checkpoint 的职责
```

### 不修改

```text
backend/api/chat.py                    # SSE 和 Session 保存边界保持不变
backend/service/session_manager.py     # 长期 Memory 不混入 Session JSON
backend/service/context_manager.py     # Compact 算法保持不变
frontend/                              # 第一版无 Memory UI
```

实际实现时若 `AgentRunContext` 仍保留在 `context_management.py`，只扩展字段；若为避免 middleware 反向依赖而移动到单独文件，需要保持单一类型入口，不能定义两份 context schema。

## 13. 测试计划

### 13.1 Store 单元测试

- 初始化数据库和表/index 幂等；
- `save -> list_metadata -> get`；
- `update` 只更新 `updated_at`，保留 `created_at`；
- `delete` 返回值正确；
- id 顺序保持；
- 非法 type、空字段、重复 name 被拒绝；
- 两个 Store 实例并发读取/顺序写入正常。

### 13.2 Selector 测试

- catalog 为空时不调 LLM；
- 合法 JSON ids 正确选择；
- 非法/未知/重复 id 被过滤；
- 最多 5 条；
- 模型异常和解析异常触发关键词降级；
- fallback 无匹配返回空列表；
- Selector 请求不进入 graph payload/checkpoint/session。

### 13.3 Extractor 测试

- 只接受四类 Memory；
- 无长期信息时返回空；
- 已有 catalog 覆盖时不保存；
- 临时任务细节和 secrets 不保存；
- 模型异常、非法 JSON 不影响主结果；
- 多候选中单条冲突不阻断其他保存；
- 使用 raw snapshot 而非 `[Compacted context]` 摘要。

### 13.4 集成测试

- 第一轮保存偏好，新 Session 能被 Selector 选中并影响主模型；
- 最多注入 5 条完整 body；
- 注入内容不出现在 `sessions/*.json`；
- 注入内容不出现在 `checkpoints.sqlite` 的 thread messages；
- Selector/Extractor prompt 不出现在 `_session_messages`；
- tool loop 的第二次主模型调用仍可看到同一批 Memory；
- Context Compact 触发后 Extractor 仍收到本轮原始快照；
- `MEMORY_ENABLED=false` 时现有测试行为不变；
- 删除 Session 不删除长期 Memory。

### 13.5 回归验证

```powershell
uv run --with pytest --with-requirements requirements.txt `
  python -m pytest tests -q `
  --basetemp ..\.repository_steward\pytest_tmp_memory
```

并执行：

```powershell
npm.cmd run build
git diff --check
```

## 14. 分阶段实施顺序

### 阶段 A：Store 与模型

1. 新建 `backend/memory/`；
2. 定义 dataclass/type；
3. 实现 schema 和 CRUD；
4. 完成 Store 单测。

验收：不接 Agent 也能独立验证 SQLite 完整 API。

### 阶段 B：Selector 与 Load

1. 实现 catalog prompt/JSON parser；
2. 实现关键词 fallback；
3. 初始化 Selector；
4. 在 `AgentManager.astream()` 前选择并加载；
5. 使用 runtime context + dynamic system prompt 临时注入；
6. 验证 Session/checkpoint 不含 Memory 正文。

验收：预置一条 Memory 后，相关请求会加载它，无关请求不加载。

### 阶段 C：Extractor 与 Save

1. 显式保留本轮 compact 前 raw snapshot；
2. 实现 Extractor prompt、解析、类型/secret/重复过滤；
3. 在主 graph 完成后调用并保存；
4. 确认失败不影响 done；
5. 完成跨 Session 测试。

验收：用户表达稳定偏好后数据库新增一条；新 Session 能重新选择并使用。

### 阶段 D：配置、文档与完整回归

1. 增加 env 配置和 `.gitignore`；
2. 更新 README 的三种持久化职责；
3. 运行全部后端测试、前端 build 和 diff check；
4. 用真实模型手工验证 select/load/extract/save 日志。

## 15. 最终运行时序

```text
用户请求
  |
  v
MemoryStore.list_metadata()
  |
  v
MemorySelector side-call --------------------失败----> keyword fallback
  |                                                     |
  +---------------- selected ids <----------------------+
  |
  v
MemoryStore.get_memories(ids)
  |
  v
AgentRunContext.memory_context
  |
  v
主 Agent graph
  -> Context Compact 只处理 conversation messages
  -> MemoryPromptMiddleware 只改本次 ModelRequest.system_message
  -> model -> tools -> model -> ... -> final
  |
  v
compact 前 raw_turn_snapshot + final answer
  |
  v
MemoryExtractor side-call
  |
  v
校验 / 去重 / secret filter
  |
  v
MemoryStore.save_memory()
  |
  v
done / Session JSON 按原逻辑保存
```

## 16. 验收标准

实现完成后至少满足：

1. `MemoryStore` 五个要求 API 全部可独立测试；
2. SQLite 中字段完整，时间和 id 稳定；
3. Selector 每轮最多选 5 条，只返回合法 id；
4. Selector 失败时关键词 fallback 可用；
5. Memory body 只临时进入主模型 system prompt；
6. Session JSON 和 checkpoint 不持久化注入正文；
7. Extractor 只在主 Agent 真正结束后调用一次；
8. Extractor 使用本轮 compact 前原始快照；
9. 只保存 user/project/feedback/reference 四类长期信息；
10. Side-call 或 Memory 保存失败不破坏主 Agent 回答；
11. 新 Session 能加载之前 Session 提取的相关 Memory；
12. 现有 Session、Context Compact、Recovery、SSE 和工具测试全部通过。

## 17. 第一版明确不做

- embedding、向量数据库或 BM25/RRF；
- 自动 Memory consolidation/Dream；
- LLM 自动更新或删除旧 Memory；
- 多用户、组织或团队隔离；
- 分布式锁和远程数据库；
- Memory 管理 API/页面；
- 将全部 Memory catalog 常驻主 system prompt；
- 把 Memory 复制进每轮 conversation history。

该边界保证第一版仍是一个可解释、可测试、可替换存储层的本地长期 Memory 模块，而不是重新引入此前被删除的复杂 Memory/RAG 子系统。
