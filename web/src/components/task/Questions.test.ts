// @vitest-environment happy-dom
// A question for a person is structured data: the comment that asks shows its questions in one
// block at the top, and the task window's banner asks for every answer at once. A review for the
// viewer is a banner of the same family that says what to check.
import { flushSync, mount, unmount, type Component } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderMarkdown } from '../../lib/markdown';
import type { AgentFlag, Comment, CommentAsk } from '../../lib/types';

const { board, task } = vi.hoisted(() => ({
  board: {
    me: { account: { id: 'me', handle: 'alex', display_name: 'Alex' }, link_schemes: ['https'] },
    now: new Date('2026-10-29T12:00:00Z'),
    accountName: (id: string) => ({ me: 'Alex', sc: 'My Agent', other: 'Sam' })[id],
    update: vi.fn(async () => true),
  },
  task: {
    me: 'me',
    answer: vi.fn(async () => true),
    comment: vi.fn(async (_itemId: string, _body: string) => true),
    reveal: vi.fn(),
    clearAgentFlag: vi.fn(async () => true),
  },
}));
vi.mock('../../lib/store.svelte', () => ({ board }));
vi.mock('../../lib/task.svelte', () => ({ task }));

import AskQuestions from './AskQuestions.svelte';
import TurnBanner from './TurnBanner.svelte';

const THREE: CommentAsk = {
  question: '3 questions: Which branch should I ship?',
  options: [],
  questions: [
    { question: 'Which branch should I ship?', options: ['main', 'next'] },
    { question: 'Tell the customer today?', options: ['Yes', 'No'] },
    { question: 'Who signs off the invoice?', options: [] },
  ],
};
const ONE: CommentAsk = { question: 'Start the import now?', options: ['Yes', 'No'], questions: [{ question: 'Start the import now?', options: ['Yes', 'No'] }] };

function askComment(over: Partial<Comment> = {}): Comment {
  return {
    id: 'C1',
    item_id: 'I1',
    author_id: 'sc',
    body_md: 'The report.',
    created_at: '2026-10-29T10:00:00Z',
    edited_at: null,
    assignee_id: 'me',
    resolved_at: null,
    ask: ONE,
    ...over,
  };
}

let close: (() => void) | null = null;
function show<P extends Record<string, unknown>>(component: Component<P>, props: P) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(component, { target, props });
  close = () => {
    void unmount(app);
    target.remove();
  };
  return target;
}
const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  close?.();
  close = null;
});

describe('the question block on a comment', () => {
  it('shows one question for the viewer, open, with live answers', () => {
    const el = show(AskQuestions, { comment: askComment() });
    expect(text(el.querySelector('.aq-head'))).toBe('Question for you Open');
    expect(el.querySelector('[data-open-ask-for-me]')?.getAttribute('data-open-ask-for-me')).toBe('C1');
    expect([...el.querySelectorAll('[data-ask-question]')].map(text)).toEqual(['Start the import now?']);
    const buttons = [...el.querySelectorAll<HTMLButtonElement>('button[data-answer-option]')];
    expect(buttons.map(text)).toEqual(['Yes', 'No']);
    buttons[1].click();
    expect(task.answer).toHaveBeenCalledWith('C1', 'No');
  });

  it('numbers three questions, each with its options as plain chips', () => {
    const el = show(AskQuestions, { comment: askComment({ ask: THREE }) });
    expect(text(el.querySelector('.aq-head'))).toBe('Questions for you Open');
    expect([...el.querySelectorAll('.aq-q')].map(text)).toEqual([
      '1. Which branch should I ship?',
      '2. Tell the customer today?',
      '3. Who signs off the invoice?',
    ]);
    expect([...el.querySelectorAll('.aq-item')].map((li) => [...li.querySelectorAll('.aq-opt')].map(text))).toEqual([
      ['main', 'next'],
      ['Yes', 'No'],
      [],
    ]);
    expect(el.querySelectorAll('button')).toHaveLength(0);
    expect([...el.querySelectorAll('[data-ask-option]')].map(text)).toEqual(['main', 'next', 'Yes', 'No']);
  });

  it('never offers live answers to someone the ask is not for, or once it is answered', () => {
    let el = show(AskQuestions, { comment: askComment({ assignee_id: 'other' }) });
    expect(text(el.querySelector('.aq-head'))).toBe('Question for Sam Open');
    expect(el.querySelectorAll('button')).toHaveLength(0);
    expect(el.querySelector('[data-open-ask-for-me]')).toBeNull();
    close!();
    el = show(AskQuestions, { comment: askComment({ resolved_at: '2026-10-29T11:00:00Z' }) });
    expect(text(el.querySelector('.aq-head'))).toBe('Question for you Answered');
    expect(el.querySelectorAll('button')).toHaveLength(0);
    expect([...el.querySelectorAll('[data-ask-option]')].map(text)).toEqual(['Yes', 'No']);
  });

  it('reads an ask from a server that sends only the one question', () => {
    const el = show(AskQuestions, { comment: askComment({ ask: { question: 'Start now?', options: ['Yes', 'No'] } }) });
    expect([...el.querySelectorAll('[data-ask-question]')].map(text)).toEqual(['Start now?']);
    expect(el.querySelectorAll('button[data-answer-option]')).toHaveLength(2);
  });
});

describe('the waiting banner with several questions', () => {
  const flag: AgentFlag = { state: 'blocked', by: 'sc', at: '2026-10-29T10:00:00Z', url: 'https://example.com/s/1' };
  const wait = { structured: true, by: 'sc', commentId: 'C1', question: THREE.question, questions: THREE.questions! };

  it('asks for every answer and sends them as one comment', async () => {
    const el = show(TurnBanner, { itemId: 'I1', wait, review: null, flag });
    const banner = el.querySelector('[data-banner="waiting"]')!;
    expect(text(banner.querySelector('.wq-heading'))).toBe('My Agent needs 3 answers from you');
    expect([...banner.querySelectorAll('.wq-q')].map(text)).toEqual([
      '1. Which branch should I ship?',
      '2. Tell the customer today?',
      '3. Who signs off the invoice?',
    ]);
    expect(banner.querySelector('[data-answer-option]')).toBeNull();
    const send = banner.querySelector<HTMLButtonElement>('[data-send-answers]')!;
    expect(send.disabled).toBe(true);

    const chip = (option: string) => banner.querySelector<HTMLButtonElement>(`[data-pick-option="${option}"]`)!;
    const typeIn = (i: number, value: string) => {
      const input = banner.querySelectorAll<HTMLInputElement>('.wq-type')[i];
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
    };
    chip('next').click();
    flushSync();
    expect(chip('next').getAttribute('aria-pressed')).toBe('true');
    chip('main').click();
    flushSync();
    expect(chip('main').getAttribute('aria-pressed')).toBe('true');
    expect(chip('next').getAttribute('aria-pressed')).toBe('false');
    typeIn(1, 'Not until Friday');
    expect(send.disabled).toBe(true);
    typeIn(2, '   ');
    expect(send.disabled).toBe(true);
    typeIn(2, 'Alex');
    expect(send.disabled).toBe(false);

    send.click();
    await vi.waitFor(() => expect(task.comment).toHaveBeenCalledTimes(1));
    expect(task.comment).toHaveBeenCalledWith(
      'I1',
      '1. Which branch should I ship? **main**\n2. Tell the customer today? **Not until Friday**\n3. Who signs off the invoice? **Alex**',
    );
    expect(task.answer).not.toHaveBeenCalled();
  });

  it('renders punctuation in typed answers literally inside bold text', async () => {
    const el = show(TurnBanner, { itemId: 'I1', wait, review: null, flag });
    const answers = ['A*B', '_draft_', '[Plan](https://example.com)'];
    el.querySelectorAll<HTMLInputElement>('.wq-type').forEach((input, i) => {
      input.value = answers[i];
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    flushSync();
    el.querySelector<HTMLButtonElement>('[data-send-answers]')!.click();
    await vi.waitFor(() => expect(task.comment).toHaveBeenCalledTimes(1));
    const body = document.createElement('div');
    body.innerHTML = renderMarkdown(task.comment.mock.calls[0][1]);
    expect([...body.querySelectorAll('li > strong')].map(text)).toEqual(answers);
    expect(body.querySelector('strong em, strong a, li > a')).toBeNull();
  });

  it('keeps Answer for a free reply, with the age and the Open link', () => {
    const el = show(TurnBanner, { itemId: 'I1', wait, review: null, flag });
    expect(text(el.querySelector('[data-waiting-age]'))).toContain('2 h');
    expect(el.querySelector<HTMLAnchorElement>('[data-waiting-open]')?.href).toBe('https://example.com/s/1');
    el.querySelector<HTMLButtonElement>('[data-answer]')!.click();
    expect(task.reveal).toHaveBeenCalledWith('C1');
  });

  it('keeps one question as it was: the question as heading and its options as answers', () => {
    const one = { structured: true, by: 'sc', commentId: 'C1', question: ONE.question, questions: ONE.questions! };
    const el = show(TurnBanner, { itemId: 'I1', wait: one, review: null, flag });
    expect(text(el.querySelector('.wq-heading'))).toBe('Start the import now?');
    expect(text(el.querySelector('.wq-sub'))).toContain('My Agent asks');
    expect(el.querySelector('[data-send-answers]')).toBeNull();
    el.querySelector<HTMLButtonElement>('[data-answer-option="Yes"]')!.click();
    expect(task.answer).toHaveBeenCalledWith('C1', 'Yes');
  });
});

describe('the review banner for the viewer', () => {
  const flag: AgentFlag = { state: 'review', by: 'sc', at: '2026-10-29T11:00:00Z', note: 'Here is the full weekly report, first line.' };
  const review = { line: 'Check the totals in the March column', by_id: 'sc', at: '2026-10-29T11:00:00Z', move_id: 'me' };

  it('says what to check, who finished, and marks it reviewed', () => {
    const el = show(TurnBanner, { itemId: 'I1', wait: null, review, flag });
    const banner = el.querySelector('[data-banner="review"]')!;
    expect([...banner.children].map((child) => child.className.split(' ')[0])).toEqual([
      'wq-heading', 'wq-head', 'wq-actions',
    ]);
    expect(text(banner.querySelector('.wq-heading'))).toBe('Check the totals in the March column');
    expect(text(banner.querySelector('.wq-sub'))).toContain('My Agent finished and asks you to check');
    expect(text(banner.querySelector('.wq-sub [data-waiting-age]'))).toContain('1 h');
    expect(text(banner)).not.toContain('first line');
    expect(banner.querySelector('[data-standing]')).toBeNull();
    banner.querySelector<HTMLButtonElement>('[data-mark-reviewed]')!.click();
    expect(task.clearAgentFlag).toHaveBeenCalledWith('I1');
  });
});
