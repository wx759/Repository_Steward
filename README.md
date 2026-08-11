# Repository Steward

Repository Steward is a small coding-agent core derived from miniOpenClaw. It keeps the existing LangChain `create_agent` runtime, tool calling, streamed chat API, local sessions, prompt components, skills, and web interface.

## Core capabilities

- OpenAI-compatible chat models, with presets for Zhipu, Bailian, DeepSeek, and OpenAI.
- LangChain `create_agent` with multi-step tool calling.
- `read_file` and `terminal` tools scoped to the backend workspace.
- FastAPI chat API with Server-Sent Events.
- JSON-backed sessions that provide persistent multi-turn context.
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
└── app.py

frontend/
└── src/                    # Next.js chat workspace
```

## Request flow

```text
POST /api/chat
  → load session JSON
  → build messages from saved history
  → LangChain create_agent
  → LLM ↔ tools
  → stream SSE events
  → save user and assistant messages to the same session JSON
```

The JSON session is the single source of truth for both the UI and agent context. Restarting the API does not discard conversation history.

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
