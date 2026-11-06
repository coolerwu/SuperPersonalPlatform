import type {
  Agent,
  AllocationEntry,
  AllocationEntryPayload,
  AllocationEntryQuery,
  AllocationEntryUpdatePayload,
  AllocationPortfolioConfig,
  AllocationPortfolioPayload,
  AllocationPortfolioUpdatePayload,
  AllocationSnapshot,
  AppSettings,
  Attachment,
  BacktestDetail,
  BacktestMeta,
  BacktestResult,
  BacktestSummary,
  ChanAnalysis,
  ChanBacktestPayload,
  ChanBacktestResult,
  Channel,
  ChannelConversation,
  ChannelLogin,
  ChannelLoginStatus,
  CronJob,
  KnowledgeState,
  KnowledgeStatus,
  MarketProbe,
  MarketStatus,
  MemoryItem,
  MemoryScope,
  Message,
  MessagePage,
  Provider,
  Run,
  RunEvent,
  ScreenshotInfo,
  Session,
  SessionSearchHit,
  SkillCatalog,
  SkillDetail,
  SystemInfo,
  ToolsCatalog,
  TradeJournalClosePayload,
  TradeJournalCreatePayload,
  TradeJournalQuery,
  TradeJournalRecord,
  TradeJournalStats,
  TradeJournalUpdatePayload,
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
      if (Array.isArray(detail.detail)) {
        const messages = detail.detail
          .map((item) => {
            if (
              item &&
              typeof item === "object" &&
              typeof (item as { msg?: unknown }).msg === "string"
            ) {
              return (item as { msg: string }).msg;
            }
            return "";
          })
          .filter(Boolean);
        if (messages.length) return messages.join("；");
      }
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
  uploadAttachment: (
    agentId: string,
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<Attachment> => {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open(
        "POST",
        `/api/agents/${agentId}/attachments?filename=${encodeURIComponent(file.name)}`,
      );
      request.withCredentials = true;
      request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onload = () => {
        let detail: unknown = request.statusText;
        if (request.responseText) {
          try {
            detail = JSON.parse(request.responseText);
          } catch {
            detail = request.responseText;
          }
        }
        if (request.status >= 200 && request.status < 300) {
          resolve(detail as Attachment);
        } else {
          reject(new ApiError(request.status, detail));
        }
      };
      request.onerror = () => reject(new ApiError(0, "上传失败"));
      request.send(file);
    });
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
  listMessagePage: (
    sessionId: string,
    params: { limit?: number; before?: string | null } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.limit) search.set("limit", String(params.limit));
    if (params.before) search.set("before", params.before);
    const query = search.toString();
    return request<MessagePage>(
      `/api/sessions/${sessionId}/messages/page${query ? `?${query}` : ""}`,
    );
  },
  listScreenshots: (sessionId: string) =>
    request<ScreenshotInfo[]>(`/api/sessions/${sessionId}/screenshots`),
  screenshotUrl: (sessionId: string, runId: string, screenshotId: string) =>
    `/api/sessions/${sessionId}/screenshots/${runId}/${screenshotId}.png`,
  uploadUrl: (agentId: string, path: string) => {
    const name = path.startsWith("/uploads/") ? path.slice("/uploads/".length) : path;
    return `/api/agents/${encodeURIComponent(agentId)}/uploads/${encodeURIComponent(name)}`;
  },

  listChannels: (agentId: string) =>
    request<Channel[]>(`/api/channels?agent_id=${encodeURIComponent(agentId)}`),
  listChannelConversations: (channelId: string) =>
    request<ChannelConversation[]>(`/api/channels/${channelId}/conversations`),
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

  listCronJobs: (agentId: string) =>
    request<CronJob[]>(`/api/cron-jobs?agent_id=${encodeURIComponent(agentId)}`),
  createCronJob: (payload: {
    agent_id: string;
    name: string;
    prompt: string;
    schedule: string;
    timezone: string;
    enabled: boolean;
  }) => request<CronJob>("/api/cron-jobs", { method: "POST", body: JSON.stringify(payload) }),
  updateCronJob: (
    jobId: string,
    payload: Partial<Pick<CronJob, "name" | "prompt" | "schedule" | "timezone" | "enabled">>,
  ) =>
    request<CronJob>(`/api/cron-jobs/${jobId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteCronJob: (jobId: string) =>
    request<{ deleted: boolean }>(`/api/cron-jobs/${jobId}`, { method: "DELETE" }),
  runCronJob: (jobId: string) =>
    request<{ run_id: string; session_id: string }>(`/api/cron-jobs/${jobId}/run`, {
      method: "POST",
    }),

  sendMessage: (sessionId: string, content: string) =>
    request<{ run_id: string; session_id: string; message_id: string }>("/api/chat/messages", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, content }),
    }),

  runChatCommand: (payload: { agent_id: string; session_id: string | null; command: string }) =>
    request<{ command: string; reply: string; cancelled_count: number }>("/api/chat/commands", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getRun: (runId: string) => request<Run>(`/api/runs/${runId}`),
  getRunEvents: (runId: string, after = 0) => request<RunEvent[]>(`/api/runs/${runId}/events?after=${after}`),
  cancelRun: (runId: string) => request<{ cancelled: boolean }>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  retryRun: (runId: string) => request<{ run_id: string }>(`/api/runs/${runId}/retry`, { method: "POST" }),
  streamUrl: (runId: string, after = 0) => `/api/runs/${runId}/stream?after=${after}`,

  getSystem: () => request<SystemInfo>("/api/system"),
  getSettings: () => request<AppSettings>("/api/settings"),
  updateSettings: (payload: { session_retention_days: number }) =>
    request<AppSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getKnowledge: () => request<KnowledgeState>("/api/knowledge"),
  updateKnowledge: (payload: {
    enabled?: boolean;
    webdav_url?: string;
    username?: string;
    password?: string;
    clear_password?: boolean;
    root?: string;
    interval_seconds?: number;
  }) =>
    request<KnowledgeState>("/api/knowledge", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  syncKnowledge: () =>
    request<{ queued: boolean; status: KnowledgeStatus }>("/api/knowledge/sync", {
      method: "POST",
    }),
  listSkills: () => request<SkillCatalog>("/api/skills"),
  getSkill: (directory: string) =>
    request<SkillDetail>(`/api/skills/${encodeURIComponent(directory)}`),
  updateSkill: (directory: string, content: string) =>
    request<SkillDetail>(`/api/skills/${encodeURIComponent(directory)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  deleteSkill: (directory: string) =>
    request<{ deleted: boolean }>(`/api/skills/${encodeURIComponent(directory)}`, {
      method: "DELETE",
    }),
  listTools: () => request<ToolsCatalog>("/api/tools"),
  searchMessages: (agentId: string, q: string, limit = 20) => {
    const search = new URLSearchParams({
      agent_id: agentId,
      q,
      limit: String(limit),
    });
    return request<SessionSearchHit[]>(`/api/sessions/search?${search.toString()}`);
  },

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

  listBacktests: (limit = 50) => request<BacktestSummary[]>(`/api/backtests?limit=${limit}`),
  getBacktestMeta: () => request<BacktestMeta>("/api/backtests/meta"),
  getBacktest: (id: string) => request<BacktestDetail>(`/api/backtests/${encodeURIComponent(id)}`),
  getBacktestResult: (id: string) =>
    request<BacktestResult>(`/api/backtests/${encodeURIComponent(id)}/result`),
  createBacktest: (payload: {
    symbol: string;
    granularity: string;
    start_ms: number;
    end_ms: number;
    initial_capital: number;
    fee_rate: number;
    slippage_rate: number;
    warmup_bars?: number;
    strategy_code: string;
  }) =>
    request<BacktestDetail>("/api/backtests", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelBacktest: (id: string) =>
    request<{ cancelled: boolean }>(`/api/backtests/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  getMarketStatus: (symbol: string, granularity: string) => {
    const search = new URLSearchParams({ symbol, granularity });
    return request<MarketStatus>(`/api/market/candles/status?${search.toString()}`);
  },
  probeMarket: (symbol: string, granularity: string) => {
    const search = new URLSearchParams({ symbol, granularity });
    return request<MarketProbe>(`/api/market/probe?${search.toString()}`);
  },

  listTradeJournal: (params: {
    market?: string;
    symbol?: string;
    direction?: string;
    status?: string;
    tag?: string;
    start?: string;
    end?: string;
    search?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && String(value).trim() !== "") {
        search.set(key, String(value));
      }
    });
    const query = search.toString();
    return request<TradeJournalQuery>(`/api/trade-journal${query ? `?${query}` : ""}`);
  },
  getTradeJournalStats: (params: {
    market?: string;
    symbol?: string;
    direction?: string;
    tag?: string;
    start?: string;
    end?: string;
  } = {}) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && String(value).trim() !== "") {
        search.set(key, String(value));
      }
    });
    const query = search.toString();
    return request<TradeJournalStats>(
      `/api/trade-journal/stats${query ? `?${query}` : ""}`,
    );
  },
  getTradeJournal: (id: string) =>
    request<TradeJournalRecord>(`/api/trade-journal/${encodeURIComponent(id)}`),
  createTradeJournal: (payload: TradeJournalCreatePayload) =>
    request<TradeJournalRecord>("/api/trade-journal", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTradeJournal: (id: string, payload: TradeJournalUpdatePayload) =>
    request<TradeJournalRecord>(`/api/trade-journal/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  closeTradeJournal: (id: string, payload: TradeJournalClosePayload) =>
    request<TradeJournalRecord>(
      `/api/trade-journal/${encodeURIComponent(id)}/close`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  analyzeChan: (payload: { symbol: string; granularity: string; bars?: number }) =>
    request<ChanAnalysis>("/api/chan/analyze", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  runChanBacktest: (payload: ChanBacktestPayload) =>
    request<ChanBacktestResult>("/api/chan/backtest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getAllocation: () => request<AllocationSnapshot>("/api/allocation"),
  createAllocationPortfolio: (payload: AllocationPortfolioPayload) =>
    request<AllocationPortfolioConfig>("/api/allocation/portfolios", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAllocationPortfolio: (
    id: string,
    payload: AllocationPortfolioUpdatePayload,
  ) =>
    request<AllocationPortfolioConfig>(
      `/api/allocation/portfolios/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  deleteAllocationPortfolio: (id: string) =>
    request<void>(`/api/allocation/portfolios/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  listAllocationEntries: (
    portfolioId: string,
    params: { limit?: number; offset?: number } = {},
  ) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        search.set(key, String(value));
      }
    });
    const query = search.toString();
    return request<AllocationEntryQuery>(
      `/api/allocation/portfolios/${encodeURIComponent(portfolioId)}/entries${
        query ? `?${query}` : ""
      }`,
    );
  },
  createAllocationEntry: (portfolioId: string, payload: AllocationEntryPayload) =>
    request<AllocationEntry>(
      `/api/allocation/portfolios/${encodeURIComponent(portfolioId)}/entries`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  updateAllocationEntry: (
    portfolioId: string,
    entryId: string,
    payload: AllocationEntryUpdatePayload,
  ) =>
    request<AllocationEntry>(
      `/api/allocation/portfolios/${encodeURIComponent(
        portfolioId,
      )}/entries/${encodeURIComponent(entryId)}`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  deleteAllocationEntry: (portfolioId: string, entryId: string) =>
    request<void>(
      `/api/allocation/portfolios/${encodeURIComponent(
        portfolioId,
      )}/entries/${encodeURIComponent(entryId)}`,
      { method: "DELETE" },
    ),
  refreshAllocation: () =>
    request<AllocationSnapshot>("/api/allocation/refresh", { method: "POST" }),
};
