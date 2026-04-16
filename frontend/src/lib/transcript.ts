import type { ContentBlock, JsonlEntry, TranscriptTask } from "@/lib/types";

export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  if (min < 60) return rem === 0 ? `${min}m` : `${min}m ${rem}s`;
  const hr = Math.floor(min / 60);
  const rm = min % 60;
  return rm === 0 ? `${hr}h` : `${hr}h ${rm}m`;
}

/**
 * Walk the transcript and build a deduped list of tasks, reflecting the
 * latest status for each taskId. Orders by first-appearance.
 *
 * Task* tool shapes (observed):
 *   TaskCreate input: { subject, description?, activeForm? }
 *   TaskUpdate input: { taskId, status?, subject?, description?, activeForm? }
 *   TaskList  input: {}                (server returns list in tool_result)
 */
export function buildTaskList(entries: JsonlEntry[]): TranscriptTask[] {
  const byId = new Map<string, TranscriptTask>();
  let order = 0;
  let autoId = 0;

  for (const e of entries) {
    if (e.type !== "assistant") continue;
    const content = e.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (typeof block === "string") continue;
      const b = block as ContentBlock;
      if (b.type !== "tool_use") continue;
      const inp = b.input || {};
      if (b.name === "TaskCreate") {
        const id = String(inp.taskId ?? inp.id ?? `t-${++autoId}`);
        if (!byId.has(id)) {
          byId.set(id, {
            id,
            subject: String(inp.subject ?? inp.title ?? "(untitled task)"),
            description: inp.description ? String(inp.description) : undefined,
            activeForm: inp.activeForm ? String(inp.activeForm) : undefined,
            status: "pending",
            order: order++,
          });
        }
      } else if (b.name === "TaskUpdate") {
        const id = String(inp.taskId ?? inp.id ?? "");
        if (!id) continue;
        const prev = byId.get(id);
        const merged: TranscriptTask = prev ?? {
          id,
          subject: String(inp.subject ?? "(unknown task)"),
          status: "pending",
          order: order++,
        };
        if (inp.subject) merged.subject = String(inp.subject);
        if (inp.description) merged.description = String(inp.description);
        if (inp.activeForm) merged.activeForm = String(inp.activeForm);
        const status = String(inp.status ?? merged.status);
        if (status === "pending" || status === "in_progress" || status === "completed") {
          merged.status = status;
        }
        byId.set(id, merged);
      }
    }
  }
  return Array.from(byId.values()).sort((a, b) => a.order - b.order);
}

export function toolResultById(entries: JsonlEntry[]): Map<string, ContentBlock> {
  const map = new Map<string, ContentBlock>();
  for (const e of entries) {
    if (e.type !== "user") continue;
    const content = e.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (typeof block === "string") continue;
      const b = block as ContentBlock;
      if (b.type === "tool_result" && b.tool_use_id) {
        map.set(b.tool_use_id, b);
      }
    }
  }
  return map;
}
