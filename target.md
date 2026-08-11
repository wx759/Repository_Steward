你现在需要对这个项目进行一次“最小核心化”重构。

项目来源：
https://github.com/jimmyzheng1027/miniOpenClaw

## 一、重构目标

我准备基于 miniOpenClaw 二次开发一个面向代码仓库长期维护场景的 Agent。

最终项目定位是：

**Repository Steward：一个能够长期接管代码仓库维护任务的多智能体 Coding Agent。**

后续我会在这个基础上逐步加入：

- Context Compaction
- Repository Memory
- Persistent Task System
- Agent Teams
- Team Protocol
- Autonomous Task Claim
- Git Worktree Isolation

因此当前阶段不要添加这些新功能。

当前任务只有一个：

> **对 miniOpenClaw 做减法，将其裁剪成一个足够简单、后续方便继续扩展的 Agent Core。**

---

## 二、希望保留的能力

请尽量保留以下基础能力：

1. 基础 Agent 运行能力
   - LLM 调用
   - LangChain `create_agent`
   - Tool Calling
   - 多轮 Agent 执行

2. 基础 Web/API 能力
   - FastAPI
   - Chat API
   - SSE 流式输出
   - Session 基础能力

3. 基础工具
   - 文件读取
   - 文件修改（如果当前已有）
   - Terminal / Shell

4. 基础 Prompt 构建机制

5. Skill 机制暂时保留
   - 后续计划用于定义 Repository Maintenance 工作流程

6. 前端暂时保留
   - 当前阶段不要重构前端
   - 如果删除后端功能导致部分前端页面失效，可以先隐藏或移除对应入口

---

## 三、希望删除的能力

请分析并删除以下与当前项目主线无关的功能：

### 1. 原有复杂 Memory

删除：

- memory_module_v1
- memory_module_v2
- pgvector 相关逻辑
- BM25 / RRF 检索
- Memory Distill
- Memory Evaluation
- Memory Retrieval
- 与复杂长期记忆系统绑定的 API、Service、配置和依赖

后续我会自己重新实现一个非常轻量的 Repository Memory，因此不要保留现有 Memory 实现。

---

### 2. Knowledge / RAG

删除：

- Knowledge Base
- Chroma
- search_knowledge_tool
- RAG 检索
- 与 Knowledge 相关的 API、配置、存储和前端入口

---

### 3. Guardian / Prompt Injection 检测

删除当前 Guardian Middleware 及其：

- 配置
- Prompt
- Middleware 注册
- API / UI 依赖

当前 Demo 不考虑完整安全防护。

---

### 4. Langfuse

删除：

- Langfuse tracing
- 初始化逻辑
- callbacks
- 环境变量
- requirements / dependencies
- 与 Langfuse 相关的配置

当前 Demo 不需要完整 Observability。

---

### 5. 当前 Context Summarization / Compress

删除：

- SummarizationMiddleware
- 当前会话 compress API
- 当前手动 history summarize / compress 逻辑
- 与这些机制绑定的配置

注意：

后续我会参考 Claude Code Harness 自己重新实现 Context Compaction，因此当前不要保留一套重复机制。

但是：

**基础 Session / Conversation State 不要因此被删除。**

---

### 6. PostgreSQL Checkpointer

如果当前 Agent 的会话状态强依赖 PostgreSQL，请将其简化。

优先目标：

- Demo 不依赖 PostgreSQL
- 能使用内存或简单本地持久化即可

如果完全删除 Checkpointer 会严重破坏现有 Agent 多轮对话，请先分析并选择最小替代方案。

---

### 7. 非必要工具

可以删除：

- fetch_url
- knowledge search
- python repl（如果 Terminal 已经能够运行 Python）

最终第一版 Tool Set 尽量控制为：

- read_file
- write_file（如果需要）
- terminal

---

## 四、重要约束

### 1. 不要直接开始修改

第一步请先完整阅读相关代码，并输出：

1. 当前项目主要目录结构
2. Agent 请求完整调用链
3. 上述待删除功能分别依赖哪些模块
4. 哪些模块可以直接删除
5. 哪些模块存在耦合，需要先解除依赖
6. 删除之后项目最小架构是什么
7. 预计修改和删除哪些文件

在我确认方案之前，不要修改代码。

---

### 2. 重构原则

遵循：

> 删除功能，而不是重新设计整个项目。

不要：

- 大规模重写 Agent Runtime
- 引入新的 Agent Framework
- 引入新的数据库
- 添加新的 Harness
- 顺手增加额外功能
- 大规模改变前端 UI

尽量保持现有基础 Agent 调用链稳定。

---

### 3. 删除必须彻底

删除某个功能时，需要同时检查：

- import
- environment variables
- config
- dependency
- API route
- service
- tool registration
- middleware
- frontend API call
- README / docs

不要留下大量死代码和失效配置。

---

## 五、期望最终结构

最终后端大致希望收敛成类似：

```text
backend/
├── api/
│   ├── chat.py
│   └── sessions.py
│
├── graph/
│   ├── agent.py
│   ├── agent_factory.py
│   └── llm.py
│
├── service/
│   ├── prompt_builder.py
│   └── session_manager.py
│
├── tools/
│   ├── read_file_tool.py
│   ├── write_file_tool.py
│   ├── terminal_tool.py
│   └── skills_scanner.py
│
├── skills/
│
└── app.py
```

这只是目标方向，不要求机械按照此目录修改。

核心原则是：

> 保留一个简单、清晰、能够正常 Tool Calling 和多轮对话的 Agent Runtime。

---

## 六、完成后的验收标准

重构完成后至少保证：

### 基础 Agent

用户：

```text
帮我读取 test.py
```

Agent 能：

```text
LLM
→ read_file
→ tool result
→ LLM
→ answer
```

### 文件修改

用户：

```text
把 test.py 中的 hello 修改成 hi
```

Agent 能完成修改。

### Shell

用户：

```text
运行 pytest
```

Agent 可以通过 terminal 执行。

### 多轮对话

同一个 Session 中：

```text
User:
读取 auth.py

User:
刚才那个文件的 verify_token 是做什么的？
```

Agent 仍然能够保持基础上下文。

### Skill

现有 Skill 加载机制仍然可用。

### 前端

原 Chat 主流程可以正常使用。

---

## 七、重构完成后输出

请给我一份总结，包括：

1. 删除了哪些模块
2. 修改了哪些模块
3. 保留了哪些核心能力
4. 当前 Agent 主调用链
5. 当前 Session 如何维护
6. 当前 Tool 如何注册和执行
7. 当前 Prompt 如何构建
8. 当前代码目录结构
9. 是否还有遗留死代码
10. 下一步适合从哪里接入新的 Harness

最后请特别说明：

> **经过本次裁剪后，这个项目与原 miniOpenClaw 相比还剩下哪些核心能力。**