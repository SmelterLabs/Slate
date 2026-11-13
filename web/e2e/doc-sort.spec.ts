// The Docs & Files tree sort reorders the rows as soon as it changes, without a reload. The hub
// spec only checks that a row stays visible after a sort; this one checks the order itself.
import { expect, test } from '@playwright/test';
import { api, BASE_URL, seed, signIn } from './support';

async function upload(docId: string, token: string, note: string) {
  const res = await fetch(`${BASE_URL}/api/v1/docs/${docId}/revisions?filename=a.txt&note=${encodeURIComponent(note)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain', Authorization: 'Bearer ' + token },
    body: note,
  });
  expect(res.ok).toBe(true);
}

test('changing the sort reorders the tree at once', async ({ page }) => {
  const { alex } = seed();
  const stamp = Date.now();
  const sec = await api<{ id: string }>('POST', '/sections', { name: `Sort ${stamp}`, audience: 'work' });
  await api('PATCH', `/sections/${sec.id}`, { private: true });
  const ids: string[] = [];
  // Created in tree order Charlie, Alpha, Bravo; Bravo is changed last.
  for (const t of ['Charlie', 'Alpha', 'Bravo']) {
    const d = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: `${t} ${stamp}`, section_id: sec.id });
    await upload(d.id, alex, `${t} v1`);
    ids.push(d.id);
  }

  await signIn(page);
  await page.goto(`/#/docs/${ids[0]}`); // a document open, as when someone is working
  const group = page.getByRole('tree', { name: 'Pages and files' }).locator('.tree-group', { hasText: `Sort ${stamp}` });
  const titles = () => group.locator('.tree-title').allInnerTexts();
  const sort = page.getByLabel('Sort', { exact: true });

  await expect.poll(titles).toEqual([`Charlie ${stamp}`, `Alpha ${stamp}`, `Bravo ${stamp}`]);
  await sort.selectOption('name');
  await expect.poll(titles).toEqual([`Alpha ${stamp}`, `Bravo ${stamp}`, `Charlie ${stamp}`]);
  await sort.selectOption('updated');
  await expect.poll(titles).toEqual([`Bravo ${stamp}`, `Alpha ${stamp}`, `Charlie ${stamp}`]);
  await sort.selectOption('tree');
  await expect.poll(titles).toEqual([`Charlie ${stamp}`, `Alpha ${stamp}`, `Bravo ${stamp}`]);
});
