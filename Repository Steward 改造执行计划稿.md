# Repository Steward 改造执行计划

## 一、改造目标

将当前 Repository Steward 从“单 Agent + 固定 backend 工作目录”改造成一个真正面向代码仓库的轻量 Coding Agent Harness。

最终目标：

- 一个 Session 绑定一个代码仓库 Workspace。
- 一个长期存在的 Steward Agent 负责普通问答、简单代码任务和复杂任务编排。
- 复杂任务由 Steward 规划后，串行调用专业 Sub-Agent。
- Sub-Agent 作为 Tool 使用，一次调用完成一个任务后销毁。
- 每个 Sub-Agent 完成后由 Reviewer 验收，不通过则携带反馈重新修复。
- 所有 Agent 共用同一个 Repository Workspace，但严格串行执行，暂不引入 worktree、并行写入和 Agent 间通信。
- 保留并复用现有 Context Compaction、Memory、Error Recovery、Checkpoint 等能力。

---

## 二、总体目标架构

```text
User
  │
  ▼
Session
  │
  ├── workspace_id
  ├── conversation history
  └── checkpoint
  │
  ▼
Steward Agent
  │
  ├── 简单任务
  │     ├── read_file
  │     ├── write_file
  │     └── terminal
  │
  └── 复杂任务
        │
        ▼
      Plan
        │
        ▼
   delegate_task(...)
        │
        ▼
   Specialized Worker
        │
        ▼
     Reviewer
      /     \
   PASS     FAIL
    │         │
    │         ▼
    │       Worker 修复
    │         │
    └─────────┘
        │
        ▼
    WorkerResult
        │
        ▼
     Steward
        │
        ▼
   下一任务 / Final Answer
```

---

## 三、阶段一：引入 Workspace

### 目标

解决当前 Agent 没有真正指定“目标代码仓库”的问题。

### 任务

1. 新增 Workspace 数据结构。

建议最小字段：

```python
class Workspace:
    workspace_id: str
    name: str
    root_path: str
```

2. 新增 `WorkspaceManager`。

职责：

- 创建 Workspace
- 校验路径是否存在
- 校验路径是否为目录
- 可选校验是否包含 `.git`
- 根据 `workspace_id` 获取 `root_path`

3. Session 增加：

```python
workspace_id: str
```

4. 规定：

```text
一个 Session 只能绑定一个 Workspace
```

不允许在同一个 Session 中途切换代码仓库。

如需切换仓库，新建 Session。

5. 修改 Session 创建流程。

原流程：

```text
create session
```

改为：

```text
选择代码仓库
    ↓
创建 / 获取 Workspace
    ↓
创建 Session
    ↓
Session 绑定 workspace_id
```

6. 修改工具构建逻辑。

当前：

```text
ReadFileTool(root_dir=settings.backend_dir)
TerminalTool(root_dir=settings.backend_dir)
```

改为：

```text
ReadFileTool(root_dir=current_workspace.root_path)
WriteFileTool(root_dir=current_workspace.root_path)
TerminalTool(root_dir=current_workspace.root_path)
```

7. 不要让全局 AgentManager 永久绑定固定 `backend_dir`。

改为根据当前 Session 的 `workspace_id` 获取 Workspace，然后创建或获取对应 Runtime。

### 验收标准

能够创建：

```text
Session A → Repository A
Session B → Repository B
```

并保证：

```text
Session A 的 read_file / write_file / terminal
只能操作 Repository A

Session B
只能操作 Repository B
```

---

## 四、阶段二：处理 Docker Workspace 映射

### 目标

保证 Backend 运行在 Docker 中时仍然能够访问用户选择的代码仓库。

### 任务

增加统一 Workspace 根目录，例如：

```text
宿主机：
D:/code/repos/

Docker：
/workspaces/
```

通过 volume：

```text
D:/code/repos → /workspaces
```

Workspace 内只保存相对仓库信息，例如：

```text
miniOpenClaw
Repository_Steward
demo_project
```

容器内部统一解析：

```text
/workspaces/miniOpenClaw
```

不要直接把 Windows 本地绝对路径传给 Docker 内部 Agent。

### 验收标准

Docker 内执行：

```text
pwd
ls
read_file
terminal
```

均能够正确作用于当前 Session 对应的 Repository。

---

## 五、阶段三：调整 Memory 的 Repository Scope

### 目标

防止不同代码仓库之间的 Project Memory 相互污染。

### 任务

Memory 增加：

```python
workspace_id: str | None
```

规则：

### User Memory

```python
workspace_id = None
```

可以跨项目使用。

例如：

```text
用户偏好 Python
用户习惯中文回答
```

### Project Memory

```python
workspace_id = 当前 workspace_id
```

只能在对应仓库中检索。

例如：

```text
项目使用 FastAPI
项目要求兼容 Python 3.11
auth 模块采用 JWT
```

Memory Selector 查询时：

```text
user memory
+
current workspace project memory
```

不允许取其他 Workspace 的 Project Memory。

### 验收标准

Repository A 保存的项目记忆不会进入 Repository B 的上下文。

---

## 六、阶段四：保留单 Steward Agent

### 目标

不要引入额外的 Router / General Agent / Leader Agent。

保留一个长期存在的主 Agent：

```text
Steward Agent
```

Steward 同时承担：

- 普通问答
- Repository 理解
- 简单代码修改
- 复杂任务规划
- Sub-Agent 调度
- 最终结果汇总

### 规则

简单任务：

```text
用户
 ↓
Steward
 ↓
read / write / terminal
 ↓
Final Answer
```

复杂任务：

```text
用户
 ↓
Steward
 ↓
Plan
 ↓
delegate_task
```

复杂与简单任务的判断由 Steward 自己完成，不额外增加 Router Agent。

---

## 七、阶段五：实现 delegate_task

### 目标

把 Sub-Agent 封装成 Steward 的 Tool。

新增核心工具：

```python
delegate_task(...)
```

建议输入：

```python
class TaskSpec:
    role: str
    description: str
    context: str
    allowed_paths: list[str]
    acceptance_criteria: list[str]
```

示例：

```python
delegate_task(
    role="backend",
    description="实现登录 API",
    context="项目使用 FastAPI",
    allowed_paths=["backend/**"],
    acceptance_criteria=[
        "POST /api/login 可正常调用",
        "错误密码返回 401",
        "相关测试通过"
    ]
)
```

Steward 只能串行调用 `delegate_task`。

必须保证：

```text
一个 delegate_task 完全结束
        ↓
结果返回 Steward
        ↓
Steward 再推理
        ↓
才能调用下一个 delegate_task
```

禁止一次并行发起多个 Worker。

---

## 八、阶段六：实现 Worker Factory

### 目标

根据 `role` 动态创建专业 Sub-Agent。

第一版支持：

```text
backend
frontend
test
general
```

每个 Worker：

- 使用独立 System Prompt
- 只获取当前 Task 所需上下文
- 不继承完整 Steward messages
- 不拥有长期 Session
- 不拥有独立长期 Memory
- 执行完成后销毁

### Worker 生命周期

```text
创建 Worker
    ↓
构造临时 messages
    ↓
执行 Agent Loop
    ↓
读取 / 修改代码
    ↓
执行命令
    ↓
产生 WorkerResult
    ↓
销毁 Worker messages
```

---

## 九、阶段七：增加 Tool Permission Scope

### 目标

控制不同 Agent 的职责边界。

例如：

### Backend Worker

```text
read: all
write: backend/**
terminal: allowed
```

### Frontend Worker

```text
read: all
write: frontend/**
terminal: allowed
```

### Test Worker

```text
read: all
write: tests/**
terminal: allowed
```

### Reviewer

```text
read: all
write: none
terminal: test/check commands only
```

第一版不需要实现特别复杂的权限系统。

可以在工具调用层直接检查：

```python
allowed_paths
```

若 Worker 尝试写越界路径，直接拒绝。

---

## 十、阶段八：实现统一 Reviewer

### 目标

每个 Worker 完成后自动进入质量验收。

Reviewer 不修改代码。

输入：

```text
TaskSpec
WorkerResult
git diff
测试结果
验收标准
```

输出：

```python
class ReviewResult:
    approved: bool
    issues: list[str]
    feedback: str
```

流程：

```text
Worker
 ↓
Reviewer
 ↓
PASS
 ↓
返回 Steward
```

失败：

```text
Worker
 ↓
Reviewer
 ↓
FAIL
 ↓
携带 feedback
 ↓
重新调用 Worker 修复
 ↓
Reviewer
```

限制：

```python
MAX_REVIEW_ROUNDS = 2
```

超过次数：

```text
Task failed
```

并将失败原因返回 Steward。

---

## 十一、阶段九：统一 WorkerResult

不要把 Worker 的全部 messages 返回给 Steward。

统一返回结构化结果：

```python
class WorkerResult:
    status: str
    summary: str
    changed_files: list[str]
    tests_run: list[str]
    issues: list[str]
```

例如：

```json
{
  "status": "success",
  "summary": "完成 JWT 登录接口及参数校验",
  "changed_files": [
    "backend/routes/auth.py",
    "backend/services/auth.py"
  ],
  "tests_run": [
    "pytest backend/tests/test_auth.py: 8 passed"
  ],
  "issues": []
}
```

Steward 上下文中只保存：

```text
Task
+
WorkerResult
```

不保存 Worker 内部执行轨迹。

---

## 十二、阶段十：增加轻量 Run / Task

### 目标

记录一次复杂任务的执行进度，方便调试和前端展示。

建议：

```python
class Run:
    run_id: str
    session_id: str
    goal: str
    status: str
    tasks: list[Task]
```

Task：

```python
class Task:
    task_id: str
    role: str
    description: str
    status: str
    result: WorkerResult | None
```

示例：

```text
Run: 实现登录功能

✓ Backend
  ✓ Review passed

✓ Frontend
  ✓ Review passed

● Test
```

暂时不要实现：

```text
owner
claim
mailbox
priority scheduling
daemon
```

Run / Task 第一版只用于：

- 状态记录
- 调试
- 前端展示

---

## 十三、阶段十一：复用现有 Context Compaction

现有 Context Compaction 继续主要作用于：

```text
Steward Agent
```

Steward 是长期存在的 Agent，因此需要：

```text
L1 / L2 / L3 / L4
```

Worker 第一版不用完整 Context Compaction。

因为：

```text
一次 Worker = 一个短生命周期 Task
```

Reviewer 同样不需要。

如果后续 Worker 单次任务过长，再复用已有 Context Manager。

---

## 十四、阶段十二：复用 Error Recovery

所有模型调用统一走已有恢复逻辑。

目标：

```text
Steward
Worker
Reviewer
```

全部使用统一：

```text
LLM Runtime
    ↓
Retry / Backoff
Context Overflow Recovery
Output Truncation Recovery
Timeout Recovery
```

不要每个 Agent 各自实现 `try/except`。

---

## 十五、阶段十三：前端最小改造

### 新建 Session

增加 Repository 选择：

```text
Repository
[ miniOpenClaw ▼ ]

[创建会话]
```

### Chat 页面顶部

显示：

```text
Repository Steward
miniOpenClaw · main
```

### 复杂任务运行中

显示简单进度：

```text
Implement login feature

✓ Backend Agent
  ✓ Review passed

✓ Frontend Agent
  ✓ Review passed

● Test Agent
```

第一版不要做复杂 Agent 图谱或实时 DAG 可视化。

---

## 十六、最终数据关系

```text
Workspace
   │
   ├── Session
   │      │
   │      ├── messages
   │      ├── checkpoint
   │      └── Run
   │            │
   │            └── Task
   │
   └── Project Memory
```

Agent 不需要持久化成独立业务实体。

Steward：

```text
Session 的长期运行 Agent
```

Worker：

```text
一次 Task 的临时执行实例
```

Reviewer：

```text
一次 Review 的临时执行实例
```

---

## 十七、最终完整执行链

```text
用户选择 Repository
        ↓
创建 Workspace
        ↓
创建 Session 并绑定 workspace_id
        ↓
用户发送消息
        ↓
恢复 Steward checkpoint
        ↓
根据 workspace_id 加载相关 Memory
        ↓
执行 Context Compaction
        ↓
Steward 判断任务复杂度
```

简单任务：

```text
Steward
 ↓
read / write / terminal
 ↓
Final Answer
```

复杂任务：

```text
Steward
 ↓
制定 Plan
 ↓
delegate backend
 ↓
Backend Worker
 ↓
Reviewer
 ↓
WorkerResult
 ↓
Steward 根据结果重新推理
 ↓
delegate frontend
 ↓
Frontend Worker
 ↓
Reviewer
 ↓
WorkerResult
 ↓
Steward
 ↓
delegate test
 ↓
Test Worker
 ↓
Reviewer
 ↓
WorkerResult
 ↓
Steward 汇总
 ↓
Final Answer
```

最后：

```text
保存 Session
 ↓
保存 Steward checkpoint
 ↓
提取 Memory
 ↓
Project Memory 写入 workspace_id
 ↓
等待下一轮用户消息
```

---

## 十八、明确不做的功能

当前版本明确不实现：

- Worker 自主认领 Task
- Worker 间直接通信
- Mailbox
- 多 Worker 并行写代码
- Worktree Isolation
- 自动 Git Merge
- Worker 长期存活
- Worker 独立长期 Memory
- Reviewer 修改代码
- Session 中途切换 Repository

这些不是遗漏，而是当前 Demo 的架构边界。

---

## 十九、推荐实际开发顺序

### P0：先修底层闭环

1. Workspace
2. Session 绑定 Workspace
3. Tool root_dir 改造
4. Docker Workspace volume
5. Project Memory 增加 workspace_id

完成后，先验证现有单 Agent 功能全部正常。

### P1：增加复杂任务委派

6. 保留单 Steward
7. 实现 `delegate_task`
8. 实现通用 Worker
9. 实现 WorkerResult
10. 强制串行调用

先用一个通用 Worker 跑通闭环。

### P2：角色专业化

11. Backend Worker
12. Frontend Worker
13. Test Worker
14. Tool Permission Scope

### P3：质量闭环

15. Reviewer
16. Review Feedback Retry
17. 最大 Review 次数限制

### P4：可观测性

18. Run / Task
19. 前端显示执行进度
20. 日志与异常状态

### P5：收尾

21. 补测试
22. 更新 README
23. 更新架构图
24. 增加一个完整 Demo 场景
25. 整理简历项目描述

---

## 二十、实现原则

改造过程中始终遵守以下原则：

1. 优先复用现有代码，不重写 Agent Loop。
2. 不为了“多智能体”而增加不必要的复杂度。
3. Steward 是唯一长期 Agent。
4. Worker 必须短生命周期、上下文隔离。
5. Worker 必须串行执行。
6. Worker 只返回结构化 Result。
7. Reviewer 只负责验收，不负责直接修代码。
8. Workspace 是所有 Repository 操作的唯一边界。
9. Project Memory 必须绑定 Workspace。
10. 每完成一个阶段，都保证当前系统仍然可运行，不一次性大重构。