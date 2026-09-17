import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

/**
 * Dark console styled replacement for a native <select>.
 *
 * Native select popups follow the OS appearance (light menus on a light macOS),
 * so they cannot be themed with CSS. This renders the list in the DOM instead.
 */
export function SelectMenu({
  value,
  options,
  onChange,
  disabled = false,
  ariaLabel = "选择",
  className = "",
  menuClassName = "",
}) {
  const [open, setOpen] = useState(false);
  const [rect, setRect] = useState(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const selectedIndex = options.findIndex((option) => option.value === value);
  const selected = options.find((option) => option.value === value);

  useLayoutEffect(() => {
    if (!open) return undefined;
    function measure() {
      const node = triggerRef.current;
      if (!node) return;
      const box = node.getBoundingClientRect();
      setRect({ top: box.bottom + 6, left: box.left, minWidth: box.width });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function handlePointerDown(event) {
      if (triggerRef.current?.contains(event.target) || panelRef.current?.contains(event.target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
  }, [open, selectedIndex]);

  function choose(next) {
    setOpen(false);
    if (next !== value) onChange?.(next);
  }

  function handleKeyDown(event) {
    if (disabled) return;
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      setOpen(true);
      return;
    }
    if (!open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        const next = current + step;
        if (next < 0) return options.length - 1;
        if (next >= options.length) return 0;
        return next;
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const option = options[activeIndex];
      if (option) choose(option.value);
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`select-menu-trigger ${className}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleKeyDown}
      >
        <span>{selected?.label ?? (value || "")}</span>
        <ChevronDown size={15} />
      </button>
      {open && rect ? (
        <div
          ref={panelRef}
          className={`select-menu-panel ${menuClassName}`}
          role="listbox"
          aria-label={ariaLabel}
          style={{ top: rect.top, left: rect.left, minWidth: rect.minWidth }}
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={`select-menu-option ${option.value === value ? "selected" : ""} ${index === activeIndex ? "active" : ""}`}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(option.value)}
            >
              <span className="select-menu-option-label">
                <strong>{option.label}</strong>
                {option.hint ? <small>{option.hint}</small> : null}
              </span>
              {option.value === value ? <Check size={15} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </>
  );
}
