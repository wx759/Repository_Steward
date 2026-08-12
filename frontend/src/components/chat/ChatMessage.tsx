"use client";

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
  const duplicatesToolOutput = toolCalls.some((call) => call.output.trim() === content.trim());

  return (
    <article className={`max-w-[90%] rounded-[28px] px-5 py-4 ${
      isUser
        ? "ml-auto bg-[rgba(13,37,48,0.92)] text-white"
        : "panel mr-auto text-[var(--color-ink)]"
    }`}>
      {!isUser && <ThoughtChain toolCalls={toolCalls} />}
      {content.trim() && !duplicatesToolOutput && (
        <div className={isUser ? "whitespace-pre-wrap leading-7" : "markdown"}>
          {isUser ? content : <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>}
        </div>
      )}
      {!isUser && !content.trim() && !toolCalls.length && (
        <div className="text-[var(--color-ink-soft)]">正在思考...</div>
      )}
      {!isUser && recoveryMessage && (
        <div className="mt-3 text-sm text-amber-700">{recoveryMessage}</div>
      )}
      {!isUser && status === "incomplete" && (
        <div className="mt-3 text-sm text-amber-700">
          回答已达到自动续写上限，当前内容可能不完整。可以发送“继续”。
        </div>
      )}
    </article>
  );
}
