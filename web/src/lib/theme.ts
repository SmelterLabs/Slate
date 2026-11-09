// Light, dark, or follow the system. The choice belongs to the account (prefs.theme on /me), so
// every device shows the same board; app.css reads it from <html data-theme>. localStorage only
// remembers the last value for the first paint, before /me answers. ClickUp is light, so a
// monitor across the room can match it.

export type Theme = 'auto' | 'light' | 'dark';
const KEY = 'slate.theme';

export function asTheme(t: unknown): Theme {
  return t === 'light' || t === 'dark' ? t : 'auto';
}

/** The last theme this browser drew, for the first paint only. */
export function cachedTheme(): Theme {
  try {
    return asTheme(localStorage.getItem(KEY));
  } catch {
    return 'auto';
  }
}

/** Draw `theme` and remember it for the next first paint. */
export function applyTheme(theme: Theme = cachedTheme()) {
  const root = document.documentElement;
  if (theme === 'auto') delete root.dataset.theme;
  else root.dataset.theme = theme;
  try {
    if (theme === 'auto') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // private mode: the account still has it
  }
}
