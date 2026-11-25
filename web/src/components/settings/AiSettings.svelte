<script lang="ts">
  // Workspace AI Task Creation settings (GET/PUT /settings/ai, /models, /test, /oauth/*).
  // Supports subscriptions (Claude CLI, ChatGPT Sign-in OAuth, Codex CLI), cloud API keys
  // (Anthropic, OpenAI, Google Gemini), and local/custom servers (Ollama, OpenAI-compatible).
  import { onDestroy } from 'svelte';
  import { get, post, put } from '../../lib/api';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';

  type ProviderId =
    | 'claude-cli'
    | 'chatgpt-oauth'
    | 'codex-cli'
    | 'anthropic'
    | 'openai'
    | 'gemini'
    | 'ollama'
    | 'openai-compatible';

  interface ModelChoice {
    id: string;
    label: string;
  }

  interface AiSettingsView {
    enabled: boolean;
    active: boolean;
    source: string;
    provider: ProviderId | null;
    model: string;
    url: string;
    has_api_key: boolean;
    oauth_connected: boolean;
    oauth_account: string | null;
    secret_key_ready: boolean;
    cli_detected: { claude: boolean; codex: boolean };
    defaults: Record<string, string>;
  }

  const PROVIDER_GROUPS: { label: string; items: { id: ProviderId; label: string }[] }[] = [
    {
      label: 'Subscriptions',
      items: [
        { id: 'claude-cli', label: 'Claude subscription (local CLI)' },
        { id: 'chatgpt-oauth', label: 'ChatGPT subscription (Sign in with ChatGPT)' },
        { id: 'codex-cli', label: 'ChatGPT / Codex subscription (local CLI)' },
      ],
    },
    {
      label: 'Cloud API keys',
      items: [
        { id: 'anthropic', label: 'Anthropic (API key)' },
        { id: 'openai', label: 'OpenAI (API key)' },
        { id: 'gemini', label: 'Google Gemini (API key)' },
      ],
    },
    {
      label: 'Local & custom',
      items: [
        { id: 'ollama', label: 'Ollama (local)' },
        { id: 'openai-compatible', label: 'OpenAI-compatible server' },
      ],
    },
  ];

  let view = $state<AiSettingsView | null>(null);
  let enabled = $state(false);
  let provider = $state<ProviderId>('anthropic');
  let model = $state('');
  let url = $state('');
  let apiKey = $state('');
  let clearApiKey = $state(false);

  let models = $state<ModelChoice[]>([]);
  let modelsBusy = $state(false);
  let modelsError = $state<string | null>(null);
  let customModel = $state(false);

  let busy = $state(false);
  let testing = $state(false);
  let testResult = $state<{ ok: boolean; latency_ms?: number; model?: string; error?: string } | null>(null);

  // Sign in with ChatGPT PKCE flow state
  let oauthPending = $state<{ authorize_url: string; state: string; redirect_uri: string } | null>(null);
  let oauthCallback = $state('');
  let oauthBusy = $state(false);

  const needsApiKey = $derived(provider === 'anthropic' || provider === 'openai' || provider === 'gemini');
  const optionalApiKey = $derived(provider === 'openai-compatible');
  const isCli = $derived(provider === 'claude-cli' || provider === 'codex-cli');
  const isUrlServer = $derived(provider === 'ollama' || provider === 'openai-compatible');
  const isOauth = $derived(provider === 'chatgpt-oauth');
  const cliName = $derived(provider === 'claude-cli' ? 'claude' : 'codex');
  const cliFound = $derived(
    provider === 'claude-cli' ? !!view?.cli_detected.claude : provider === 'codex-cli' ? !!view?.cli_detected.codex : false,
  );
  const statusLabel = $derived(!view ? '' : view.active ? 'Active' : view.enabled ? 'Needs setup' : 'Off');

  function applyView(v: AiSettingsView) {
    view = v;
    enabled = v.enabled;
    provider = v.provider ?? 'anthropic';
    model = v.model || v.defaults[provider] || '';
    url = v.url || '';
    apiKey = '';
    clearApiKey = false;
  }

  async function load(fetchModels = false) {
    try {
      const v = await get<AiSettingsView>('/settings/ai');
      applyView(v);
      if (fetchModels) void refreshModels();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  $effect(() => {
    void load(true);
  });

  const off = board.onEvent((ev) => {
    if (ev.type === 'settings.changed' && ev.field === 'ai.config') void load(false);
  });
  onDestroy(off);

  function onProviderChange(next: ProviderId) {
    provider = next;
    model = view?.defaults[next] ?? '';
    models = [];
    modelsError = null;
    customModel = false;
    testResult = null;
    oauthPending = null;
    if (next === 'ollama' && !url) url = 'http://localhost:11434';
    void refreshModels();
  }

  async function refreshModels() {
    modelsBusy = true;
    modelsError = null;
    try {
      const res = await post<{ models: ModelChoice[]; error?: string }>('/settings/ai/models', {
        provider,
        url: url.trim() || undefined,
        api_key: apiKey.trim() || undefined,
      });
      models = res.models ?? [];
      modelsError = res.error ?? null;
      if (models.length && model && !models.some((m) => m.id === model)) {
        customModel = true;
      }
    } catch (e) {
      modelsError = e instanceof Error ? e.message : String(e);
    } finally {
      modelsBusy = false;
    }
  }

  async function save() {
    busy = true;
    testResult = null;
    try {
      const payload: Record<string, unknown> = {
        enabled,
        provider,
        model: model.trim(),
        url: url.trim(),
      };
      if (clearApiKey) payload.clear_api_key = true;
      else if (apiKey.trim()) payload.api_key = apiKey.trim();
      const v = await put<AiSettingsView>('/settings/ai', payload);
      applyView(v);
      board.flash('AI settings saved');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function testConnection() {
    testing = true;
    testResult = null;
    try {
      testResult = await post<{ ok: boolean; latency_ms?: number; model?: string; error?: string }>(
        '/settings/ai/test',
        {
          provider,
          model: model.trim() || undefined,
          url: url.trim() || undefined,
          api_key: apiKey.trim() || undefined,
        },
      );
    } catch (e) {
      testResult = { ok: false, error: e instanceof Error ? e.message : String(e) };
    } finally {
      testing = false;
    }
  }

  async function startChatGptSignIn() {
    oauthBusy = true;
    try {
      const res = await post<{ authorize_url: string; state: string; redirect_uri: string }>(
        '/settings/ai/oauth/start',
      );
      oauthPending = res;
      oauthCallback = '';
      window.open(res.authorize_url, '_blank', 'noopener,noreferrer');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      oauthBusy = false;
    }
  }

  async function completeChatGptSignIn() {
    if (!oauthCallback.trim()) return;
    oauthBusy = true;
    try {
      const v = await post<AiSettingsView>('/settings/ai/oauth/complete', {
        callback: oauthCallback.trim(),
        state: oauthPending?.state,
      });
      oauthPending = null;
      oauthCallback = '';
      applyView(v);
      board.flash('Signed in with ChatGPT');
      void refreshModels();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      oauthBusy = false;
    }
  }

  async function disconnectChatGpt() {
    oauthBusy = true;
    try {
      const v = await post<AiSettingsView>('/settings/ai/oauth/disconnect');
      oauthPending = null;
      applyView(v);
      board.flash('Disconnected ChatGPT account');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      oauthBusy = false;
    }
  }
</script>

{#if view}
  <section class="ai-settings" aria-label="AI Task Creation">
    <div class="ai-head">
      <div>
        <div class="ai-title-row">
          <strong>AI Task Creation</strong>
          <span class="ai-pill" class:ok={view.active} class:warn={view.enabled && !view.active}>{statusLabel}</span>
        </div>
        <span class="muted small">
          Turn natural-language quick add into structured tasks with list routing, due dates, and recurrence.
        </span>
      </div>
      <label class="ai-toggle">
        <input type="checkbox" bind:checked={enabled} disabled={busy} />
        <span>Enable AI add</span>
      </label>
    </div>

    <div class="ai-grid">
      <label class="ai-field">
        <span class="ai-label">Provider</span>
        <select
          aria-label="AI provider"
          value={provider}
          disabled={busy}
          onchange={(e) => onProviderChange(e.currentTarget.value as ProviderId)}
        >
          {#each PROVIDER_GROUPS as g (g.label)}
            <optgroup label={g.label}>
              {#each g.items as item (item.id)}
                <option value={item.id}>{item.label}</option>
              {/each}
            </optgroup>
          {/each}
        </select>
      </label>

      {#if isOauth}
        <div class="ai-field">
          <span class="ai-label">ChatGPT account</span>
          {#if view.oauth_connected}
            <div class="ai-inline">
              <span class="ai-connected">
                <Icon name="check" size={14} />
                <span>{view.oauth_account ? `Signed in as ${view.oauth_account}` : 'Signed in with ChatGPT'}</span>
              </span>
              <button type="button" class="ghost" disabled={oauthBusy} onclick={() => void disconnectChatGpt()}>
                Disconnect
              </button>
            </div>
          {:else}
            <div class="ai-oauth-box">
              <button
                type="button"
                class="primary"
                disabled={oauthBusy || !view.secret_key_ready}
                onclick={() => void startChatGptSignIn()}
              >
                {oauthBusy ? 'Starting…' : 'Sign in with ChatGPT'}
              </button>
              {#if !view.secret_key_ready}
                <p class="muted small">Set <code>SLATE_SECRET_KEY</code> on the server to store encrypted credentials.</p>
              {/if}
              {#if oauthPending}
                <div class="ai-oauth-step">
                  <p class="small">
                    1. Sign in at <a href={oauthPending.authorize_url} target="_blank" rel="noopener noreferrer">OpenAI</a>.
                    After approving, your browser redirects to a localhost URL — copy that full URL from the address bar and paste it below.
                  </p>
                  <div class="ai-inline">
                    <input
                      type="text"
                      bind:value={oauthCallback}
                      placeholder="http://localhost:1455/auth/callback?code=..."
                      disabled={oauthBusy}
                    />
                    <button
                      type="button"
                      class="primary"
                      disabled={oauthBusy || !oauthCallback.trim()}
                      onclick={() => void completeChatGptSignIn()}
                    >
                      Complete sign-in
                    </button>
                  </div>
                </div>
              {/if}
            </div>
          {/if}
        </div>
      {/if}

      {#if isCli}
        <div class="ai-field">
          <span class="ai-label">Host CLI / Bridge URL</span>
          <div class="ai-cli-status small">
            {#if cliFound}
              <span class="ai-connected"><Icon name="check" size={14} /> <code>{cliName}</code> detected on server</span>
            {:else}
              <span class="muted"><code>{cliName}</code> not on server PATH — if running in Docker, run <code>python3 scripts/ai_cli_bridge.py</code> on the host and enter its URL below.</span>
            {/if}
          </div>
          <input
            type="url"
            bind:value={url}
            placeholder="http://host.docker.internal:11435 (optional for local install)"
            disabled={busy}
          />
        </div>
      {/if}

      {#if isUrlServer}
        <label class="ai-field">
          <span class="ai-label">Server URL</span>
          <input
            type="url"
            bind:value={url}
            placeholder={provider === 'ollama' ? 'http://localhost:11434' : 'https://openrouter.ai/api'}
            disabled={busy}
          />
        </label>
      {/if}

      {#if needsApiKey || optionalApiKey}
        <div class="ai-field">
          <span class="ai-label">{optionalApiKey ? 'API key (optional)' : 'API key'}</span>
          <div class="ai-inline">
            <input
              type="password"
              autocomplete="off"
              bind:value={apiKey}
              placeholder={view.has_api_key && !clearApiKey ? 'Saved (enter a new key to replace)' : 'Paste API key'}
              disabled={busy || !view.secret_key_ready}
              oninput={() => { if (apiKey) clearApiKey = false; }}
            />
            {#if view.has_api_key && !clearApiKey}
              <button type="button" class="ghost" disabled={busy} onclick={() => { clearApiKey = true; apiKey = ''; }}>
                Clear
              </button>
            {/if}
          </div>
          {#if clearApiKey}
            <span class="muted small">Saved key will be removed when you click Save.</span>
          {:else if !view.secret_key_ready}
            <span class="muted small">Set <code>SLATE_SECRET_KEY</code> on the server to save encrypted API keys.</span>
          {/if}
        </div>
      {/if}

      <div class="ai-field">
        <span class="ai-label">Model</span>
        <div class="ai-inline">
          {#if models.length && !customModel}
            <select
              aria-label="AI model"
              value={model}
              disabled={busy}
              onchange={(e) => {
                if (e.currentTarget.value === '__custom__') customModel = true;
                else model = e.currentTarget.value;
              }}
            >
              {#each models as m (m.id)}
                <option value={m.id}>{m.label}</option>
              {/each}
              <option value="__custom__">Custom model…</option>
            </select>
          {:else}
            <input
              type="text"
              aria-label="AI model"
              bind:value={model}
              placeholder={view.defaults[provider] || 'Model id'}
              disabled={busy}
            />
            {#if models.length}
              <button type="button" class="ghost" onclick={() => (customModel = false)}>List</button>
            {/if}
          {/if}
          <button type="button" class="ghost" disabled={modelsBusy} onclick={() => void refreshModels()}>
            <Icon name="refresh" size={14} />
            <span>{modelsBusy ? 'Loading…' : 'Refresh'}</span>
          </button>
        </div>
        {#if modelsError}
          <span class="muted small">{modelsError}</span>
        {/if}
      </div>
    </div>

    <div class="ai-actions">
      <button type="button" class="primary" disabled={busy} onclick={() => void save()}>
        {busy ? 'Saving…' : 'Save'}
      </button>
      <button type="button" class="ghost" disabled={testing || busy} onclick={() => void testConnection()}>
        {testing ? 'Testing…' : 'Test connection'}
      </button>
      {#if testResult}
        <span class="ai-test" class:ok={testResult.ok} class:err={!testResult.ok}>
          {#if testResult.ok}
            Connected{testResult.model ? ` (${testResult.model})` : ''}{testResult.latency_ms ? ` · ${testResult.latency_ms}ms` : ''}
          {:else}
            {testResult.error || 'Connection failed'}
          {/if}
        </span>
      {/if}
    </div>
  </section>
{/if}

<style>
  .ai-settings {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .ai-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .ai-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 2px;
  }
  .ai-pill {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--muted);
  }
  .ai-pill.ok {
    background: color-mix(in srgb, var(--ok, #16a34a) 15%, transparent);
    color: var(--ok, #16a34a);
  }
  .ai-pill.warn {
    background: color-mix(in srgb, var(--warn, #d97706) 15%, transparent);
    color: var(--warn, #d97706);
  }
  .ai-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
  }
  .ai-grid {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .ai-field {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .ai-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }
  .ai-field input,
  .ai-field select {
    flex: 1;
    min-width: 0;
  }
  .ai-inline {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .ai-connected {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 13px;
    color: var(--ok, #16a34a);
    font-weight: 500;
  }
  .ai-oauth-box,
  .ai-oauth-step {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ai-oauth-step {
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface-2);
  }
  .ai-oauth-step p {
    margin: 0;
  }
  .ai-cli-status {
    margin-bottom: 2px;
  }
  .ai-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding-top: 4px;
  }
  .ai-test {
    font-size: 13px;
  }
  .ai-test.ok {
    color: var(--ok, #16a34a);
  }
  .ai-test.err {
    color: var(--danger, #dc2626);
  }
</style>
