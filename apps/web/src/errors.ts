import { ApiError, errorText } from "./api";

/** Add status-aware wording for API failures; keep the original detail otherwise. */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "网络连接失败：请检查网络后重试。";
    if (error.status === 401) return "登录已过期：请重新登录。";
    if (error.status === 404) return "请求的资源不存在，可能已被删除。";
    if (error.status === 409) {
      // The backend returns actionable Chinese details for conflicts (Agent
      // paused/archived, session owned by another Agent, deleting, attachment
      // restrictions). Show that instead of hiding it behind a generic text.
      const detail = errorText(error);
      if (detail && detail !== error.message) return detail;
      return "操作冲突：这个会话还有进行中的任务，请等待完成后再试。";
    }
    if (error.status === 413) return "附件超过 10 MB：请压缩后再上传。";
    if (error.status >= 500) return "服务端暂时不可用：请稍后重试。";
  }
  return errorText(error);
}
