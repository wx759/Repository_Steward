"use client";

import { MessageSquare, Plus, Trash2 } from "lucide-react";
import { useAppStore } from "@/lib/store";

function formatTime(timestamp: number) {
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function Sidebar({ onSelect }: { onSelect?: () => void }) {
  const { sessions, currentSessionId, selectSession, createNewSession, removeSession } = useAppStore();
  return (
    <aside className="panel flex h-full flex-col rounded-2xl p-3">
      <div className="flex items-center justify-between px-1 pb-3 pt-1">
        <div><p className="text-[10px] font-medium uppercase tracking-[0.22em] text-[var(--color-ink-soft)]">Workspace</p><h2 className="mt-1 text-sm font-semibold text-slate-900">最近会话</h2></div>
        <button className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-ocean-soft)] text-[#6859d9] transition hover:bg-violet-100" onClick={() => void createNewSession()} aria-label="新建会话" type="button"><Plus size={16} /></button>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {sessions.map((session) => {
          const active = session.id === currentSessionId;
          return (
            <div className={`group relative rounded-xl border transition ${active ? "border-violet-200 bg-violet-50" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`} key={session.id}>
              <button className="w-full px-3 py-3 text-left" onClick={() => { void selectSession(session.id); onSelect?.(); }} type="button">
                <div className="flex items-start gap-2.5">
                  <MessageSquare className={active ? "mt-0.5 shrink-0 text-[#6859d9]" : "mt-0.5 shrink-0 text-slate-400"} size={15} />
                  <div className="min-w-0 flex-1"><p className={`truncate text-sm ${active ? "font-medium text-slate-900" : "text-slate-600"}`}>{session.title}</p><p className="mt-1 text-[11px] text-slate-400">{session.message_count} 条消息 · {formatTime(session.updated_at)}</p></div>
                </div>
              </button>
              <button className="absolute right-2 top-2.5 flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 opacity-0 transition hover:bg-red-50 hover:text-red-500 group-hover:opacity-100 focus:opacity-100" onClick={(event) => { event.stopPropagation(); if (window.confirm(`确定删除“${session.title}”吗？`)) void removeSession(session.id); }} aria-label={`删除会话 ${session.title}`} type="button"><Trash2 size={14} /></button>
            </div>
          );
        })}
      </div>
      <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-3">
        <div className="flex items-center justify-between text-[11px] text-[var(--color-ink-soft)]"><span>本地会话</span><span className="text-[var(--color-success)]">● 已同步</span></div>
        <p className="mt-1.5 text-[11px] leading-5 text-slate-400">对话记录保存在本机，不同会话彼此隔离。</p>
      </div>
    </aside>
  );
}
