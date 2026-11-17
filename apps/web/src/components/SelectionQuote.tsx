import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

type SelectionQuoteProps = {
  scrollRef: RefObject<HTMLDivElement | null>;
  onQuote: (text: string) => void;
};

type QuoteCandidate = {
  text: string;
  top: number;
  left: number;
};

const BUTTON_WIDTH = 60;
const BUTTON_HEIGHT = 30;
const GAP = 8;
const EDGE = 8;

function closestQuoteable(node: Node | null): HTMLElement | null {
  if (!node) return null;
  const element =
    node.nodeType === Node.ELEMENT_NODE
      ? (node as Element)
      : node.parentElement;
  return element?.closest<HTMLElement>("[data-quoteable]") ?? null;
}

export function SelectionQuote({ scrollRef, onQuote }: SelectionQuoteProps) {
  const [candidate, setCandidate] = useState<QuoteCandidate | null>(null);
  const candidateRef = useRef<QuoteCandidate | null>(null);
  const frameRef = useRef<number | null>(null);
  const draggingRef = useRef(false);

  const clear = useCallback(() => {
    candidateRef.current = null;
    setCandidate(null);
  }, []);

  const evaluate = useCallback(() => {
    // Mouse drags only offer quoting after the button is released.
    if (draggingRef.current) {
      clear();
      return;
    }
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      clear();
      return;
    }
    try {
      const range = selection.getRangeAt(0);
      const startBubble = closestQuoteable(range.startContainer);
      const endBubble = closestQuoteable(range.endContainer);
      if (!startBubble || startBubble !== endBubble) {
        clear();
        return;
      }
      const text = range.toString().trim();
      if (!text) {
        clear();
        return;
      }
      const rects = range.getClientRects();
      const anchor = rects[rects.length - 1] ?? range.getBoundingClientRect();
      if (anchor.width === 0 && anchor.height === 0) {
        clear();
        return;
      }
      const left = Math.min(
        Math.max(anchor.left + anchor.width / 2 - BUTTON_WIDTH / 2, EDGE),
        window.innerWidth - BUTTON_WIDTH - EDGE,
      );
      const top =
        anchor.top - BUTTON_HEIGHT - GAP >= EDGE
          ? anchor.top - BUTTON_HEIGHT - GAP
          : anchor.bottom + GAP;
      const next = { text, top, left };
      candidateRef.current = next;
      setCandidate(next);
    } catch {
      clear();
    }
  }, [clear]);

  const schedule = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null;
      evaluate();
    });
  }, [evaluate]);

  useEffect(() => {
    const scroll = scrollRef.current;
    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse") draggingRef.current = true;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (event.pointerType !== "mouse") return;
      draggingRef.current = false;
      schedule();
    };
    document.addEventListener("selectionchange", schedule);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("touchend", schedule);
    window.addEventListener("resize", clear);
    scroll?.addEventListener("scroll", clear, { passive: true });
    return () => {
      document.removeEventListener("selectionchange", schedule);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("pointerup", onPointerUp);
      document.removeEventListener("touchend", schedule);
      window.removeEventListener("resize", clear);
      scroll?.removeEventListener("scroll", clear);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [scrollRef, schedule, clear]);

  if (!candidate) return null;

  function quote() {
    const text = candidateRef.current?.text;
    window.getSelection()?.removeAllRanges();
    clear();
    if (text) onQuote(text);
  }

  return (
    <button
      type="button"
      className="selection-quote"
      style={{ top: candidate.top, left: candidate.left }}
      aria-label="引用选中内容"
      title="引用选中内容"
      onMouseDown={(event) => event.preventDefault()}
      onClick={quote}
    >
      引用
    </button>
  );
}
