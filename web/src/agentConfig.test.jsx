import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentConfigEditor, parseConfigDraft } from "./configEditor.jsx";

afterEach(cleanup);
function setup(onSave = vi.fn().mockResolvedValue(true)) {
  const onChange = vi.fn();
  render(<AgentConfigEditor draft={"nutstore:\n  enabled: true\ncontext:\n  webdav_sync:\n    enabled: true\n"} onChange={onChange} onSave={onSave} readOnly={false} />);
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
  expect(agents[1].webdav.directories).toEqual([]);
});

test("multiple directories keep independent permissions and descriptions", async () => {
  const { onSave } = setup();
  fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
  fireEvent.click(screen.getByLabelText("启用 WebDAV"));
  fireEvent.click(screen.getByRole("button", { name: "添加目录" }));
  fireEvent.change(screen.getByLabelText("映射目录（相对于全局同步目录）"), { target: { value: "/资料" } });
  fireEvent.change(screen.getByLabelText("访问权限"), { target: { value: "read" } });
  fireEvent.click(screen.getByRole("button", { name: "添加目录" }));
  const path = screen.getAllByLabelText("映射目录（相对于全局同步目录）")[1];
  path.focus();
  fireEvent.change(path, { target: { value: "/资料/草稿" } });
  expect(path).toHaveFocus();
  expect(screen.getAllByLabelText("访问权限")[1]).toHaveValue("write");
  fireEvent.change(screen.getAllByLabelText("目录说明")[1], { target: { value: "保存草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));
  await waitFor(() => expect(onSave).toHaveBeenCalled());
  expect(parseConfigDraft(onSave.mock.calls[0][0]).config.agents.definitions[0].webdav.directories).toEqual([
    { path: "/资料", permission: "read", description: "" }, { path: "/资料/草稿", permission: "write", description: "保存草稿" },
  ]);
});

test("directory picker navigates folders and allows manual fallback", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ entries: [{ name: "资料", type: "directory" }, { name: "private.md", type: "file" }] }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ entries: [] }) }).mockResolvedValue({ ok: false }));
  try {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
    fireEvent.click(screen.getByLabelText("启用 WebDAV"));
    fireEvent.click(screen.getByRole("button", { name: "添加目录" }));
    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    fireEvent.click(await screen.findByRole("button", { name: "资料 /" }));
    await screen.findByText("没有已同步的子目录，也可手动填写路径。");
    expect(screen.queryByText("private.md")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择此目录" }));
    expect(screen.getByLabelText("映射目录（相对于全局同步目录）")).toHaveValue("/资料");
    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("可手动填写");
    fireEvent.change(screen.getByLabelText("映射目录（相对于全局同步目录）"), { target: { value: "/未同步目录" } });
    fireEvent.click(screen.getByRole("button", { name: "移除目录 1" }));
    expect(screen.getByText("尚未授权任何目录。")).toBeInTheDocument();
  } finally { vi.unstubAllGlobals(); }
});

test("shows source mapping and WebDAV approval requirement", () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "配置 Agent 默认助手" }));
  expect(screen.getByText("来源：坚果云 · 同步目录 /notebook")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("启用 WebDAV"));
  fireEvent.click(screen.getByRole("button", { name: "添加目录" }));
  fireEvent.change(screen.getByLabelText("映射目录（相对于全局同步目录）"), { target: { value: "/日记" } });
  expect(screen.getByText("坚果云 /notebook/日记 → Agent /webdav/日记")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "读写 · 写入需审批" })).toBeInTheDocument();
});
