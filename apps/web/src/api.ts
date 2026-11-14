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
  ArtifactPreview,
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
  MemoryDocument,
  AgentAutoMemory,
  Provider,
  SessionSummary,
  SessionTurnStatus,
  SkillCatalog,
  SkillDetail,
  SystemInfo,
  ToolsCatalog,
  PluginsCatalog,
  TradeJournalClosePayload,
  TradeJournalCreatePayload,
  TradeJournalQuery,
  TradeJournalRecord,
  TradeJournalStats,
  TradeJournalUpdatePayload,
  TranscriptMessage,
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
  previewWorkspaceFile: (agentId: string, path: string, signal?: AbortSignal) =>
    request<ArtifactPreview>(
      `/api/agents/${encodeURIComponent(agentId)}/file-preview/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      signal ? { signal } : {},
    ),
  createAgent: (payload: {
    name: string;
    instructions: string;
    provider_id?: string;
    auto_memory?: boolean;
  }) => request<Agent>("/api/agents", { method: "POST", body: JSON.stringify(payload) }),
  updateAgent: (
    id: string,
    payload: Partial<
      Pick<Agent, "name" | "instructions" | "provider_id" | "status" | "auto_memory">
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

  listSessions: (agentId: string) =>
    request<SessionSummary[]>(`/api/agents/${agentId}/sessions`),
  listSessionMessages: (sessionId: string, agentId: string) =>
    request<TranscriptMessage[]>(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages?agent_id=${encodeURIComponent(agentId)}`,
    ),
  interruptSession: (sessionId: string) =>
    request<{ interrupted: boolean; cancelled_turn_ids: string[] }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/interrupt`,
      { method: "POST" },
    ),
  cancelQueuedTurn: (sessionId: string, turnId: string) =>
    request<{ cancelled: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      { method: "POST" },
    ),
  getSessionTurn: (sessionId: string, turnId?: string) => {
    const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : "";
    return request<SessionTurnStatus>(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn${query}`,
    );
  },
  renameSession: (sessionId: string, agentId: string, title: string) =>
    request<{ renamed: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/rename?agent_id=${encodeURIComponent(agentId)}`,
      { method: "POST", body: JSON.stringify({ title }) },
    ),
  deleteSession: (sessionId: string, agentId: string) =>
    request<{ deleted: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}?agent_id=${encodeURIComponent(agentId)}`,
      { method: "DELETE" },
    ),
  /** `after` is the buffer index of the first event to replay (inclusive). */
  sessionStreamUrl: (sessionId: string, turnId?: string, after?: number) => {
    const params = new URLSearchParams();
    if (turnId) params.set("turn_id", turnId);
    if (after !== undefined && after > 0) params.set("after", String(after));
    const query = params.toString();
    return `/api/sessions/${encodeURIComponent(sessionId)}/stream${
      query ? `?${query}` : ""
    }`;
  },
  uploadUrl: (agentId: string, path: string) => {
    // Attachments written by newer runs use a workspace-relative path
    // ("uploads/x"); transcripts from before the SDK migration may still
    // carry "/uploads/x". Both resolve to the same download endpoint.
    const relative = path.startsWith("/") ? path.slice(1) : path;
    const name = relative.startsWith("uploads/")
      ? relative.slice("uploads/".length)
      : relative;
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
    push_channel_id?: string;
    push_conversation_id?: string;
  }) => request<CronJob>("/api/cron-jobs", { method: "POST", body: JSON.stringify(payload) }),
  updateCronJob: (
    jobId: string,
    payload: Partial<
      Pick<
        CronJob,
        | "name"
        | "prompt"
        | "schedule"
        | "timezone"
        | "enabled"
        | "push_channel_id"
        | "push_conversation_id"
      >
    >,
  ) =>
    request<CronJob>(`/api/cron-jobs/${jobId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteCronJob: (jobId: string) =>
    request<CronJob>(`/api/cron-jobs/${jobId}`, { method: "DELETE" }),
  runCronJob: (jobId: string) =>
    request<CronJob>(`/api/cron-jobs/${jobId}/run`, {
      method: "POST",
    }),

  sendMessage: (input: { agent_id: string; session_id: string; content: string }) =>
    request<{ session_id: string; turn_id: string; accepted: boolean }>("/api/chat/messages", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  runChatCommand: (payload: { agent_id: string; session_id: string | null; command: string }) =>
    request<{ command: string; reply: string; cancelled_count: number; forward: boolean }>(
      "/api/chat/commands",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  getSlashCommands: (agentId: string) =>
    request<{ commands: string[] }>(
      `/api/chat/slash-commands?agent_id=${encodeURIComponent(agentId)}`,
    ),

  getSystem: () => request<SystemInfo>("/api/system"),
  getSettings: () => request<AppSettings>("/api/settings"),
  updateSettings: (payload: {
    session_retention_days: number;
  }) =>
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
  listTools: (agentId: string) =>
    request<ToolsCatalog>(`/api/tools?agent_id=${encodeURIComponent(agentId)}`),
  listPlugins: () => request<PluginsCatalog>("/api/plugins"),
  addPluginMarketplace: () =>
    request<PluginsCatalog>("/api/plugins/marketplace", { method: "POST" }),
  updatePluginMarketplace: () =>
    request<PluginsCatalog>("/api/plugins/marketplace/update", {
      method: "POST",
    }),
  removePluginMarketplace: () =>
    request<PluginsCatalog>("/api/plugins/marketplace", { method: "DELETE" }),
  installPlugin: (name: string) =>
    request<PluginsCatalog>("/api/plugins/install", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  enablePlugin: (pluginId: string) =>
    request<PluginsCatalog>(
      `/api/plugins/${encodeURIComponent(pluginId)}/enable`,
      { method: "POST" },
    ),
  disablePlugin: (pluginId: string) =>
    request<PluginsCatalog>(
      `/api/plugins/${encodeURIComponent(pluginId)}/disable`,
      { method: "POST" },
    ),
  updatePlugin: (pluginId: string) =>
    request<PluginsCatalog>(
      `/api/plugins/${encodeURIComponent(pluginId)}/update`,
      { method: "POST" },
    ),
  uninstallPlugin: (pluginId: string) =>
    request<PluginsCatalog>(
      `/api/plugins/${encodeURIComponent(pluginId)}`,
      { method: "DELETE" },
    ),

  getMemory: () => request<MemoryDocument>("/api/memories"),
  saveMemory: (content: string) =>
    request<MemoryDocument>("/api/memories", {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  getAgentMemory: (agentId: string) =>
    request<MemoryDocument>(
      `/api/memories/agents/${encodeURIComponent(agentId)}`,
    ),
  saveAgentMemory: (agentId: string, content: string) =>
    request<MemoryDocument>(
      `/api/memories/agents/${encodeURIComponent(agentId)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    ),
  getAgentAutoMemory: (agentId: string) =>
    request<AgentAutoMemory>(
      `/api/memories/agents/${encodeURIComponent(agentId)}/auto-memory`,
    ),

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

  analyzeChan: (payload: {
    symbol: string;
    granularity: string;
    bars?: number;
    start_date?: string;
    end_date?: string;
  }) =>
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
