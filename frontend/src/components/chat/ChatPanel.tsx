"use client";

import { useEffect, useRef } from "react";
import { Braces, FileSearch, Terminal } from "lucide-react";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { useAppStore } from "@/lib/store";

const suggestions = [
  { icon: FileSearch, label: "了解仓库", prompt: "请分析当前仓库的目录结构和主要模块" },
  { icon: Braces, label: "检查代码", prompt: "请检查当前代码中最值得优先改进的问题" },
  { icon: Terminal, label: "运行测试", prompt: "请运行项目测试并分析结果" }
];

export function ChatPanel() {
  const { messages, sendMessage, isStreaming } = useAppStore();
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  return (
    <section className="panel flex h-full min-w-0 flex-1 flex-col overflow-hidden rounded-2xl">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-10 lg:py-8">
          <div className="mx-auto w-full max-w-4xl space-y-7">
            {!messages.length && (
              <div className="flex min-h-[56vh] flex-col items-center justify-center py-10 text-center">
                <div className="relative mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-[#8d80ed] to-[#6555d2] text-white shadow-[0_18px_45px_rgba(104,89,217,.22)]"><Braces size={27} /><div className="absolute -right-1 -top-1 h-3 w-3 rounded-full border-2 border-white bg-[var(--color-success)]" /></div>
                <p className="text-[11px] font-medium uppercase tracking-[0.25em] text-slate-400">AI repository maintainer</p>
                <h1 className="mt-3 text-3xl font-semibold tracking-[-0.045em] text-slate-900 sm:text-4xl">今天想维护什么？</h1>
                <p className="mt-3 max-w-lg text-sm leading-6 text-[var(--color-ink-soft)]">我可以读取代码、修改文件、运行命令，并在多轮对话中持续理解你的仓库。</p>
                <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-3">
                  {suggestions.map(({ icon: Icon, label, prompt }) => (
                    <button className="subtle-panel group flex items-center gap-3 rounded-xl px-4 py-3 text-left text-sm text-slate-600 shadow-sm transition hover:border-violet-200 hover:bg-violet-50 hover:text-slate-900" key={label} onClick={() => void sendMessage(prompt)} type="button"><Icon className="shrink-0 text-slate-400 transition group-hover:text-[#6859d9]" size={16} /><span>{label}</span></button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message) => <ChatMessage key={message.id} {...message} />)}
            <div ref={endRef} />
          </div>
        </div>
        <ChatInput disabled={isStreaming} onSend={sendMessage} />
      </div>
    </section>
  );
}
