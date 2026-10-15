import { describe, expect, test } from "vitest";

import { CHAT_THINKING_LIMIT, mergeChatThinking, runEventThinkingText } from "./chatRuntime.js";

describe("run event thinking text", () => {
  test("names the tools a model step called", () => {
    expect(
      runEventThinkingText({
        type: "agent_update",
        payload: { nodes: ["model"], preview: "我先看一下文件", message_type: "ai", tool_calls: ["read_file", "glob"] },
      }),
    ).toBe("调用工具：read_file, glob");
  });

  test("keeps tool results but drops model text and bare node updates", () => {
    expect(
      runEventThinkingText({
        type: "agent_update",
        payload: { nodes: ["tools"], preview: "文件内容", message_type: "tool" },
      }),
    ).toBe("文件内容");
    expect(
      runEventThinkingText({
        type: "agent_update",
        payload: { nodes: ["model"], preview: "我先看一下文件", message_type: "ai" },
      }),
    ).toBe("");
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

describe("mergeChatThinking", () => {
  test("trims, deduplicates and keeps the shared entry limit", () => {
    expect(mergeChatThinking([], ["", "  调用工具：read_file  ", "调用工具：read_file"])).toEqual([
      "调用工具：read_file",
    ]);
    expect(CHAT_THINKING_LIMIT).toBe(20);
    const merged = mergeChatThinking([], Array.from({ length: 25 }, (_, index) => `工具返回 ${index}`));
    expect(merged).toHaveLength(CHAT_THINKING_LIMIT);
    expect(merged[0]).toBe("工具返回 5");
    expect(merged.at(-1)).toBe("工具返回 24");
  });
});
