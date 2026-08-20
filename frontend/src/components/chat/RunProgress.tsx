"use client";

import { CheckCircle2, Circle, XCircle } from "lucide-react";
import { useAppStore } from "@/lib/store";

export function RunProgress() {
  const { runs } = useAppStore();
  const run = runs[0];
  if (!run?.tasks.length) return null;
  return (
    <div className="mb-5 rounded-xl border border-[#e5e5e5] bg-[#f7f7f7] p-4">
      <p className="truncate text-xs font-medium text-slate-800">{run.goal}</p>
      <div className="mt-3 space-y-2">
        {run.tasks.map((task) => {
          const Icon = task.status === "completed" ? CheckCircle2 : task.status === "failed" ? XCircle : Circle;
          return (
            <div className="flex items-start gap-2 text-xs" key={task.task_id}>
              <Icon className={task.status === "completed" ? "text-[#10a37f]" : task.status === "failed" ? "text-red-500" : "animate-pulse text-[#6b6b6b]"} size={15} />
              <div><p className="text-slate-700">{task.role} Agent · {task.description}</p><p className="mt-0.5 text-[10px] text-slate-400">Review {task.review_status}</p></div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
