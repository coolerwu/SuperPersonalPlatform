import type { LiveTask } from "../types";
import {
  sortTasks,
  taskStatusClass,
  taskStatusLabel,
} from "../taskFormat";

export function SessionTaskPanel({
  tasks,
}: {
  tasks: Record<string, LiveTask>;
}) {
  const list = sortTasks(tasks);
  if (list.length === 0) return null;
  const running = list.filter(
    (task) =>
      task.status === "running" ||
      task.status === "in_progress" ||
      task.status === "pending",
  ).length;
  return (
    <details className="session-tasks" open>
      <summary>
        <span>后台任务</span>
        <span className="session-tasks-count">
          {running > 0 ? `${running} 个运行中` : `${list.length} 个`}
        </span>
      </summary>
      <div className="session-tasks-list">
        {list.map((task) => (
          <div
            className={`session-task ${taskStatusClass(task.status)}`}
            key={task.id}
          >
            <span className="session-task-dot" aria-hidden="true" />
            <span className="session-task-label">
              {task.description || task.id}
            </span>
            <span className="session-task-status">
              {taskStatusLabel(task.status)}
            </span>
            {task.lastToolName && (
              <span className="session-task-tool">{task.lastToolName}</span>
            )}
            {task.summary && (
              <small className="session-task-summary">{task.summary}</small>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
