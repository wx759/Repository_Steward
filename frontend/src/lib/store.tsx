"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  createSession,
  deleteSession,
  getSessionHistory,
  listSessions,
  listSkills,
  loadFile,
  renameSession,
  saveFile,
  streamChat,
  type SessionSummary,
  type ToolCall
} from "@/lib/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  status?: "incomplete" | "error";
  recoveryMessage?: string;
};

type AppStore = {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  skills: Array<{ name: string; description: string; path: string }>;
  editableFiles: string[];
  inspectorPath: string;
  inspectorContent: string;
  inspectorDirty: boolean;
  sidebarWidth: number;
  createNewSession: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  sendMessage: (value: string) => Promise<void>;
  renameCurrentSession: (title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  loadInspectorFile: (path: string) => Promise<void>;
  updateInspectorContent: (value: string) => void;
  saveInspector: () => Promise<void>;
  setSidebarWidth: (width: number) => void;
};

const FIXED_FILES = [
  "workspace/SOUL.md",
  "workspace/IDENTITY.md",
  "workspace/USER.md",
  "workspace/AGENTS.md"
];

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
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [skills, setSkills] = useState<Array<{ name: string; description: string; path: string }>>([]);
  const [inspectorPath, setInspectorPath] = useState("workspace/AGENTS.md");
  const [inspectorContent, setInspectorContent] = useState("");
  const [inspectorDirty, setInspectorDirty] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(308);

  const editableFiles = useMemo(
    () => [...FIXED_FILES, ...skills.map((skill) => skill.path)],
    [skills]
  );

  async function refreshSessions() {
    setSessions(await listSessions());
  }

  async function refreshSkills() {
    setSkills(await listSkills());
  }

  async function refreshSessionDetails(sessionId: string) {
    const history = await getSessionHistory(sessionId);
    setMessages(toUiMessages(history.messages));
  }

  async function createNewSession() {
    const created = await createSession();
    await refreshSessions();
    setCurrentSessionId(created.id);
    setMessages([]);
  }

  async function selectSession(sessionId: string) {
    setCurrentSessionId(sessionId);
    await refreshSessionDetails(sessionId);
  }

  async function ensureSession() {
    if (currentSessionId) return currentSessionId;
    const created = await createSession();
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
          } else if (event === "new_response") {
            // Keep all internal tool rounds inside the current assistant turn.
            // The UI exposes only one compact working state and the final answer.
            return;
          } else if (event === "done") {
            patchAssistant((message) => ({
              ...message,
              content: message.content || String(data.content ?? ""),
              status: data.status === "incomplete" || data.status === "error"
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
      setIsStreaming(false);
    }
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
    }
  }

  async function loadInspectorFile(path: string) {
    const file = await loadFile(path);
    setInspectorPath(path);
    setInspectorContent(file.content);
    setInspectorDirty(false);
  }

  async function saveInspector() {
    await saveFile(inspectorPath, inspectorContent);
    setInspectorDirty(false);
    await refreshSkills();
  }

  useEffect(() => {
    void (async () => {
      const [initialSessions, initialSkills] = await Promise.all([listSessions(), listSkills()]);
      setSkills(initialSkills);
      if (initialSessions.length) {
        setSessions(initialSessions);
        setCurrentSessionId(initialSessions[0].id);
        await refreshSessionDetails(initialSessions[0].id);
      } else {
        const created = await createSession();
        setSessions([created]);
        setCurrentSessionId(created.id);
      }
      const file = await loadFile("workspace/AGENTS.md");
      setInspectorContent(file.content);
    })();
  }, []);

  return <StoreContext.Provider value={{
    sessions, currentSessionId, messages, isStreaming, skills, editableFiles,
    inspectorPath, inspectorContent, inspectorDirty, sidebarWidth,
    createNewSession, selectSession, sendMessage, renameCurrentSession, removeSession,
    loadInspectorFile, updateInspectorContent: (value) => {
      setInspectorContent(value);
      setInspectorDirty(true);
    }, saveInspector, setSidebarWidth
  }}>{children}</StoreContext.Provider>;
}

export function useAppStore() {
  const value = useContext(StoreContext);
  if (!value) throw new Error("useAppStore must be used inside AppProvider");
  return value;
}
