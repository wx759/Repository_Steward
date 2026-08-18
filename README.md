# Repository Steward

Repository Steward 是一个小型、单 Agent 的代码仓库助手。

项目基于 LangChain `create_agent` 和 LangGraph checkpoint 构建，保留最基本的
“模型调用工具、读取仓库、执行终端命令、连续对话”能力。在这个基础上，目前主要
尝试解决三个问题：

1. 对话跨 Session 之后，Agent 如何记住真正长期有效的信息；
2. 对话和工具输出越来越长时，Agent 如何压缩上下文但继续完成原任务；
3. 模型限流、过载、上下文超限或输出被截断时，Agent 如何尽量自动恢复。

因此，这个项目现在的重点不是多智能体调度，而是验证一个单 Agent 的
**长期记忆、上下文管理和故障恢复**能否保持边界清晰，并在实际仓库任务中稳定工作。

## 当前能力

- 支持智谱、百炼、DeepSeek 和 OpenAI 等 OpenAI-compatible 模型服务；
- 基于 LangChain `create_agent` 的多轮工具调用；
- `read_file` 和 `terminal` 两个仓库内工具；
- FastAPI + SSE 流式聊天接口；
- Next.js 聊天界面、Session 管理、工具调用轨迹和 Prompt 查看；
- JSON 完整会话归档与 SQLite LangGraph checkpoint；
- SQLite 跨 Session 长期记忆；
- L3 → L1 → L2 → L4 四层上下文压缩；
- 模型错误分类、退避重试、上下文超限恢复和自动续写；
- 文件化 System Prompt 和轻量 Skill 扫描。

## 一次请求如何运行

```text
用户发送消息
  ↓
POST /api/chat
  ↓
根据 session_id 恢复 LangGraph checkpoint
  ↓
从长期记忆目录中选择与当前请求相关的 Memory
  ↓
在每次模型调用前执行上下文管理
  ↓
LLM ↔ read_file / terminal
  ↓
模型调用失败时进入故障恢复
  ↓
通过 SSE 返回文本、工具轨迹和临时恢复状态
  ↓
保存 checkpoint 和完整 Session 归档
  ↓
从本轮原始用户信息中提取长期 Memory
```

这条主链仍然只有一个 Agent。Memory、上下文压缩和故障恢复都是挂在主 Agent
周围的独立模块，没有重新实现 Agent Loop。

## 1. 长期记忆

### 想解决的问题

聊天记录适合回答“刚才说了什么”，但不适合直接承担长期记忆：历史会不断增长，
大量临时任务细节也不值得永久带入后续请求。因此项目把长期 Memory 从普通对话
记录中独立出来，只保存用户明确表达且以后仍可能有用的信息。

当前 Memory 分为四类：

| 类型 | 内容示例 |
|---|---|
| `user` | 用户稳定的偏好和协作方式 |
| `project` | 项目长期有效的约束和约定 |
| `feedback` | 用户对已有做法的明确纠正 |
| `reference` | 用户希望后续继续参考的资料或事实 |

每条 Memory 还具有 `active`、`superseded`、`archived` 三种状态。旧信息被新信息
替代时不会直接删除，而是保留历史状态。

### 请求前：选择相关记忆

每轮主 Agent 开始前，`MemorySelector` 会收到：

- 当前用户请求；
- 当前有效 Memory 的元数据：`id + name + description + status`。

Selector 最多选择 5 条 Memory，随后才从 `backend/memory.sqlite` 读取正文。正文只会
临时追加到本次模型调用的 System Prompt，不会写入聊天消息、Session JSON 或
LangGraph checkpoint。

如果选择模型失败，系统会退回本地关键词匹配；如果整个选择过程仍然失败，本轮请求
会在没有长期 Memory 的情况下继续，不阻断主 Agent。

### 请求后：提取长期信息

一轮回答成功完成后，`MemoryExtractor` 会从压缩前保存的原始本轮快照中寻找长期信息。
它只接受能够引用用户原文作为证据的内容，可以创建新 Memory，也可以让旧 Memory
进入 `superseded` 或 `archived` 状态。

当前有意拒绝保存：

- 临时任务步骤和一次性进度；
- Agent 自己推断、但用户没有明确表达的事实；
- 密钥、Token 等敏感信息；
- 与现有 Memory 重复且没有新增价值的内容。

Memory 提取使用隔离的旁路模型调用。提取失败只会记录日志，不会把已经完成的主回答
改成失败。

### Memory 的边界

```text
Memory 不是完整聊天历史
Memory 不是 LangGraph 运行状态
Memory 不是 Agent 可以无条件相信的系统指令
```

Memory 内容被当作可参考的数据注入，由当前请求和更高优先级的 System Prompt 决定
最终行为。

实现入口：

- `backend/memory/store.py`
- `backend/memory/selector.py`
- `backend/memory/extractor.py`
- `backend/memory/middleware.py`
- `backend/tests/test_memory.py`

## 2. 上下文压缩

### 想解决的问题

代码 Agent 的上下文增长通常不只来自聊天文本，还来自源码、日志、测试输出等工具结果。
如果只在超出模型窗口后才做一次总结，很容易同时遇到总结输入过长、工具调用关系被拆散、
关键工作状态丢失等问题。

当前方案在每次主模型调用前运行同一条四层流水线：

```text
L3 大工具输出落盘
  ↓
L1 裁剪消息中段
  ↓
L2 压缩较旧的工具结果
  ↓
L4 必要时生成工作摘要
```

### L3：大工具输出落盘

当最近一批 `ToolMessage` 的总大小超过预算时，优先把最大的完整结果保存到：

```text
backend/.task_outputs/tool-results/<session_id>/
```

模型上下文中只保留文件路径和一段预览。这样完整日志仍可重新读取，但不会持续占用
上下文窗口。

### L1：裁剪消息中段

当消息数量超过上限时，保留开头的约束信息和最近的工作现场，用占位消息替换中间历史。
裁剪边界会避开 `AIMessage(tool_calls)` 与相邻 `ToolMessage` 组成的调用组，防止产生
不合法的工具调用历史。

### L2：压缩旧工具结果

只保留最近若干条工具结果的完整正文，更早的 `ToolMessage` 会替换成固定占位文本。
工具消息本身及其 `tool_call_id` 仍然存在，因此消息结构不会被破坏；如果旧结果再次
重要，Agent 可以重新执行工具。

### L4：生成可继续工作的摘要

前三层完成后，如果估算 Token 数加预留空间仍超过阈值，系统才调用总结模型。
摘要要求保留：

1. 当前目标；
2. 关键发现、决定以及被否决方案的原因；
3. 已读取或修改的文件；
4. 尚未完成的工作；
5. 用户约束。

摘要会与一段满足预算的最近消息共同组成新的活动上下文。总结调用本身也有独立的输入
裁剪、重试和输出 Token 上限。

### 完整历史与活动上下文分离

上下文压缩不会改写用于 UI 展示的完整 Session 归档：

| 存储 | 作用 | 是否被上下文压缩 |
|---|---|---|
| `backend/sessions/*.json` | 完整对话归档和 UI 历史 | 否 |
| `backend/checkpoints.sqlite` | Agent 下一轮继续使用的活动状态 | 是 |
| `backend/memory.sqlite` | 跨 Session 的长期信息 | 否，独立选择 |

压缩后的活动状态由 LangGraph checkpoint 持久化，因此下一轮对话或后端重启后会继续
使用已经压缩的上下文，而不是每次都从完整 Session 历史重新膨胀回来。

实现入口：

- `backend/service/context_manager.py`
- `backend/middleware/context_management.py`
- `backend/tests/test_context_manager.py`
- `backend/tests/test_checkpoint_persistence.py`

## 3. 故障重试与恢复

### 想解决的问题

模型调用失败不只有一种原因。限流适合等待后重试，认证失败应该直接提示配置问题，
上下文过长需要压缩，输出达到长度上限则应该续写。如果把所有异常都做成同一种重试，
只会增加等待时间并掩盖真实故障。

项目先将模型异常分类，再决定恢复动作：

| 故障 | 当前处理方式 |
|---|---|
| 429 限流 | 指数退避后重试，支持服务端 `Retry-After` |
| 529 / 服务过载 | 退避重试；连续过载且配置备用模型时可切换模型 |
| 5xx 服务错误 | 退避重试 |
| Timeout / Connection | 退避重试 |
| Prompt Too Long | 强制执行一次现有 L4，再用压缩后的上下文重试 |
| Authentication / Bad Request | 不做无意义重试，返回明确错误 |
| 输出长度截断 | 保留已有文本，在内部自动续写并合并结果 |

### 短暂故障重试

可重试错误默认最多重试 3 次，等待时间使用指数退避、随机抖动和最大延迟限制。
重试期间后端会发送 `recovery` SSE 事件，前端可以显示“限流等待”“服务过载”等状态；
这些提示只属于运行时状态，不会混进永久聊天历史。

如果配置了 `LLM_FALLBACK_MODEL`，主模型连续过载达到阈值后会切换到同一 Provider、
Base URL 和 API Key 下的备用模型。

### 上下文超限恢复

常规 L4 由本地 Token 预算触发，但本地估算与服务端计算可能不同。如果 Provider 仍然
返回上下文超限，恢复中间件会强制调用同一个 L4 算法一次，再重试原模型请求。

同一轮不会无限重复强制 L4。如果压缩后仍被拒绝，系统会返回明确错误状态。

### 输出截断续写

当模型返回 `length`、`max_tokens` 或 `max_output_tokens` 时，系统会：

1. 保留已经生成的内容；
2. 在内部加入续写指令；
3. 使用更高的输出 Token 上限继续生成；
4. 把多段内容合并成一条最终回答。

内部续写提示不会进入用户的永久会话历史。默认最多续写 3 次；如果仍未完成，已经生成
的文本不会丢失，但最终消息会以 `status: incomplete` 持久化，前端可以据此提示用户。

实现入口：

- `backend/service/model_recovery.py`
- `backend/middleware/error_recovery.py`
- `backend/service/message_codec.py`
- `backend/tests/test_error_recovery.py`

## 三项功能如何协作

```text
长期 Memory
  负责跨 Session 提供少量稳定背景

Session JSON
  负责保存完整、可展示的聊天归档

LangGraph checkpoint + 上下文压缩
  负责保存下一轮真正要继续使用的活动工作状态

故障恢复
  负责包裹每次主模型调用，并在需要时触发重试、强制 L4 或续写
```

三者有意避免共用一份“万能历史”。这样可以分别回答：什么值得长期记住、什么必须完整
留档、什么需要继续带给模型，以及一次模型调用失败后应该如何恢复。

## 项目结构

```text
backend/
├── api/                    # Chat、Session、文件和 Skill API
├── config/                 # 模型与功能参数
├── graph/
│   ├── agent.py            # 主 Agent 组装和流式事件处理
│   ├── agent_factory.py    # create_agent 构造入口
│   └── llm.py              # OpenAI-compatible 模型配置
├── memory/                 # Store、Selector、Extractor、Prompt Middleware
├── middleware/
│   ├── context_management.py
│   └── error_recovery.py
├── service/
│   ├── context_manager.py
│   ├── model_recovery.py
│   ├── message_codec.py
│   ├── prompt_builder.py
│   └── session_manager.py
├── tools/                  # read_file、terminal、Skill 扫描
├── skills/                 # Skill 快照和本地 Skill
├── workspace/              # 文件化 System Prompt 组件
├── sessions/               # 运行时生成的完整 Session 归档
├── checkpoints.sqlite      # 运行时生成的 LangGraph 活动状态
├── memory.sqlite           # 运行时生成的长期 Memory
└── app.py

frontend/
└── src/                    # Next.js 聊天界面
```

## 关键配置

完整示例见 `backend/config/.env.example`。

| 配置前缀 | 作用 |
|---|---|
| `LLM_*` | Provider、模型、API Key、Base URL 和备用模型 |
| `MEMORY_*` | Memory 开关、选择数量、旁路调用超时和提取上限 |
| `CONTEXT_*` | Token/消息预算、工具结果预算、预览和摘要上限 |
| `RECOVERY_*` | 重试次数、退避时间、续写次数和输出 Token 上限 |

关闭长期 Memory：

```dotenv
MEMORY_ENABLED=false
```

## 启动方式

### Docker Compose（推荐）

先创建 `backend/config/.env`。例如使用阿里云百炼：

```dotenv
LLM_PROVIDER=bailian
LLM_MODEL=qwen3.7-plus
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

在项目根目录构建并启动：

```bash
docker compose up --build -d
```

访问：

- 前端：`http://127.0.0.1:7788`
- 后端健康检查：`http://127.0.0.1:8002/health`

常用命令：

```bash
docker compose logs -f
docker compose ps
docker compose down
```

也可以分别构建镜像：

```bash
docker build -t repository-steward-backend ./backend
docker build -t repository-steward-frontend ./frontend

docker run --rm --env-file backend/config/.env -p 8002:8002 repository-steward-backend
docker run --rm -p 7788:7788 repository-steward-frontend
```

Compose 会把本地 `./backend` 挂载到容器 `/app`。Agent 对仓库文件的修改、Session、
checkpoint 和 Memory 因而会保留在宿主机。单独运行上面的 backend 镜像时，Agent
操作的是构建进镜像的源码。

### 本地启动后端

```bash
cd backend
python -m venv .venv
```

激活虚拟环境并安装依赖：

```bash
pip install -r requirements.txt
```

复制 `config/.env.example` 为 `config/.env`，配置模型：

```dotenv
LLM_PROVIDER=zhipu
LLM_MODEL=glm-5
LLM_API_KEY=your_key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
```

启动：

```bash
uvicorn app:app --host 0.0.0.0 --port 8002 --reload
```

### 本地启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:7788`。

## Prompt 与 Skill

System Prompt 由以下文件组合：

- `backend/skills/SKILLS_SNAPSHOT.md`
- `backend/workspace/SOUL.md`
- `backend/workspace/IDENTITY.md`
- `backend/workspace/USER.md`
- `backend/workspace/AGENTS.md`

`backend/skills/` 下的每个一级目录可以提供一个带 YAML front matter 的 `SKILL.md`。
后端启动时会刷新轻量 Skill 快照，Agent 再根据需要使用 `read_file` 阅读完整说明。

## 测试

在项目根目录运行：

```bash
pytest backend/tests
```

测试目前覆盖 Session 消息兼容、checkpoint 恢复、Memory 状态和证据约束、四层上下文
压缩、工具调用组完整性、异常分类、退避重试、强制 L4 和输出续写。

## 当前边界与希望请教的问题

这个项目仍是一个小型实验性实现，当前边界包括：

- 单进程、单 Agent，没有分布式调度和多 Agent 协作；
- Memory Selector 和 Extractor 默认复用同一模型服务，只做调用上下文隔离；
- Token 数量采用本地近似估算，服务端仍可能给出不同的上下文判断；
- 长期 Memory 目前没有专门的可视化审核和人工编辑界面；
- L4 摘要是有损压缩，依赖提示词和后续关键证据复查；
- 故障恢复面向模型调用，不试图自动修复任意工具或业务异常；
- SQLite 和本地文件适合单机 Demo，尚未处理多实例并发一致性。

特别希望得到关于以下问题的建议：

1. Memory 的“自动提取 + 用户原文证据 + 状态演进”边界是否合理；
2. 完整 Session、活动 checkpoint、长期 Memory 三份数据的职责划分是否过重；
3. L3/L1/L2/L4 的顺序以及工具调用组保护是否还有明显缺口；
4. 强制 L4、模型重试、备用模型和自动续写之间是否存在不易察觉的状态冲突；
5. 如果继续演进，最值得优先补充的是可观测性、Memory 审核，还是更严格的评测体系。
