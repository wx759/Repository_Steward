"use client";

import Editor from "@monaco-editor/react";
import { Check, Save, X } from "lucide-react";
import { useAppStore } from "@/lib/store";

export function InspectorPanel({ onClose }: { onClose?: () => void }) {
  const { editableFiles, inspectorPath, inspectorContent, inspectorDirty, loadInspectorFile, updateInspectorContent, saveInspector } = useAppStore();
  return (
    <aside className="panel flex h-full flex-col overflow-hidden rounded-2xl bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 sm:px-5">
        <div className="min-w-0"><p className="text-[10px] font-medium uppercase tracking-[0.22em] text-[var(--color-ink-soft)]">Configuration</p><h2 className="mt-1 truncate text-sm font-semibold text-slate-900">Skills & Prompt Inspector</h2></div>
        <div className="flex items-center gap-2">
          <button className={`flex h-9 items-center gap-2 rounded-xl px-3 text-xs transition ${inspectorDirty ? "bg-[#6859d9] text-white hover:bg-[#594bc4]" : "bg-slate-100 text-slate-400"}`} disabled={!inspectorDirty} onClick={() => void saveInspector()} type="button">{inspectorDirty ? <Save size={14} /> : <Check size={14} />}{inspectorDirty ? "保存修改" : "已同步"}</button>
          {onClose && <button className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 hover:text-slate-800" onClick={onClose} aria-label="关闭" type="button"><X size={16} /></button>}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
        <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 bg-slate-50/60 p-2 sm:w-56 sm:flex-col sm:overflow-y-auto sm:border-b-0 sm:border-r">
          {editableFiles.map((path) => <button className={`shrink-0 truncate rounded-lg px-3 py-2 text-left text-xs transition ${path === inspectorPath ? "bg-violet-100 text-[#594bc4]" : "text-slate-500 hover:bg-white hover:text-slate-800"}`} key={path} onClick={() => void loadInspectorFile(path)} title={path} type="button">{path.replace("workspace/", "")}</button>)}
        </nav>
        <div className="min-h-0 flex-1 bg-white">
          <Editor defaultLanguage="markdown" height="100%" onChange={(value) => updateInspectorContent(value ?? "")} options={{ fontFamily: "var(--font-mono)", fontSize: 13, lineHeight: 22, minimap: { enabled: false }, padding: { top: 16 }, scrollBeyondLastLine: false, wordWrap: "on" }} path={inspectorPath} theme="vs-light" value={inspectorContent} />
        </div>
      </div>
    </aside>
  );
}
