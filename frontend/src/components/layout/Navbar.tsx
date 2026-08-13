"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, Menu, PanelTop, Plus, Settings2 } from "lucide-react";
import { InspectorPanel } from "@/components/editor/InspectorPanel";
import { Sidebar } from "@/components/layout/Sidebar";
import { useAppStore } from "@/lib/store";

export function Navbar() {
  const [isInspectorOpen, setIsInspectorOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const { createNewSession, renameCurrentSession, sessions, currentSessionId, isStreaming } = useAppStore();
  const currentTitle = sessions.find((session) => session.id === currentSessionId)?.title ?? "新会话";
  const rename = () => {
    const next = window.prompt("重命名当前会话", currentTitle);
    if (next?.trim()) void renameCurrentSession(next);
  };

  return (
    <>
      <header className="panel flex h-[66px] shrink-0 items-center justify-between rounded-2xl px-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-[var(--color-ink-soft)] hover:bg-slate-100 hover:text-slate-900 md:hidden" onClick={() => setIsSidebarOpen(true)} aria-label="打开会话列表" type="button"><Menu size={19} /></button>
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-ocean-soft)] text-ocean ring-1 ring-inset ring-violet-200/60"><Bot size={19} /></div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="hidden text-[10px] font-medium uppercase tracking-[0.22em] text-[var(--color-ink-soft)] sm:block">Repository Steward</p>
              <span className={`h-1.5 w-1.5 rounded-full ${isStreaming ? "animate-pulse bg-[var(--color-ember)]" : "bg-[var(--color-success)]"}`} />
              <span className="text-[10px] text-[var(--color-ink-soft)]">{isStreaming ? "工作中" : "已就绪"}</span>
            </div>
            <button className="block max-w-[42vw] truncate text-left text-sm font-medium text-slate-900 hover:text-[#594bc4] sm:max-w-sm" onClick={rename} title="点击重命名" type="button">{currentTitle}</button>
          </div>
        </div>
        <div className="flex items-center gap-1.5 sm:gap-2">
          <div className="mr-1 hidden items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-[var(--color-ink-soft)] lg:flex"><PanelTop size={14} /> Agent Core</div>
          <button className="flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 shadow-sm transition hover:border-violet-200 hover:bg-violet-50" onClick={() => void createNewSession()} type="button"><Plus size={16} /><span className="hidden sm:inline">新会话</span></button>
          <button className="flex h-9 w-9 items-center justify-center rounded-xl text-[var(--color-ink-soft)] transition hover:bg-slate-100 hover:text-slate-900" onClick={() => setIsInspectorOpen(true)} title="打开配置编辑器" type="button"><Settings2 size={17} /></button>
        </div>
      </header>
      {mounted && isSidebarOpen && createPortal(
        <div className="fixed inset-0 z-[9998] md:hidden"><button className="absolute inset-0 bg-slate-900/25 backdrop-blur-sm" onClick={() => setIsSidebarOpen(false)} aria-label="关闭会话列表" type="button" /><div className="relative h-full w-[86vw] max-w-[350px] p-3"><Sidebar onSelect={() => setIsSidebarOpen(false)} /></div></div>, document.body
      )}
      {mounted && isInspectorOpen && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-3 sm:p-6"><button className="absolute inset-0 bg-slate-900/25 backdrop-blur-md" onClick={() => setIsInspectorOpen(false)} aria-label="关闭配置编辑器" type="button" /><div className="relative z-10 h-[88vh] w-full max-w-5xl"><InspectorPanel onClose={() => setIsInspectorOpen(false)} /></div></div>, document.body
      )}
    </>
  );
}
