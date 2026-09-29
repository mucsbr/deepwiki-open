/** Bottom-following is user intent, not a side effect of a resize scroll event. */
export function createScrollFollower(view: HTMLElement, content: HTMLElement, onFollowing: (value: boolean) => void) {
  const epsilon = 2; // Only tolerate fractional-pixel rounding, not unread lines.
  const measure = () => ({ top: view.scrollTop, height: view.scrollHeight, viewport: view.clientHeight });
  let last = measure();
  let following = true;
  let frame: number | null = null;
  let touchY: number | undefined;
  let disposed = false;

  function setFollowing(value: boolean) {
    if (following === value) return;
    following = value;
    onFollowing(value);
  }

  function pause() {
    setFollowing(false);
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }

  function pin() {
    view.scrollTo({ top: Math.max(0, view.scrollHeight - view.clientHeight), behavior: 'instant' });
    last = measure();
  }

  function schedulePin() {
    if (disposed || !following || frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!disposed && following) pin();
    });
  }

  function onScroll() {
    const next = measure();
    const resized = next.height !== last.height || next.viewport !== last.viewport;
    const atBottom = next.height - next.viewport - next.top <= epsilon;
    if (atBottom) {
      setFollowing(true);
    } else if (!resized && Math.abs(next.top - last.top) > epsilon) {
      // Includes dragging the scrollbar and native keyboard navigation.
      pause();
    } else if (resized && following) {
      // Content/viewport reflow is not the user asking to stop following.
      schedulePin();
    }
    last = next;
  }

  function nestedScrollerConsumes(target: EventTarget | null, delta: number) {
    for (let node = target instanceof Element ? target : null; node && node !== view; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) {
        if (delta < 0 ? node.scrollTop > 0 : node.scrollTop + node.clientHeight < node.scrollHeight - epsilon) return true;
      }
    }
    return false;
  }

  function onWheel(event: WheelEvent) {
    if (!event.defaultPrevented && !event.ctrlKey && event.deltaY < 0 && !nestedScrollerConsumes(event.target, event.deltaY)) pause();
  }

  function onTouchStart(event: TouchEvent) { touchY = event.touches[0]?.clientY; }
  function onTouchMove(event: TouchEvent) {
    const y = event.touches[0]?.clientY;
    if (y !== undefined && touchY !== undefined && y > touchY && !nestedScrollerConsumes(event.target, touchY - y)) pause();
    touchY = y;
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.defaultPrevented || (event.target instanceof Element && event.target.closest('input, textarea, [contenteditable="true"]'))) return;
    if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) {
      if (!nestedScrollerConsumes(event.target, -1)) pause();
    }
  }

  function onPointerDown(event: PointerEvent) {
    // Scrollbar/track dragging must cancel any already scheduled auto-scroll.
    if (event.target === view && view.scrollHeight > view.clientHeight + epsilon) pause();
  }

  const observer = new ResizeObserver(() => {
    if (following) schedulePin();
    else last = measure();
  });
  observer.observe(view);
  observer.observe(content);
  view.addEventListener('scroll', onScroll, { passive: true });
  view.addEventListener('wheel', onWheel, { passive: true });
  view.addEventListener('touchstart', onTouchStart, { passive: true });
  view.addEventListener('touchmove', onTouchMove, { passive: true });
  view.addEventListener('keydown', onKeyDown);
  view.addEventListener('pointerdown', onPointerDown);
  schedulePin();

  return {
    jump(reveal = true) {
      setFollowing(true);
      pin();
      if (reveal) {
        // The transcript and the page are separate scroll containers. Reveal the
        // actual tail in ancestor viewports only after an explicit user request.
        content.querySelector('[data-chat-scroll-end]')?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
        pin();
      }
      schedulePin();
    },
    dispose() {
      disposed = true;
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
      view.removeEventListener('scroll', onScroll);
      view.removeEventListener('wheel', onWheel);
      view.removeEventListener('touchstart', onTouchStart);
      view.removeEventListener('touchmove', onTouchMove);
      view.removeEventListener('keydown', onKeyDown);
      view.removeEventListener('pointerdown', onPointerDown);
    },
  };
}
