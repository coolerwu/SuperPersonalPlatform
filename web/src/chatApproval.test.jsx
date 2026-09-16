import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ApprovalPanel } from "./chatComponents.jsx";

afterEach(cleanup);

function approvalFor(action) {
  return { interrupts: [{ interrupt_id: "interrupt-1", actions: [action] }] };
}

test("system prompt approval shows the current and proposed prompt instead of raw args", () => {
  const description = [
    "Agent「Assistant（assistant）」申请修改本 Agent 的系统提示词；批准后将在下一次运行生效。",
    "",
    "拟修改为（新）：",
    "你是新人格。",
    "",
    "当前（旧）：",
    "Be direct.",
  ].join("\n");

  render(
    <ApprovalPanel
      approval={approvalFor({
        name: "update_system_prompt",
        args: { new_prompt: "你是新人格。", reason: "用户要求" },
        description,
        allowed_decisions: ["approve", "reject"],
      })}
      onDecision={() => {}}
    />
  );

  const review = document.querySelector(".approval-prompt-review");
  expect(review).toBeInTheDocument();
  expect(review).toHaveTextContent("拟修改为（新）：");
  expect(review).toHaveTextContent("你是新人格。");
  expect(review).toHaveTextContent("当前（旧）：");
  expect(review).toHaveTextContent("Be direct.");
  expect(screen.queryByText(/"new_prompt"/)).not.toBeInTheDocument();
  expect(screen.getByText("修改本 Agent 的系统提示词，批准后从下一次运行生效")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /仅批准本次/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /批准当前文件/ })).not.toBeInTheDocument();
});

test("other tools keep the raw argument block and no prompt comparison", () => {
  render(
    <ApprovalPanel
      approval={approvalFor({
        name: "write_file",
        args: { file_path: "/webdav/team/note.md" },
        description: "写入团队文档",
        allowed_decisions: ["approve", "reject"],
      })}
      onDecision={() => {}}
    />
  );

  expect(document.querySelector(".approval-prompt-review")).not.toBeInTheDocument();
  expect(document.querySelector(".approval-action pre")).toHaveTextContent("/webdav/team/note.md");
  expect(screen.getByText("写入团队文档")).toBeInTheDocument();
});
