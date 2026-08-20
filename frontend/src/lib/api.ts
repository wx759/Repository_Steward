export type ToolCall = {
  tool: string;
  input: string;
  output: string;
};

export type SessionSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  workspace_id: string;
};

export type Workspace = {
  workspace_id: string;
  name: string;
  root_path: string;
  relative_path: string;
  branch: string;
};

export type RunTask = {
  task_id: string;
  role: string;
  description: string;
  status: "running" | "completed" | "failed";
  review_status: "pending" | "passed" | "failed";
};

export type Run = {
  run_id: string;
  session_id: string;
  goal: string;
  status: "running" | "completed" | "interrupted" | "failed";
  reason?: string | null;
  error?: string | null;
  created_at: number;
  updated_at: number;
  tasks: RunTask[];
};

export type SessionHistory = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
    tool_calls?: ToolCall[];
    status?: "incomplete" | "interrupted" | "error";
    finish_reason?: string;
    continuation_count?: number;
  }>;
};

export type StreamHandlers = {
  onEvent: (event: string, data: Record<string, unknown>) => void;
};

function getApiBase() {
  if (typeof window === "undefined") {
    return "http://127.0.0.1:8002/api";
  }
  return `http://${window.location.hostname}:8002/api`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function listSessions() {
  return request<SessionSummary[]>("/sessions");
}

export function listWorkspaces() {
  return request<Workspace[]>("/workspaces");
}

export function createWorkspace(rootPath: string, name?: string) {
  return request<Workspace>("/workspaces", {
    method: "POST",
    body: JSON.stringify({ root_path: rootPath, name: name || undefined, require_git: true })
  });
}

export function getWorkspaceConfig() {
  return request<{ workspace_root: string }>("/workspaces/config");
}

export function discoverRepositories() {
  return request<Array<{ name: string; root_path: string; relative_path: string }>>("/workspaces/discover");
}

export function createSession(workspaceId: string, title = "新会话") {
  return request<SessionSummary>("/sessions", {
    method: "POST",
    body: JSON.stringify({ title, workspace_id: workspaceId })
  });
}

export function listRuns(sessionId: string) {
  return request<Run[]>(`/runs?session_id=${encodeURIComponent(sessionId)}`);
}

export function cancelRun(runId: string) {
  return request<Run>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST"
  });
}

export function renameSession(sessionId: string, title: string) {
  return request<SessionSummary>(`/sessions/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify({ title })
  });
}

export function deleteSession(sessionId: string) {
  return request<{ ok: boolean }>(`/sessions/${sessionId}`, { method: "DELETE" });
}

export function getSessionHistory(sessionId: string) {
  return request<SessionHistory>(`/sessions/${sessionId}/history`);
}

export async function streamChat(
  payload: { message: string; session_id: string },
  handlers: StreamHandlers
) {
  const response = await fetch(`${getApiBase()}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, stream: true })
  });
  if (!response.ok || !response.body) {
    throw new Error(`Chat request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushBlock = (block: string) => {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) {
      handlers.onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      flushBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) {
      if (buffer.trim()) flushBlock(buffer);
      break;
    }
  }
}
