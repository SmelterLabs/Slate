// Phone and desktop notifications for this browser (Web Push). The server pushes the account's
// inbox entries, filtered by its `push_reasons` prefs (service/push.py); this file turns the
// browser's subscription on and off and keeps the server's copy current.
//
// iPhone: Safari allows push only in the installed app (Add to Home Screen), iOS 16.4+.

import { get, post } from "./api";

export interface PushDevice {
  id: string;
  label: string | null;
  endpoint_host: string;
  created_at: string;
  last_ok_at: string | null;
  failures: number;
}

export interface PushTestResult extends PushDevice {
  ok: boolean;
  error: string | null;
}

export type PushState =
  | "unsupported"
  | "needs-install"
  | "blocked"
  | "off"
  | "on"
  | "server-off";

/** The reasons a person can choose to be pushed for, as the server names them. */
export const PUSH_REASONS: { key: string; label: string }[] = [
  { key: "mention", label: "Someone mentions me" },
  { key: "reply", label: "Replies in chat threads I am in" },
  { key: "chat", label: "New messages in chats I can see" },
  { key: "assigned", label: "I am assigned a task" },
  { key: "waiting", label: "Someone is waiting on me" },
  { key: "due", label: "My task is due or overdue" },
  { key: "unblocked", label: "My task is no longer blocked" },
  { key: "nudge", label: "An agent I look after has a task to start" },
  { key: "reminder", label: "Reminders" },
  { key: "digest", label: "My daily digest" },
  { key: "health", label: "Something in Slate keeps failing (owners and admins)" },
  { key: "access", label: "A project guest session (mine, or one I issued) is about to end" },
  { key: "comment", label: "Comments on tasks I follow" },
  { key: "status", label: "Status changes on tasks I follow" },
  { key: "update", label: "Other changes on tasks I follow" },
  { key: "watching", label: "Anything happens on a task I watch" },
];
export const DEFAULT_PUSH_REASONS = [
  "mention",
  "reply",
  "chat",
  "assigned",
  "waiting",
  "due",
  "unblocked",
  "nudge",
  "reminder",
  "digest",
  "health",
  "access",
  "watching",
];

/** Email defaults (service/email.py DEFAULT_REASONS). */
export const DEFAULT_EMAIL_REASONS = ["mention", "waiting"];

/** The Notifications grid: a few plain rows, each covering several server reasons. A row's
 * box is on when all its reasons are chosen; ticking it chooses or clears them all. `chat`
 * (plain channel traffic) is a toast only: email never offers it. */
export const NOTIFY_GROUPS: { key: string; label: string; reasons: string[] }[] = [
  { key: "mention", label: "I'm mentioned or asked something", reasons: ["mention"] },
  { key: "messages", label: "Replies, comments and chat", reasons: ["reply", "comment", "chat"] },
  { key: "assigned", label: "I'm assigned a task", reasons: ["assigned", "nudge"] },
  { key: "waiting", label: "Someone is waiting on me", reasons: ["waiting", "unblocked"] },
  { key: "due", label: "Due dates, reminders and my day", reasons: ["due", "reminder", "digest"] },
  {
    key: "watching",
    label: "Anything on tasks I watch",
    reasons: ["status", "update", "watching"],
  },
  { key: "system", label: "Slate alerts and project access", reasons: ["health", "access"] },
];

export async function testEmail(): Promise<{ ok: boolean; error: string | null }> {
  return post<{ ok: boolean; error: string | null }>("/email/test", {});
}

export function urlBase64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function bytesToUrlBase64(buf: ArrayBuffer | null): string {
  if (!buf) return "";
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** "iPhone app", "Mac (Chrome)", "Android", from the user agent. */
export function deviceLabel(ua: string, standalone: boolean): string {
  const os = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Mac OS X/.test(ua)
          ? "Mac"
          : /Windows/.test(ua)
            ? "Windows"
            : /Linux/.test(ua)
              ? "Linux"
              : "This device";
  if (standalone) return `${os} app`;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "";
  return browser ? `${os} (${browser})` : os;
}

/** Where to look when a test was delivered but nothing appeared: the operating system's own
 * switch for the browser (or for Slate, installed as an app). macOS asks for it once, the first
 * time the browser shows any notification; a dismissed prompt leaves it off and the browser
 * accepts every push in silence. */
export function missedHint(ua: string, standalone: boolean): string {
  const app = standalone ? "Slate" : browserName(ua) || "your browser";
  if (/iPhone|iPad/.test(ua)) return `If nothing appeared: Settings → Notifications → Slate → Allow Notifications, and check Focus.`;
  if (/Android/.test(ua)) return `If nothing appeared: Android Settings → Apps → ${app} → Notifications on.`;
  if (/Mac OS X/.test(ua))
    return `If nothing appeared on this Mac: System Settings → Notifications → ${app} → Allow notifications (Banners or Alerts), and turn off Focus. If ${app} is not in that list, quit and reopen it, then send another test so macOS asks.`;
  if (/Windows/.test(ua))
    return `If nothing appeared: Windows Settings → System → Notifications → ${app} on, and Focus assist or Do not disturb off.`;
  return `If nothing appeared, check the system's notification settings for ${app}.`;
}

function browserName(ua: string): string {
  return /Edg\//.test(ua) ? "Microsoft Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Google Chrome" : /Safari\//.test(ua) ? "Safari" : "";
}

const standalone = () =>
  window.matchMedia?.("(display-mode: standalone)").matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true;

const supported = () =>
  "serviceWorker" in navigator &&
  "PushManager" in window &&
  "Notification" in window;

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  return (
    (await navigator.serviceWorker.getRegistration()) ??
    navigator.serviceWorker.register("/sw.js")
  );
}

async function serverKey(): Promise<string | null> {
  const k = await get<{ enabled: boolean; public_key: string | null }>(
    "/push/key",
  );
  return k.enabled ? k.public_key : null;
}

async function current(): Promise<PushSubscription | null> {
  if (!supported()) return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return (await reg?.pushManager.getSubscription()) ?? null;
}

export async function pushState(): Promise<PushState> {
  if (!supported())
    return /iPhone|iPad/.test(navigator.userAgent) && !standalone()
      ? "needs-install"
      : "unsupported";
  if (Notification.permission === "denied") return "blocked";
  if (!(await serverKey().catch(() => null))) return "server-off";
  return (await current()) ? "on" : "off";
}

async function register(sub: PushSubscription): Promise<void> {
  const json = sub.toJSON();
  await post("/push/subscriptions", {
    endpoint: sub.endpoint,
    keys: {
      p256dh: json.keys?.p256dh ?? bytesToUrlBase64(sub.getKey("p256dh")),
      auth: json.keys?.auth ?? bytesToUrlBase64(sub.getKey("auth")),
    },
    label: deviceLabel(navigator.userAgent, standalone()),
  });
}

/** Ask permission (must follow a tap), subscribe this browser, tell the server. */
export async function enablePush(): Promise<void> {
  if (!supported())
    throw new Error("This browser cannot show notifications from Slate");
  const key = await serverKey();
  if (!key)
    throw new Error(
      "The server cannot send notifications (no SLATE_SECRET_KEY)",
    );
  if ((await Notification.requestPermission()) !== "granted") {
    throw new Error(
      "Notifications are blocked for Slate in this browser’s settings",
    );
  }
  const reg = await registration();
  if (!reg) throw new Error("No service worker");
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToBytes(key),
  });
  await register(sub);
}

export async function disablePush(): Promise<void> {
  const sub = await current();
  if (!sub) return;
  await post("/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
  await sub.unsubscribe();
}

/** On each start: if this browser is subscribed, make sure the server still has it (it drops
 * expired ones) and that it was made with the server's current key (it changes with
 * SLATE_SECRET_KEY); re-subscribe if not. Silent: never prompts. */
export async function syncPush(): Promise<void> {
  if (!supported() || Notification.permission !== "granted") return;
  const sub = await current();
  if (!sub) return;
  const key = await serverKey().catch(() => null);
  if (!key) return;
  const had = bytesToUrlBase64(sub.options.applicationServerKey);
  if (had && had !== key) {
    await sub.unsubscribe();
    const reg = await registration();
    if (!reg) return;
    await register(
      await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToBytes(key),
      }),
    );
    return;
  }
  await register(sub);
}

export const listDevices = () => get<PushDevice[]>("/push/subscriptions");
export const testPush = () => post<PushTestResult[]>("/push/test");
