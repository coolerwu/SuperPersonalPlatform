import type { LiveTask, SessionTask, SessionTaskEvent } from "./types";

export function emptyTask(
  id: string,
  toolUseId: string | null,
  description = "",
): LiveTask {
  return {
    id,
    toolUseId,
    description,
    status: "running",
    summary: "",
    lastToolName: "",
    usage: null,
  };
}

export function sessionTaskToLive(task: SessionTask): LiveTask {
  return {
    id: task.task_id,
    toolUseId: task.tool_use_id ?? null,
    description: task.description ?? "",
    status: task.status || "running",
    summary: task.summary ?? "",
    lastToolName: task.last_tool_name ?? "",
    usage: task.usage ?? null,
    startedAt: task.started_at,
    updatedAt: task.updated_at,
    completedAt: task.completed_at ?? null,
  };
}

type LiveTaskEvent = Exclude<
  SessionTaskEvent,
  { type: "tasks_snapshot" } | { type: "task_removed" }
>;

export function applyTaskEvent(
  tasks: Record<string, LiveTask>,
  event: LiveTaskEvent,
): Record<string, LiveTask> {
  const id = event.task_id;
  const toolUseId = "tool_use_id" in event ? (event.tool_use_id ?? null) : null;
  const existing = tasks[id] ?? emptyTask(id, toolUseId);
  if (event.type === "task_started") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        toolUseId: event.tool_use_id ?? existing.toolUseId,
        description: event.description || existing.description,
        status: "running",
      },
    };
  }
  if (event.type === "task_progress") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        description: event.description || existing.description,
        lastToolName: event.last_tool_name || existing.lastToolName,
        usage: event.usage ?? existing.usage,
      },
    };
  }
  if (event.type === "task_updated") {
    return {
      ...tasks,
      [id]: {
        ...existing,
        status: event.status || existing.status,
      },
    };
  }
  return {
    ...tasks,
    [id]: {
      ...existing,
      status: event.status || existing.status,
      summary: event.summary || existing.summary,
      usage: event.usage ?? existing.usage,
    },
  };
}

export function sortTasks(tasks: Record<string, LiveTask>): LiveTask[] {
  const rank = (status: string) =>
    status === "running" || status === "in_progress" ? 0 : 1;
  return Object.values(tasks).sort((left, right) => {
    const byRank = rank(left.status) - rank(right.status);
    if (byRank !== 0) return byRank;
    return (right.updatedAt ?? "").localeCompare(left.updatedAt ?? "");
  });
}

export function taskStatusLabel(status: string): string {
  switch (status) {
    case "running":
    case "in_progress":
      return "运行中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "stopped":
    case "killed":
      return "已停止";
    case "interrupted":
      return "已中断";
    default:
      return status || "未知";
  }
}

export function taskStatusClass(status: string): string {
  if (status === "completed") return "ok";
  if (status === "failed") return "failed";
  if (status === "stopped" || status === "killed") return "stopped";
  if (status === "interrupted") return "interrupted";
  return "running";
}
