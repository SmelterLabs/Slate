/** Conservative exposure: the whole message must be visible for 500ms in a focused tab.
 * Intersection alone is insufficient (a drawer or another pane can cover a message).
 * Oversized messages stay unread until explicitly marked read; no inferred seen receipts.
 */

// IntersectionObserver does not report a scrim disappearing at unchanged geometry.
// Share one event-driven observer while messages are mounted, rather than one body
// observer per message or a permanent polling loop. Batch DOM churn once per frame.
const occlusionChecks = new Set<() => void>();
let occlusionObserver: MutationObserver | undefined;
let occlusionFrame: number | undefined;
function scheduleOcclusionCheck() {
  if (occlusionFrame !== undefined) return;
  occlusionFrame = requestAnimationFrame(() => {
    occlusionFrame = undefined;
    for (const check of occlusionChecks) check();
  });
}
function watchOcclusion(check: () => void) {
  occlusionChecks.add(check);
  if (!occlusionObserver) {
    occlusionObserver = new MutationObserver(scheduleOcclusionCheck);
    occlusionObserver.observe(document.body, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['class', 'style', 'hidden', 'open'],
    });
    document.addEventListener('transitionend', scheduleOcclusionCheck, true);
    document.addEventListener('animationend', scheduleOcclusionCheck, true);
  }
  return () => {
    occlusionChecks.delete(check);
    if (occlusionChecks.size) return;
    occlusionObserver?.disconnect();
    occlusionObserver = undefined;
    if (occlusionFrame !== undefined) cancelAnimationFrame(occlusionFrame);
    occlusionFrame = undefined;
    document.removeEventListener('transitionend', scheduleOcclusionCheck, true);
    document.removeEventListener('animationend', scheduleOcclusionCheck, true);
  };
}

export function exposure(node: HTMLElement, read: () => void) {
  let intersecting = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function eligible() {
    if (!intersecting || !node.isConnected || document.visibilityState !== 'visible' || !document.hasFocus()) return false;
    const r = node.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    return [0.2, 0.8].every(y => node.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height * y)));
  }
  function check() {
    if (!eligible()) {
      clearTimeout(timer);
      timer = undefined;
    } else if (timer === undefined) {
      // Unrelated DOM changes must not continually restart an eligible dwell.
      timer = setTimeout(() => {
        timer = undefined;
        if (eligible()) read();
      }, 500);
    }
  }
  const observer = new IntersectionObserver(([entry]) => {
    intersecting = entry.intersectionRatio >= 0.99;
    check();
  }, { threshold: [0, 0.99, 1] });
  observer.observe(node);
  const unwatchOcclusion = watchOcclusion(check);
  window.addEventListener('focus', check);
  window.addEventListener('blur', check);
  document.addEventListener('visibilitychange', check);
  return { destroy() {
    clearTimeout(timer); observer.disconnect(); unwatchOcclusion();
    window.removeEventListener('focus', check); window.removeEventListener('blur', check);
    document.removeEventListener('visibilitychange', check);
  } };
}
