"use client";

import { Check, ChevronRight, TerminalSquare } from "lucide-react";
import type { ToolCall } from "@/lib/api";

export function ThoughtChain({ toolCalls }: { toolCalls: ToolCall[] }) {
  if (!toolCalls.length) return null;
  const toolNames = Array.from(new Set(toolCalls.map((call) => call.tool))).join(" · ");
  return (
    <details className="group mb-4 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs text-slate-500 outline-none hover:bg-slate-100/70">
        <ChevronRight className="transition group-open:rotate-90" size={14} /><TerminalSquare className="text-[var(--color-ember)]" size={15} /><span>已执行 {toolCalls.length} 次工具调用</span><span className="min-w-0 truncate text-slate-400">{toolNames}</span>
      </summary>
      <div className="space-y-2 border-t border-slate-200 p-2">
        {toolCalls.map((toolCall, index) => (
          <div className="rounded-lg border border-slate-200 bg-white p-3" key={`${toolCall.tool}-${index}`}>
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-700"><span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-50 text-emerald-600"><Check size={10} /></span>{toolCall.tool}</div>
            <div className="space-y-2 text-[11px]">
              {toolCall.input && <div><div className="mb-1 text-slate-400">输入</div><pre className="mono max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-2.5 text-slate-600">{toolCall.input}</pre></div>}
              {toolCall.output && <div><div className="mb-1 text-slate-400">输出</div><div className="mono max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-2.5 text-slate-600">{toolCall.output}</div></div>}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}
