# Agent 错误恢复：实际实施说明

## 1. 实施结果

错误恢复已在不重写 Agent Loop 的前提下接入现有 LangChain `create_agent` 架构。主体是一个 `ErrorRecoveryMiddleware`，与原有 `ContextManagementMiddleware` 组合使用。

已落地能力：

- 429、529、500/502/503/504、连接错误和超时的异步退避重试；
- 通过 SSE 向前端显示恢复状态；
- 模型明确拒绝上下文时，强制复用一次现有 L4；
- 输出截断时保留内容并自动续写；
- 最多续写3次，仍截断时保存合并内容并标记 `incomplete`；
- 恢复耗尽且没有有效输出时，生成系统标记的错误 `AIMessage`；
- 未捕获工具异常转成 error `ToolMessage`，不自动重放 terminal；
- 可选同 endpoint 备用模型，默认关闭；
- Session JSON 和 SQLite Checkpoint 保存最终整理后的消息。

## 2. 主要文件

```text
backend/
├── middleware/error_recovery.py     # 恢复状态机和模型调用包装
├── service/model_recovery.py       # 错误分类和退避计算
├── middleware/context_management.py
├── service/context_manager.py      # force_summary 与 summary 瞬态重试
├── graph/agent.py                   # middleware 注册、custom stream、done 状态
├── service/message_codec.py         # incomplete/error 持久化
└── tests/test_error_recovery.py

frontend/src/
├── lib/store.tsx                    # recovery SSE 和消息状态
└── components/chat/ChatMessage.tsx # 重试和 incomplete 提示
```

## 3. 实际 Agent Loop 位置

```text
ContextManagementMiddleware.abefore_model
  -> L3 -> L1 -> L2 -> 必要时 L4
  -> ErrorRecoveryMiddleware.awrap_model_call
       -> handler(request) 真正调用模型
       -> 错误分类 / 重试 / 强制L4 / 续写
  -> LangChain 工具节点
  -> 如果需要，再次进入模型节点
```

`RecoveryState` 放在本轮 `AgentRunContext` 内。429重试次数、续写次数和“本轮是否已经 L4”都在新的用户请求时清零，不会变成长期会话计数。

## 4. 429 和 529

`classify_model_error()` 优先读取结构化 HTTP status，再回退到异常类名和文本。

```text
429 -> rate_limit
529 -> overloaded
5xx -> server_error
```

默认最多额外重试3次，延迟从0.5秒开始指数增长，上限8秒，加0到25%抖动。存在 `Retry-After` 时优先使用服务端值。

middleware 写入 custom stream：

```json
{
  "type": "recovery",
  "reason": "overloaded",
  "attempt": 2,
  "max_attempts": 3,
  "delay_seconds": 1.1,
  "message": "模型服务当前过载，正在重试。"
}
```

`AgentManager.astream()` 将 `custom` 模式转为 SSE。前端只把它显示为当前回答的临时状态，成功或结束后清除，不写入聊天历史。

## 5. 上下文超限后的 L4

项目只有一套 L4。`force_summary=True` 只跳过本地 token 阈值判断，summary prompt、最近消息保留和 pair-safe 逻辑都不变。

```text
模型报 prompt_too_long
  -> 本轮尚未执行 L4
       -> force_summary=True
       -> RemoveMessage(REMOVE_ALL_MESSAGES)
       -> summary + 最近消息 + 新AI回答
       -> 压缩 State 写入 Checkpoint

  -> 本轮已经执行 L4
       -> 不再重复总结
       -> 返回清洗后的错误AI消息
```

“本轮最多一次”只针对一次 `/api/chat`。用户下一轮追问时状态重置，必要时可以再次 L4。

## 6. 输出截断与续写

当 `finish_reason=length` 或等价值出现时：

```text
保留已生成内容
  -> 临时加入已生成 AIMessage
  -> 临时加入内部 continuation HumanMessage
  -> 以最大64K输出预算续写
  -> 新 token 追加到当前前端回答
```

内部 continuation prompt 不进入 middleware 最终返回的 `ModelResponse`，因此不进入 Session 或 Checkpoint。

最终两个存储都只看到：

```text
HumanMessage：用户真实问题
AIMessage：第一段 + 续写1 + 续写2 + 续写3
```

三次仍被截断时，AIMessage 增加：

```json
{
  "recovery": {
    "status": "incomplete",
    "reason": "max_output_tokens",
    "continuation_count": 3
  }
}
```

前端提示用户可发送“继续”。这会开始新的真实用户回合，并把续写计数重置为0。

## 7. 最终失败

没有有效输出时，middleware 返回系统创建的 `AIMessage`，例如：

```text
模型服务持续过载，自动重试已经结束，请稍后再试。
```

该消息带有 `status=error`、reason、attempts 和 `generated_by=system`。原始异常、header、API Key 和请求体不进入 Session。

如果已经有流式部分输出，则保留部分文本并转入内部续写；续写也无法恢复时标记 `incomplete`，不用错误文字覆盖有效内容。

## 8. Session 与 Checkpoint

`message_codec.py` 对 AI 记录增加可选：

```text
status
finish_reason
continuation_count
```

内部恢复事件不保存；最终整理后的 AIMessage 会同时进入完整 Session 档案和 LangGraph 活跃 State。

## 9. 配置

```dotenv
RECOVERY_MAX_RETRIES=3
RECOVERY_INITIAL_DELAY_MS=500
RECOVERY_MAX_DELAY_MS=8000
RECOVERY_MAX_CONTINUATIONS=3
RECOVERY_DEFAULT_MAX_OUTPUT_TOKENS=8192
RECOVERY_ESCALATED_MAX_OUTPUT_TOKENS=65536
LLM_FALLBACK_MODEL=
```

`LLM_FALLBACK_MODEL` 为空时不创建备用模型。配置后备用模型复用当前 provider、base URL 和 API Key，只在连续过载时切换。

## 10. 验证

自动测试覆盖：

- 429重试后成功与 recovery 事件；
- 529耗尽后系统错误 AIMessage；
- prompt-too-long 强制现有 L4；
- 同一轮不重复 L4；
- 截断输出合并续写；
- 三次续写后 incomplete；
- 内部 continuation prompt 不进入 SQLite Checkpoint；
- Session 对 incomplete 状态的往返和前端投影；
- summary 失败后的清洗错误消息；
- 原有上下文压缩、Session、工具和 Checkpointer 回归。

最终回归结果：

```text
30 passed
```

实施仍保留 Demo 边界：Session JSON 与 SQLite 不是同一数据库事务；terminal 不自动重放；没有实现跨云 provider fallback 和进程崩溃中的流式 token 恢复。
