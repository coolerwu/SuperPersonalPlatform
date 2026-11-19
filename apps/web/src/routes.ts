export type View =
  | "chat"
  | "settings"
  | "memory"
  | "skills"
  | "tools"
  | "plugins"
  | "channels"
  | "cron"
  | "backtest"
  | "journal"
  | "chan"
  | "allocation";

const VIEW_PATHS: Record<View, string> = {
  chat: "/chat",
  settings: "/config",
  memory: "/memory",
  skills: "/skills",
  tools: "/tools",
  plugins: "/plugins",
  channels: "/channels",
  cron: "/cron",
  backtest: "/backtest",
  journal: "/journal",
  chan: "/chan",
  allocation: "/allocation",
};

// The selected agent travels in the path (`/a/<agent>/chat/...`) so a refresh,
// a bookmark or a shared link reopens the same agent.
const AGENT_PREFIX = /^\/a\/([^/]+)(\/.*)?$/;

export interface Route {
  view: View;
  sessionId: string;
  agentId: string;
}

export function pathForView(view: View, sessionId: string, agentId = ""): string {
  const prefix = agentId ? `/a/${encodeURIComponent(agentId)}` : "";
  if (view === "chat" && sessionId) {
    return `${prefix}${VIEW_PATHS.chat}/${encodeURIComponent(sessionId)}`;
  }
  return `${prefix}${VIEW_PATHS[view]}`;
}

export function parsePath(pathname: string): Route | null {
  const raw = pathname.replace(/\/+$/, "") || "/";
  let path = raw;
  let agentId = "";
  const prefixed = AGENT_PREFIX.exec(raw);
  if (prefixed) {
    try {
      agentId = decodeURIComponent(prefixed[1]);
    } catch {
      return null;
    }
    path = prefixed[2] || "/";
  }
  if (path === "/") {
    // `/a/<agent>` with nothing after it lands on the chat view.
    return agentId ? { view: "chat", sessionId: "", agentId } : null;
  }
  for (const [view, viewPath] of Object.entries(VIEW_PATHS) as [View, string][]) {
    if (path === viewPath) return { view, sessionId: "", agentId };
    if (view === "chat" && path.startsWith(`${viewPath}/`)) {
      const rest = path.slice(viewPath.length + 1);
      if (rest && !rest.includes("/")) {
        try {
          return { view, sessionId: decodeURIComponent(rest), agentId };
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}
