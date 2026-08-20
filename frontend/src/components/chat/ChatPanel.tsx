"use client";

import { useEffect, useRef } from "react";
import { Braces, FileSearch, FolderPlus, Terminal } from "lucide-react";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { useAppStore } from "@/lib/store";
import { RunProgress } from "@/components/chat/RunProgress";

const suggestions = [
  { icon: FileSearch, label: "了解仓库", prompt: "请分析当前仓库的目录结构和主要模块" },
  { icon: Braces, label: "检查代码", prompt: "请检查当前代码中最值得优先改进的问题" },
  { icon: Terminal, label: "运行测试", prompt: "请运行项目测试并分析结果" }
];

export function ChatPanel() {
  const { messages, sendMessage, cancelCurrentRun, isStreaming, currentWorkspace } = useAppStore();
  const endRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  return (
    <section className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-white">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-8 lg:px-12 lg:py-8">
          <div className="mx-auto w-full max-w-4xl space-y-7">
            <RunProgress />
            {!messages.length && !currentWorkspace && (
              <div className="flex min-h-[56vh] flex-col items-center justify-center py-10 text-center"><div className="mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-[#e5e5e5] bg-white text-[#212121]"><FolderPlus size={22} /></div><p className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">No repository selected</p><h1 className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-[#212121]">先连接一个真实代码仓库</h1><p className="mt-3 max-w-md text-sm leading-6 text-[#6b6b6b]">从左侧 Workspace 区域添加本地 Git 仓库。Repository Steward 不会再默认操作自身代码。</p></div>
            )}
            {!messages.length && currentWorkspace && (
              <div className="flex min-h-[56vh] flex-col items-center justify-center py-10 text-center">
                <div className="relative mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-[#10a37f] text-white"><Braces size={22} /></div>
                <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">{currentWorkspace.name} · {currentWorkspace.branch}</p>
                <h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em] text-[#212121]">从一个真实任务开始</h1>
                <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-3">
                  {suggestions.map(({ icon: Icon, label, prompt }) => (
                    <button className="group flex items-center gap-3 rounded-xl border border-[#e5e5e5] bg-white px-4 py-3 text-left text-sm text-[#5d5d5d] transition hover:bg-[#f7f7f7] hover:text-[#212121]" key={label} onClick={() => void sendMessage(prompt)} type="button"><Icon className="shrink-0 text-[#8a8a8a] transition group-hover:text-[#212121]" size={16} /><span>{label}</span></button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message) => <ChatMessage key={message.id} {...message} />)}
            <div ref={endRef} />
          </div>
        </div>
        <ChatInput running={isStreaming} repositoryReady={Boolean(currentWorkspace)} onSend={sendMessage} onStop={cancelCurrentRun} />
      </div>
    </section>
  );
}
