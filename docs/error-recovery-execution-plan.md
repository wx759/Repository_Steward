# Agent 错误恢复：现状分析与最小实施方案

> 状态：已确认并实施  
> 参考：[wx759/learn-claude-code - s11_error_recovery](https://github.com/wx759/learn-claude-code/tree/main/s11_error_recovery)

## 1. 本次目标与边界

目标是在现有 Repository Steward Agent Demo 中加入一套容易讲解、容易测试的错误恢复机制，覆盖参考章节的三条核心路径：

```text
模型调用
  ├─ 输出达到 token 上限 -> 提升输出预算 / 自动续写
  ├─ 模型明确拒绝过长上下文 -> 强制执行现有 L4 -> 重试一次
  ├─ 429 / 529 / 5xx / 连接超时 -> 指数退避重试
  └─ 最终仍无法恢复 -> 形成明确、可持久化的结束状态
```

本次继续坚持个人 Demo 范围：

- 不重写 LangChain/LangGraph Agent Loop；
- 优先复用现有 middleware、message、SSE、Session 和 Checkpointer；
- 不引入任务队列、Redis、分布式锁或外部重试服务；
- 不做无限重试；
- 不对可能产生副作用的终端命令自动重放；
- 错误分类保持短小，不追求覆盖所有供应商错误码；
- 首版以当前 OpenAI-compatible 接口和百炼 `qwen3.7-plus` 为主要验证对象。

本文件最初用于实施前确认；方案现已落地。最终代码边界和验证结果见 `error-recovery-actual-implementation.md`。

## 2. 参考章节的核心机制

`s11_error_recovery` 在手写 `agent_loop()` 外包裹 `try/except`，并维护一个仅在本轮运行期间有效的 `RecoveryState`。

它实现三条主要路径：

| 路径 | 触发 | 恢复 |
|---|---|---|
| 输出截断 | `stop_reason == "max_tokens"` | 首次将输出预算从 8K 提升至 64K；仍截断则保存已生成内容并注入续写提示，最多续写 3 次 |
| 上下文超限 | `prompt_too_long`、`context_length_exceeded` 等 | Reactive compact 后重试一次；再次失败则结束 |
| 瞬态故障 | 429、529 | 指数退避和随机抖动；连续过载时可切换备用模型 |

参考代码中的教学版 Reactive compact 只保留最后 5 条消息。当前项目已经有 L4 summary 和 Checkpointer，因此不应照搬“只保留尾部”的做法，而应复用现有 L4 生成可继续工作的摘要。

## 3. 当前项目的真实错误边界

### 3.1 模型调用在 LangChain Agent Loop 内部

当前调用链是：

```text
POST /api/chat
  -> AgentManager.astream()
  -> create_agent(...) 生成的 LangGraph
  -> ContextManagementMiddleware.abefore_model()
  -> 内部模型节点
  -> 内部工具节点
  -> 必要时再次进入模型节点
```

项目没有一段可以直接复制参考代码 `while True + client.messages.create()` 的手写循环。错误恢复应挂在 LangChain middleware 的模型调用包装钩子中，而不是在 FastAPI 外层重新实现 Agent Loop。

### 3.2 当前只有最终错误转发

`backend/api/chat.py` 目前捕获所有未处理异常并发送 SSE `error`：

```python
except Exception as exc:
    yield {"type": "error", "error": str(exc)}
```

这能避免 HTTP 连接直接崩溃，但不会执行重试、压缩、续写或模型降级。

### 3.3 当前 L4 是主动压缩，不是服务端拒绝后的恢复

`ContextManagementMiddleware.abefore_model()` 根据本地近似 token 估算执行 L3、L1、L2 和必要时的 L4。

模型服务仍可能因为以下差异拒绝请求：

- 本地使用近似 token 估算，不是供应商官方 tokenizer；
- 当前估算主要针对 `state["messages"]`；
- 实际请求还包含 System Prompt、工具 Schema 和工具参数；
- 部分供应商校验输入与最大输出预算之和；
- 切换模型后上下文窗口可能发生变化。

当前 `qwen3.7-plus` 的上下文窗口约 1M，而项目主动压缩阈值默认 50K，正常情况下再次超限的概率很低。“上下文超限后的 L4 重试”只是服务端明确拒绝后的最后兜底，不是另一套压缩算法。

### 3.4 当前没有输出截断恢复

`backend/graph/llm.py` 没有统一设置初始输出 token 预算，也没有检查最终响应中的 `finish_reason == "length"` 或等价字段。

同时，模型 token 会立即通过 SSE 发给前端。为了避免丢弃已显示内容或重新生成导致重复，本方案根据项目实际调整为：保留已生成部分，通过内部提示从中断处续写。

### 3.5 当前工具只处理了部分预期错误

- `terminal` 超时会返回错误字符串；
- `read_file` 的越界、文件不存在和目录路径会返回错误字符串；
- terminal 非零退出码作为工具输出交给模型；
- 未捕获的编码、权限和文件系统异常仍可能中断 Agent；
- 对 terminal 自动重试可能重复执行有副作用的命令。

### 3.6 Checkpoint 与失败回合可能不同步

LangGraph 可以把本轮 HumanMessage 和已完成节点写入 Checkpoint，但 Session JSON 只在收到 `done` 后追加本轮标准消息。

如果最终异常直接走 SSE `error`：

- SQLite 可能已有本轮输入；
- Session JSON 可能没有本轮输入和错误结果；
- 前端刷新历史后，刚才失败的问题可能消失；
- 下一轮 Thread 状态与完整展示档案可能不一致。

错误恢复实现需要明确最终失败回合的持久化规则。

## 4. 最小总体设计

新增一个主要 middleware：

```text
backend/middleware/error_recovery.py
```

建议包含：

```text
RecoveryPolicy              # 重试、退避、续写等阈值
RecoveryState               # 本次 HTTP/Agent run 的恢复计数
classify_model_error()       # 错误分类
retry_delay()               # 指数退避 + 抖动 + Retry-After
ErrorRecoveryMiddleware     # 模型调用包装与恢复状态机
```

注册结构：

```python
middleware=[
    ContextManagementMiddleware(...),
    ErrorRecoveryMiddleware(...),
    # 可选的 ToolErrorMiddleware(...)
]
```

两类 middleware 的职责保持分离：

| 组件 | 职责 |
|---|---|
| `ContextManagementMiddleware` | 每次模型调用前主动执行 L3 -> L1 -> L2 -> 必要时 L4 |
| `ErrorRecoveryMiddleware` | 包裹实际模型调用，处理瞬态错误、服务端上下文拒绝和输出截断 |
| `ToolErrorMiddleware` | 将未捕获的工具异常转换为模型可见的错误 ToolMessage |

LangChain 当前版本已经提供 `ModelRetryMiddleware`、`ModelFallbackMiddleware` 和 `ToolErrorMiddleware`。瞬态重试可以直接复用内置实现，但参考章节还要求上下文超限后的 L4 重试、输出截断续写、恢复事件和连续过载计数。为了让逻辑集中、面试时容易说明，建议主模型部分使用一个小型自定义 `ErrorRecoveryMiddleware`，工具异常则优先复用内置 `ToolErrorMiddleware`。

## 5. RecoveryState 的生命周期

参考实现每次进入 `agent_loop()` 都创建新的恢复状态。当前项目对应的是每次 `/api/chat` 请求创建一次：

```python
RecoveryState(
    transient_retry_count=0,
    consecutive_overload_count=0,
    l4_attempted_this_run=False,
    output_escalated=False,
    continuation_count=0,
)
```

建议将它放入本轮 `AgentRunContext`，由 `AgentManager.astream()` 创建，并传给 middleware。

这些字段不需要长期写入 SQLite：

- 新的用户回合重新从 0 开始计数；
- 因服务端拒绝而强制 L4 重建的 messages 需要进入 Checkpoint；
- 已生成的截断/续写结果需要进入 Checkpoint 和 Session；
- “本轮已经重试几次”只是执行控制状态，不是会话记忆。

## 6. 错误分类

建议集中实现 `classify_model_error(exc)`，不要在多个文件里通过字符串随意判断。

返回有限分类：

```text
rate_limit          # 429
overloaded          # 529，或供应商明确 overloaded
server_error        # 500 / 502 / 503 / 504
timeout             # API timeout / read timeout
connection          # API connection failure
prompt_too_long     # context_length_exceeded 等
authentication      # 401 / 403，不重试
bad_request         # 普通 400，不重试
unknown             # 默认不重试
```

优先读取异常对象上的：

```text
status_code
response.status_code
response.headers
```

只有供应商没有结构化信息时，才回退到异常类名和错误文本匹配。

错误展示与持久化时只保留经过清洗的类型和短消息，不保存 API Key、Authorization header、完整请求体或供应商内部响应。

## 7. 路径一：瞬态错误重试

### 7.1 重试范围

首版建议自动重试：

- 429 rate limit；
- 529 overloaded；
- 500、502、503、504；
- 模型 API 连接失败；
- 模型 API 超时。

不自动重试：

- 401、403；
- 缺少 API Key；
- 模型不存在；
- 普通参数错误；
- 本地代码的 `TypeError`、`ValueError`；
- 上下文超限，它走“强制现有 L4 并重试”路径。

### 7.2 退避算法

建议沿用参考章节：

```text
base = min(initial_delay * 2^attempt, max_delay)
delay = base + random(0, base * 25%)
```

如果响应包含有效的 `Retry-After`，优先使用服务端时间。

个人 Demo 默认建议：

```text
最大额外重试：3 次
初始延迟：0.5 秒
最大延迟：8 秒
抖动：开启
```

参考实现最多重试 10 次，但 SSE 页面可能长时间无反馈。首版默认 3 次更适合本 Demo，同时保留环境变量调整能力。

### 7.3 恢复状态事件

使用 `runtime.stream_writer` 从 middleware 发出 LangGraph custom stream event：

```json
{
  "type": "recovery",
  "reason": "rate_limit",
  "attempt": 2,
  "max_attempts": 3,
  "delay_seconds": 1.2
}
```

`AgentManager.astream()` 增加 `custom` stream mode，并把事件转成 SSE。前端可以显示：

```text
模型服务繁忙，正在进行第 2/3 次重试……
```

不建议静默等待，因为用户会误以为页面卡住。

## 8. 路径二：上下文超限后的 L4 重试

### 8.1 触发条件

只有模型服务明确返回以下类型时触发：

```text
prompt_too_long
prompt_is_too_long
context_length_exceeded
max_context_window
```

本地近似 token 超过预算仍由原来的主动 L4 处理，不走错误恢复。

### 8.2 只复用现有 L4

这不是另一套 L4 算法。普通 L4 和错误后强制 L4 使用相同的 summary prompt、保留规则和上下文重建逻辑，差别只是触发时机：

```text
普通 L4
  -> 本地近似 token 估算超阈值
  -> 调用 L4

上下文超限后的 L4 重试
  -> 本地估算没有触发 L4
  -> 模型服务器明确拒绝
  -> 跳过本地阈值，强制调用同一个 L4
```

扩展 `ContextManager.manage()` 时只需增加显式强制参数：

```python
manage(
    messages,
    summary_model=summary_model,
    run_id=session_id,
    force_summary=True,
)
```

该参数只表示跳过：

```python
if token_count <= max_context_tokens:
    return managed_messages
```

除此之外，不改变 L4 算法，不使用更激进的裁剪，也不使用另一个 summary prompt。

### 8.3 同一轮最多执行一次 L4

强制 L4 前必须检查当前上下文是否已经含有 L4 标记：

```text
模型返回上下文超限
  -> 本轮之前没有执行 L4
       -> 强制执行现有 L4
       -> 重试模型一次

  -> 本轮之前已经执行 L4
       -> 不再重复总结 summary
       -> 停止恢复并返回明确错误
```

如果普通 L4 已经执行，再对同一个 summary 运行相同 L4 通常不会明显变小，反而会多一次模型调用并继续丢失信息。

目标流程：

```text
主模型拒绝上下文
  -> ErrorRecoveryMiddleware 捕获
  -> ContextManager 强制 L4
  -> RemoveMessage(REMOVE_ALL_MESSAGES) + rebuilt messages
  -> 用新 messages 重试模型一次
  -> 成功后的压缩 State 写入 Checkpoint
```

### 8.4 必须验证的技术点

强制 L4 不能只修改一次模型请求的临时副本，必须最终进入 LangGraph State。

建议先写一个聚焦测试，验证自定义 model wrapper 返回：

```text
RemoveMessage(REMOVE_ALL_MESSAGES)
+ compacted messages
+ 成功的 AIMessage
```

能否由当前 `create_agent` 的 message reducer 正确持久化。

如果当前 LangChain 版本不接受这种 ModelResponse，则退回到一个小型 `aafter_model` state update；不为此重写 Graph。

### 8.5 Summary 模型自身的错误

L4 summary 使用独立的非流式 ChatModel 实例，它的调用不经过主模型的 model wrapper。

因此瞬态重试逻辑应提取成可复用的小函数或服务：

```text
ModelCallRetrier
  ├─ ErrorRecoveryMiddleware 调主模型时使用
  └─ ContextManager._summarize() 调 summary model 时使用
```

Summary 模型只执行429/5xx/连接超时重试。它不能在 summary 失败时再次强制 L4，避免递归恢复。

## 9. 路径三：输出截断与自动续写

### 9.1 检测方式

OpenAI-compatible 响应通常通过以下元数据表示输出达到上限：

```text
finish_reason == "length"
stop_reason == "max_tokens"
```

实现时应集中读取最终 `AIMessage.response_metadata`，并兼容以上两个常见字段，不通过回答文本猜测。

### 9.2 建议状态机

参考章节会丢弃第一次 8K 截断输出，提升到64K后从头重新生成。当前项目已经把 token 实时显示给前端，因此调整为保留已有内容并从中断处续写：

```text
初始输出预算：8K
  ↓ 达到输出上限
保留已生成的 8K 内容
  ↓
注入内部续写提示，同时将后续输出预算提升到64K
  ↓
最多自动续写3次
  ↓ 仍未完成
保留所有有效输出，标记 incomplete，结束本轮
```

当前 `qwen3.7-plus` 官方最大输出长度为 65,536 token，因此64K可作为续写请求的升级上限。为了兼容其他模型，最终应允许环境变量覆盖，而不是在 middleware 中只写死百炼数值。

### 9.3 内部怎样续写

第一次响应达到输出上限后，恢复 middleware 为下一次模型请求临时组装：

```text
HumanMessage：用户真实问题
AIMessage：已经生成但被截断的内容
HumanMessage：内部续写提示
```

内部续写提示：

```text
Output token limit hit. Resume directly.
Do not apologize or recap. Continue from the exact stopping point.
Break the remaining work into smaller pieces.
```

续写 token 直接追加到当前回答，不单独显示成新的用户消息。

前端不新建用户气泡或 assistant 气泡，只把续写 token 追加到当前 assistant 消息，并可以显示：

```text
输出达到长度限制，正在自动续写（1/3）……
```

### 9.4 内部过程与最终持久化

内部过程可能是：

```text
HumanMessage：用户真实问题
AIMessage：第一段截断内容
HumanMessage：内部续写提示
AIMessage：续写1
HumanMessage：内部续写提示
AIMessage：续写2
```

内部 continuation prompt 只是本轮执行控制指令，不是用户真实输入。它：

- 不展示在前端；
- 不写入 Session JSON；
- 不作为永久 HumanMessage 留在最终 Checkpoint；
- 不影响用户下一轮看到的完整历史。

本轮结束前把各段 AI 文本合并，最终 Session 与 Checkpoint 都保存：

```text
HumanMessage：用户真实问题
AIMessage：第一段 + 续写1 + 续写2
```

### 9.5 三次续写仍未完成

达到续写上限后：

- 立即停止自动调用模型；
- 保留第一段回答和所有续写内容；
- 不把有价值的部分当成普通 error 丢弃；
- 最终事件标记为未完成；
- Session 和 Checkpoint 保存已生成内容及未完成标记；
- 用户可以下一轮输入“继续”。

建议最终事件：

```json
{
  "type": "done",
  "content": "已经生成的全部内容……",
  "status": "incomplete",
  "reason": "max_output_tokens",
  "continuation_count": 3
}
```

前端显示一条非阻塞提示：

```text
回答已达到自动续写上限，当前内容可能不完整。可以发送“继续”。
```

如果用户之后真实发送“继续”，这是一个新的用户回合：

```text
从 Checkpoint 恢复上一轮的合并回答
  -> 加入用户真实 HumanMessage("继续")
  -> 重新启动 Agent Loop
  -> 本轮续写计数从 0 开始
```

首版只实现固定最多3次，不实现复杂的内容相似度和收益递减算法；后续如果真实出现重复续写，再补“新增 token 太少时提前停止”。

## 10. 备用模型切换

参考章节在连续3次529后切换 fallback model。当前项目只有一套 provider、base URL 和 API Key 配置。

最小方案只支持同一 OpenAI-compatible endpoint 下的可选备用模型：

```text
LLM_FALLBACK_MODEL=
```

规则：

- 未配置：不切换，继续主模型退避重试；
- 已配置：连续达到过载阈值后，用相同 endpoint 和 API Key 构造备用 ChatModel；
- 只针对 overloaded/529/503 切换；
- 429 不切换，因为它可能是账号级限流，换同账号模型未必有效；
- fallback 也失败后，继续走剩余重试或最终失败；
- 切换时通过 recovery SSE 告知前端；
- 不在首版支持备用 provider、第二套 API Key 或跨云切换。

具体备用模型涉及能力和费用选择，不能由代码默认指定；配置为空时功能应完全关闭。

## 11. 工具错误恢复

建议首版增加 `ToolErrorMiddleware`，把未捕获异常转换成经过清洗的 `ToolMessage(status="error")`，让模型自行决定改参数、换工具或向用户说明。

建议策略：

| 工具 | 自动重试 | 最终处理 |
|---|---|---|
| `read_file` | 首版不自动重试 | 异常转 ToolMessage，模型可换路径或命令 |
| `terminal` | 不自动重试 | 异常转 ToolMessage，避免命令被重复执行 |

当前工具已经返回的业务错误字符串保持不变。ToolErrorMiddleware 只处理真正抛出的未捕获异常。

不建议首版加入通用 `ToolRetryMiddleware(Exception)`，因为它会把编程错误和副作用工具一起重试。

## 12. 最终失败与持久化

建议区分两类结束：

### 12.1 有有效部分输出

例如续写3次仍截断：

- 走 `done`；
- 保存完整已生成文本；
- 标记 `status=incomplete`；
- Session JSON 与 Checkpoint 都保留该回答；
- 用户下一轮可以继续。

### 12.2 完全没有模型输出

例如429重试耗尽、认证失败，或强制现有 L4 后仍超限：

- 保存本轮 HumanMessage；
- 保存一条清洗后的错误 AIMessage；
- 不保存底层敏感异常详情；
- 让 Agent 以明确的失败结果结束，使 JSON 与 Checkpoint 对齐；
- SSE 可以使用 `error` 或专门的 `recovery_exhausted` 事件，但刷新历史后仍能看到该失败回合。

建议 Session 中保存类似：

```text
模型服务暂时不可用，已完成3次自动重试。本轮未获得模型回答。
```

详细异常只写后端日志，不写入模型上下文。

为了保存未完成状态，`message_codec.py` 可以为 AI 记录增加可选字段：

```json
{
  "type": "ai",
  "content": "部分回答",
  "status": "incomplete",
  "finish_reason": "max_output_tokens"
}
```

旧 Session 不受影响，字段缺失时仍按普通完成消息处理。

## 13. SSE 与前端事件

建议新增但保持数量很少：

| 事件 | 作用 |
|---|---|
| `recovery` | 展示重试、强制 L4、自动续写、模型切换等状态 |
| `done` 扩展字段 | 表示 `completed` 或 `incomplete` |
| `error` | 最终无法产生有效输出的失败 |

示例：

```text
recovery: 正在进行第2/3次重试
recovery: 上下文被模型拒绝，正在强制执行现有L4
recovery: 输出达到上限，正在自动续写（1/3）
token...
done: status=incomplete
```

前端只增加恢复状态和 incomplete 提示，续写 token 仍沿用当前 token 追加逻辑，不清空已生成回答，也不增加复杂任务面板。

## 14. 拟修改文件

| 文件 | 改动级别 | 拟修改内容 |
|---|---|---|
| `backend/middleware/error_recovery.py` | 新增 | RecoveryPolicy、RecoveryState、错误分类、退避、强制 L4 重试、截断续写 |
| `backend/middleware/__init__.py` | 小改 | 导出错误恢复组件 |
| `backend/graph/agent.py` | 小改 | 注册 middleware、创建本轮 RecoveryState、转发 custom stream、合并续写内容 |
| `backend/graph/agent_factory.py` | 小改 | 保持 middleware 列表透传；必要时扩展模型配置 |
| `backend/graph/llm.py` | 小改 | 支持每次请求覆盖输出 token；可选构造同 endpoint fallback model |
| `backend/service/context_manager.py` | 小改 | 增加 `force_summary` 入口和 summary 模型瞬态重试 |
| `backend/service/message_codec.py` | 小改 | 保存 AI 完成/未完成状态 |
| `backend/service/session_manager.py` | 小改 | 追加最终失败或未完成回合 |
| `backend/api/chat.py` | 小改 | 转发 recovery/incomplete 事件，最终失败时保持 Session 对齐 |
| `backend/config/config.py` | 小改 | RecoveryPolicy 环境变量 |
| `backend/config/.env.example` | 小改 | 示例配置和说明 |
| `frontend/src/lib/store.tsx` | 小改 | 展示 recovery 和 incomplete，续写 token 追加到当前回答 |
| `backend/tests/test_error_recovery.py` | 新增 | 三条恢复路径和上限测试 |
| `backend/tests/test_checkpoint_persistence.py` | 小改 | 强制 L4 与失败/未完成状态跨回合恢复 |
| `README.md` | 小改 | 实施后补充能力和配置，不在方案确认前修改 |

## 15. 建议配置项

建议保持少量环境变量：

```dotenv
# Error recovery
RECOVERY_MAX_RETRIES=3
RECOVERY_INITIAL_DELAY_MS=500
RECOVERY_MAX_DELAY_MS=8000
RECOVERY_MAX_CONTINUATIONS=3
RECOVERY_DEFAULT_MAX_OUTPUT_TOKENS=8192
RECOVERY_ESCALATED_MAX_OUTPUT_TOKENS=65536
LLM_FALLBACK_MODEL=
```

默认值由 `Settings` 读取，非法或非正整数继续使用默认值。测试中直接构造 `RecoveryPolicy`，不依赖真实 `.env`。

## 16. Middleware 执行顺序

目标顺序是：

```text
每次模型调用前：
  ContextManagementMiddleware.abefore_model
    -> L3 -> L1 -> L2 -> 必要时主动 L4

实际模型调用外层：
  ErrorRecoveryMiddleware.awrap_model_call
    -> 瞬态重试
    -> 上下文超限时强制现有 L4
    -> 输出截断恢复

工具执行异常：
  ToolErrorMiddleware
    -> 转换为 ToolMessage(status=error)
```

LangChain 多个 wrap middleware 的嵌套顺序容易影响“先重试还是先 fallback”。实施时先用两个记录调用顺序的 fake middleware 写测试，再确定最终注册顺序，不凭列表直觉判断。

## 17. 测试方案

所有核心测试使用 fake chat model 和人工异常，不调用真实百炼服务。

### 17.1 瞬态错误

- 前两次429，第三次成功；
- 503重试后成功；
- 401不重试；
- 超过最大次数后形成最终失败状态；
- 测试时关闭真实 sleep 或注入 fake sleeper；
- Retry-After 优先于指数退避。

### 17.2 上下文超限后的 L4 重试

- 第一次 prompt-too-long，强制 L4 后成功；
- 普通 L4 与强制 L4 调用相同总结逻辑；
- 同一回合最多执行 L4 一次；
- 已经执行普通 L4 时，服务端仍拒绝则不重复总结；
- 第二次仍超限时停止；
- 强制生成的 summary 包含目标、决策、文件、剩余任务和用户约束；
- 替换后的 messages 进入 SQLite Checkpoint；
- 下一轮恢复 summary，不恢复被压缩的大历史。

### 17.3 输出截断

- 8K截断后保留已有内容，注入内部续写提示；
- 续写请求的输出预算提升到64K；
- 续写 token 追加到当前前端回答；
- 内部续写 HumanMessage 不写入最终 Session 和 Checkpoint；
- 最多续写3次；
- 第3次仍截断时保存部分内容并标记 incomplete；
- `done.content` 包含第一段和所有续写文本的合并结果；
- 用户下一轮“继续”可以看到已保存的部分回答。

### 17.4 工具异常

- read_file 未捕获异常转为 error ToolMessage；
- terminal 未捕获异常不自动重放；
- 原有工具业务错误行为不变；
- tool call id 与 error ToolMessage 仍正确配对。

### 17.5 Session、SSE 与 Checkpoint

- 正常回答仍按现有格式保存；
- 完全失败时用户问题不会从历史消失；
- incomplete 回答可以前端展示；
- recovery 事件不被写成聊天消息；
- Session JSON 和最新 Checkpoint 在结束边界上语义一致；
- API 重启后可以从失败或未完成回合继续。

## 18. 实施顺序

建议分成可单独验证的小步骤：

1. 新增错误分类、RecoveryPolicy、RecoveryState 和单元测试。
2. 实现主模型瞬态错误的异步退避重试。
3. 增加 recovery custom stream 和 SSE 转发。
4. 给 summary model 增加同类瞬态重试。
5. 增加“上下文超限后强制现有 L4”入口，并验证 Checkpoint 持久化。
6. 增加输出长度检测、保留截断内容、内部续写提示和后续64K输出预算。
7. 增加最多3次续写、AI 文本合并和 incomplete 结束状态。
8. 增加 ToolErrorMiddleware，不自动重试 terminal。
9. 对齐最终失败时的 Session、SSE 和 Checkpoint。
10. 增加可选同 endpoint fallback model。
11. 跑完整测试、Python compileall、前端语法/类型检查和 `git diff --check`。
12. 实施完成后另写“错误恢复：实际实施说明”，不覆盖本方案文档。

## 19. 首版不做的内容

- 不做跨供应商 fallback；
- 不保存或恢复进程崩溃时尚未完成的流式 token；
- 不做复杂的内容重复率和收益递减检测；
- 不自动重试 terminal；
- 不做用户可编辑的重试控制面板；
- 不做人工点击某个历史 Checkpoint 重放；
- 不实现 Claude Code 的十几种 transition/reason code；
- 不做分布式请求去重和 exactly-once 工具执行；
- 不保证 Session JSON 与 SQLite 的数据库级原子事务。

## 20. 确认状态

以下选择会直接改变产品行为。已讨论清楚的部分标记为已确认，其余部分在实施前继续确认：

| 项目 | 本方案建议 | 当前状态 |
|---|---|---|
| 输出截断 | 保留已生成内容，使用内部提示从中断处续写，后续输出预算提升到64K | 已确认 |
| 内部续写提示 | 只用于恢复调用，不展示，不作为永久 HumanMessage 写入 Session 和 Checkpoint | 已确认 |
| 三次续写仍截断 | 合并保留全部有效内容，标记 incomplete，用户可下一轮真实发送“继续” | 已确认 |
| 上下文超限后的 L4 重试 | 复用现有 L4，只跳过本地阈值；同一轮已执行 L4 时不再重复 | 已确认 |
| 默认瞬态重试次数 | 额外重试3次，而不是参考实现的10次 | 已实施 |
| 最终完全失败 | Session 保存用户问题和一条清洗后的错误回答 | 已实施 |
| fallback model | 支持可选 `LLM_FALLBACK_MODEL`，默认留空关闭 | 已实施 |
| 工具异常 | 转为 error ToolMessage；terminal 不自动重试 | 已实施 |

上述行为已全部落地；实际实施中仍保持备用模型默认关闭，只有显式配置时才启用。
