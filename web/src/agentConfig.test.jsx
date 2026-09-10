import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentConfigEditor, parseConfigDraft } from "./configEditor.jsx";

afterEach(cleanup);
function setup(onSave = vi.fn().mockResolvedValue(true)) {
  const onChange = vi.fn();
  render(<AgentConfigEditor draft="" onChange={onChange} onSave={onSave} readOnly={false} />);
  return { onSave, onChange };
}

test("list opens isolated drafts and cancellation discards edits and new agents", () => {
  const { onSave, onChange } = setup();
  expect(screen.queryByLabelText("ID")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "不保存" } });
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
  expect(screen.getByLabelText("名称")).toHaveValue("默认助手");
  fireEvent.click(screen.getByRole("button", { name: "关闭 Agent 配置" }));
  fireEvent.click(screen.getByRole("button", { name: "新增" }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.getAllByRole("button", { name: /配置 Agent/ })).toHaveLength(1);
  expect(onSave).not.toHaveBeenCalled();
  expect(onChange).not.toHaveBeenCalled();
});

test("save failures keep the draft and allow retry; saving prevents duplicate submit", async () => {
  let complete;
  const onSave = vi.fn().mockRejectedValueOnce(new Error("ID 已存在")).mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
  setup(onSave);
  fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
  const input = screen.getByLabelText("ID");
  input.focus();
  fireEvent.change(input, { target: { value: "changed" } });
  expect(screen.getByLabelText("ID")).toBe(input);
  expect(input).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ID 已存在");
  expect(input).toHaveValue("changed");
  fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));
  expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  complete(true);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(onSave).toHaveBeenCalledTimes(2);
  expect(parseConfigDraft(onSave.mock.calls[1][0]).config.agents.definitions[0].id).toBe("changed");
});

test("new Agent is only added through successful modal save", async () => {
  const { onSave } = setup();
  fireEvent.click(screen.getByRole("button", { name: "新增" }));
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "研究助手" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const agents = parseConfigDraft(onSave.mock.calls[0][0]).config.agents.definitions;
  expect(agents).toHaveLength(2);
  expect(agents[1].name).toBe("研究助手");
  expect(agents[1].webdav.permission).toBe("write");
});
