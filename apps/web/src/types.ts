export type Agent = {
  id: string;
  name: string;
  instructions: string;
  provider_id: string;
  provider_name: string;
  model: string;
  status: string;
  auto_memory: boolean;
  created_at: string;
  updated_at: string;
};

export type AgentSavePayload = {
  name: string;
  instructions: string;
  provider_id: string;
  auto_memory: boolean;
  status?: "active" | "paused";
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

export type PluginMarketplace = {
  name: string;
  repo: string;
  install_location: string;
};

export type MemoryDocument = {
  content: string;
  saved?: boolean;
};

export type AutoMemoryFile = {
  name: string;
  content: string;
  size: number;
  updated_at: string;
};

export type AgentAutoMemory = {
  directory: string;
  files: AutoMemoryFile[];
  errors: string[];
};

export type InstalledPlugin = {
  id: string;
  name: string;
  marketplace: string;
  version: string;
  enabled: boolean;
  scope: string;
  installed_at: string;
  last_updated: string;
};

export type AvailablePlugin = {
  name: string;
  description: string;
  author: string;
  category: string;
  homepage: string;
};

export type PluginsCatalog = {
  cli_available: boolean;
  marketplaces: PluginMarketplace[];
  installed: InstalledPlugin[];
  available: AvailablePlugin[];
  errors: string[];
};

export type ToolInfo = {
  name: string;
  description: string;
  registered: boolean;
  skills: string[];
  source: "claude-code" | "platform" | "skill" | string;
  wired: boolean;
};

export type ToolsCatalog = {
  platform_tools: ToolInfo[];
  filesystem_tools: ToolInfo[];
  skill_tools: ToolInfo[];
  claude_code_note: string;
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

export type SessionSummary = {
  id: string;
  title: string;
  last_modified: number;
  channel_type: string | null;
  channel_name: string | null;
  busy: boolean;
};

export type TranscriptMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  blocks: Array<{ type: string; [key: string]: unknown }>;
};

export type TurnStreamEvent =
  | { type: "assistant_delta"; delta: string }
  | { type: "reasoning_delta"; delta: string }
  | { type: "tool_call"; name: string; input: Record<string, unknown> }
  | {
      type: "turn_end";
      status?: "completed" | "failed" | "cancelled";
      error?: string | null;
      terminal_reason?: string | null;
      usage?: Record<string, unknown> | null;
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
  last_skipped_at: string | null;
  push_channel_id: string | null;
  push_conversation_id: string | null;
  push_channel_name: string;
  push_conversation_name: string;
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

export type ChannelConversation = {
  id: string;
  channel_id: string;
  external_id: string;
  display_name: string;
  session_id: string;
  session_title: string;
  created_at: string;
  updated_at: string;
};

export type Attachment = {
  name: string;
  path: string;
  size: number;
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

export type KnowledgeConfig = {
  enabled: boolean;
  webdav_url: string;
  username: string;
  root: string;
  interval_seconds: number;
  has_password: boolean;
  local_dir: string;
};

export type KnowledgeSyncResult = {
  downloaded: number;
  uploaded: number;
  conflicts: number;
  removed_local: number;
  skipped: number;
  errors: number;
};

export type KnowledgeStatus = {
  enabled: boolean;
  configured: boolean;
  blocked: boolean;
  syncing: boolean;
  retry_at: string | null;
  local_dir: string;
  interval_seconds: number;
  last_sync_at: string | null;
  last_error: string;
  last_result: KnowledgeSyncResult | null;
};

export type KnowledgeState = {
  config: KnowledgeConfig;
  status: KnowledgeStatus;
};

export type AppSettings = {
  session_retention_days: number;
  session_cleanup_interval_seconds: number;
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
  expected_bars?: number;
  missing_bars?: number;
  gap_count?: number;
  warmup_bars?: number;
  requested_warmup_bars?: number;
  eval_start_ms?: number;
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
  future_check_bars?: number;
  future_check_mismatches?: number | null;
  future_check_ok?: boolean | null;
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
  config?: { warmup_bars?: number };
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

export type TradeJournalStatus = "open" | "closed" | string;

export type TradeJournalRecord = {
  id: string;
  market: string;
  instrument_type: string;
  symbol: string;
  direction: "long" | "short" | string;
  currency: string;
  entry_at: string;
  entry_price: number;
  exit_at: string | null;
  exit_price: number | null;
  stop_loss: number | null;
  quantity: number | null;
  fees: number;
  followed_plan: boolean | null;
  thesis: string;
  review: string;
  tags: string[];
  source: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  status: TradeJournalStatus;
  return_pct: number | null;
  r_multiple: number | null;
  pnl: number | null;
  win: boolean | null;
  warnings: string[];
};

export type TradeJournalQuery = {
  total: number;
  items: TradeJournalRecord[];
  offset: number;
  limit: number;
};

export type TradeJournalStatsGroup = {
  tag?: string;
  market?: string;
  count: number;
  win_rate: number | null;
  avg_return: number | null;
  avg_r: number | null;
  total_pnl: number | null;
  currency: string | null;
  pnl_currencies: string[];
  profit_factor: number | null;
};

export type TradeJournalStats = TradeJournalStatsGroup & {
  total: number;
  open_count: number;
  by_market: TradeJournalStatsGroup[];
  by_tag: TradeJournalStatsGroup[];
  best: TradeJournalRecord | null;
  worst: TradeJournalRecord | null;
};

export type TradeJournalCreatePayload = {
  symbol: string;
  entry_at: string;
  entry_price: number;
  thesis: string;
  direction?: string;
  market?: string;
  currency?: string;
  instrument_type?: string;
  stop_loss?: number | null;
  quantity?: number | null;
  fees?: number;
  exit_at?: string | null;
  exit_price?: number | null;
  review?: string;
  tags?: string[];
  followed_plan?: boolean | null;
  meta?: Record<string, unknown>;
};

export type TradeJournalUpdatePayload = Partial<TradeJournalCreatePayload>;

export type TradeJournalClosePayload = {
  exit_at: string;
  exit_price: number;
  fees?: number;
  review?: string;
  tags?: string[];
  followed_plan?: boolean | null;
};

export type ChanCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type ChanMergedBar = ChanCandle & {
  start_time: number;
  end_time: number;
  merged_index: number;
};

export type ChanFractal = {
  type: "top" | "bottom" | string;
  time: number;
  price: number;
  merged_index: number;
  used?: boolean;
};

export type ChanStroke = {
  start_time: number;
  start_price: number;
  end_time: number;
  end_price: number;
  direction: "up" | "down" | string;
  high: number;
  low: number;
};

export type ChanZone = {
  start_time: number;
  end_time: number;
  zg: number;
  zd: number;
  entry_stroke: number;
  start_stroke: number;
  end_stroke: number;
  exit_stroke: number;
};

export type ChanPoint = {
  type: "1buy" | "2buy" | "3buy" | "1sell" | "2sell" | "3sell" | string;
  time: number;
  price: number;
  stroke_index: number;
  reason: string;
  ref_price?: number;
  broken?: boolean;
  broken_time?: number;
  broken_price?: number;
};

export type ChanMacdPoint = {
  time: number;
  dif: number;
  dea: number;
  hist: number;
};

export type ChanMacd = {
  params: { fast: number; slow: number; signal: number };
  series: ChanMacdPoint[];
};

export type ChanDivergence = {
  kind: "bottom" | "top" | string;
  time: number;
  price: number;
  stroke_index: number;
  prev_time: number;
  prev_price: number;
  area: number;
  prev_area: number;
  ratio: number;
  dif?: number;
  prev_dif?: number;
};

export type ChanRules = {
  name: string;
  bi_min_span: number;
  bi_rule: string;
  fractal_rule: string;
  pending_rule?: string;
  zone_rule: string;
  point_rule: string;
  divergence_rule?: string;
  macd_params?: { fast: number; slow: number; signal: number };
  segments: boolean;
  buy_sell_points: boolean;
};

export type ChanAnalysis = {
  symbol: string;
  granularity: string;
  requested_bars: number;
  bars: number;
  merged_bars: number;
  stroke_count: number;
  zone_count: number;
  point_count: number;
  start_ms: number;
  end_ms: number;
  candles: ChanCandle[];
  merged: ChanMergedBar[];
  fractals: ChanFractal[];
  strokes: ChanStroke[];
  pending_strokes?: ChanStroke[];
  zones: ChanZone[];
  points: ChanPoint[];
  macd: ChanMacd;
  divergences: ChanDivergence[];
  divergence_count?: number;
  rules: ChanRules;
  warnings: string[];
};

export type ChanBacktestTrade = {
  signal_type: string;
  side: "long" | "short" | string;
  confirm_time: number;
  entry_time: number;
  entry_price: number;
  exit_time: number;
  exit_price: number;
  quantity: number;
  stop_loss: number;
  take_profit: number;
  bars_held: number;
  pnl: number;
  r_multiple: number | null;
  reason: string;
  forced: boolean;
};

export type ChanBacktestTypeStats = {
  type: string;
  trades: number;
  win_rate: number | null;
  avg_r: number | null;
  profit_factor: number | null;
  total_pnl: number;
};

export type ChanBacktestResult = {
  symbol: string;
  granularity: string;
  requested_bars: number;
  bars: number;
  start_ms: number;
  end_ms: number;
  causal_signals: number;
  initial_capital: number;
  final_equity: number;
  total_return: number;
  annual_return: number | null;
  max_drawdown: number;
  win_rate: number | null;
  avg_r: number | null;
  profit_factor: number | null;
  trades: number;
  total_pnl: number;
  exposure: number;
  by_type: ChanBacktestTypeStats[];
  trades_detail: ChanBacktestTrade[];
  signals: Record<string, unknown>[];
  skipped_signals: number;
  equity: [number, number][];
  warnings: string[];
  data_warnings: string[];
};

export type ChanBacktestPayload = {
  symbol: string;
  granularity: string;
  bars?: number;
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  fee_rate?: number;
  slippage_rate?: number;
  stop_pct?: number;
  risk_pct?: number;
  r_multiple?: number;
  max_hold_bars?: number;
};

export type AllocationPriceSource =
  | "live"
  | "stock"
  | "mark"
  | "cache"
  | "cost"
  | "none"
  | string;

export type AllocationMark = {
  price: number;
  as_of: string | null;
};

export type AllocationPosition = {
  symbol: string;
  market: string;
  quantity: number;
  avg_cost: number | null;
  cost: number;
  price: number | null;
  price_source: AllocationPriceSource;
  price_at: string | null;
  price_stale: boolean;
  market_value: number | null;
  weight_pct: number | null;
  target_pct: number | null;
  drift_pp: number | null;
  rebalance_needed: boolean | null;
  rebalance_amount: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  realized_pnl: number;
  dividends: number;
  fees: number;
  entry_count: number;
  last_entry_at: string | null;
  warnings: string[];
};

export type AllocationAccrual = {
  annual_rate: number;
  start: string;
  compound: boolean;
};

export type AllocationPortfolio = {
  id: string;
  name: string;
  currency: string;
  note: string;
  cash: number;
  total_value: number;
  holdings_value: number;
  invested_cost: number;
  net_contributions: number;
  unrealized_pnl: number;
  realized_pnl: number;
  dividends: number;
  fees: number;
  total_return: number;
  total_return_pct: number | null;
  xirr_pct: number | null;
  cash_target_pct: number | null;
  rebalance_band_pct: number;
  targets: Record<string, number>;
  marks: Record<string, AllocationMark>;
  accruals: Record<string, AllocationAccrual>;
  cash_weight_pct: number | null;
  cash_drift_pp: number | null;
  cash_rebalance_needed: boolean | null;
  cash_rebalance_amount: number | null;
  target_sum_pct: number | null;
  unallocated_pct: number | null;
  entry_count: number;
  last_entry_at: string | null;
  positions: AllocationPosition[];
  warnings: string[];
  error: string | null;
};

export type AllocationPortfolioConfig = {
  id: string;
  name: string;
  currency: string;
  cash_target_pct: number | null;
  rebalance_band_pct: number;
  targets: Record<string, number>;
  marks: Record<string, AllocationMark>;
  accruals: Record<string, AllocationAccrual>;
  note: string;
  created_at: string;
  updated_at: string;
};

export type AllocationFx = {
  usd_cny: number;
  at: string | null;
  source: string;
};

export type AllocationOverviewPortfolio = {
  id: string;
  name: string;
  currency: string;
  rate_to_usd: number;
  total_value: number;
  total_value_usd: number;
  cash: number;
  cash_usd: number;
  weight_pct: number | null;
};

export type AllocationOverviewPosition = {
  symbol: string;
  market: string;
  quantity: number;
  cost_usd: number;
  avg_cost_usd: number | null;
  price_usd: number | null;
  price_source: string;
  price_stale: boolean;
  market_value_usd: number | null;
  weight_pct: number | null;
  unrealized_pnl_usd: number | null;
  realized_pnl_usd: number;
  dividends_usd: number;
  fees_usd: number;
  portfolio_count: number;
};

export type AllocationOverview = {
  display_currency: string;
  usd_cny: number;
  fx_at: string | null;
  fx_source: string;
  fx_stale: boolean;
  total_value: number;
  cash: number;
  holdings_value: number;
  invested_cost: number;
  net_contributions: number;
  unrealized_pnl: number;
  realized_pnl: number;
  dividends: number;
  fees: number;
  total_return: number;
  total_return_pct: number | null;
  xirr_pct: number | null;
  xirr_approximate: boolean;
  portfolios: AllocationOverviewPortfolio[];
  positions: AllocationOverviewPosition[];
  warnings: string[];
};

export type AllocationSnapshot = {
  generated_at: string;
  fx: AllocationFx;
  overview: AllocationOverview;
  portfolios: AllocationPortfolio[];
  warnings: string[];
  price_errors: { symbol: string; error: string }[];
  fx_error?: string;
};

export type AllocationEntry = {
  id: string;
  portfolio_id: string;
  type: string;
  at: string;
  amount?: number;
  symbol?: string;
  quantity?: number;
  price?: number;
  fees?: number;
  note: string;
  source: string;
  created_at: string;
  updated_at: string;
};

export type AllocationEntryQuery = {
  total: number;
  items: AllocationEntry[];
  offset: number;
  limit: number;
};

export type AllocationPortfolioPayload = {
  name: string;
  currency: string;
  cash_target_pct: number | null;
  rebalance_band_pct: number;
  targets: Record<string, number>;
  marks: Record<string, { price: number }>;
  accruals?: Record<string, AllocationAccrual>;
  note: string;
};

export type AllocationPortfolioUpdatePayload = Partial<AllocationPortfolioPayload>;

export type AllocationEntryPayload = {
  type: string;
  at: string;
  amount?: number | null;
  symbol?: string | null;
  quantity?: number | null;
  price?: number | null;
  fees?: number | null;
  note?: string;
};

export type AllocationEntryUpdatePayload = Partial<AllocationEntryPayload>;
