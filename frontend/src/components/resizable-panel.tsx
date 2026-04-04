"use client";

import { useCallback, useRef, useState, type ReactNode } from "react";

export function ResizablePanel({
  left,
  right = null,
  defaultRightWidth,
  minRightWidth,
  maxRightWidth,
}: {
  left: ReactNode;
  right?: ReactNode | null;
  defaultRightWidth?: number;
  minRightWidth?: number;
  maxRightWidth?: number;
}) {
  const [rightWidth, setRightWidth] = useState(defaultRightWidth ?? 500);
  const dragging = useRef(false);

  const onMouseDown = useCallback(() => {
    dragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const newWidth = window.innerWidth - e.clientX;
      const min = minRightWidth ?? 300;
      const max = maxRightWidth ?? window.innerWidth * 0.7;
      setRightWidth(Math.max(min, Math.min(max, newWidth)));
    };

    const onMouseUp = () => {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, [minRightWidth, maxRightWidth]);

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex-1 flex flex-col min-h-0 min-w-0">{left}</div>
      {right && (
        <>
          <div
            className="w-1 cursor-col-resize bg-th-border hover:bg-th-accent active:bg-th-accent transition-colors flex-shrink-0"
            onMouseDown={onMouseDown}
          />
          <div className="flex flex-col min-h-0" style={{ width: rightWidth }}>{right}</div>
        </>
      )}
    </div>
  );
}
