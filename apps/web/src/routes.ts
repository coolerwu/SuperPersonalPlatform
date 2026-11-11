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

export const DEFAULT_PATH = VIEW_PATHS.chat;

export interface Route {
  view: View;
  sessionId: string;
}

export function pathForView(view: View, sessionId: string): string {
  if (view === "chat" && sessionId) {
    return `${VIEW_PATHS.chat}/${encodeURIComponent(sessionId)}`;
  }
  return VIEW_PATHS[view];
}

export function parsePath(pathname: string): Route | null {
  const path = pathname.replace(/\/+$/, "") || "/";
  for (const [view, viewPath] of Object.entries(VIEW_PATHS) as [View, string][]) {
    if (path === viewPath) return { view, sessionId: "" };
    if (view === "chat" && path.startsWith(`${viewPath}/`)) {
      const rest = path.slice(viewPath.length + 1);
      if (rest && !rest.includes("/")) {
        try {
          return { view, sessionId: decodeURIComponent(rest) };
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}
