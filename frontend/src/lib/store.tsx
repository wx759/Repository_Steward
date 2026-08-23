"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  createSession,
  createWorkspace,
  cancelRun,
  deleteSession,
  getSessionHistory,
  listSessions,
  listRuns,
  listWorkspaces,
  renameSession,
  streamChat,
  type SessionSummary,
  type Run,
  type Workspace,
  type ToolCall
} from "@/lib/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  status?: "incomplete" | "interrupted" | "error";
  recoveryMessage?: string;
};

type AppStore = {
  activeView: "chat" | "memory";
  sessions: SessionSummary[];
  workspaces: Workspace[];
  selectedWorkspaceId: string;
  currentWorkspace: Workspace | null;
  runs: Run[];
  currentSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  activeRunId: string | null;
  sidebarWidth: number;
  createNewSession: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  sendMessage: (value: string) => Promise<void>;
  cancelCurrentRun: () => Promise<void>;
  renameCurrentSession: (title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  setSidebarWidth: (width: number) => void;
  setActiveView: (view: "chat" | "memory") => void;
  setSelectedWorkspaceId: (workspaceId: string) => void;
  addWorkspace: (rootPath: string, name?: string) => Promise<Workspace>;
};

const StoreContext = createContext<AppStore | null>(null);
const makeId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

function toUiMessages(history: Awaited<ReturnType<typeof getSessionHistory>>["messages"]): Message[] {
  const messages: Message[] = [];

  for (const item of history) {
    const next: Message = {
      id: makeId(),
      role: item.role,
      content: item.content ?? "",
      toolCalls: item.tool_calls ?? [],
      status: item.status
    };
    const previous = messages.at(-1);

    // A single agent turn can contain many internal AI/tool records. Present
    // them as one assistant response instead of one card per tool round-trip.
    if (next.role === "assistant" && previous?.role === "assistant") {
      previous.toolCalls.push(...next.toolCalls);
      if (next.content.trim()) {
        previous.content = previous.content.trim()
          ? `${previous.content}\n\n${next.content}`
          : next.content;
      }
      previous.status = next.status ?? previous.status;
      continue;
    }

    messages.push(next);
  }

  return messages;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [activeView, setActiveView] = useState<"chat" | "memory">("chat");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(308);
  const currentSession = sessions.find((item) => item.id === currentSessionId);
  const currentWorkspace = workspaces.find(
    (item) => item.workspace_id === (currentSession?.workspace_id || selectedWorkspaceId)
  ) ?? null;

  async function refreshSessions() {
    setSessions(await listSessions());
  }

  async function refreshSessionDetails(sessionId: string) {
    const history = await getSessionHistory(sessionId);
    setMessages(toUiMessages(history.messages));
    setRuns(await listRuns(sessionId));
  }

  async function createNewSession() {
    setActiveView("chat");
    setCurrentSessionId(null);
    setMessages([]);
    setRuns([]);
  }

  async function addWorkspace(rootPath: string, name?: string) {
    const created = await createWorkspace(rootPath, name);
    const next = await listWorkspaces();
    setWorkspaces(next);
    setSelectedWorkspaceId(created.workspace_id);
    setCurrentSessionId(null);
    setMessages([]);
    setRuns([]);
    return created;
  }

  async function selectSession(sessionId: string) {
    setActiveView("chat");
    setCurrentSessionId(sessionId);
    const session = sessions.find((item) => item.id === sessionId);
    if (session?.workspace_id) setSelectedWorkspaceId(session.workspace_id);
    await refreshSessionDetails(sessionId);
  }

  async function ensureSession() {
    if (currentSessionId) return currentSessionId;
    const workspaceId = selectedWorkspaceId || workspaces[0]?.workspace_id;
    if (!workspaceId) throw new Error("请先选择 Repository");
    const created = await createSession(workspaceId);
    setCurrentSessionId(created.id);
    await refreshSessions();
    return created.id;
  }

  async function sendMessage(value: string) {
    if (!value.trim() || isStreaming) return;
    const sessionId = await ensureSession();
    const userMessage: Message = {
      id: makeId(), role: "user", content: value.trim(), toolCalls: []
    };
    const assistantMessage: Message = {
      id: makeId(), role: "assistant", content: "", toolCalls: []
    };
    setMessages((previous) => [...previous, userMessage, assistantMessage]);
    setIsStreaming(true);

    const activeAssistantId = assistantMessage.id;
    const patchAssistant = (update: (message: Message) => Message) => {
      setMessages((previous) =>
        previous.map((message) => message.id === activeAssistantId ? update(message) : message)
      );
    };

    try {
      await streamChat({ message: value.trim(), session_id: sessionId }, {
        onEvent(event, data) {
          if (event === "token") {
            patchAssistant((message) => ({
              ...message,
              content: `${message.content}${String(data.content ?? "")}`,
              recoveryMessage: undefined
            }));
          } else if (event === "recovery") {
            patchAssistant((message) => ({
              ...message,
              recoveryMessage: String(data.message ?? "正在尝试恢复模型调用……")
            }));
          } else if (event === "run") {
            const nextRunId = String(data.run_id ?? "");
            if (nextRunId) setActiveRunId(nextRunId);
            void listRuns(sessionId).then(setRuns);
          } else if (event === "tool_start") {
            patchAssistant((message) => ({
              ...message,
              recoveryMessage: undefined,
              toolCalls: [...message.toolCalls, {
                tool: String(data.tool ?? "tool"),
                input: String(data.input ?? ""),
                output: ""
              }]
            }));
          } else if (event === "tool_end") {
            patchAssistant((message) => ({
              ...message,
              toolCalls: message.toolCalls.map((call, index, calls) =>
                index === calls.length - 1 ? { ...call, output: String(data.output ?? "") } : call
              )
            }));
            if (String(data.tool ?? "") === "delegate_task") void listRuns(sessionId).then(setRuns);
          } else if (event === "new_response") {
            // Keep all internal tool rounds inside the current assistant turn.
            // The UI exposes only one compact working state and the final answer.
            return;
          } else if (event === "done") {
            patchAssistant((message) => ({
              ...message,
              content: message.content || String(data.content ?? ""),
              status: data.status === "incomplete" || data.status === "error" || data.status === "interrupted"
                ? data.status
                : undefined,
              recoveryMessage: undefined
            }));
          } else if (event === "title") {
            void refreshSessions();
          } else if (event === "error") {
            patchAssistant((message) => ({
              ...message,
              content: message.content || `请求失败：${String(data.error ?? "unknown error")}`,
              status: "error",
              recoveryMessage: undefined
            }));
          }
        }
      });
      await refreshSessions();
      await refreshSessionDetails(sessionId);
    } finally {
      setActiveRunId(null);
      setIsStreaming(false);
    }
  }

  async function cancelCurrentRun() {
    let runId = activeRunId;
    if (!runId && currentSessionId) {
      const latestRuns = await listRuns(currentSessionId);
      runId = latestRuns.find((run) => run.status === "running")?.run_id ?? null;
    }
    if (!runId) return;
    await cancelRun(runId);
    if (currentSessionId) setRuns(await listRuns(currentSessionId));
  }

  async function renameCurrentSession(title: string) {
    if (!currentSessionId || !title.trim()) return;
    await renameSession(currentSessionId, title.trim());
    await refreshSessions();
  }

  async function removeSession(sessionId: string) {
    await deleteSession(sessionId);
    const remaining = await listSessions();
    setSessions(remaining);
    if (currentSessionId !== sessionId) return;
    if (remaining.length) {
      setCurrentSessionId(remaining[0].id);
      await refreshSessionDetails(remaining[0].id);
      } else {
        setCurrentSessionId(null);
        setMessages([]);
      setRuns([]);
    }
  }

  useEffect(() => {
    void (async () => {
      const [initialSessions, initialWorkspaces] = await Promise.all([
        listSessions(), listWorkspaces()
      ]);
      setWorkspaces(initialWorkspaces);
      setSelectedWorkspaceId(initialWorkspaces[0]?.workspace_id ?? "");
      if (initialSessions.length) {
        setSessions(initialSessions);
        setCurrentSessionId(initialSessions[0].id);
        setSelectedWorkspaceId(initialSessions[0].workspace_id);
        await refreshSessionDetails(initialSessions[0].id);
      }
    })();
  }, []);

  return <StoreContext.Provider value={{
    activeView, sessions, workspaces, selectedWorkspaceId, currentWorkspace, runs,
    currentSessionId, messages, isStreaming, activeRunId, sidebarWidth,
    createNewSession, selectSession, sendMessage, cancelCurrentRun, renameCurrentSession, removeSession,
    setSidebarWidth, setActiveView,
    setSelectedWorkspaceId: (workspaceId) => {
      setSelectedWorkspaceId(workspaceId);
      setCurrentSessionId(null);
      setMessages([]);
      setRuns([]);
    }, addWorkspace
  }}>{children}</StoreContext.Provider>;
}

export function useAppStore() {
  const value = useContext(StoreContext);
  if (!value) throw new Error("useAppStore must be used inside AppProvider");
  return value;
}
