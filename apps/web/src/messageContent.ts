export const MEMORY_COMMAND =
  "请整理长期记忆：先读取 /skills/memory-organizer/SKILL.md，然后按技能说明合并重复记忆、删除失效记忆，不要新增事实。";

const SLASH_COMMAND = /^\/[A-Za-z][A-Za-z0-9_-]{0,15}$/;

/** True only when the whole text is one slash command token, like /new. */
export function isSlashCommand(text: string): boolean {
  return SLASH_COMMAND.test(text.trim());
}

export type SlashCommandItem = {
  command: string;
  label: string;
  description: string;
};

/** Suggestions for the composer's "/" palette; keep in sync with chat_commands. */
export const SLASH_COMMANDS: SlashCommandItem[] = [
  { command: "/new", label: "新会话", description: "开始新会话，旧会话保留在会话列表" },
  { command: "/clear", label: "同 /new", description: "与 /new 相同，不删除历史" },
  { command: "/stop", label: "停止任务", description: "停止当前会话排队或运行中的任务" },
  { command: "/status", label: "会话状态", description: "查看当前 Agent、会话与任务数量" },
  { command: "/help", label: "命令说明", description: "查看全部可用命令" },
];

const COMMAND_DRAFT = /^\/[A-Za-z0-9_-]*$/;

/**
 * The prefix typed after "/" while the composer should offer commands.
 * Returns null when the draft is not a bare command prefix.
 */
export function slashCommandQuery(text: string): string | null {
  const value = text.trimStart();
  if (!COMMAND_DRAFT.test(value)) return null;
  return value.toLowerCase();
}

export function filterSlashCommands(query: string): SlashCommandItem[] {
  return SLASH_COMMANDS.filter((item) => item.command.startsWith(query));
}

const ATTACHMENT_HEADER = "附件已上传到工作区：";
const ATTACHMENT_LINE = /^- (.+?)（工作区路径：(.*)）$/;
const ATTACHMENT_SIZE = /^(.*)，大小：(\d+)\s*(?:字节)?$/;

export type MessageFile = {
  name: string;
  path: string;
  size: number | null;
};

const IMAGE_SUFFIXES = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
  ".heic",
  ".heif",
  ".avif",
]);

export function isImageName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && IMAGE_SUFFIXES.has(name.slice(dot).toLowerCase());
}

export type UserContent =
  | { kind: "text"; text: string }
  | { kind: "attachments"; files: MessageFile[]; text: string }
  | { kind: "memory-command" };

function parseFileLine(line: string): MessageFile | null {
  const match = line.match(ATTACHMENT_LINE);
  if (!match) return null;
  let path = match[2];
  let size: number | null = null;
  const sized = path.match(ATTACHMENT_SIZE);
  if (sized) {
    path = sized[1];
    size = Number(sized[2]);
  }
  return { name: match[1], path, size: Number.isFinite(size) ? size : null };
}

/**
 * Attachments and chat commands are still plain message content (the model
 * reads the real paths and instructions); the UI only hides the scaffolding.
 */
export function parseUserContent(content: string): UserContent {
  if (content.trim() === MEMORY_COMMAND) return { kind: "memory-command" };
  const lines = content.split("\n");
  if (lines[0]?.trim() !== ATTACHMENT_HEADER) return { kind: "text", text: content };
  const files: MessageFile[] = [];
  let index = 1;
  for (; index < lines.length; index += 1) {
    const file = parseFileLine(lines[index]);
    if (!file) break;
    files.push(file);
  }
  if (files.length === 0) return { kind: "text", text: content };
  return { kind: "attachments", files, text: lines.slice(index).join("\n").trim() };
}

/** What the user meant to write, for copy/quote actions. */
export function userMessageText(content: string): string {
  const parsed = parseUserContent(content);
  if (parsed.kind === "memory-command") return "整理记忆";
  if (parsed.kind === "attachments") {
    return parsed.text || parsed.files.map((file) => file.name).join("、");
  }
  return content;
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
