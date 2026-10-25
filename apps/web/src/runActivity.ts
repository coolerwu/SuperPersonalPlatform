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
    if (event.type === type) text += String(event.payload.delta ?? "");
  }
  return text;
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

/** Latest tool activity in the current attempt; cleared when the model speaks. */
export function activityFromEvents(events: RunEvent[]): string {
  let activity = "";
  for (const event of attemptEvents(events)) {
    if (event.type === "tool_call") {
      activity = describeToolCall(event.payload);
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
