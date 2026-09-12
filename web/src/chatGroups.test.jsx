import React from "react";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ChatGroupsPage } from "./chatGroups.jsx";
import { ChatComposer } from "./chatComponents.jsx";

afterEach(cleanup);
const config = "agents:\n  definitions:\n    - id: assistant\n      name: 基础助手\n";
const base = () => ({ id: "group_one", name: "设计讨论", archived: false, members: [
  { id: "host", name: "主持", agent_id: "assistant", prompt: "组织讨论" },
  { id: "review", name: "评审", agent_id: "assistant", prompt: "检查问题" },
], host_member_id: "host", messages: [], executions: [] });

function mockApi(group = base()) {
  return vi.fn(async (url, options) => {
    if (url === "/api/workspace/read") return { content: config };
    if (url === "/api/chat-groups" && !options) return { groups: [{ id: group.id, name: group.name }] };
    if (options?.method === "PUT") return { ...group, ...JSON.parse(options.body) };
    return group;
  });
}

test("shared composer preserves IME and shift-enter, prevents sends while busy", () => {
  const send = vi.fn();
  const { rerender } = render(<ChatComposer value="你好" onChange={() => {}} onSend={send} />);
  const input = screen.getByRole("textbox");
  fireEvent.compositionStart(input);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).toHaveBeenCalledTimes(1);
  rerender(<ChatComposer value="你好" onChange={() => {}} onSend={send} busy />);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(send).toHaveBeenCalledTimes(1);
});

test("group mentions submit member IDs in textual order and default sends have no mentions", async () => {
  const api = mockApi();
  render(<ChatGroupsPage api={api} />);
  const input = await screen.findByPlaceholderText("输入 @ 选择角色，或直接与主持交流");
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(screen.getByRole("option", { name: "评审" }));
  fireEvent.change(input, { target: { value: "@评审 请检查 @" } });
  fireEvent.click(screen.getByRole("option", { name: "主持" }));
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/messages", expect.objectContaining({ method: "POST" })));
  const call = api.mock.calls.find(([url]) => url.endsWith("/messages"));
  expect(JSON.parse(call[1].body).mentions).toEqual(["review", "host"]);
  expect(JSON.parse(call[1].body).client_message_id).toBeTruthy();
  await waitFor(() => expect(input).toHaveValue(""));
  fireEvent.change(input, { target: { value: "请安排工作" } });
  fireEvent.click(screen.getByRole("button", { name: "开始协作" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/collaborations", expect.anything()));
});

test("editing uses PUT and stable member keys preserve typing focus", async () => {
  const api = mockApi();
  render(<ChatGroupsPage api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑群聊" }));
  const names = screen.getAllByLabelText("群内名称");
  await userEvent.type(names[0], "负责人");
  expect(names[0]).toHaveFocus();
  expect(names[0]).toHaveValue("主持负责人");
  fireEvent.click(screen.getByRole("button", { name: "保存群聊" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one", expect.objectContaining({ method: "PUT" })));
  const call = api.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(call[1].body)).not.toHaveProperty("_groupId");
});

test("restored group execution uses shared thinking and approval components and can stop", async () => {
  const group = base();
  group.executions = [{ id: "exec", status: "waiting_approval", round: 1, automatic: true, current_step: { name: "评审" } }];
  group.active_run = { run_id: "run-1", state: { status: "waiting_approval" }, partial: { content: "初步分析", thinking: ["检查共享文档"] }, approval: { status: "pending", request: { interrupts: [{ actions: [{ name: "write_file", args: { path: "/webdav/a.md" } }] }] } } };
  const api = mockApi(group);
  render(<ChatGroupsPage api={api} />);
  expect(await screen.findByText("初步分析")).toBeVisible();
  expect(screen.getByText("检查共享文档")).toBeVisible();
  expect(screen.getByText("等待操作审批")).toBeVisible();
  expect(screen.getByPlaceholderText("输入 @ 选择角色，或直接与主持交流")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "批准并继续" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/runs/run-1/resume", expect.objectContaining({ method: "POST" })));
  await waitFor(() => expect(screen.getByRole("button", { name: "停止" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chat-groups/group_one/collaborations/exec/stop", expect.anything()));
});

test("mention matching does not accidentally target a member with a prefix name", async () => {
  const { mentionPosition } = await import("./chatGroups.jsx");
  expect(mentionPosition("@产品经理 请设计", "@产品")).toBe(-1);
  expect(mentionPosition("@产品经理 @产品 请设计", "@产品")).toBe(6);
});
