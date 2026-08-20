"use client";

import { ArrowUp, LoaderCircle, Paperclip } from "lucide-react";
import { useRef, useState } from "react";

export function ChatInput({ disabled, repositoryReady, onSend }: { disabled: boolean; repositoryReady: boolean; onSend: (value: string) => Promise<void> }) {
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
    <div className="shrink-0 bg-white px-4 pb-5 pt-2 sm:px-8 lg:px-12">
      <div className="mx-auto max-w-4xl">
        <div className="rounded-[24px] border border-[#d9d9d9] bg-[#f4f4f4] p-2 transition focus-within:border-[#b9b9b9]">
          <textarea ref={textareaRef} className="max-h-44 min-h-[52px] w-full resize-none bg-transparent px-3 py-2 text-[14px] leading-6 text-[#252a31] outline-none placeholder:text-slate-400" disabled={disabled} onChange={(event) => { setValue(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 176)}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} placeholder={!repositoryReady ? "请先从左侧添加一个 Repository" : disabled ? "Steward 正在处理…" : "描述你想在这个仓库中完成的任务…"} rows={1} value={value} />
          <div className="flex items-center justify-between px-1 pb-1">
            <div className="flex items-center gap-2"><button className="flex h-8 w-8 cursor-not-allowed items-center justify-center rounded-lg text-slate-400" disabled title="文件上传即将支持" type="button"><Paperclip size={16} /></button><span className="hidden text-[11px] text-slate-400 sm:inline">Enter 发送 · Shift + Enter 换行</span></div>
            <button className="flex h-9 w-9 items-center justify-center rounded-full bg-[#212121] text-white transition hover:bg-black disabled:cursor-not-allowed disabled:bg-[#d7d7d7] disabled:text-white" disabled={disabled || !value.trim()} onClick={submit} aria-label="发送消息" type="button">{disabled ? <LoaderCircle className="animate-spin" size={17} /> : <ArrowUp size={18} />}</button>
          </div>
        </div>
        <p className="mt-2 text-center text-[10px] text-slate-400">Agent 可能会修改文件或运行命令，请留意工具调用记录。</p>
      </div>
    </div>
  );
}
