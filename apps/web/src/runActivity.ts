import type { RunEvent } from "./types";

/** The current attempt starts after the most recent requeue. */
export function attemptEvents(events: RunEvent[]): RunEvent[] {
  let boundary = 0;
  for (const event of events) {
    if (event.type === "run_queued") boundary = event.id;
  }
  return events.filter((event) => event.id > boundary);
}

export function attemptText(
  events: RunEvent[],
  type: "assistant_delta" | "reasoning_delta",
): string {
  let text = "";
  for (const event of attemptEvents(events)) {
    if (event.type === type) {
      text += String(event.payload.delta ?? "");
    } else if (type === "assistant_delta" && event.type === "assistant_replace") {
      // A rubric revision supersedes everything streamed before it.
      text = String(event.payload.text ?? "");
    }
  }
  return text;
}

export type RunTraceStep =
  | { kind: "reasoning"; id: number; text: string }
  | { kind: "tool"; id: number; tool: string; agent: string; label: string }
  | {
      kind: "rubric";
      id: number;
      iteration: number;
      result: string;
      passed: boolean;
      total: number;
      failed: number;
      failures: { name: string; gap: string }[];
    };

export function appendReasoningStep(
  trace: RunTraceStep[],
  id: number,
  delta: string,
): RunTraceStep[] {
  if (!delta) return trace;
  const last = trace[trace.length - 1];
  if (last?.kind === "reasoning") {
    return [...trace.slice(0, -1), { ...last, text: last.text + delta }];
  }
  return [...trace, { kind: "reasoning", id, text: delta }];
}

export function toolTraceStep(event: RunEvent): RunTraceStep {
  const payload = event.payload;
  return {
    kind: "tool",
    id: event.id,
    tool: String(payload.tool ?? ""),
    agent: String(payload.agent ?? ""),
    label: describeToolStep(payload),
  };
}

export function rubricTraceStep(event: RunEvent): RunTraceStep {
  const payload = event.payload;
  const failures = Array.isArray(payload.failures)
    ? payload.failures
        .filter(
          (item): item is Record<string, unknown> =>
            Boolean(item) && typeof item === "object",
        )
        .map((item) => ({
          name: String(item.name ?? ""),
          gap: String(item.gap ?? ""),
        }))
    : [];
  return {
    kind: "rubric",
    id: event.id,
    iteration: Number(payload.iteration ?? 0),
    result: String(payload.result ?? ""),
    passed: Boolean(payload.passed),
    total: Number(payload.total ?? 0),
    failed: Number(payload.failed ?? 0),
    failures,
  };
}

/** Live activity line while the reply passes (or fails) the quality gate. */
export function rubricActivity(event: RunEvent): string {
  if (event.type === "rubric_start") return "质检中";
  const result = String(event.payload.result ?? "");
  if (result === "satisfied") return "质检通过";
  if (result === "needs_revision") return "质检未通过，正在修订";
  if (result === "grader_error" || result === "failed") return "质检失败";
  return "质检未通过";
}

/** Reasoning and tool calls in the order they were emitted for one attempt. */
export function buildRunTrace(events: RunEvent[]): RunTraceStep[] {
  let trace: RunTraceStep[] = [];
  for (const event of attemptEvents(events)) {
    if (event.type === "reasoning_delta") {
      trace = appendReasoningStep(
        trace,
        event.id,
        String(event.payload.delta ?? ""),
      );
    } else if (event.type === "tool_call") {
      trace = [...trace, toolTraceStep(event)];
    } else if (event.type === "rubric_result") {
      trace = [...trace, rubricTraceStep(event)];
    }
  }
  return trace;
}

function textArg(args: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function shortenPath(path: string, max = 48): string {
  const clean = path.replace(/^\/+/, "");
  if (clean.length <= max) return clean;
  return `…${clean.slice(-(max - 1))}`;
}

function shortenText(text: string, max = 28): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

/** Human-readable "what is the agent doing now" line for one tool call. */
export function describeToolCall(payload: Record<string, unknown>): string {
  const tool = String(payload.tool ?? "");
  const rawArgs = payload.args;
  const args =
    rawArgs && typeof rawArgs === "object" ? (rawArgs as Record<string, unknown>) : {};
  const path = shortenPath(textArg(args, "file_path", "path"));
  const pattern = textArg(args, "pattern");
  const query = textArg(args, "query");
  const url = textArg(args, "url");

  switch (tool) {
    case "read_file":
      return path ? `正在读取 ${path}` : "正在读取文件";
    case "write_file":
      return path ? `正在写入 ${path}` : "正在写入文件";
    case "edit_file":
      return path ? `正在编辑 ${path}` : "正在编辑文件";
    case "delete":
      return path ? `正在删除 ${path}` : "正在删除文件";
    case "ls":
      return path ? `正在查看 ${path}` : "正在查看工作区";
    case "glob":
      return pattern ? `正在查找 ${shortenText(pattern)}` : "正在查找文件";
    case "grep":
      return pattern ? `正在搜索 ${shortenText(pattern)}` : "正在搜索工作区";
    case "browser_open":
      return url ? `正在浏览 ${hostOf(url)}` : "正在浏览网页";
    case "browser_snapshot":
      return "正在读取页面内容";
    case "browser_click":
    case "browser_press":
      return "正在操作页面";
    case "browser_fill":
      return "正在填写表单";
    case "browser_screenshot":
      return "正在截图";
    case "browser_close":
      return "正在关闭浏览器";
    case "web_search":
      return query ? `正在搜索「${shortenText(query)}」` : "正在搜索网页";
    case "web_fetch":
      return url ? `正在读取 ${hostOf(url)}` : "正在读取网页";
    case "memory_list":
    case "memory_update":
    case "memory_delete":
      return "正在整理长期记忆";
    case "task":
      return "子 Agent 正在执行任务";
    default:
      return tool ? `正在调用 ${tool}` : "";
  }
}

/** A neutral label for a completed trace step; activity uses the present tense. */
export function describeToolStep(payload: Record<string, unknown>): string {
  return describeToolCall(payload).replace(/^正在/, "");
}

/** Latest tool activity in the current attempt; cleared when the model speaks. */
export function activityFromEvents(events: RunEvent[]): string {
  let activity = "";
  for (const event of attemptEvents(events)) {
    if (event.type === "tool_call") {
      activity = describeToolCall(event.payload);
    } else if (event.type === "rubric_start" || event.type === "rubric_result") {
      activity = rubricActivity(event);
    } else if (
      event.type === "browser_action" ||
      event.type === "assistant_delta" ||
      event.type === "reasoning_delta"
    ) {
      activity = "";
    }
  }
  return activity;
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
}
