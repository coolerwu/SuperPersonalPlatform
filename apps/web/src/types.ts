export type Agent = {
  id: string;
  name: string;
  instructions: string;
  provider_id: string;
  provider_name: string;
  model: string;
  status: string;
  created_at: string;
  updated_at: string;
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
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  run_id: string | null;
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

export type DrawerTab = "run" | "agent";
