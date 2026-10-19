import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";

export type SelectOption = {
  value: string;
  label: string;
  description?: string;
};

type SelectProps = {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
};

export function Select({
  value,
  options,
  onChange,
  placeholder = "Select…",
  disabled = false,
  ariaLabel,
  className = "",
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const id = useId();
  const selected = options.find((option) => option.value === value);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));

  function updatePosition() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const menuHeight = Math.min(264, Math.max(44, options.length * 54 + 8));
    const spaceBelow = window.innerHeight - rect.bottom;
    const next: CSSProperties = {
      position: "fixed",
      left: rect.left,
      width: rect.width,
      zIndex: 100,
      maxHeight: 264,
    };
    if (spaceBelow < menuHeight + 8 && rect.top > menuHeight + 8) {
      next.bottom = window.innerHeight - rect.top + 6;
    } else {
      next.top = rect.bottom + 6;
    }
    setMenuStyle(next);
  }

  function openMenu() {
    if (disabled) return;
    updatePosition();
    setActiveIndex(selectedIndex);
    setOpen(true);
  }

  function closeMenu() {
    setOpen(false);
    setActiveIndex(-1);
  }

  function choose(index: number) {
    const option = options[index];
    if (!option) return;
    onChange(option.value);
    closeMenu();
    triggerRef.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      closeMenu();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
        triggerRef.current?.focus();
        return;
      }
      if (event.key === "Tab") {
        closeMenu();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((current) => Math.min(options.length - 1, current + 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((current) => Math.max(0, current - 1));
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        setActiveIndex(0);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        setActiveIndex(options.length - 1);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        choose(activeIndex);
      }
    };
    const onViewportChange = () => updatePosition();
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeIndex, options, onChange]);

  function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMenu();
    }
  }

  return (
    <div className={`select ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className="select-trigger"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-label={ariaLabel}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        <span className={`select-value ${selected ? "" : "placeholder"}`}>
          {selected?.label ?? placeholder}
        </span>
        <span className="select-chevron" aria-hidden="true">⌄</span>
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={`${id}-listbox`}
            className="select-menu"
            role="listbox"
            aria-label={ariaLabel}
            style={menuStyle}
          >
            {options.length === 0 && <div className="select-empty">No options</div>}
            {options.map((option, index) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                className={`select-option ${index === activeIndex ? "active" : ""} ${option.value === value ? "selected" : ""}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(index)}
              >
                <span className="select-option-main">
                  <span className="select-option-label">{option.label}</span>
                  {option.description && (
                    <span className="select-option-description">{option.description}</span>
                  )}
                </span>
                {option.value === value && <span className="select-check" aria-hidden="true">✓</span>}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
