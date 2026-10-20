import type {
  Agent,
  Attachment,
  Channel,
  ChannelLogin,
  ChannelLoginStatus,
  MemoryItem,
  MemoryScope,
  Message,
  Provider,
  Run,
  RunEvent,
  ScreenshotInfo,
  Session,
  SkillCatalog,
  SkillDetail,
  SystemInfo,
  ToolsCatalog,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function responseError(response: Response): Promise<ApiError> {
  let detail: unknown = response.statusText;
  const body = await response.text();
  if (body) {
    try {
      detail = JSON.parse(body);
    } catch {
      detail = body;
    }
  }
  return new ApiError(response.status, detail);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { detail?: unknown; message?: unknown } | string | undefined;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") return detail.message;
      if (typeof detail.detail === "string") return detail.detail;
      const nested = detail.detail as { message?: unknown } | undefined;
      if (nested && typeof nested === "object" && typeof nested.message === "string") {
        return nested.message;
      }
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

export const api = {
  me: () => request<{ authenticated: boolean }>("/api/auth/me"),
  login: (token: string) =>
    request<{ authenticated: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  logout: () => request<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),

  listAgents: () => request<Agent[]>("/api/agents"),
  createAgent: (payload: {
    name: string;
    instructions: string;
    provider_id?: string;
    auto_memory?: boolean;
    enabled_skills?: string[];
  }) => request<Agent>("/api/agents", { method: "POST", body: JSON.stringify(payload) }),
  updateAgent: (
    id: string,
    payload: Partial<
      Pick<Agent, "name" | "instructions" | "provider_id" | "status" | "auto_memory" | "enabled_skills">
    >,
  ) => request<Agent>(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  archiveAgent: (id: string) => request<{ archived: boolean }>(`/api/agents/${id}`, { method: "DELETE" }),
  deleteAgentPermanently: (id: string) =>
    request<{ deleted: boolean; sessions: number; runs: number }>(`/api/agents/${id}/permanent`, {
      method: "DELETE",
    }),
  uploadAttachment: async (agentId: string, file: File): Promise<Attachment> => {
    const response = await fetch(
      `/api/agents/${agentId}/attachments?filename=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      },
    );
    if (!response.ok) {
      throw await responseError(response);
    }
    return (await response.json()) as Attachment;
  },

  listProviders: () => request<Provider[]>("/api/providers"),
  createProvider: (payload: { name: string; base_url: string; model: string; api_key?: string }) =>
    request<Provider>("/api/providers", { method: "POST", body: JSON.stringify(payload) }),
  updateProvider: (
    id: string,
    payload: Partial<Pick<Provider, "name" | "base_url" | "model">> & { api_key?: string },
  ) => request<Provider>(`/api/providers/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  listSessions: (agentId: string) => request<Session[]>(`/api/agents/${agentId}/sessions`),
  createSession: (agentId: string, title = "新会话") =>
    request<Session>(`/api/agents/${agentId}/sessions`, { method: "POST", body: JSON.stringify({ title }) }),
  getSessionActiveRun: (sessionId: string) =>
    request<Run | null>(`/api/sessions/${sessionId}/active-run`),
  deleteSession: (sessionId: string) =>
    request<{ deleted: boolean }>(`/api/sessions/${sessionId}`, { method: "DELETE" }),
  listMessages: (sessionId: string) => request<Message[]>(`/api/sessions/${sessionId}/messages`),
  listScreenshots: (sessionId: string) =>
    request<ScreenshotInfo[]>(`/api/sessions/${sessionId}/screenshots`),
  screenshotUrl: (sessionId: string, runId: string, screenshotId: string) =>
    `/api/sessions/${sessionId}/screenshots/${runId}/${screenshotId}.png`,

  listChannels: (agentId: string) =>
    request<Channel[]>(`/api/channels?agent_id=${encodeURIComponent(agentId)}`),
  createChannel: (agentId: string, name: string) =>
    request<Channel>("/api/channels", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId, name }),
    }),
  updateChannel: (channelId: string, payload: { name?: string; status?: "active" | "paused" }) =>
    request<Channel>(`/api/channels/${channelId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteChannel: (channelId: string) =>
    request<{ deleted: boolean }>(`/api/channels/${channelId}`, { method: "DELETE" }),
  startWechatLogin: (channelId: string) =>
    request<ChannelLogin>(`/api/channels/${channelId}/wechat/login`, { method: "POST" }),
  getWechatLoginStatus: (channelId: string, qrcode: string) =>
    request<ChannelLoginStatus>(
      `/api/channels/${channelId}/wechat/login/status?qrcode=${encodeURIComponent(qrcode)}`,
    ),
  reloginWechat: (channelId: string) =>
    request<Channel>(`/api/channels/${channelId}/wechat/relogin`, { method: "POST" }),

  sendMessage: (sessionId: string, content: string) =>
    request<{ run_id: string; session_id: string; message_id: string }>("/api/chat/messages", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, content }),
    }),

  getRun: (runId: string) => request<Run>(`/api/runs/${runId}`),
  getRunEvents: (runId: string, after = 0) => request<RunEvent[]>(`/api/runs/${runId}/events?after=${after}`),
  cancelRun: (runId: string) => request<{ cancelled: boolean }>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  retryRun: (runId: string) => request<{ run_id: string }>(`/api/runs/${runId}/retry`, { method: "POST" }),
  streamUrl: (runId: string, after = 0) => `/api/runs/${runId}/stream?after=${after}`,

  getSystem: () => request<SystemInfo>("/api/system"),
  listSkills: () => request<SkillCatalog>("/api/skills"),
  getSkill: (directory: string) =>
    request<SkillDetail>(`/api/skills/${encodeURIComponent(directory)}`),
  updateSkill: (directory: string, content: string) =>
    request<SkillDetail>(`/api/skills/${encodeURIComponent(directory)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  listTools: () => request<ToolsCatalog>("/api/tools"),

  listMemories: (params: { scope: MemoryScope; agentId?: string; q?: string }) => {
    const search = new URLSearchParams({ scope: params.scope });
    if (params.agentId) search.set("agent_id", params.agentId);
    if (params.q) search.set("q", params.q);
    return request<MemoryItem[]>(`/api/memories?${search.toString()}`);
  },
  createMemory: (payload: {
    scope: MemoryScope;
    agent_id?: string;
    content: string;
    tags?: string[];
  }) => request<MemoryItem>("/api/memories", { method: "POST", body: JSON.stringify(payload) }),
  updateMemory: (id: string, payload: { content?: string; tags?: string[] }) =>
    request<MemoryItem>(`/api/memories/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteMemory: (id: string) => request<{ deleted: boolean }>(`/api/memories/${id}`, { method: "DELETE" }),
};
