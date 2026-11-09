import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Slate, SlateError } from '../clients/typescript/slate.ts';

for (const denied of [false, true]) {
  test(`execution proof and permission loss (denied=${denied})`, async () => {
    let takes = 0;
    const mutations = [];
    const sdk = new Slate('http://fixture', 'test-token', { fetch: async (url, opts) => {
      const body = opts.body ? JSON.parse(opts.body) : {};
      if (url.endsWith('/work/next')) {
        return Response.json(takes++ ? { item: null } : { item: {id:'task'}, lease: {run_id:'attempt-proof'} });
      }
      mutations.push({url, body});
      if (url.endsWith('/heartbeat') && denied) return Response.json({detail:'lost access'}, {status:403});
      if (url.endsWith('/heartbeat')) return Response.json({run_id:'attempt-proof'});
      if (url.endsWith('/finish')) return Response.json({item:{id:'task'}, next:null});
      throw new Error(`unexpected ${url}`);
    }});
    const run = sdk.workForever(() => 'answer', {once:true, wait:0});
    if (denied) await assert.rejects(run, SlateError);
    else await run;
    assert.ok(mutations.length);
    assert.ok(mutations.every(x => x.body.run_id === 'attempt-proof'));
    assert.equal(mutations.filter(x=>x.url.endsWith('/finish')).length, denied ? 0 : 1);
  });
}

test('legacy loop uses fenced admission rather than report/claim', async () => {
  const calls = [];
  const sdk = new Slate('http://fixture', 'test-token', {fetch: async (url) => {
    calls.push(url);
    if (url.endsWith('/work/next')) return Response.json({item:null});
    throw new Error('unfenced legacy loop');
  }});
  await sdk.runForever(() => {throw new Error('must not run')}, {once:true});
  assert.equal(calls.length, 1);
});
