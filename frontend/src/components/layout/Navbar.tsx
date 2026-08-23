"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { GitBranch, Menu } from "lucide-react";

import { Sidebar } from "@/components/layout/Sidebar";
import { useAppStore } from "@/lib/store";

export function Navbar() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const {
    activeView, renameCurrentSession, sessions, currentSessionId,
    isStreaming, currentWorkspace
  } = useAppStore();
  const currentTitle = activeView === "memory"
    ? "长期记忆"
    : currentSessionId
      ? sessions.find((session) => session.id === currentSessionId)?.title ?? "会话"
      : "新任务";

  const rename = () => {
    if (activeView === "memory" || !currentSessionId) return;
    const next = window.prompt("重命名当前会话", currentTitle);
    if (next?.trim()) void renameCurrentSession(next);
  };

  return <>
    <header className="flex h-[62px] shrink-0 items-center justify-between border-b border-[#e6e8eb] bg-white px-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 md:hidden" onClick={() => setIsSidebarOpen(true)} aria-label="打开导航" type="button"><Menu size={18} /></button>
        <div className="min-w-0">
          <button className={`block max-w-[48vw] truncate text-left text-sm font-semibold text-[#171a1f] ${activeView === "chat" && currentSessionId ? "hover:text-[#567000]" : "cursor-default"}`} onClick={rename} type="button">{currentTitle}</button>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
            {currentWorkspace ? <><span>{currentWorkspace.name}</span><span className="flex items-center gap-1"><GitBranch size={10} />{currentWorkspace.branch}</span></> : <span>请先添加并选择 Repository</span>}
          </div>
        </div>
      </div>
      <div className="hidden items-center gap-2 text-[10px] text-slate-400 sm:flex"><span className={`h-1.5 w-1.5 rounded-full ${isStreaming ? "animate-pulse bg-amber-500" : "bg-emerald-500"}`} />{isStreaming ? "Steward working" : "Ready"}</div>
    </header>
    {mounted && isSidebarOpen && createPortal(<div className="fixed inset-0 z-[9998] md:hidden"><button className="absolute inset-0 bg-black/50" onClick={() => setIsSidebarOpen(false)} aria-label="关闭导航" type="button" /><div className="relative h-full w-[86vw] max-w-[320px] p-2"><Sidebar onSelect={() => setIsSidebarOpen(false)} /></div></div>, document.body)}
  </>;
}
