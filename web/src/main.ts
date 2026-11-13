import { mount } from 'svelte';
import './app.css';
import './board.css';
import './subtasks.css';
import './design.css';
import './work.css';
import './docs.css';
import './chat.css';
import './members.css';
import App from './App.svelte';
import { applyTheme } from './lib/theme';
import { installTouchDrag } from './lib/touchdrag';
import { installKeyboardInset } from './lib/viewport';

applyTheme();
installTouchDrag();
installKeyboardInset();

const app = mount(App, { target: document.getElementById('app')! });

// Installable app: the service worker caches the shell only (public/sw.js). Not in dev, where
// Vite serves unhashed modules that a cache would only get in the way of.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

export default app;
