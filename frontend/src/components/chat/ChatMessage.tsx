"use client";

import { Bot, UserRound } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ThoughtChain } from "@/components/chat/ThoughtChain";
import type { ToolCall } from "@/lib/api";

export function ChatMessage({
  role,
  content,
  toolCalls,
  status,
  recoveryMessage
}: {
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  status?: "incomplete" | "error";
  recoveryMessage?: string;
}) {
  const isUser = role === "user";
  const isWorking = !isUser && !content.trim() && toolCalls.length > 0;

  return (
    <article className={`flex gap-3 sm:gap-4 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${isUser ? "bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200" : "bg-[var(--color-ocean-soft)] text-[#6859d9] ring-1 ring-inset ring-violet-200"}`}>
        {isUser ? <UserRound size={15} /> : <Bot size={16} />}
      </div>
      <div className={`min-w-0 ${isUser ? "max-w-[82%] rounded-2xl rounded-tr-md border border-violet-200 bg-[#eeeafe] px-4 py-3 text-[#403779] shadow-sm" : "max-w-[calc(100%-3rem)] flex-1 pt-1"}`}>
        {!isUser && <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">Repository Steward</div>}
        {isWorking && <ThoughtChain />}
        {content.trim() && (
          <div className={isUser ? "whitespace-pre-wrap text-[15px] leading-7" : "markdown"}>
            {isUser ? content : <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>}
          </div>
        )}
        {!isUser && !content.trim() && !toolCalls.length && (
          <div className="flex items-center gap-1.5 py-2">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#6859d9]" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#6859d9] [animation-delay:150ms]" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#6859d9] [animation-delay:300ms]" />
          </div>
        )}
        {!isUser && recoveryMessage && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">{recoveryMessage}</div>
        )}
        {!isUser && status === "incomplete" && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            回答已达到自动续写上限，当前内容可能不完整。你可以发送“继续”。
          </div>
        )}
      </div>
    </article>
  );
}
