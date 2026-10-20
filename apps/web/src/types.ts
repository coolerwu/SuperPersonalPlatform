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
};

export type SkillCatalog = {
  skills: SkillInfo[];
  errors: string[];
};

export type ToolInfo = {
  name: string;
  description: string;
  registered: boolean;
  skills: string[];
};

export type ToolsCatalog = {
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

export type Attachment = {
  name: string;
  path: string;
  size: number;
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
