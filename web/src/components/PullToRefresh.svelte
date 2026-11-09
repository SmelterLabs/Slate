<script lang="ts">
  // Pull to refresh, as in every phone app: at the top of a page or column, pull down and let go
  // to load everything again (board.refresh). The installed iPhone app has no browser reload
  // button, so this is how a finger gets what F5 or Cmd+R give a keyboard. Only a finger can
  // pull; a held finger is a drag (lib/touchdrag.ts) and a sideways move scrolls the board.
  import { onMount } from 'svelte';
  import { board } from '../lib/store.svelte';
  import { haptic } from '../lib/touchdrag';
  import { axisOf } from '../lib/swipe';
  import Icon from './Icon.svelte';

  const TRIGGER = 72;
  let pull = $state(0);
  let busy = $state(false);

  /** True when nothing between the finger and the page can scroll up any further. */
  function atTop(el: Element | null): boolean {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (n.scrollTop > 0) return false;
    }
    return (document.scrollingElement?.scrollTop ?? 0) <= 0;
  }

  onMount(() => {
    let x0 = 0;
    let y0 = 0;
    let tracking = false;
    let pulling = false;
    let armed = false;

    const start = (e: TouchEvent) => {
      const t = e.target as Element;
      tracking =
        e.touches.length === 1 &&
        !busy &&
        !!t.closest('.main') &&
        !t.closest('.tw, .dd-panel, .menu-pop, input, textarea, select, [contenteditable="true"]') &&
        atTop(t);
      pulling = false;
      armed = false;
      x0 = e.touches[0]?.clientX ?? 0;
      y0 = e.touches[0]?.clientY ?? 0;
    };
    const move = (e: TouchEvent) => {
      if (!tracking) return;
      if (document.documentElement.classList.contains('touch-dragging')) {
        tracking = false;
        pull = 0;
        return;
      }
      const t = e.touches[0];
      const dx = t.clientX - x0;
      const dy = t.clientY - y0;
      if (!pulling) {
        const axis = axisOf(dx, dy);
        if (axis === null) return;
        if (axis !== 'y' || dy <= 0) {
          tracking = false;
          return;
        }
        pulling = true;
      }
      if (e.cancelable) e.preventDefault(); // our spinner instead of the rubber band
      pull = Math.max(0, Math.min(TRIGGER * 1.6, dy * 0.55));
      if (pull >= TRIGGER !== armed) {
        armed = pull >= TRIGGER;
        if (armed) haptic();
      }
    };
    const end = async () => {
      if (!tracking) return;
      tracking = false;
      if (!pulling) return;
      if (!armed) {
        pull = 0;
        return;
      }
      busy = true;
      pull = TRIGGER;
      try {
        await board.refresh();
      } finally {
        busy = false;
        pull = 0;
      }
    };
    document.addEventListener('touchstart', start, { passive: true });
    document.addEventListener('touchmove', move, { passive: false });
    document.addEventListener('touchend', end);
    document.addEventListener('touchcancel', end);
    return () => {
      document.removeEventListener('touchstart', start);
      document.removeEventListener('touchmove', move);
      document.removeEventListener('touchend', end);
      document.removeEventListener('touchcancel', end);
    };
  });
</script>

{#if pull > 0 || busy}
  <div
    class="ptr"
    class:armed={pull >= TRIGGER}
    class:busy
    role="status"
    aria-label={busy ? 'Refreshing' : pull >= TRIGGER ? 'Release to refresh' : 'Pull to refresh'}
    style:translate="-50% {Math.min(pull, TRIGGER) - 40}px"
  >
    <span class="ptr-icon" style:rotate="{busy ? 0 : (pull / TRIGGER) * 300}deg"><Icon name="refresh" size={18} /></span>
  </div>
{/if}

<style>
  .ptr {
    position: fixed;
    z-index: 50;
    top: calc(env(safe-area-inset-top) + 8px);
    left: 50%;
    width: 38px;
    height: 38px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--panel);
    color: var(--muted);
    border: 1px solid var(--line);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.25);
    pointer-events: none;
  }
  .ptr.armed,
  .ptr.busy {
    color: var(--accent);
  }
  .ptr-icon {
    display: grid;
  }
  .ptr.busy .ptr-icon {
    animation: ptr-spin 0.8s linear infinite;
  }
  @keyframes ptr-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
