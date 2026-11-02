export type Agent = {
  id: string;
  name: string;
  instructions: string;
  provider_id: string;
  provider_name: string;
  model: string;
  status: string;
  auto_memory: boolean;
  enabled_skills: string[];
  created_at: string;
  updated_at: string;
};

export type AgentSavePayload = {
  name: string;
  instructions: string;
  provider_id: string;
  auto_memory: boolean;
};

export type SkillInfo = {
  name: string;
  description: string;
  path: string;
  directory: string;
  include_tools: string[];
  can_enable: boolean;
  warnings: string[];
  source: "builtin" | "ai" | "manual" | string;
};

export type SkillCatalog = {
  skills: SkillInfo[];
  errors: string[];
};

export type SkillDetail = {
  name: string;
  directory: string;
  path: string;
  content: string;
  warnings: string[];
  source: "builtin" | "ai" | "manual" | string;
};

export type ToolInfo = {
  name: string;
  description: string;
  registered: boolean;
  skills: string[];
};

export type ToolsCatalog = {
  platform_tools: ToolInfo[];
  filesystem_tools: ToolInfo[];
  skill_tools: ToolInfo[];
};

export type Provider = {
  id: string;
  name: string;
  base_url: string;
  model: string;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

export type Session = {
  id: string;
  agent_id: string;
  title: string;
  origin: "chat" | "cron" | string;
  message_count: number;
  active_run_id: string | null;
  active_run_status: string | null;
  channel_type: string | null;
  channel_name: string | null;
  created_at: string;
  updated_at: string;
};

export type CronJob = {
  id: string;
  agent_id: string;
  name: string;
  prompt: string;
  schedule: string;
  timezone: string;
  enabled: boolean;
  session_id: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_run_status: string | null;
  last_skipped_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ChannelStatus = "pairing" | "active" | "paused" | "expired";

export type Channel = {
  id: string;
  agent_id: string;
  type: string;
  name: string;
  status: ChannelStatus | string;
  account_id: string;
  has_credentials: boolean;
  conversation_count: number;
  last_seen_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type ChannelLogin = {
  qrcode: string;
  scan_url: string;
  qr_svg: string;
  expires_in: number;
};

export type ChannelLoginStatus = {
  status: string;
  account_id: string;
};

export type Message = {
  id: string;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  run_id: string | null;
  created_at: string;
};

export type SessionSearchHit = {
  message_id: string;
  session_id: string;
  session_title: string;
  role: "user" | "assistant" | string;
  snippet: string;
  created_at: string;
};

export type Attachment = {
  name: string;
  path: string;
  size: number;
};

export type ScreenshotInfo = {
  id: string;
  run_id: string;
  created_at: string;
};

export type Run = {
  id: string;
  agent_id: string;
  session_id: string;
  source: string;
  status: string;
  input: string;
  output: string | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type RunEvent = {
  id: number;
  run_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type SystemInfo = {
  version: string;
  execution_core: string;
  database: string;
  workspace_root: string;
  skills_root: string;
  run_timeout_seconds: number;
  max_history_messages: number;
  counts: {
    providers: number;
    agents: number;
    sessions: number;
    messages: number;
    runs: number;
  };
  capabilities: Record<string, boolean>;
};

export type MemoryScope = "user" | "agent";

export type MemoryItem = {
  id: string;
  scope: MemoryScope;
  agent_id: string | null;
  content: string;
  tags: string[];
  source_message_id: string | null;
  source_run_id: string | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
};

export type DrawerTab = "run" | "agent";

export type BacktestStatus =
  | "queued"
  | "fetching"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | string;

export type BacktestMetrics = {
  bars: number;
  start_ms: number;
  end_ms: number;
  years: number;
  initial_capital: number;
  final_equity: number;
  total_return: number;
  annual_return: number | null;
  max_drawdown: number;
  sharpe: number | null;
  sortino: number | null;
  volatility: number | null;
  win_rate: number | null;
  trades: number;
  profit_factor: number | null;
  benchmark_return: number;
  benchmark_annual_return: number | null;
  avg_exposure: number;
  total_cost: number;
  avg_trade_return: number | null;
};

export type BacktestSummary = {
  id: string;
  symbol: string;
  granularity: string;
  start_ms: number;
  end_ms: number;
  initial_capital: number;
  fee_rate: number;
  slippage_rate: number;
  status: BacktestStatus;
  error: string | null;
  metrics: BacktestMetrics | null;
  warnings: string[];
  bar_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type BacktestProgress = {
  stage?: string;
  fetched_bars?: number;
  reason?: string;
};

export type BacktestDetail = BacktestSummary & {
  strategy_code: string;
  cancel_requested: boolean;
  error_detail: string | null;
  progress: BacktestProgress;
  data_start_ms: number | null;
  data_end_ms: number | null;
  trades_total: number;
  trades_truncated: boolean;
};

export type BacktestTrade = {
  entry_ts: number;
  exit_ts: number;
  side: "long" | "short" | string;
  entry_price: number;
  exit_price: number;
  bars: number;
  return: number;
  pnl: number;
  closed: boolean;
};

export type BacktestResult = {
  id: string;
  status: BacktestStatus;
  metrics: BacktestMetrics | null;
  equity: [number, number][];
  drawdown: [number, number][];
  candles: [number, number, number, number, number, number][];
  trades: BacktestTrade[];
  warnings: string[];
  trades_total: number;
  trades_truncated: boolean;
  data_start_ms: number | null;
  data_end_ms: number | null;
  bar_count: number;
};

export type BacktestMeta = {
  granularities: { value: string; label: string }[];
  max_bars: number;
  max_trades: number;
  worker_running: boolean;
  proxy_configured: boolean;
  bitget_base_url: string;
};

export type MarketStatus = {
  symbol: string;
  granularity: string;
  rows: number;
  first_ms: number | null;
  last_ms: number | null;
  oldest_available_ms: number | null;
  last_synced_at: string | null;
  last_error: string | null;
};

export type MarketProbe = {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  base_url: string;
  proxy_configured: boolean;
};
