"use client";

import { ChatPanel } from "@/components/chat/ChatPanel";
import { Navbar } from "@/components/layout/Navbar";
import { ResizeHandle } from "@/components/layout/ResizeHandle";
import { Sidebar } from "@/components/layout/Sidebar";
import { AppProvider, useAppStore } from "@/lib/store";

function Workspace() {
  const { sidebarWidth, setSidebarWidth } = useAppStore();

  return (
    <main className="h-screen overflow-hidden bg-white">
      <div className="flex h-full w-full overflow-hidden bg-white">
        <div style={{ width: sidebarWidth, flexShrink: 0 }} className="hidden h-full md:block">
          <Sidebar />
        </div>
        <ResizeHandle onResize={(delta) => setSidebarWidth(Math.min(380, Math.max(250, sidebarWidth + delta)))} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Navbar />
          <div className="min-h-0 flex-1">
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
