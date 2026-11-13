import { describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import { LazyPage } from './lazy.svelte';

const Page = (() => {}) as unknown as Component;

describe('LazyPage', () => {
  it('loads once on demand and keeps the component', async () => {
    const loader = vi.fn(async () => ({ default: Page }));
    const page = new LazyPage(loader);
    expect(page.component).toBeNull();
    expect(loader).not.toHaveBeenCalled();
    await Promise.all([page.load(), page.load()]);
    expect(page.component).toBe(Page);
    await page.load();
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('a failed load says so, and trying again loads it', async () => {
    const loader = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue({ default: Page });
    const page = new LazyPage(loader);
    await page.load();
    expect(page.failed).toBe(true);
    expect(page.component).toBeNull();
    await page.load();
    expect(page.failed).toBe(false);
    expect(page.component).toBe(Page);
  });

  it('a background fetch that fails stays quiet, so opening the page still tries', async () => {
    const loader = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue({ default: Page });
    const page = new LazyPage(loader);
    await page.prefetch();
    expect(page.failed).toBe(false);
    expect(page.component).toBeNull();
    await page.load();
    expect(page.component).toBe(Page);
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
