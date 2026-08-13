"use client";

import { ArrowUp, LoaderCircle, Paperclip } from "lucide-react";
import { useRef, useState } from "react";

export function ChatInput({ disabled, onSend }: { disabled: boolean; onSend: (value: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const submit = () => {
    const nextValue = value.trim();
    if (!nextValue || disabled) return;
    void onSend(nextValue);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };
  return (
    <div className="shrink-0 border-t border-slate-200/80 bg-white/80 px-3 pb-3 pt-3 sm:px-6 sm:pb-5 lg:px-10">
      <div className="mx-auto max-w-4xl">
        <div className="rounded-2xl border border-slate-200 bg-white p-2 shadow-[0_10px_30px_rgba(47,54,79,.1)] transition focus-within:border-violet-300 focus-within:ring-2 focus-within:ring-violet-100">
          <textarea ref={textareaRef} className="max-h-44 min-h-[52px] w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 text-slate-800 outline-none placeholder:text-slate-400" disabled={disabled} onChange={(event) => { setValue(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 176)}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} placeholder={disabled ? "Agent 正在处理，请稍候…" : "向 Repository Steward 发送消息…"} rows={1} value={value} />
          <div className="flex items-center justify-between px-1 pb-1">
            <div className="flex items-center gap-2"><button className="flex h-8 w-8 cursor-not-allowed items-center justify-center rounded-lg text-slate-400" disabled title="文件上传即将支持" type="button"><Paperclip size={16} /></button><span className="hidden text-[11px] text-slate-400 sm:inline">Enter 发送 · Shift + Enter 换行</span></div>
            <button className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#6859d9] text-white shadow-[0_8px_20px_rgba(104,89,217,.22)] transition hover:bg-[#594bc4] disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none" disabled={disabled || !value.trim()} onClick={submit} aria-label="发送消息" type="button">{disabled ? <LoaderCircle className="animate-spin" size={17} /> : <ArrowUp size={18} />}</button>
          </div>
        </div>
        <p className="mt-2 text-center text-[10px] text-slate-400">Agent 可能会修改文件或运行命令，请留意工具调用记录。</p>
      </div>
    </div>
  );
}
