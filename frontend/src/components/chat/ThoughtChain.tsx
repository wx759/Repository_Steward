"use client";

import { LoaderCircle } from "lucide-react";

export function ThoughtChain() {
  return (
    <div
      aria-live="polite"
      className="mb-3 flex items-center gap-2 text-sm text-slate-500"
    >
      <LoaderCircle className="animate-spin text-[#6859d9]" size={15} />
      <span>正在处理请求…</span>
    </div>
  );
}
