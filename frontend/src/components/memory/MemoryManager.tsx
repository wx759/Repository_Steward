"use client";

import { useCallback, useEffect, useState } from "react";
import { Archive, Brain, Globe2, RefreshCw, ShieldCheck } from "lucide-react";

import { archiveMemory, listMemories, type MemoryRecord } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type StatusFilter = "all" | MemoryRecord["status"];
type TypeFilter = "all" | MemoryRecord["type"];

const statusLabels: Record<MemoryRecord["status"], string> = {
  active: "使用中",
  superseded: "已替代",
  archived: "已归档"
};

const typeLabels: Record<MemoryRecord["type"], string> = {
  user: "用户偏好",
  project: "项目约束",
  feedback: "协作反馈",
  reference: "参考资料"
};

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      }).format(date);
}

export function MemoryManager() {
  const { currentWorkspace } = useAppStore();
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [type, setType] = useState<TypeFilter>("all");
  const [loading, setLoading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [error, setError] = useState("");
  const selected = memories.find((memory) => memory.id === selectedId) ?? memories[0] ?? null;

  const refresh = useCallback(async () => {
    if (!currentWorkspace) {
      setMemories([]);
      setSelectedId(null);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const records = await listMemories(currentWorkspace.workspace_id, {
        status: status === "all" ? undefined : status,
        type: type === "all" ? undefined : type
      });
      setMemories(records);
      setSelectedId((current) => records.some((item) => item.id === current) ? current : records[0]?.id ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取长期记忆");
    } finally {
      setLoading(false);
    }
  }, [currentWorkspace, status, type]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function archiveSelected() {
    if (!currentWorkspace || !selected || selected.status !== "active" || archiving) return;
    if (!window.confirm(`归档“${selected.name}”？归档后 Agent 将不再召回它。`)) return;
    setArchiving(true);
    setError("");
    try {
      await archiveMemory(selected.id, currentWorkspace.workspace_id);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "归档失败");
    } finally {
      setArchiving(false);
    }
  }

  if (!currentWorkspace) {
    return <section className="flex h-full items-center justify-center bg-[#fafafa] p-6"><div className="max-w-sm text-center"><Brain className="mx-auto text-[#10a37f]" size={30} /><h2 className="mt-4 text-base font-semibold text-[#212121]">先选择一个 Repository</h2><p className="mt-2 text-xs leading-5 text-[#777]">长期记忆按“全局用户 + 当前仓库”范围展示。</p></div></section>;
  }

  return (
    <section className="h-full overflow-y-auto bg-[#fafafa] p-4 sm:p-6">
      <div className="mx-auto max-w-6xl">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div><div className="flex items-center gap-2 text-[#087f5f]"><ShieldCheck size={17} /><span className="text-[10px] font-semibold uppercase tracking-[0.18em]">只读审核</span></div><h1 className="mt-2 text-xl font-semibold text-[#202124]">长期记忆</h1><p className="mt-1 text-xs text-[#777]">查看 Agent 在跨会话中会复用的信息；错误或过期记录可以归档。</p></div>
          <button className="flex h-9 items-center justify-center gap-2 rounded-lg border border-[#dedede] bg-white px-3 text-xs font-medium text-[#555] hover:bg-[#f1f1f1] disabled:opacity-50" disabled={loading} onClick={() => void refresh()} type="button"><RefreshCw className={loading ? "animate-spin" : ""} size={14} />刷新</button>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <select aria-label="记忆状态" className="h-9 rounded-lg border border-[#dedede] bg-white px-3 text-xs text-[#444] outline-none focus:border-[#10a37f]" onChange={(event) => setStatus(event.target.value as StatusFilter)} value={status}><option value="all">全部状态</option><option value="active">使用中</option><option value="superseded">已替代</option><option value="archived">已归档</option></select>
          <select aria-label="记忆类型" className="h-9 rounded-lg border border-[#dedede] bg-white px-3 text-xs text-[#444] outline-none focus:border-[#10a37f]" onChange={(event) => setType(event.target.value as TypeFilter)} value={type}><option value="all">全部类型</option><option value="user">用户偏好</option><option value="project">项目约束</option><option value="feedback">协作反馈</option><option value="reference">参考资料</option></select>
          <div className="flex h-9 items-center gap-1.5 rounded-lg bg-[#eef7f4] px-3 text-[11px] text-[#087f5f]"><Globe2 size={13} />全局用户 + {currentWorkspace.name}</div>
        </div>

        {error && <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{error}</div>}

        <div className="mt-4 grid min-h-[480px] overflow-hidden rounded-2xl border border-[#e1e1e1] bg-white shadow-sm lg:grid-cols-[320px_1fr]">
          <div className="border-b border-[#e8e8e8] lg:border-b-0 lg:border-r">
            <div className="border-b border-[#ededed] px-4 py-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#999]">{loading ? "加载中" : `${memories.length} 条记录`}</div>
            <div className="max-h-[360px] overflow-y-auto p-2 lg:max-h-[620px]">
              {!loading && !memories.length && <div className="px-4 py-14 text-center"><Brain className="mx-auto text-[#bbb]" size={25} /><p className="mt-3 text-sm font-medium text-[#555]">没有匹配的记忆</p><p className="mt-1 text-[11px] text-[#999]">长期信息会在成功回合结束后自动提取。</p></div>}
              {memories.map((memory) => <button className={`mb-1 w-full rounded-xl px-3 py-3 text-left transition ${selected?.id === memory.id ? "bg-[#eaf5f1]" : "hover:bg-[#f5f5f5]"}`} key={memory.id} onClick={() => setSelectedId(memory.id)} type="button"><div className="flex items-start justify-between gap-2"><p className="truncate text-xs font-semibold text-[#282828]">{memory.name}</p><span className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] ${memory.status === "active" ? "bg-emerald-100 text-emerald-700" : "bg-[#ededed] text-[#777]"}`}>{statusLabels[memory.status]}</span></div><p className="mt-1.5 line-clamp-2 text-[11px] leading-4 text-[#777]">{memory.description}</p><p className="mt-2 text-[9px] text-[#aaa]">{typeLabels[memory.type]} · {memory.workspace_id ? "当前仓库" : "全局"}</p></button>)}
            </div>
          </div>

          <div className="min-w-0 p-5 sm:p-7">
            {selected ? <><div className="flex flex-col justify-between gap-3 border-b border-[#ededed] pb-5 sm:flex-row sm:items-start"><div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-[#222]">{selected.name}</h2><span className="rounded-md bg-[#f0f0f0] px-2 py-1 text-[9px] font-medium text-[#666]">{typeLabels[selected.type]}</span></div><p className="mt-2 text-xs leading-5 text-[#666]">{selected.description}</p></div>{selected.status === "active" && <button className="flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-[#dedede] px-3 text-xs font-medium text-[#555] hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700 disabled:opacity-50" disabled={archiving} onClick={() => void archiveSelected()} type="button"><Archive size={14} />{archiving ? "归档中" : "归档"}</button>}</div>
              <div className="mt-6"><p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#999]">记忆内容</p><div className="mt-2 whitespace-pre-wrap rounded-xl border border-[#e8e8e8] bg-[#fafafa] p-4 text-sm leading-6 text-[#333]">{selected.body}</div></div>
              <div className="mt-5"><p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#999]">用户原文证据</p>{selected.evidence_quote ? <blockquote className="mt-2 rounded-xl border-l-4 border-[#10a37f] bg-[#eef7f4] px-4 py-3 text-sm leading-6 text-[#315c50]">“{selected.evidence_quote}”</blockquote> : <p className="mt-2 rounded-xl bg-[#f6f6f6] px-4 py-3 text-xs text-[#999]">旧记录没有保存原文证据。</p>}</div>
              <dl className="mt-6 grid gap-3 border-t border-[#ededed] pt-5 text-[11px] text-[#777] sm:grid-cols-2"><div><dt className="text-[#aaa]">Scope</dt><dd className="mt-1 font-medium text-[#555]">{selected.workspace_id ? currentWorkspace.name : "全局用户"}</dd></div><div><dt className="text-[#aaa]">更新时间</dt><dd className="mt-1 font-medium text-[#555]">{formatTime(selected.updated_at)}</dd></div><div><dt className="text-[#aaa]">状态</dt><dd className="mt-1 font-medium text-[#555]">{statusLabels[selected.status]}</dd></div><div><dt className="text-[#aaa]">Memory ID</dt><dd className="mt-1 truncate font-mono text-[10px] text-[#777]">{selected.id}</dd></div></dl>
            </> : <div className="flex h-full min-h-[360px] items-center justify-center text-center"><div><Brain className="mx-auto text-[#c5c5c5]" size={28} /><p className="mt-3 text-sm text-[#777]">选择一条记忆查看详情</p></div></div>}
          </div>
        </div>
      </div>
    </section>
  );
}
