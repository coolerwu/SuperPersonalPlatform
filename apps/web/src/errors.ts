import { ApiError, errorText } from "./api";

const RUN_ERROR_RULES: Array<[RegExp, string]> = [
  [
    /provider.*api_key.*not configured/i,
    "Provider 还没有配置 API Key：请到配置页填写。",
  ],
  [
    /api[_ ]?key|authentication|unauthorized|\b401\b/,
    "Provider 认证失败：请检查配置页里的 API Key。",
  ],
  [
    /rate.?limit|too many requests|\b429\b/,
    "Provider 触发了限流（429）：稍后重试，或减少同时运行的对话。",
  ],
  [
    /context[_ ]?length|maximum context|context window|too many tokens|上下文.*(超|长)/,
    "上下文超出模型窗口：请新建会话，或缩短输入后重试。",
  ],
  [
    /run time|execution timeout|RunTimeoutError/,
    "Run 超过执行时限，已停止。可以重试，或把任务拆小。",
  ],
  [
    /timeout|timed out|APITimeout/,
    "模型调用超时：请重试。",
  ],
  [
    /connection|connecterror|network|无法连接/,
    "无法连接 Provider：请检查网络和 Provider 的 base_url。",
  ],
  [
    /provider.*api_key.*not configured|ProviderError/,
    "Provider 还没有配置 API Key：请到配置页填写。",
  ],
  [
    /ConfigurationError|agent, session, or provider not found/,
    "Agent、会话或 Provider 配置缺失：请检查配置页。",
  ],
  [
    /graphrecursionerror|recursion limit/,
    "执行步骤达到上限：可以重试，或把任务拆小。",
  ],
  [
    /Error:/,
    "工具调用失败。可以在详情里查看原始错误，或重试。",
  ],
];

/** Turn a Run.error string such as "ModelRateLimitError: ..." into a short hint. */
export function describeRunError(raw: string | null | undefined): string {
  const text = (raw ?? "").trim();
  if (!text) return "Run 执行失败。";
  for (const [pattern, message] of RUN_ERROR_RULES) {
    if (pattern.test(text)) return message;
  }
  return text;
}

/** Add status-aware wording for API failures; keep the original detail otherwise. */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "网络连接失败：请检查网络后重试。";
    if (error.status === 401) return "登录已过期：请重新登录。";
    if (error.status === 404) return "请求的资源不存在，可能已被删除。";
    if (error.status === 409) return "操作冲突：这个会话还有进行中的 Run，请等待完成后再试。";
    if (error.status === 413) return "附件超过 10 MB：请压缩后再上传。";
    if (error.status >= 500) return "服务端暂时不可用：请稍后重试。";
  }
  return errorText(error);
}
