<script lang="ts">
  // Workspace AI Task Creation settings (GET/PUT /settings/ai, /models, /test, /oauth/*).
  // Supports primary + optional backup provider across subscriptions (Claude CLI, ChatGPT Sign-in
  // OAuth, Codex CLI), cloud API keys (Anthropic, OpenAI, Google Gemini), and local/custom servers
  // (Ollama, OpenAI-compatible).
  import { onDestroy } from 'svelte';
  import { get, post, put } from '../../lib/api';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';

  type ProviderId =
    | 'claude-cli'
    | 'codex-cli'
    | 'chatgpt-oauth'
    | 'anthropic'
    | 'openai'
    | 'gemini'
    | 'ollama'
    | 'openai-compatible';

  interface ModelChoice {
    id: string;
    label: string;
  }

  interface AiBackupView {
    enabled: boolean;
    active: boolean;
    provider: ProviderId | null;
    model: string;
    url: string;
    has_api_key: boolean;
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
    bridge_url?: string | null;
    status_reason?: string;
    defaults: Record<string, string>;
    backup: AiBackupView;
  }

  interface TestResult {
    ok: boolean;
    latency_ms?: number;
    model?: string;
    detail?: string;
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

  function urlBucket(p: ProviderId | ''): string {
    if (p === 'claude-cli' || p === 'codex-cli') return 'cli';
    if (p === 'ollama') return 'ollama';
    if (p === 'openai-compatible') return 'compat';
    return 'none';
  }

  function defaultUrlFor(p: ProviderId | ''): string {
    if (p === 'ollama') return 'http://localhost:11434';
    return '';
  }

  let view = $state<AiSettingsView | null>(null);
  let enabled = $state(false);

  // Primary slot state
  let provider = $state<ProviderId>('anthropic');
  let model = $state('');
  let url = $state('');
  let apiKey = $state('');
  let clearApiKey = $state(false);
  let urlCache = $state<Record<string, string>>({});
  let models = $state<ModelChoice[]>([]);
  let modelsBusy = $state(false);
  let modelsError = $state<string | null>(null);
  let customModel = $state(false);
  let testing = $state(false);
  let testResult = $state<TestResult | null>(null);

  // Backup slot state
  let backupProvider = $state<ProviderId | ''>('');
  let backupModel = $state('');
  let backupUrl = $state('');
  let backupApiKey = $state('');
  let backupClearApiKey = $state(false);
  let backupUrlCache = $state<Record<string, string>>({});
  let backupModels = $state<ModelChoice[]>([]);
  let backupModelsBusy = $state(false);
  let backupModelsError = $state<string | null>(null);
  let backupCustomModel = $state(false);
  let backupTesting = $state(false);
  let backupTestResult = $state<TestResult | null>(null);

  let busy = $state(false);

  // Sign in with ChatGPT PKCE flow state
  let oauthPending = $state<{ authorize_url: string; state: string; redirect_uri: string } | null>(null);
  let oauthCallback = $state('');
  let oauthBusy = $state(false);

  const needsKey = (p: ProviderId | '') => p === 'anthropic' || p === 'openai' || p === 'gemini';
  const optionalKey = (p: ProviderId | '') => p === 'openai-compatible';
  const isCli = (p: ProviderId | '') => p === 'claude-cli' || p === 'codex-cli';
  const isUrlServer = (p: ProviderId | '') => p === 'ollama' || p === 'openai-compatible';
  const isOauth = (p: ProviderId | '') => p === 'chatgpt-oauth';
  const cliFound = (p: ProviderId | '') =>
    p === 'claude-cli' ? !!view?.cli_detected.claude : p === 'codex-cli' ? !!view?.cli_detected.codex : false;

  const statusLabel = $derived(!view ? '' : view.active ? 'Active' : view.enabled ? 'Needs setup' : 'Off');

  function applyView(v: AiSettingsView) {
    view = v;
    enabled = v.enabled;
    provider = v.provider ?? 'anthropic';
    model = v.model || v.defaults[provider] || '';
    url = v.url || '';
    const pb = urlBucket(provider);
    if (pb !== 'none' && url) urlCache[pb] = url;
    apiKey = '';
    clearApiKey = false;

    const b = v.backup;
    backupProvider = b?.enabled && b.provider ? b.provider : '';
    backupModel = b?.model || (backupProvider ? v.defaults[backupProvider] || '' : '');
    backupUrl = b?.url || '';
    const bb = urlBucket(backupProvider);
    if (bb !== 'none' && backupUrl) backupUrlCache[bb] = backupUrl;
    backupApiKey = '';
    backupClearApiKey = false;
  }

  async function load(fetchModels = false) {
    try {
      const v = await get<AiSettingsView>('/settings/ai');
      applyView(v);
      if (fetchModels) {
        void refreshModels('primary');
        if (backupProvider) void refreshModels('backup');
      }
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
    const prevBucket = urlBucket(provider);
    if (prevBucket !== 'none') urlCache[prevBucket] = url;
    provider = next;
    const nextBucket = urlBucket(next);
    url = nextBucket === 'none' ? '' : (urlCache[nextBucket] ?? defaultUrlFor(next));
    model = view?.defaults[next] ?? '';
    models = [];
    modelsError = null;
    customModel = false;
    testResult = null;
    oauthPending = null;
    void refreshModels('primary');
  }

  function onBackupProviderChange(next: ProviderId | '') {
    const prevBucket = urlBucket(backupProvider);
    if (prevBucket !== 'none') backupUrlCache[prevBucket] = backupUrl;
    backupProvider = next;
    const nextBucket = urlBucket(next);
    backupUrl = nextBucket === 'none' ? '' : (backupUrlCache[nextBucket] ?? defaultUrlFor(next));
    backupModel = next ? (view?.defaults[next] ?? '') : '';
    backupModels = [];
    backupModelsError = null;
    backupCustomModel = false;
    backupTestResult = null;
    if (next) void refreshModels('backup');
  }

  async function refreshModels(slot: 'primary' | 'backup') {
    const isBackup = slot === 'backup';
    const prov = isBackup ? backupProvider : provider;
    if (!prov) return;
    if (isBackup) {
      backupModelsBusy = true;
      backupModelsError = null;
    } else {
      modelsBusy = true;
      modelsError = null;
    }
    try {
      const res = await post<{ models: ModelChoice[]; error?: string }>('/settings/ai/models', {
        provider: prov,
        url: (isBackup ? backupUrl : url).trim() || undefined,
        api_key: (isBackup ? backupApiKey : apiKey).trim() || undefined,
        slot,
      });
      const list = res.models ?? [];
      if (isBackup) {
        backupModels = list;
        backupModelsError = res.error ?? null;
        if (list.length && (!backupModel || !list.some((m) => m.id === backupModel))) {
          if (!backupModel) backupModel = list[0].id;
        }
        backupCustomModel = false;
      } else {
        models = list;
        modelsError = res.error ?? null;
        if (list.length && (!model || !list.some((m) => m.id === model))) {
          if (!model) model = list[0].id;
        }
        customModel = false;
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (isBackup) backupModelsError = msg;
      else modelsError = msg;
    } finally {
      if (isBackup) backupModelsBusy = false;
      else modelsBusy = false;
    }
  }

  async function save() {
    busy = true;
    testResult = null;
    backupTestResult = null;
    try {
      const backupPayload: Record<string, unknown> = backupProvider
        ? {
            enabled: true,
            provider: backupProvider,
            model: backupModel.trim(),
            url: backupUrl.trim(),
            ...(backupClearApiKey ? { clear_api_key: true } : backupApiKey.trim() ? { api_key: backupApiKey.trim() } : {}),
          }
        : { enabled: false, provider: null, model: '', url: '', clear_api_key: backupClearApiKey || undefined };

      const payload: Record<string, unknown> = {
        enabled,
        provider,
        model: model.trim(),
        url: url.trim(),
        backup: backupPayload,
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

  async function testConnection(slot: 'primary' | 'backup') {
    const isBackup = slot === 'backup';
    const prov = isBackup ? backupProvider : provider;
    if (!prov) return;
    if (isBackup) {
      backupTesting = true;
      backupTestResult = null;
    } else {
      testing = true;
      testResult = null;
    }
    try {
      const res = await post<TestResult>('/settings/ai/test', {
        provider: prov,
        model: (isBackup ? backupModel : model).trim() || undefined,
        url: (isBackup ? backupUrl : url).trim() || undefined,
        api_key: (isBackup ? backupApiKey : apiKey).trim() || undefined,
        slot,
      });
      if (isBackup) backupTestResult = res;
      else testResult = res;
    } catch (e) {
      const fail: TestResult = { ok: false, detail: e instanceof Error ? e.message : String(e) };
      if (isBackup) backupTestResult = fail;
      else testResult = fail;
    } finally {
      if (isBackup) backupTesting = false;
      else testing = false;
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
      void refreshModels('primary');
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
          {view.status_reason || 'Turn natural-language quick add into structured tasks with list routing, due dates, and recurrence.'}
        </span>
      </div>
      <label class="ai-toggle">
        <input type="checkbox" bind:checked={enabled} disabled={busy} />
        <span>Enable AI add</span>
      </label>
    </div>

    <div class="ai-grid">
      <label class="ai-field">
        <span class="ai-label">Primary provider</span>
        <select
          aria-label="Primary AI provider"
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

      {#if isOauth(provider)}
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

      {#if isCli(provider)}
        <div class="ai-field">
          <span class="ai-label">Host CLI / Bridge URL</span>
          <div class="ai-cli-status small">
            {#if cliFound(provider)}
              <span class="ai-connected">
                <Icon name="check" size={14} />
                <code>{provider === 'claude-cli' ? 'claude' : 'codex'}</code>
                {view.bridge_url ? `detected via host bridge (${view.bridge_url})` : 'detected on server'}
              </span>
            {:else}
              <span class="muted"><code>{provider === 'claude-cli' ? 'claude' : 'codex'}</code> not detected on server or host bridge — run <code>python3 scripts/ai_cli_bridge.py</code> on the host, or enter its Bridge URL below.</span>
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

      {#if isUrlServer(provider)}
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

      {#if needsKey(provider) || optionalKey(provider)}
        <div class="ai-field">
          <span class="ai-label">{optionalKey(provider) ? 'API key (optional)' : 'API key'}</span>
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
        <span class="ai-label">Primary model</span>
        <div class="ai-inline">
          {#if models.length && !customModel}
            <select
              aria-label="Primary AI model"
              value={model}
              disabled={busy}
              onchange={(e) => {
                if (e.currentTarget.value === '__custom__') customModel = true;
                else model = e.currentTarget.value;
              }}
            >
              {#if model && !models.some((m) => m.id === model)}
                <option value={model}>{model} (saved)</option>
              {/if}
              {#each models as m (m.id)}
                <option value={m.id}>{m.label}</option>
              {/each}
              <option value="__custom__">Custom model…</option>
            </select>
          {:else}
            <input
              type="text"
              aria-label="Primary AI model"
              bind:value={model}
              placeholder={view.defaults[provider] || 'Model id'}
              disabled={busy}
            />
            {#if models.length}
              <button type="button" class="ghost" onclick={() => (customModel = false)}>List</button>
            {/if}
          {/if}
          <button type="button" class="ghost" disabled={modelsBusy} onclick={() => void refreshModels('primary')}>
            <Icon name="refresh" size={14} />
            <span>{modelsBusy ? 'Loading…' : 'Refresh'}</span>
          </button>
        </div>
        {#if modelsError}
          <span class="muted small">{modelsError}</span>
        {/if}
      </div>

      <div class="ai-slot-test">
        <button type="button" class="ghost" disabled={testing || busy} onclick={() => void testConnection('primary')}>
          {testing ? 'Testing…' : 'Test primary'}
        </button>
        {#if testResult}
          <span class="ai-test" class:ok={testResult.ok} class:err={!testResult.ok}>
            {#if testResult.ok}
              Connected{testResult.model ? ` (${testResult.model})` : ''}{testResult.latency_ms ? ` · ${testResult.latency_ms}ms` : ''}
            {:else}
              {testResult.detail || 'Connection failed'}
            {/if}
          </span>
        {/if}
      </div>
    </div>

    <div class="ai-backup">
      <div class="ai-backup-head">
        <strong>Backup provider (optional)</strong>
        <span class="muted small">
          Used automatically if the primary provider fails or times out, before falling back to local parsing.
        </span>
      </div>

      <div class="ai-grid">
        <label class="ai-field">
          <span class="ai-label">Backup provider</span>
          <select
            aria-label="Backup AI provider"
            value={backupProvider}
            disabled={busy}
            onchange={(e) => onBackupProviderChange(e.currentTarget.value as ProviderId | '')}
          >
            <option value="">None (local parser fallback only)</option>
            {#each PROVIDER_GROUPS as g (g.label)}
              <optgroup label={g.label}>
                {#each g.items as item (item.id)}
                  <option value={item.id}>{item.label}</option>
                {/each}
              </optgroup>
            {/each}
          </select>
        </label>

        {#if backupProvider}
          {#if isOauth(backupProvider)}
            <div class="ai-field">
              <span class="ai-label">ChatGPT account</span>
              {#if view.oauth_connected}
                <span class="ai-connected">
                  <Icon name="check" size={14} />
                  <span>{view.oauth_account ? `Using ${view.oauth_account}` : 'Using connected ChatGPT account'}</span>
                </span>
              {:else}
                <span class="muted small">Connect ChatGPT above (or select Sign in with ChatGPT) to use it as a backup.</span>
              {/if}
            </div>
          {/if}

          {#if isCli(backupProvider)}
            <div class="ai-field">
              <span class="ai-label">Backup CLI / Bridge URL</span>
              <input
                type="url"
                bind:value={backupUrl}
                placeholder="http://host.docker.internal:11435 (optional for local install)"
                disabled={busy}
              />
            </div>
          {/if}

          {#if isUrlServer(backupProvider)}
            <label class="ai-field">
              <span class="ai-label">Backup server URL</span>
              <input
                type="url"
                bind:value={backupUrl}
                placeholder={backupProvider === 'ollama' ? 'http://localhost:11434' : 'https://openrouter.ai/api'}
                disabled={busy}
              />
            </label>
          {/if}

          {#if needsKey(backupProvider) || optionalKey(backupProvider)}
            <div class="ai-field">
              <span class="ai-label">{optionalKey(backupProvider) ? 'Backup API key (optional)' : 'Backup API key'}</span>
              <div class="ai-inline">
                <input
                  type="password"
                  autocomplete="off"
                  bind:value={backupApiKey}
                  placeholder={view.backup.has_api_key && !backupClearApiKey ? 'Saved (enter a new key to replace)' : 'Paste API key'}
                  disabled={busy || !view.secret_key_ready}
                  oninput={() => { if (backupApiKey) backupClearApiKey = false; }}
                />
                {#if view.backup.has_api_key && !backupClearApiKey}
                  <button type="button" class="ghost" disabled={busy} onclick={() => { backupClearApiKey = true; backupApiKey = ''; }}>
                    Clear
                  </button>
                {/if}
              </div>
            </div>
          {/if}

          <div class="ai-field">
            <span class="ai-label">Backup model</span>
            <div class="ai-inline">
              {#if backupModels.length && !backupCustomModel}
                <select
                  aria-label="Backup AI model"
                  value={backupModel}
                  disabled={busy}
                  onchange={(e) => {
                    if (e.currentTarget.value === '__custom__') backupCustomModel = true;
                    else backupModel = e.currentTarget.value;
                  }}
                >
                  {#if backupModel && !backupModels.some((m) => m.id === backupModel)}
                    <option value={backupModel}>{backupModel} (saved)</option>
                  {/if}
                  {#each backupModels as m (m.id)}
                    <option value={m.id}>{m.label}</option>
                  {/each}
                  <option value="__custom__">Custom model…</option>
                </select>
              {:else}
                <input
                  type="text"
                  aria-label="Backup AI model"
                  bind:value={backupModel}
                  placeholder={view.defaults[backupProvider] || 'Model id'}
                  disabled={busy}
                />
                {#if backupModels.length}
                  <button type="button" class="ghost" onclick={() => (backupCustomModel = false)}>List</button>
                {/if}
              {/if}
              <button type="button" class="ghost" disabled={backupModelsBusy} onclick={() => void refreshModels('backup')}>
                <Icon name="refresh" size={14} />
                <span>{backupModelsBusy ? 'Loading…' : 'Refresh'}</span>
              </button>
            </div>
            {#if backupModelsError}
              <span class="muted small">{backupModelsError}</span>
            {/if}
          </div>

          <div class="ai-slot-test">
            <button type="button" class="ghost" disabled={backupTesting || busy} onclick={() => void testConnection('backup')}>
              {backupTesting ? 'Testing…' : 'Test backup'}
            </button>
            {#if backupTestResult}
              <span class="ai-test" class:ok={backupTestResult.ok} class:err={!backupTestResult.ok}>
                {#if backupTestResult.ok}
                  Connected{backupTestResult.model ? ` (${backupTestResult.model})` : ''}{backupTestResult.latency_ms ? ` · ${backupTestResult.latency_ms}ms` : ''}
                {:else}
                  {backupTestResult.detail || 'Connection failed'}
                {/if}
              </span>
            {/if}
          </div>
        {/if}
      </div>
    </div>

    <div class="ai-actions">
      <button type="button" class="primary" disabled={busy} onclick={() => void save()}>
        {busy ? 'Saving…' : 'Save'}
      </button>
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
  .ai-inline,
  .ai-slot-test {
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
  .ai-backup {
    border-top: 1px solid var(--border);
    padding-top: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .ai-backup-head {
    display: flex;
    flex-direction: column;
    gap: 2px;
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
