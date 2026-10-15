export function runEventThinkingText(event) {
  const payload = event?.payload || {};
  if (event?.type === "running") {
    return payload.message || "DeepAgent 已开始处理";
  }
  if (event?.type === "agent_update") {
    if (payload.preview) return payload.preview;
    const nodes = Array.isArray(payload.nodes) ? payload.nodes.filter(Boolean).join(", ") : "";
    return nodes ? `图节点更新：${nodes}` : "DeepAgent 状态已更新";
  }
  if (event?.type === "subagent_response") {
    const label = String(payload.agent || "sub-agent");
    const content = String(payload.content || "").trim();
    const preview = content.length > 1_000 ? `${content.slice(0, 1_000)}...` : content;
    return preview ? `子 Agent ${label}：${preview}` : `子 Agent ${label} 已完成`;
  }
  if (event?.type === "stream_fallback") {
    return payload.message || "当前运行时不支持增量流，已切换为最终结果模式";
  }
  if (event?.type === "image_attachments_textified") {
    return payload.message || "图片已转为文本附件说明";
  }
  if (event?.type === "approval_required") {
    const names = (payload.request?.interrupts || [])
      .flatMap((interrupt) => interrupt.actions || [])
      .map((action) => action.name)
      .filter(Boolean);
    return names.length ? `等待审批：${names.join(", ")}` : "等待用户审批";
  }
  if (event?.type === "approval_resolved") {
    return payload.decision === "reject" ? "操作已拒绝，DeepAgent 继续处理" : "操作已批准，DeepAgent 继续运行";
  }
  return "";
}
