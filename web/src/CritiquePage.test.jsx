import "@testing-library/jest-dom/vitest";
import { act } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CritiquePage } from "./CritiquePage.jsx";


const disciplines = [
  {
    id: "d-economics",
    name: "经济学",
    known_scope: "微观决策与机会成本",
    critique_focus: "成本、激励和替代方案",
    default_enabled: true,
    created_at: "2026-06-21T00:00:00Z",
    updated_at: "2026-06-21T00:00:00Z"
  },
  {
    id: "d-psychology",
    name: "心理学",
    known_scope: "认知偏差与动机",
    critique_focus: "自我欺骗和逃避行为",
    default_enabled: false,
    created_at: "2026-06-21T00:00:00Z",
    updated_at: "2026-06-21T00:00:00Z"
  }
];


function installApiMock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path, options) => {
      if (path === "/api/critique/disciplines" && options?.method === "POST") {
        const payload = JSON.parse(options.body);
        return {
          ok: true,
          json: async () => ({
            discipline: {
              id: "d-philosophy",
              ...payload,
              created_at: "2026-06-21T00:00:00Z",
              updated_at: "2026-06-21T00:00:00Z"
            }
          })
        };
      }
      if (path === "/api/critique/disciplines") {
        return { ok: true, json: async () => ({ disciplines }) };
      }
      if (path === "/api/critique/runs") {
        return { ok: true, json: async () => ({ runs: [] }) };
      }
      return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
    })
  );
}


function installWebSocketMock() {
  const sockets = [];
  class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;

    constructor(url) {
      this.url = url;
      this.readyState = MockWebSocket.CONNECTING;
      this.sent = [];
      sockets.push(this);
      setTimeout(() => {
        this.readyState = MockWebSocket.OPEN;
        this.onopen?.();
        this.onmessage?.({ data: JSON.stringify({ type: "status", status: "connected" }) });
      }, 0);
    }

    send(data) {
      const payload = JSON.parse(data);
      this.sent.push(payload);
      if (payload.type !== "run") return;
      const result = {
        discipline_id: "d-economics",
        discipline_name: "经济学",
        status: "completed",
        analysis: {
          core_assumption: "需求真实存在",
          counterevidence: "没有付费数据",
          opportunity_cost: "放弃稳定收入",
          key_question: "谁会持续付费？"
        },
        error: ""
      };
      const run = {
        id: "r-1",
        question: payload.question,
        model_id: "fast",
        disciplines: [disciplines[0]],
        results: [result],
        judgment: {
          weakest_assumption: "没有付费证据",
          largest_disagreement: "动机与收益解释冲突",
          recommended_validation: "先验证十个付费用户"
        },
        status: "completed",
        created_at: "2026-06-21T00:00:00Z",
        updated_at: "2026-06-21T00:01:00Z"
      };
      [
        { type: "run_started", run_id: "r-1" },
        { type: "discipline_status", run_id: "r-1", discipline_id: "d-economics", status: "running" },
        { type: "discipline_status", run_id: "r-1", discipline_id: "d-economics", status: "completed", result },
        { type: "judgment_status", run_id: "r-1", status: "completed", judgment: run.judgment },
        { type: "run_completed", run }
      ].forEach((event) => this.onmessage?.({ data: JSON.stringify(event) }));
    }

    close() {
      this.readyState = MockWebSocket.CLOSED;
      this.onclose?.();
    }
  }
  vi.stubGlobal("WebSocket", MockWebSocket);
  return sockets;
}


describe("CritiquePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("runs default disciplines and renders the matrix plus judgment", async () => {
    installApiMock();
    const sockets = installWebSocketMock();
    const user = userEvent.setup();
    render(<CritiquePage onUnauthorized={vi.fn()} />);

    expect(await screen.findByRole("columnheader", { name: "核心假设" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "反证" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "机会成本" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "关键追问" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /经济学/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /心理学/ })).not.toBeChecked();

    await user.type(screen.getByPlaceholderText("输入你想被质疑的问题..."), "我是否应该辞职？");
    await user.click(screen.getByRole("button", { name: "开始压榨" }));

    expect(sockets[0].sent[0]).toEqual({
      type: "run",
      question: "我是否应该辞职？",
      discipline_ids: ["d-economics"]
    });
    expect(await screen.findByText("需求真实存在")).toBeInTheDocument();
    expect(screen.getByText("没有付费证据")).toBeInTheDocument();
    expect(screen.getByText("先验证十个付费用户")).toBeInTheDocument();
  });

  it("adds a discipline with the user's known scope and critique focus", async () => {
    installApiMock();
    installWebSocketMock();
    const user = userEvent.setup();
    render(<CritiquePage onUnauthorized={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "添加学科" }));
    await user.type(screen.getByLabelText("学科名称"), "哲学");
    await user.type(screen.getByLabelText("我了解的范围"), "伦理与价值判断");
    await user.type(screen.getByLabelText("重点批判方向"), "价值一致性");
    fireEvent.click(screen.getByRole("checkbox", { name: "默认参与" }));
    await user.click(screen.getByRole("button", { name: "保存学科" }));

    await waitFor(() => expect(screen.getByText("哲学")).toBeInTheDocument());
    const saveCall = fetch.mock.calls.find(
      ([path, options]) => path === "/api/critique/disciplines" && options?.method === "POST"
    );
    expect(JSON.parse(saveCall[1].body)).toEqual({
      name: "哲学",
      known_scope: "伦理与价值判断",
      critique_focus: "价值一致性",
      default_enabled: false
    });
  });
});
