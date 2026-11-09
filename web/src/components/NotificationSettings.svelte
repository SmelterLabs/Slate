<script lang="ts">
  // Notifications: what reaches you outside the board. This device's phone/desktop push (on,
  // off, test), every device that gets them, the Tell me when grid (a few plain rows of inbox
  // reasons against toast/push and, when the server has SMTP, email), the daily digest
  // (on/off, hour, what it would say today) and your upcoming reminders. Choices are account
  // prefs, so phone and PC show the same; only "this device" is per browser.
  import { onMount } from 'svelte';
  import { api, get } from '../lib/api';
  import { notifySettings } from '../lib/notify.svelte';
  import {
    DEFAULT_EMAIL_REASONS,
    DEFAULT_PUSH_REASONS,
    disablePush,
    enablePush,
    listDevices,
    missedHint,
    NOTIFY_GROUPS,
    pushState,
    PUSH_REASONS,
    testEmail,
    testPush,
    type PushDevice,
    type PushState,
  } from '../lib/push';
  import { digestText, hourLabel, whenText, type DigestCounts, type Reminder } from '../lib/reminders';
  import { board } from '../lib/store.svelte';
  import { ago } from '../lib/format';
  import Icon from './Icon.svelte';

  let pstate = $state<PushState | null>(null);
  let devices = $state<PushDevice[]>([]);
  let upcoming = $state<Reminder[]>([]);
  let digest = $state<{ counts: DigestCounts } | null>(null);
  let busy = $state(false);
  let note = $state<string | null>(null);
  /** After a delivered test: where to look if nothing showed (the OS's own switch). */
  let sent = $state<string | null>(null);

  const reasons = $derived(new Set(board.prefs.push_reasons ?? DEFAULT_PUSH_REASONS));
  const emailOn = $derived(!!board.me?.email);
  const mailTo = $derived(board.prefs.email_to ?? '');
  const mailReasons = $derived(new Set(board.prefs.email_reasons ?? DEFAULT_EMAIL_REASONS));
  const digestOn = $derived(board.prefs.digest ?? board.me?.account.kind !== 'agent');
  const digestHour = $derived(board.prefs.digest_hour ?? 7);
  const renudge = $derived(board.prefs.chat_renudge_minutes ?? 10);
  const RENUDGE_CHOICES: { minutes: number; label: string }[] = [
    { minutes: 0, label: 'Never' },
    { minutes: 5, label: 'after 5 minutes' },
    { minutes: 10, label: 'after 10 minutes' },
    { minutes: 30, label: 'after 30 minutes' },
    { minutes: 60, label: 'after an hour' },
  ];
  const AUTO_WATCH: { key: 'created' | 'assigned' | 'commented'; label: string }[] = [
    { key: 'created', label: 'Tasks I create' },
    { key: 'assigned', label: 'Tasks I am assigned' },
    { key: 'commented', label: 'Tasks I comment on' },
  ];
  // Default on for people, off for agents (service/watchers.py).
  const autoWatch = $derived.by(() => {
    const on = board.me?.account.kind !== 'agent';
    const p = board.prefs.auto_watch ?? {};
    return { created: p.created ?? on, assigned: p.assigned ?? on, commented: p.commented ?? on };
  });
  const HOURS = Array.from({ length: 24 }, (_, h) => h);

  const STATE_TEXT: Record<PushState, string> = {
    on: 'On for this device.',
    off: 'Off for this device.',
    blocked: 'Blocked in this browser’s settings. Allow notifications for Slate there, then come back.',
    'needs-install':
      'On iPhone, notifications work in the installed app: Share, Add to Home Screen, then open Slate from the Home Screen.',
    unsupported: 'This browser cannot show notifications.',
    'server-off': 'The server cannot send notifications yet (it needs SLATE_SECRET_KEY).',
  };

  async function refresh() {
    pstate = await pushState().catch(() => 'unsupported' as PushState);
    devices = await listDevices().catch(() => []);
  }

  onMount(() => {
    void refresh();
    void get<Reminder[]>('/reminders?limit=20').then((r) => (upcoming = r)).catch(() => {});
    void get<{ counts: DigestCounts }>('/me/digest').then((d) => (digest = d)).catch(() => {});
  });

  async function run(fn: () => Promise<unknown>, done?: string) {
    busy = true;
    note = null;
    try {
      await fn();
      if (done) board.flash(done);
    } catch (e) {
      note = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
      await refresh();
    }
  }

  async function test() {
    await run(async () => {
      const out = await testPush();
      const bad = out.filter((d) => !d.ok);
      if (!out.length) throw new Error('No device has notifications on yet');
      if (bad.length) throw new Error(`${bad.map((d) => d.label ?? d.endpoint_host).join(', ')}: ${bad[0].error}`);
      board.flash(`Test sent to ${out.length === 1 ? 'your device' : `${out.length} devices`}`);
      const inApp = window.matchMedia?.('(display-mode: standalone)').matches ?? false;
      sent = `Delivered to ${out.length === 1 ? 'this device' : `${out.length} devices`}. ${missedHint(navigator.userAgent, inApp)}`;
    });
  }

  /** Whether all, some or none of a row's reasons are chosen. */
  function groupState(chosen: Set<string>, keys: string[]): 'all' | 'some' | 'none' {
    const n = keys.filter((k) => chosen.has(k)).length;
    return n === keys.length ? 'all' : n ? 'some' : 'none';
  }

  function togglePush(keys: string[], on: boolean) {
    const next = PUSH_REASONS.map((r) => r.key).filter((k) => (keys.includes(k) ? on : reasons.has(k)));
    void board.setPrefs({ push_reasons: next });
  }

  function toggleMail(keys: string[], on: boolean) {
    const all = NOTIFY_GROUPS.flatMap((g) => g.reasons).filter((k) => k !== 'chat');
    const known = [...new Set([...all, ...mailReasons])];
    void board.setPrefs({ email_reasons: known.filter((k) => (keys.includes(k) ? on : mailReasons.has(k))) });
  }

  async function saveMailTo(value: string) {
    note = null;
    const v = value.trim();
    if (v && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v)) {
      note = 'That does not look like an email address';
      return;
    }
    await board.setPrefs({ email_to: v || null });
  }

  async function mailTest() {
    await run(async () => {
      const out = await testEmail();
      if (!out.ok) throw new Error(out.error ?? 'The email could not be sent');
      board.flash(`Test email sent to ${mailTo}`);
    });
  }

  function close() {
    notifySettings.open = false;
  }
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape') close();
  }}
/>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={close}></button>
<div class="settings notify-settings" role="dialog" aria-label="Notifications" aria-modal="true">
  <div class="drawer-top">
    <h2>Notifications</h2>
    <button class="icon" aria-label="Close" onclick={close}><Icon name="x" size={16} /></button>
  </div>

  <section class="ns-block" aria-label="This device">
    <h3><Icon name="bell" size={15} /> Phone and desktop</h3>
    <p class="ns-state" data-state={pstate ?? 'loading'}>{pstate ? STATE_TEXT[pstate] : 'Checking…'}</p>
    <div class="inline">
      {#if pstate === 'off'}
        <button class="primary" disabled={busy} onclick={() => void run(enablePush, 'Notifications on for this device')}
          >Turn on for this device</button
        >
      {:else if pstate === 'on'}
        <button class="secondary" disabled={busy} onclick={() => void test()}>Send a test</button>
        <button class="link" disabled={busy} onclick={() => void run(disablePush, 'Notifications off for this device')}
          >Turn off here</button
        >
      {/if}
    </div>
    {#if note}<p class="ns-error" role="alert">{note}</p>{/if}
    {#if sent}<p class="ns-sent muted small" data-sent>{sent}</p>{/if}
    {#if devices.length}
      <ul class="ns-devices" aria-label="Devices">
        {#each devices as d (d.id)}
          <li>
            <Icon name="bell" size={12} />
            <span class="grow">{d.label ?? d.endpoint_host}</span>
            <span class="muted small">{d.last_ok_at ? `last sent ${ago(d.last_ok_at, board.now)}` : `added ${ago(d.created_at, board.now)}`}</span>
            <button
              class="icon"
              aria-label="Turn off {d.label ?? d.endpoint_host}"
              onclick={() => void run(() => api('DELETE', `/push/subscriptions/${d.id}`))}><Icon name="x" size={13} /></button
            >
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="ns-block" aria-label="Tell me when">
    <h3>Tell me when</h3>
    <table class="ns-grid">
      <thead>
        <tr>
          <th scope="col"><span class="sr-only">What happens</span></th>
          <th scope="col">Toast / push</th>
          {#if emailOn}<th scope="col">Email</th>{/if}
        </tr>
      </thead>
      <tbody>
        {#each NOTIFY_GROUPS as g (g.key)}
          {@const p = groupState(reasons, g.reasons)}
          {@const mailable = g.reasons.filter((r) => r !== 'chat')}
          {@const m = groupState(mailReasons, mailable)}
          <tr data-group={g.key}>
            <th scope="row">{g.label}</th>
            <td>
              <input
                type="checkbox"
                aria-label="{g.label}: toast / push"
                checked={p === 'all'}
                indeterminate={p === 'some'}
                onchange={(e) => togglePush(g.reasons, e.currentTarget.checked)}
              />
            </td>
            {#if emailOn}
              <td>
                <input
                  type="checkbox"
                  aria-label="{g.label}: email"
                  disabled={!mailTo}
                  checked={m === 'all'}
                  indeterminate={m === 'some'}
                  onchange={(e) => toggleMail(mailable, e.currentTarget.checked)}
                />
              </td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="muted small">
      Everything but plain chat lands in your Inbox and the bell at the top right either way.{#if emailOn}
        An email goes out only if you still have not seen it in Slate after five minutes, and a burst comes as one email.{/if}
    </p>
    {#if emailOn}
      <div class="inline ns-email">
        <label class="inline">
          <span>Email me at</span>
          <input
            type="email"
            placeholder="you@example.com"
            value={mailTo}
            onchange={(e) => void saveMailTo(e.currentTarget.value)}
          />
        </label>
        <button class="secondary" disabled={busy || !mailTo} onclick={() => void mailTest()}>Send a test email</button>
      </div>
    {/if}
    <label class="inline ns-renudge">
      <span>Nudge me again about chat I still have not read</span>
      <select
        aria-label="Nudge again about unread chat"
        disabled={!reasons.has('chat')}
        value={renudge}
        onchange={(e) => void board.setPrefs({ chat_renudge_minutes: Number(e.currentTarget.value) })}
      >
        {#each RENUDGE_CHOICES as c (c.minutes)}<option value={c.minutes}>{c.label}</option>{/each}
      </select>
    </label>
  </section>

  <section class="ns-block" aria-label="Watch tasks automatically">
    <h3><Icon name="eye" size={15} /> Watch tasks automatically</h3>
    <div class="ns-reasons">
      {#each AUTO_WATCH as w (w.key)}
        <label class="ns-check">
          <input
            type="checkbox"
            checked={autoWatch[w.key]}
            onchange={(e) => void board.setPrefs({ auto_watch: { ...autoWatch, [w.key]: e.currentTarget.checked } })}
          />
          {w.label}
        </label>
      {/each}
    </div>
    <p class="muted small">You hear about everything on a task you watch. Add or remove watchers from the eye in a task’s window.</p>
  </section>

  <section class="ns-block" aria-label="Daily digest">
    <h3><Icon name="sun" size={15} /> Daily digest</h3>
    <div class="inline">
      <label class="ns-check">
        <input type="checkbox" checked={digestOn} onchange={(e) => void board.setPrefs({ digest: e.currentTarget.checked })} />
        Send me my day every morning
      </label>
      <select
        aria-label="Digest hour"
        disabled={!digestOn}
        value={digestHour}
        onchange={(e) => void board.setPrefs({ digest_hour: Number(e.currentTarget.value) })}
      >
        {#each HOURS as h (h)}<option value={h}>{hourLabel(h)}</option>{/each}
      </select>
    </div>
    {#if digest}<p class="muted small">Today it says: {digestText(digest.counts)}. Nothing is sent on an empty day.</p>{/if}
  </section>

  <section class="ns-block" aria-label="Upcoming reminders">
    <h3><Icon name="clock" size={15} /> Upcoming reminders</h3>
    {#if !upcoming.length}
      <p class="muted small">None. Set one from a task’s window with Remind me.</p>
    {:else}
      <ul class="ns-upcoming">
        {#each upcoming as r (r.id)}
          <li>
            <button
              class="link"
              onclick={() => {
                close();
                void board.open(r.item_id);
              }}>{r.item_title ?? 'A task'}</button
            >
            <span class="muted small">{whenText(r, board.now)}{r.note ? ` · ${r.note}` : ''}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

<style>
  .ns-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 0;
    border-top: 1px solid var(--line);
  }
  .ns-block h3 {
    margin: 0;
    font-size: var(--fs-md, 14px);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .ns-state {
    margin: 0;
    color: var(--muted);
  }
  .ns-state[data-state='on'] {
    color: var(--success);
  }
  .ns-error {
    margin: 0;
    color: var(--danger);
  }
  .ns-sent {
    margin: 0;
  }
  .ns-devices,
  .ns-upcoming {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ns-devices li,
  .ns-upcoming li {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .ns-upcoming li {
    justify-content: space-between;
  }
  .grow {
    flex: 1;
  }
  .ns-reasons {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 4px 12px;
  }
  .ns-check {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .ns-grid {
    border-collapse: collapse;
    width: 100%;
  }
  .ns-grid th,
  .ns-grid td {
    padding: 5px 6px;
    border-bottom: 1px solid var(--line);
    text-align: center;
  }
  .ns-grid th[scope='row'] {
    text-align: left;
    font-weight: normal;
  }
  .ns-grid thead th {
    font-size: var(--fs-sm, 12px);
    color: var(--muted);
    font-weight: 600;
    white-space: nowrap;
  }
  .ns-email {
    gap: 8px;
    flex-wrap: wrap;
  }
  .ns-email input[type='email'] {
    min-width: 220px;
  }
  .ns-renudge {
    margin-top: 8px;
    gap: 8px;
    flex-wrap: wrap;
  }
</style>
