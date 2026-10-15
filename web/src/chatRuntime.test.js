import { describe, expect, test } from "vitest";

import { runEventThinkingText } from "./chatRuntime.js";

describe("run event thinking text", () => {
  test("keeps graph updates that carry message content", () => {
    expect(
      runEventThinkingText({ type: "agent_update", payload: { nodes: ["model"], preview: "正在搜索资料" } }),
    ).toBe("正在搜索资料");
  });

  test("drops graph updates without message content", () => {
    expect(
      runEventThinkingText({
        type: "agent_update",
        payload: { nodes: ["HumanInTheLoopMiddleware.after_model"] },
      }),
    ).toBe("");
    expect(
      runEventThinkingText({
        type: "agent_update",
        payload: { nodes: ["PatchToolCallsMiddleware.before_agent", "SkillsMiddleware.before_agent"] },
      }),
    ).toBe("");
    expect(runEventThinkingText({ type: "agent_update", payload: {} })).toBe("");
  });

  test("maps the remaining public run events", () => {
    expect(runEventThinkingText({ type: "running", payload: {} })).toBe("DeepAgent 已开始处理");
    expect(
      runEventThinkingText({
        type: "subagent_response",
        payload: { agent: "researcher", content: "已完成资料核对" },
      }),
    ).toBe("子 Agent researcher：已完成资料核对");
    expect(
      runEventThinkingText({
        type: "approval_required",
        payload: { request: { interrupts: [{ actions: [{ name: "write_file" }] }] } },
      }),
    ).toBe("等待审批：write_file");
    expect(runEventThinkingText({ type: "assistant_delta", payload: { delta: "正文" } })).toBe("");
  });
});
