"use client";

import { useEffect, useState } from "react";

export function ResizeHandle({ onResize }: { onResize: (delta: number) => void }) {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMouseMove = (event: MouseEvent) => onResize(event.movementX);
    const onMouseUp = () => setDragging(false);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging, onResize]);

  return (
    <div
      aria-hidden
      className="group hidden w-3 cursor-col-resize items-center justify-center md:flex"
      onMouseDown={() => setDragging(true)}
    >
      <div className={`h-12 w-px rounded-full transition-all ${dragging ? "h-24 bg-[#10a37f]" : "bg-slate-300 group-hover:h-20 group-hover:bg-slate-400"}`} />
    </div>
  );
}
