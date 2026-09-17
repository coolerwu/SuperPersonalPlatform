import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SelectMenu } from "./selectMenu.jsx";

afterEach(cleanup);

const OPTIONS = [
  { value: "default", label: "默认助手", hint: "default" },
  { value: "wife-agent", label: "wife-agent", hint: "wife-agent" },
  { value: "elaine-agent", label: "elaine-agent", hint: "elaine-agent" },
];

function setup(onChange = vi.fn()) {
  render(<SelectMenu ariaLabel="选择 Agent" value="wife-agent" options={OPTIONS} onChange={onChange} />);
  return { onChange };
}

test("renders the current value and a themed listbox instead of a native select", () => {
  setup();

  const trigger = screen.getByRole("button", { name: "选择 Agent" });
  expect(trigger).toHaveTextContent("wife-agent");
  expect(document.querySelector("select")).not.toBeInTheDocument();

  fireEvent.click(trigger);

  expect(screen.getByRole("listbox", { name: "选择 Agent" })).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(3);
  expect(screen.getByRole("option", { name: /默认助手/ })).toBeInTheDocument();
});

test("selects an option with the pointer and closes the list", () => {
  const { onChange } = setup();

  fireEvent.click(screen.getByRole("button", { name: "选择 Agent" }));
  fireEvent.click(screen.getByRole("option", { name: /elaine-agent/ }));

  expect(onChange).toHaveBeenCalledWith("elaine-agent");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("keeps keyboard navigation and escape working", () => {
  const { onChange } = setup();
  const trigger = screen.getByRole("button", { name: "选择 Agent" });

  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  // Opens on the selected entry, then ArrowUp moves to the first option.
  fireEvent.keyDown(trigger, { key: "ArrowUp" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(onChange).toHaveBeenCalledWith("default");

  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("does not change value when choosing the same option", () => {
  const { onChange } = setup();

  fireEvent.click(screen.getByRole("button", { name: "选择 Agent" }));
  fireEvent.click(screen.getByRole("option", { name: /wife-agent/ }));

  expect(onChange).not.toHaveBeenCalled();
});
