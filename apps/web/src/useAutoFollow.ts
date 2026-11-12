import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

const BOTTOM_THRESHOLD = 96;
const INTENT_WINDOW_MS = 600;
const SHRINK_WINDOW_MS = 250;

function isScrollKey(key: string): boolean {
  return ["ArrowUp", "PageUp", "Home", " "].includes(key);
}

/**
 * Pins a chat scroll container to the newest content unless the user scrolls
 * away. Scroll events caused by content growth or programmatic scrolling never
 * release the pin; only real user input does.
 */
export function useAutoFollow(
  scrollRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
) {
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const intentUntilRef = useRef(0);
  const shrinkUntilRef = useRef(0);
  const lastHeightRef = useRef(0);
  const lastTopRef = useRef(0);

  const setStick = useCallback((next: boolean) => {
    if (stickRef.current === next) return;
    stickRef.current = next;
    setAtBottom(next);
  }, []);

  const scrollToBottom = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    lastTopRef.current = element.scrollTop;
  }, [scrollRef]);

  const follow = useCallback(() => {
    setStick(true);
    scrollToBottom();
    window.requestAnimationFrame(() => {
      scrollToBottom();
      window.requestAnimationFrame(scrollToBottom);
    });
  }, [scrollToBottom, setStick]);

  const pauseFollow = useCallback(() => {
    setStick(false);
  }, [setStick]);

  const followNow = useCallback(() => {
    if (!stickRef.current) return;
    scrollToBottom();
    // The virtualizer may adjust scrollTop after re-measuring rows; one more
    // pinned pass keeps the newest content in view.
    window.requestAnimationFrame(() => {
      if (stickRef.current) scrollToBottom();
    });
  }, [scrollToBottom]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;

    const markIntent = () => {
      intentUntilRef.current = performance.now() + INTENT_WINDOW_MS;
    };
    const onScroll = () => {
      const distance =
        element.scrollHeight - element.scrollTop - element.clientHeight;
      const top = element.scrollTop;
      const delta = top - lastTopRef.current;
      lastTopRef.current = top;
      if (distance < BOTTOM_THRESHOLD) {
        setStick(true);
        return;
      }
      if (performance.now() < shrinkUntilRef.current) return;
      if (performance.now() < intentUntilRef.current || delta < -8) {
        setStick(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      if (isScrollKey(event.key)) markIntent();
    };

    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("wheel", markIntent, { passive: true });
    element.addEventListener("touchmove", markIntent, { passive: true });
    element.addEventListener("pointerdown", markIntent);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      element.removeEventListener("scroll", onScroll);
      element.removeEventListener("wheel", markIntent);
      element.removeEventListener("touchmove", markIntent);
      element.removeEventListener("pointerdown", markIntent);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [scrollRef, setStick]);

  useEffect(() => {
    const content = contentRef.current;
    const element = scrollRef.current;
    if (!content || !element) return;
    const observer = new ResizeObserver(() => {
      const height = element.scrollHeight;
      if (height < lastHeightRef.current) {
        shrinkUntilRef.current = performance.now() + SHRINK_WINDOW_MS;
      }
      lastHeightRef.current = height;
      followNow();
    });
    observer.observe(content);
    observer.observe(element);
    return () => observer.disconnect();
  }, [contentRef, scrollRef, followNow]);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const onResize = () => followNow();
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, [followNow]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") followNow();
    };
    document.addEventListener("visibilitychange", onVisible);
    followNow();
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [followNow]);

  return { atBottom, follow, pauseFollow };
}
