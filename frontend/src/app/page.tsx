"use client";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { Navbar } from "@/components/layout/Navbar";
import { ResizeHandle } from "@/components/layout/ResizeHandle";
import { Sidebar } from "@/components/layout/Sidebar";
import { AppProvider, useAppStore } from "@/lib/store";

function Workspace() {
  const { sidebarWidth, setSidebarWidth } = useAppStore();

  return (
    <main className="flex h-screen flex-col overflow-hidden px-3 py-3 sm:px-4 sm:py-4 lg:px-6">
      <div className="mx-auto flex min-h-0 w-full max-w-[1720px] flex-1 flex-col gap-3 lg:gap-4">
        <Navbar />
        <div className="flex min-h-0 flex-1 gap-0">
          <div style={{ width: sidebarWidth, flexShrink: 0 }} className="hidden h-full md:block">
            <Sidebar />
          </div>
          <ResizeHandle onResize={(delta) => setSidebarWidth(Math.min(440, Math.max(260, sidebarWidth + delta)))} />
          <div className="h-full min-w-0 flex-1">
            <ChatPanel />
          </div>
        </div>
      </div>
    </main>
  );
}

export default function Page() {
  return <AppProvider><Workspace /></AppProvider>;
}
