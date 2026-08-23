"use client";

import { useState } from "react";
import { Brain, Check, FolderGit2, MessageSquare, Plus, Trash2, X } from "lucide-react";
import { useAppStore } from "@/lib/store";
import { discoverRepositories, getWorkspaceConfig } from "@/lib/api";

function formatTime(timestamp: number) {
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function Sidebar({ onSelect }: { onSelect?: () => void }) {
  const { activeView, setActiveView, sessions, currentSessionId, selectSession, createNewSession, removeSession, workspaces, selectedWorkspaceId, setSelectedWorkspaceId, addWorkspace } = useAppStore();
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [discovered, setDiscovered] = useState<Array<{ name: string; root_path: string }>>([]);

  const openAdd = async () => {
    setShowAdd(true); setError("");
    try {
      const [config, repositories] = await Promise.all([getWorkspaceConfig(), discoverRepositories()]);
      setWorkspaceRoot(config.workspace_root); setDiscovered(repositories);
    } catch { setError("无法读取 Repository 配置"); }
  };

  const submitRepository = async () => {
    if (!path.trim() || saving) return;
    setSaving(true); setError("");
    try { await addWorkspace(path.trim()); setPath(""); setShowAdd(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "添加仓库失败"); }
    finally { setSaving(false); }
  };

  return (
    <aside className="sidebar-shell flex h-full flex-col overflow-hidden">
      <div className="px-4 pb-3 pt-5"><div className="flex items-center gap-2.5"><div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#212121] text-white"><FolderGit2 size={17} /></div><div><p className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#8a8a8a]">Repository</p><p className="text-sm font-medium text-[#212121]">Steward</p></div></div></div>
      <div className="px-3"><button className="flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-[#dedede] bg-white text-sm font-medium text-[#212121] transition hover:bg-[#ececec]" onClick={() => void createNewSession()} type="button"><Plus size={16} /> 新任务</button></div>

      <div className="mt-2 px-3"><button className={`flex h-10 w-full items-center gap-2 rounded-lg px-3 text-sm font-medium transition ${activeView === "memory" ? "bg-[#e2f3ee] text-[#087f5f]" : "text-[#555] hover:bg-[#ececec]"}`} onClick={() => { setActiveView("memory"); onSelect?.(); }} type="button"><Brain size={16} /> 长期记忆<span className="ml-auto rounded-full bg-white/70 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider">审核</span></button></div>

      <div className="mt-5 px-3">
        <div className="mb-2 flex items-center justify-between px-1"><span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-[#8a8a8a]">Workspace</span><button className="flex h-6 w-6 items-center justify-center rounded-md text-[#777] transition hover:bg-[#e7e7e7] hover:text-[#212121]" onClick={() => void openAdd()} title="添加本地仓库" type="button"><Plus size={14} /></button></div>
        {workspaces.length ? <div className="space-y-1">{workspaces.map((workspace) => { const active = workspace.workspace_id === selectedWorkspaceId; return <button className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition ${active ? "bg-[#e7e7e7] text-[#212121]" : "text-[#666] hover:bg-[#ececec] hover:text-[#212121]"}`} key={workspace.workspace_id} onClick={() => setSelectedWorkspaceId(workspace.workspace_id)} type="button"><FolderGit2 size={14} className={active ? "text-[#10a37f]" : "text-[#999]"} /><span className="min-w-0 flex-1 truncate">{workspace.name}</span>{active && <Check size={13} className="text-[#10a37f]" />}</button>; })}</div> : <button className="w-full rounded-lg border border-dashed border-[#d0d0d0] px-3 py-4 text-left transition hover:border-[#aaa] hover:bg-[#ececec]" onClick={() => void openAdd()} type="button"><p className="text-xs font-medium text-[#444]">添加你的第一个仓库</p><p className="mt-1 text-[10px] leading-4 text-[#8a8a8a]">选择真实项目后才能开始对话</p></button>}
      </div>

      <div className="mt-6 flex min-h-0 flex-1 flex-col"><div className="px-4 text-[10px] font-semibold uppercase tracking-[0.2em] text-[#8a8a8a]">Recent sessions</div><div className="mt-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {!sessions.length && <p className="px-2 py-3 text-[11px] leading-5 text-[#999]">完成第一次真实交互后，会话会出现在这里。</p>}
        {sessions.map((session) => { const active = session.id === currentSessionId; const workspace = workspaces.find((item) => item.workspace_id === session.workspace_id); return <div className={`group relative rounded-lg transition ${active ? "bg-[#e7e7e7]" : "hover:bg-[#ececec]"}`} key={session.id}><button className="w-full px-2.5 py-2.5 text-left" onClick={() => { void selectSession(session.id); onSelect?.(); }} type="button"><div className="flex gap-2.5"><MessageSquare className={`mt-0.5 shrink-0 ${active ? "text-[#555]" : "text-[#aaa]"}`} size={14} /><div className="min-w-0 flex-1"><p className={`truncate text-xs ${active ? "font-medium text-[#212121]" : "text-[#666]"}`}>{session.title}</p><p className="mt-1 truncate text-[9px] text-[#999]">{workspace?.name ?? "Repository"} · {formatTime(session.updated_at)}</p></div></div></button><button className="absolute right-1.5 top-2 flex h-7 w-7 items-center justify-center rounded-md text-[#888] opacity-0 transition hover:bg-red-50 hover:text-red-500 group-hover:opacity-100" onClick={(event) => { event.stopPropagation(); if (window.confirm(`删除“${session.title}”？`)) void removeSession(session.id); }} aria-label={`删除会话 ${session.title}`} type="button"><Trash2 size={13} /></button></div>; })}
      </div></div>

      {showAdd && <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60 p-4"><div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#212121] p-5 shadow-2xl"><div className="flex items-start justify-between"><div><h3 className="text-base font-semibold text-white">添加本地仓库</h3><p className="mt-1 text-xs text-white/40">允许的根目录：<span className="font-mono text-white/60">{workspaceRoot || "加载中…"}</span></p></div><button className="rounded-lg p-1.5 text-white/40 hover:bg-white/10 hover:text-white" onClick={() => setShowAdd(false)} type="button"><X size={16} /></button></div>{discovered.length > 0 && <div className="mt-4"><p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-white/35">Detected repositories</p><div className="space-y-1">{discovered.map((repository) => <button className="flex w-full items-center gap-2 rounded-lg border border-white/5 bg-white/[.04] px-3 py-2 text-left text-xs text-white/70 hover:border-white/20 hover:text-white" key={repository.root_path} onClick={() => setPath(repository.root_path)} type="button"><FolderGit2 size={13} className="text-[#10a37f]" /><span className="truncate">{repository.name}</span></button>)}</div></div>}<label className="mt-4 block text-[11px] font-medium text-white/55">仓库绝对路径<input autoFocus className="mt-2 w-full rounded-xl border border-white/10 bg-black/25 px-3 py-3 font-mono text-xs text-white outline-none placeholder:text-white/20 focus:border-white/30" onChange={(event) => setPath(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitRepository(); }} placeholder={`${workspaceRoot || "/workspace"}/my-project`} value={path} /></label>{error && <p className="mt-2 rounded-lg bg-red-400/10 px-3 py-2 text-[11px] text-red-300">{error}</p>}<div className="mt-5 flex justify-end gap-2"><button className="rounded-lg px-3 py-2 text-xs text-white/50 hover:bg-white/5" onClick={() => setShowAdd(false)} type="button">取消</button><button className="rounded-lg bg-white px-4 py-2 text-xs font-semibold text-[#171717] disabled:opacity-40" disabled={!path.trim() || saving} onClick={() => void submitRepository()} type="button">{saving ? "正在验证…" : "添加并使用"}</button></div></div></div>}
    </aside>
  );
}
