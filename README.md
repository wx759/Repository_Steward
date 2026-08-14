# Repository Steward

Repository Steward is a small coding-agent core derived from miniOpenClaw. It keeps the existing LangChain `create_agent` runtime, tool calling, streamed chat API, local sessions, prompt components, skills, and web interface.

## Core capabilities

- OpenAI-compatible chat models, with presets for Zhipu, Bailian, DeepSeek, and OpenAI.
- LangChain `create_agent` with multi-step tool calling.
- `read_file` and `terminal` tools scoped to the backend workspace.
- FastAPI chat API with Server-Sent Events.
- JSON-backed sessions that provide persistent multi-turn context.
- Four-layer context compaction before every agent model call.
- Model error recovery with backoff, forced L4 retry, and output continuation.
- SQLite-backed cross-session long-term Memory with isolated select/extract LLM calls.
- File-based prompt components and a lightweight Skill scanner.
- Next.js chat interface with session management, tool traces, and a prompt inspector.

## Architecture

```text
backend/
├── api/
│   ├── chat.py
│   ├── files.py
│   └── sessions.py
├── config/
├── graph/
│   ├── agent.py
│   ├── agent_factory.py
│   └── llm.py
├── memory/                 # SQLite store, selector, extractor, transient prompt middleware
├── service/
│   ├── prompt_builder.py
│   └── session_manager.py
├── tools/
│   ├── read_file_tool.py
│   ├── terminal_tool.py
│   └── skills_scanner.py
├── skills/
├── workspace/
├── sessions/              # created at runtime
├── memory.sqlite          # created at runtime
└── app.py

frontend/
└── src/                    # Next.js chat workspace
```

## Request flow

```text
POST /api/chat
  → use session_id as LangGraph thread_id
  → restore the active Agent state from SQLite checkpoint
  → if no checkpoint exists, seed it once from the session JSON
  → append the current HumanMessage
  → select and load up to five relevant long-term memories
  → LangChain create_agent
  → LLM ↔ tools
  → extract durable facts from the completed raw turn
  → stream SSE events
  → checkpoint the active (possibly compacted) Agent state
  → append the complete turn to the session JSON archive
```

The three persistent stores have separate jobs. Session JSON is the complete conversation
archive used by the UI. `backend/checkpoints.sqlite` is the active Agent state used by
LangGraph, so compacted context is reused on later turns and after API restarts.
`backend/memory.sqlite` contains selected durable facts that can be reused across sessions.

Session files use a small LangChain-aligned schema: `human`, `ai`, and `tool` records.
Tool calls are linked to tool results with `id` / `tool_call_id`. The Session API projects
these records into the existing `user` / `assistant` UI shape. The records seed a new
checkpoint without dropping tool messages. Older UI-oriented session files are migrated when
they are read. SSE events remain a transport-only format and are not used to rebuild
persisted messages.

## Long-term Memory

Before each main Agent run, a side-model sees only the current request and the Memory
catalog (`id + name + description`) and returns at most five ids. The selected bodies are
loaded from `backend/memory.sqlite` and appended only to that run's model system prompt.
They are not added to graph messages, Session JSON, or the LangGraph checkpoint. If the
selection call fails, a local keyword matcher is used instead.

After a completed main Agent turn, another isolated side-model extracts only stable user
preferences, long-term project facts, durable Agent feedback, and important references from
the pre-compaction turn snapshot. Temporary task details and secrets are rejected. Extractor
failure never changes an already completed main answer. The feature and its limits use the
`MEMORY_*` settings in `backend/config/.env.example`; set `MEMORY_ENABLED=false` to disable
all select/load/extract/save work.

## Context compaction

Every Agent LLM call runs a small middleware pipeline in this order:

1. **L3 tool result budget** persists oversized results under
   `backend/.task_outputs/tool-results/` and keeps a path plus preview.
2. **L1 message snip** keeps the initial and recent messages while preserving complete
   AI tool-call/ToolMessage groups.
3. **L2 micro compact** keeps only the most recent tool results in full and replaces
   older results with a placeholder.
4. **L4 summary** runs only when the first three layers still exceed the configured
   context budget and rebuilds the Agent context from a summary plus a recent suffix.

The session JSON retains complete UI history. LangGraph stores the compressed active
Agent state in `backend/checkpoints.sqlite`, keyed by the Session ID as `thread_id`.
This means L1/L2/L3/L4 state survives later user turns and API restarts.
Thresholds can be overridden with the `CONTEXT_*` settings shown in
`backend/config/.env.example`.

## Error recovery

Model calls are wrapped by an Agent middleware that retries transient 429, 529,
5xx, timeout, and connection failures with exponential backoff. Recovery progress
is streamed to the UI without becoming conversation history. A provider-reported
context overflow forces the existing L4 summary once for that user turn. Output
length truncation keeps generated text and continues internally up to three times;
if still truncated, the merged answer is persisted with `status: incomplete`.

The optional `LLM_FALLBACK_MODEL` uses the same provider, base URL, and API key and
is disabled when empty. Retry and continuation limits use the `RECOVERY_*` settings
in `backend/config/.env.example`.

## Setup

### Docker Compose (recommended)

Create `backend/config/.env` first. For Alibaba Cloud Model Studio:

```dotenv
LLM_PROVIDER=bailian
LLM_MODEL=qwen3.7-plus
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

Build and start both containers from the repository root:

```bash
docker compose up --build -d
```

Open `http://127.0.0.1:7788`. The backend health endpoint is available at
`http://127.0.0.1:8002/health`.

Useful commands:

```bash
docker compose logs -f
docker compose ps
docker compose down
```

The images can also be built independently:

```bash
docker build -t repository-steward-backend ./backend
docker build -t repository-steward-frontend ./frontend

docker run --rm --env-file backend/config/.env -p 8002:8002 repository-steward-backend
docker run --rm -p 7788:7788 repository-steward-frontend
```

Use Compose when the Agent must edit host files; the standalone backend command above
runs against the source copied into its image.

The Compose setup bind-mounts `./backend` at `/app`. Agent file changes and local
session files therefore persist on the host. The frontend runs from its standalone
production image.

### Backend

```bash
cd backend
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
pip install -r requirements.txt
```

Copy `config/.env.example` to `config/.env` and set the model credentials:

```dotenv
LLM_PROVIDER=zhipu
LLM_MODEL=glm-5
LLM_API_KEY=your_key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
```

Start the API:

```bash
uvicorn app:app --host 0.0.0.0 --port 8002 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:7788`.

## Prompt and skills

The system prompt is assembled from:

- `skills/SKILLS_SNAPSHOT.md`
- `workspace/SOUL.md`
- `workspace/IDENTITY.md`
- `workspace/USER.md`
- `workspace/AGENTS.md`

Each directory directly below `backend/skills/` may provide a `SKILL.md` file with YAML front matter. The scanner refreshes the snapshot at API startup. The agent can then inspect a relevant skill with `read_file`.

## Tests

From the repository root:

```bash
pytest backend/tests
```
