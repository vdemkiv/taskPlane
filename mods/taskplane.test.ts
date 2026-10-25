import { expect, mock, test } from 'claude-code/testing';
import type { On, RenderElement, RenderPropsOf } from 'claude-code';
import { Coordinator } from './taskplane';
import type { Binding, Snapshot } from './taskplane';

const findingSelection = (id: string, origin: string | null = null) => JSON.stringify([origin, id]);
const binding: Binding = { workspace: '/fixture', root: 'session-1', run: 'run-1', revision: 7, visit: 'visit-1', checkpoint: 'checkpoint-1', manifest_digest: 'evidence-1', scope_digest: 'scope-1' };
const snapshot = (generation = 0, run = 'run-1'): Snapshot => ({
  schema: 'taskplane.claude-mods-snapshot/v1', selection: { workspace: '/fixture', root: 'session-1', run, generation }, status: 'ready', identity: { ...binding, run }, phase: 'build', work: 'complete', decision: 'pending', revision: 7, packet_revision: 6,
  evidence: { status: 'validated', drift: false }, findings: Array.from({ length: 12 }, (_,i) => ({ id: `F-${i+1}`, title: `Finding ${i+1}`, disposition: 'unresolved', verification: 'unknown', related_tasks: ['BLD-INTERFACE'], review_lenses: ['interface'], sources: [{ reference: 'report.json', status: 'available', text: `Source ${i+1}` }] })), current_checks: [{ id: 'check-1', status: 'failed', historical: false }], unresolved_count: 12, workers: { live: 2 }, run_usage: { tokens: { total_tokens: 0 }, coverage: 'partial', measurement: '2026-10-21T00:00:00Z' }, confirmed_at: '1970-01-01T00:00:01Z', snapshot_id: 'snapshot-1', capabilities: { snapshot: true, evidence: true, intent: true, direct_decision: true, native_confirmation: true, preview: false, reasons: [] }, fallback: { dashboard: '/fixture/.taskplane/dashboard.html', decision: 'conversation' }
});
function receiptFor(intent: Record<string, unknown>) {
  return { confirmed_revision: 8, confirmed_at: '1970-01-01T00:00:01Z', decision: { event_id: `mods/${String(intent.intent_id)}`, human: true, automatic: false, choice: intent.choice, binding: intent.binding, provenance: { comment: intent.comment } } };
}
function fixture(preview = false) {
  let now = 1000, seq = 0;
  const calls: Record<string, unknown>[] = [];
  const intents = new Map<string, Record<string, unknown>>();
  let lost = false, saved: unknown;
  const io: ConstructorParameters<typeof Coordinator>[0] = {
    identity: { workspace: '/fixture', root: 'session-1' },
    report: async (run: string, generation: number) => snapshot(generation, run || 'run-1'),
    decide: async (request: Record<string, unknown>) => {
      calls.push(request);
      const id = String(request.intent_id);
      if (request.operation === 'prepare') {
        const intent = { intent_id: id, binding: request.binding as Binding, choice: request.choice as 'approved', comment: String(request.comment), payload_digest: `digest-${id}` };
        intents.set(id, intent);
        return { schema: 'taskplane.claude-mods-response/v1', status: 'prepared', intent, presentation: { text: `Exact comment:\n${intent.comment}` } };
      }
      if (request.operation === 'record_ui' && lost) { lost = false; throw Error('reply lost'); }
      const intent = intents.get(id) as { intent_id: string; binding: Binding; choice: 'approved'; comment: string; payload_digest: string } | undefined;
      return { schema: 'taskplane.claude-mods-response/v1', status: intent ? 'recorded' : 'not_recorded', intent, receipt: intent ? receiptFor(intent) : undefined };
    },
    save: async (value: unknown) => { saved = JSON.parse(JSON.stringify(value)); }, now: async () => now, changed: () => {}, id: () => `attempt-${++seq}`
  };
  const c = new Coordinator(io, preview);
  return { c, io, calls, intents, clock: (time: number) => { now = time; }, lose: () => { lost = true; }, saved: () => saved };
}
const press = (element = 'confirm-changes') => ({ plugin: 'taskplane', component: 'Pane' as const, element, requestId: 'taskplane-checkpoint', surface: 'terminal' as const });

function drift(s: Snapshot) {
  s.decision = 'stale'; s.evidence = { status: 'stale', invalidation_pending: true };
  s.capabilities.intent = false; s.capabilities.direct_decision = false;
  s.capabilities.reasons = ['Evidence changed'];
  s.capabilities.choices = {
    approved: { intent: false, direct_decision: false, reasons: ['Evidence changed'] },
    changes_requested: { intent: true, direct_decision: true, reasons: [] }
  };
  return s;
}

test('per-choice advice keeps negative confirmation after drift but retains binding checks', async () => {
  for (const choice of ['approved', 'changes_requested'] as const) {
    const f = fixture(); await f.c.restore(undefined);
    await f.c.prepare(choice, binding, '  Fix café\n');
    const attempt = f.c.pending!;
    drift(f.c.snapshot!);
    await f.c.confirm(attempt, press(choice === 'approved' ? 'confirm-accept' : 'confirm-changes'), 'session-1');
    expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(choice === 'approved' ? 0 : 1);
    expect(attempt.comment).toBe('  Fix café\n');
  }
  const f = fixture(); await f.c.restore(undefined); drift(f.c.snapshot!);
  await f.c.prepare('changes_requested', binding, 'after drift');
  expect(f.calls).toHaveLength(1);
  f.c.snapshot!.identity = { ...binding, revision: 8 };
  await f.c.confirm(f.c.pending!, press(), 'session-1');
  expect(f.calls).toHaveLength(1);
});

test('legacy capability fallback is conservative and malformed per-choice branches refuse', async () => {
  const f = fixture(); await f.c.restore(undefined);
  expect(f.c.eligibility(binding, 'changes_requested')).toBeUndefined();
  f.c.snapshot!.capabilities.direct_decision = false;
  expect(f.c.eligibility(binding, 'changes_requested')).toBeDefined();
  for (const choices of [null, {}, { changes_requested: {} }, { changes_requested: { intent: 'yes', direct_decision: true, reasons: [] } }, { changes_requested: { intent: true, direct_decision: true, reasons: null } }]) {
    f.c.snapshot!.capabilities.choices = choices as unknown as Snapshot['capabilities']['choices'];
    expect(f.c.eligibility(binding, 'changes_requested')).toBeDefined();
  }
});

test('exact Unicode and multiline intent survives lost acknowledgement and reload without replay', async () => {
  const f = fixture(); await f.c.restore(undefined);
  const text = '  Repair café 🛠️\n\nKeep the final newline.\n';
  await f.c.edit(text, binding); await f.c.prepare('changes_requested', binding, text);
  expect(f.calls[0]?.comment).toBe(text);
  const pending = f.c.pending!;
  f.lose(); await f.c.confirm(pending, press(), 'session-1');
  expect(f.c.pending?.outcome).toBe('unknown'); expect(f.c.draft(binding)).toBe(text);
  const reload = new Coordinator(f.io);
  await reload.restore(f.saved());
  expect(reload.pending?.outcome).toBe('recorded');
  expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(1);
  expect(f.calls.at(-1)?.operation).toBe('receipt');
  expect(reload.message).toBe('Decision recorded by Taskplane');
});

test('every binding field and a stale confirmation reject before any write', async () => {
  const f = fixture(); await f.c.restore(undefined);
  for (const field of Object.keys(binding)) {
    await f.c.prepare('approved', { ...binding, [field]: field === 'revision' ? 8 : 'other' }, 'comment');
  }
  expect(f.calls).toHaveLength(0);
  await f.c.prepare('approved', binding, 'comment');
  const pending = f.c.pending!;
  f.c.snapshot!.identity = { ...binding, revision: 8 };
  await f.c.confirm(pending, press('confirm-accept'), 'session-1');
  expect(f.calls).toHaveLength(1); expect(f.c.message).toMatch('Checkpoint changed');
});

test('preview blocks preparation, confirmation, receipt and reload mutation', async () => {
  const f = fixture(true); await f.c.restore(undefined);
  await f.c.prepare('approved', binding, 'comment'); await f.c.reconcile();
  expect(f.calls).toHaveLength(0); expect(f.c.eligibility()).toMatch('Preview');
});

test('duplicate presses freeze one intent and record only once', async () => {
  const f = fixture(); await f.c.restore(undefined);
  await Promise.all([f.c.prepare('approved', binding, 'first'), f.c.prepare('changes_requested', binding, 'second')]);
  expect(f.calls).toHaveLength(1); expect(f.c.pending?.comment).toBe('first');
  const pending = f.c.pending!;
  await Promise.all([f.c.confirm(pending, press('confirm-accept'), 'session-1'), f.c.confirm(pending, press('confirm-accept'), 'session-1')]);
  expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(1);
  expect(f.c.pending?.outcome).toBe('recorded');
});

test('obsolete run reply is discarded and selected generation is fetched serially', async () => {
  const f = fixture();
  let release!: (s: Snapshot) => void;
  const old = new Promise<Snapshot>(resolve => { release = resolve; });
  const requests: string[] = [];
  f.io.report = async (run, generation) => { requests.push(run); return requests.length === 1 ? old : snapshot(generation,run); };
  const first = f.c.refresh(); const change = f.c.select('run-2');
  expect(f.c.snapshot).toBeUndefined(); release(snapshot()); await Promise.all([first,change]);
  expect(f.c.snapshot?.selection.run).toBe('run-2'); expect(requests).toEqual(['','run-2']);
});

test('refresh failure and five-second lapse disable decisions; fresh recovery restores them', async () => {
  const f = fixture(); await f.c.restore(undefined);
  f.clock(6001); f.io.report = async () => { throw Error('permission denied'); }; await f.c.tick();
  expect(f.c.stale).toBe(true); expect(f.c.snapshot?.confirmed_at).toBe('1970-01-01T00:00:01Z');
  await f.c.prepare('approved', binding, 'retained'); expect(f.calls).toHaveLength(0);
  f.io.report = async (run,generation) => ({ ...snapshot(generation,run), confirmed_at: new Date(6001).toISOString() }); await f.c.refresh();
  expect(f.c.stale).toBe(false); expect(f.c.eligibility()).toBeUndefined();
});

test('run selection during the refresh clock read cannot restore or persist the obsolete run', async () => {
  const f = fixture();
  let release!: (time: number) => void, entered!: () => void;
  const clockEntered = new Promise<void>(resolve => { entered = resolve; });
  const delayedClock = new Promise<number>(resolve => { release = resolve; });
  let reads = 0;
  const savedRuns: string[] = [];
  const save = f.io.save;
  f.io.save = async state => { savedRuns.push(state.run); await save(state); };
  f.io.now = async () => { if (++reads === 1) { entered(); return delayedClock; } return 1000; };
  const refresh = f.c.refresh(); await clockEntered;
  const selection = f.c.select('run-2'); release(1000);
  await Promise.all([refresh, selection]);
  expect(f.c.run).toBe('run-2'); expect(f.c.runInput).toBe('run-2');
  expect(f.c.snapshot?.selection.run).toBe('run-2');
  expect(f.c.snapshot?.selection.generation).toBe(1);
  expect(savedRuns).toEqual(['run-2']);
});

test('preparation rejection before ledger creation retains text and allows explicit retry after reload', async () => {
  const f = fixture(); await f.c.restore(undefined);
  const decide = f.io.decide!;
  f.io.decide = async request => {
    if (request.operation === 'prepare' && String(request.comment).length > 4000) throw Error('complete presentation exceeds limit');
    return decide(request);
  };
  const text = 'x'.repeat(4090); await f.c.edit(text,binding);
  await f.c.prepare('changes_requested',binding,text);
  expect(f.c.pending?.outcome).toBe('not_recorded'); expect(f.c.draft(binding)).toBe(text);
  await f.c.reconcile(); expect(f.calls).toHaveLength(0);
  const reload = new Coordinator(f.io); await reload.restore(f.saved());
  expect(reload.pending?.outcome).toBe('not_recorded'); expect(reload.draft(binding)).toBe(text);
  expect(f.calls).toHaveLength(0);
  await reload.edit('shorter revision',binding); await reload.prepare('changes_requested',binding,'shorter revision');
  expect(reload.pending?.outcome).toBe('prepared');
  await reload.confirm(reload.pending!,press(),'session-1');
  expect(reload.pending?.outcome).toBe('recorded');
  expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(1);
});

test('restoring interrupted preparation permits cancellation without consulting a missing ledger', async () => {
  const f = fixture();
  await f.c.restore({ run: 'run-1', drafts: {}, pending: { id: 'interrupted', binding, choice: 'approved', comment: 'kept', outcome: 'preparing' } });
  expect(f.c.pending?.outcome).toBe('not_recorded'); expect(f.calls).toHaveLength(0);
  await f.c.cancel(); expect(f.c.pending).toBeUndefined();
});

test('lost confirmation with unavailable receipt stays uncertain across reload and cannot be discarded', async () => {
  const f = fixture(); await f.c.restore(undefined);
  await f.c.edit('retain me',binding); await f.c.prepare('approved',binding,'retain me');
  const decide = f.io.decide!;
  f.io.decide = async request => {
    if (request.operation === 'receipt') throw Error('No saved attempt');
    return decide(request);
  };
  f.lose(); await f.c.confirm(f.c.pending!,press('confirm-accept'),'session-1');
  const reload = new Coordinator(f.io); await reload.restore(f.saved());
  await reload.cancel(); await reload.prepare('approved',binding,'replacement');
  expect(reload.pending?.outcome).toBe('unknown'); expect(reload.draft(binding)).toBe('retain me');
  expect(f.calls.filter(x => x.operation === 'prepare')).toHaveLength(1);
  expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(1);
});

test('drafts are checkpoint scoped and oversize input is never truncated or submitted', async () => {
  const f = fixture(); await f.c.restore(undefined);
  const text = '🛠'.repeat(6000); await f.c.edit(text,binding); await f.c.prepare('changes_requested',binding,text);
  expect(f.calls).toHaveLength(0); expect(f.c.draft(binding)).toBe(text);
  expect(f.c.draft({ ...binding, checkpoint: 'next' })).toBe('');
});

test('stable finding selection survives reorder and reports removal without choosing a neighbor', async () => {
  const f = fixture(); await f.c.restore(undefined); f.c.pick(findingSelection('F-12'));
  f.io.report = async (_,generation) => ({ ...snapshot(generation), findings: snapshot().findings!.toReversed() });
  await f.c.refresh(); expect(f.c.finding).toBe(findingSelection('F-12'));
  f.io.report = async (_,generation) => ({ ...snapshot(generation), findings: [] });
  await f.c.refresh(); expect(f.c.finding).toBe(findingSelection('F-12'));
});

function hostFixtures(on: On, initial = snapshot(), session = 'session-1') {
  const clock = mock.clock(on, { now: 1000 }); mock.store(on);
  on('session.start', (_$,e) => ({ cwd: e.cwd }));
  on('command.register', (_$,e) => ({ value: { command: e.name } }));
  on('ui.close', () => ({ value: undefined }));
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => { const { Text } = $.ui.resolve(e); return h(Text, {}, 'Other Mod band') as RenderElement; });
  const calls: Record<string, unknown>[] = [];
  const processes: { argv: readonly string[]; env?: Record<string, string | undefined>; cwd?: string }[] = [];
  const state = { value: initial, fail: false, lost: false, recorded: false, intent: undefined as Record<string, unknown> | undefined };
  on('session.root', () => ({ value: '/fixture' })); on('session.id', () => ({ value: session }));
  on('session.usage', () => ({ value: { startedAt: 1000, context: { window: 200000 }, rateLimits: [] } }));
  on('process.run', (_$, e) => {
    const argv = e.argv;
    processes.push({ argv, env: e.init?.env, cwd: e.init?.cwd });
    if (state.fail) return { value: { exitCode: 2, stdout: JSON.stringify({ status: 'blocked', reason: 'permission denied' }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } };
    let reply: unknown;
    if (argv.includes('--mods')) {
      const generation = Number(argv[argv.indexOf('--mods-generation')+1]);
      const run = argv.includes('--run') ? argv[argv.indexOf('--run')+1] : state.value.selection.run;
      reply = { ...state.value, selection: { ...state.value.selection, generation, run } };
    } else {
      const request = JSON.parse(argv[argv.indexOf('--mods-request-json')+1]!) as Record<string,unknown>; calls.push(request);
      if (request.operation === 'prepare') {
        state.intent = { intent_id: request.intent_id, binding: request.binding, choice: request.choice, comment: request.comment, payload_digest: 'digest-1' };
        reply = { schema: 'taskplane.claude-mods-response/v1', status: 'prepared', intent: state.intent, presentation: { text: `Review exact choice and comment:\n${String(request.choice)}\n${String(request.comment)}` } };
      } else {
        if (request.operation === 'record_ui') { state.recorded = true; if (state.lost) { state.lost = false; return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }; } }
        reply = { schema: 'taskplane.claude-mods-response/v1', status: state.recorded ? 'recorded' : 'prepared', intent: state.intent, receipt: state.recorded ? receiptFor(state.intent!) : undefined };
      }
    }
    return { value: { exitCode: 0, stdout: JSON.stringify(reply), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } };
  });
  return { clock, calls, processes, state };
}
const pane = (width: number): RenderPropsOf['Pane'] => ({ title: 'Taskplane', isFocused: true, bodyColumns: width, placement: width >= 110 ? 'dock' : 'inline', scroll: { offset: 0, bodyRows: 6 }, view: {} });

for (const surface of ['terminal', 'desktop'] as const) {
  for (const choice of ['approved', 'changes_requested'] as const) {
    test(`${surface}: drift checks saved ${choice} confirmation`, async ($, on) => {
      const f = hostFixtures(on);
      await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
      const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
      await ui.press({ key: choice === 'approved' ? 'prepare-accept' : 'prepare-changes' });
      drift(f.state.value); await ui.press({ key: 'refresh' });
      expect(await ui.find({ key: 'confirm-accept' })).toBeUndefined();
      expect(await ui.find({ type: 'Text', text: 'Evidence changed' })).toBeDefined();
      if (choice === 'changes_requested') {
        expect(await ui.find({ key: 'confirm-changes' })).toBeDefined();
        f.state.value.identity = { ...binding, revision: 8 };
        await ui.press({ key: 'refresh' });
        expect(await ui.find({ key: 'confirm-changes' })).toBeUndefined();
      }
      expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(0);
      await ui.unmount();
    });
  }
  test(`${surface}: Request changes remains available to prepare after evidence drift`, async ($, on) => {
    const f = hostFixtures(on, drift(snapshot()));
    await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
    const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
    expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
    expect(await ui.find({ key: 'prepare-changes' })).toBeDefined();
    await ui.press({ key: 'prepare-changes' }); await ui.press({ key: 'confirm-changes' });
    expect(f.calls.filter(x => x.operation === 'record_ui')).toHaveLength(1);
    await ui.unmount();
  });
  for (const source of ['journal_status', 'journal_detail_status', 'measurement', 'sessions', 'complete', 'unknown', 'missing', 'malformed']) {
    test(`${surface}: usage band preserves ${source} accounting coverage`, async ($, on) => {
      const s = snapshot();
      const counts: Record<string, unknown> = { measured_sessions: 2, unmeasured_sessions: 0, partial_sessions: 0 };
      s.run_usage.coverage = counts; s.run_usage.measurement = { status: 'measured' };
      if (source === 'journal_status' || source === 'journal_detail_status') counts[source] = 'partial';
      if (source === 'measurement') s.run_usage.measurement = { status: 'partial' };
      if (source === 'sessions') counts.partial_sessions = 1;
      if (source === 'unknown') { s.run_usage.measurement = { status: 'unavailable' }; s.run_usage.tokens = null; }
      if (source === 'missing') s.run_usage.coverage = null;
      if (source === 'malformed') s.run_usage.coverage = 'partial';
      hostFixtures(on, s);
      await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
      const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 6, bodyColumns: 80, scroll: { offset: 0, bodyRows: 5 }, view: {} } });
      const label = ['unknown', 'missing', 'malformed'].includes(source) ? 'Unknown coverage' : source === 'complete' ? '2 measured sessions' : 'Partial coverage';
      expect(await ui.find({ type: 'Text', text: `Run tokens: ${source === 'unknown' ? 'Unknown' : '0'} (${label})` })).toBeDefined();
      await ui.unmount();
    });
  }
}

for (const surface of ['terminal','desktop'] as const) {
  for (const width of [40,80,160]) {
    test(`${surface} ${width} columns: native controls, metadata and sequential evidence`, async ($, on) => {
      const f = hostFixtures(on);
      await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
      const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(width) });
      expect(await ui.find({ type: 'Text', text: 'Evidence validation: validated' })).toBeDefined();
      expect(await ui.find({ type: 'Text', text: 'Unresolved findings: 12' })).toBeDefined();
      expect(await ui.find({ type: 'Text', text: 'Run tokens: 0' })).toBeDefined();
      expect(await ui.find({ type: 'Text', text: 'Context window: Unknown' })).toBeDefined();
      expect(await ui.find({ type: 'Text', text: 'checkpoint-1' })).toBeDefined();
      await ui.select({ key: 'finding-number', value: findingSelection('F-12') });
      expect(await ui.find({ type: 'Text', text: 'Finding 12 of 12' })).toBeDefined();
      await ui.press({ key: 'previous' }); expect(await ui.find({ type: 'Text', text: 'Finding 11 of 12' })).toBeDefined();
      await ui.press({ key: 'next' }); await ui.press({ key: 'view-source' });
      expect(await ui.find({ type: 'Text', text: 'Source 12' })).toBeDefined();
      await ui.press({ key: 'view-verification' }); expect(await ui.find({ type: 'Text', text: 'failed' })).toBeDefined();
      expect(f.calls).toHaveLength(0);
      await ui.press({ key: 'close' }); expect(f.calls).toHaveLength(0);
      await ui.unmount();
    });
  }
  test(`${surface}: native change event and explicit confirmation preserve exact observed metadata`, async ($, on) => {
    const f = hostFixtures(on);
    await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
    const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
    const text = '  café\nfix it 🛠️\n';
    await ui.input({ key: 'revision-comment', text, kind: 'change' });
    await ui.input({ key: 'revision-comment', text, kind: 'submit' }); expect(f.calls).toHaveLength(0);
    await ui.press({ key: 'prepare-changes' }); expect(f.calls).toHaveLength(1); expect(f.calls[0]?.comment).toBe(text);
    expect(await ui.find({ type: 'Text', text })).toBeDefined();
    await ui.press({ key: 'confirm-changes' });
    expect(f.calls[1]?.control).toEqual({ plugin: 'taskplane', component: 'checkpoint', element: 'confirm-changes', request_id: 'taskplane-checkpoint', surface, session: 'session-1' });
    expect(await ui.find({ type: 'Text', text: 'Decision recorded by Taskplane' })).toBeDefined();
    await ui.unmount();
  });
}

test('AbovePrompt composes the engine tree and opens only on explicit press', async ($, on) => {
  const f = hostFixtures(on); let opens = 0;
  on('ui.open', (_$, e) => { opens++; expect(e.focus).toBe(true); expect(e.closeOnEscape).toBe(true); return { value: { isPlaced: true } }; });
  await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 6, bodyColumns: 40, scroll: { offset: 0, bodyRows: 5 }, view: {} } });
  expect(await ui.find({ type: 'Text', text: 'Other Mod band' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'Live workers: 2' })).toBeDefined(); expect(opens).toBe(0);
  await ui.press({ key: 'open-checkpoint' }); expect(opens).toBe(1); expect(f.calls).toHaveLength(0); await ui.unmount();
});

test('host permission refusal retains draft and prevents final confirmation', async ($, on) => {
  const f = hostFixtures(on); await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
  await ui.input({ key: 'revision-comment', text: 'preserve me', kind: 'change' });
  f.state.fail = true; await ui.press({ key: 'refresh' });
  expect(await ui.find({ type: 'Text', text: 'permission denied' })).toBeDefined();
  expect((await ui.find({ key: 'revision-comment' }))?.text).toBe('preserve me');
  expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined(); expect(f.calls).toHaveLength(0); await ui.unmount();
});

test('host missing, empty, removed findings remain distinct', async ($, on) => {
  const f = hostFixtures(on); await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
  await ui.select({ key: 'finding-number', value: findingSelection('F-12') });
  f.state.value.findings = [f.state.value.findings![0]!]; await ui.press({ key: 'refresh' });
  expect(await ui.find({ type: 'Text', text: 'Selected finding no longer' })).toBeDefined();
  f.state.value.findings = []; await ui.press({ key: 'refresh' }); expect(await ui.find({ type: 'Text', text: 'No findings recorded' })).toBeDefined();
  f.state.value.findings = null; await ui.press({ key: 'refresh' }); expect(await ui.find({ type: 'Text', text: 'Findings unavailable' })).toBeDefined(); await ui.unmount();
});


test('old, invalid and future controller timestamps never become fresh on arrival', async () => {
  for (const time of ['1970-01-01T00:00:01Z','invalid','2099-01-01T00:00:00Z']) {
    const f = fixture(); f.clock(9000);
    f.io.report = async (_,generation) => ({ ...snapshot(generation), confirmed_at: time });
    await f.c.restore(undefined); expect(f.c.stale).toBe(true);
    await f.c.prepare('approved',binding,'text'); expect(f.calls).toHaveLength(0);
  }
});

test('empty or mismatched authoritative receipts never report success', async () => {
  for (const wrong of ['empty','id','binding','choice','comment','time','revision','automatic']) {
    const f = fixture(); await f.c.restore(undefined); await f.c.edit('retain exact text',binding);
    await f.c.prepare('approved',binding,'retain exact text');
    const pending = f.c.pending!;
    const receipt = receiptFor(pending.intent!);
    if (wrong === 'id') receipt.decision.event_id = 'mods/foreign';
    if (wrong === 'binding') receipt.decision.binding = { ...binding, revision: 8 };
    if (wrong === 'choice') receipt.decision.choice = 'changes_requested';
    if (wrong === 'comment') receipt.decision.provenance.comment = 'different';
    if (wrong === 'time') receipt.confirmed_at = 'invalid';
    if (wrong === 'revision') receipt.confirmed_revision = 6;
    if (wrong === 'automatic') receipt.decision.automatic = true;
    f.io.decide = async () => ({ schema: 'taskplane.claude-mods-response/v1', status: 'recorded', intent: pending.intent!, receipt: wrong === 'empty' ? {} : receipt });
    await f.c.confirm(pending,press('confirm-accept'),'session-1');
    expect(f.c.pending?.outcome).toBe('unknown'); expect(f.c.message).toMatch('Outcome unknown'); expect(f.c.draft(binding)).toBe('retain exact text');
  }
});

test('failed durable client correlation stops submission while retaining the draft', async () => {
  const f = fixture(); await f.c.restore(undefined);
  f.io.save = async () => { throw Error('storage unavailable'); };
  await f.c.edit('draft',binding); await f.c.prepare('changes_requested',binding,'draft');
  expect(f.calls).toHaveLength(0); expect(f.c.draft(binding)).toBe('draft'); expect(f.c.pending?.outcome).toBe('not_recorded');
});

test('binding property order does not lose a checkpoint draft', async () => {
  const f = fixture(); await f.c.restore(undefined); await f.c.edit('draft',binding);
  const reordered = Object.fromEntries(Object.entries(binding).toReversed()) as Binding;
  expect(f.c.draft(reordered)).toBe('draft');
});

test('native preview and missing decision capability draw no decision controls', async ($, on) => {
  const s = snapshot(); s.capabilities.preview = true;
  const f = hostFixtures(on,s); await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
  expect(await ui.find({ type: 'Text', text: 'Preview — decisions unavailable' })).toBeDefined();
  expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
  f.state.value.capabilities.preview = false; f.state.value.capabilities.direct_decision = false;
  await ui.press({ key: 'refresh' }); expect(await ui.find({ key: 'prepare-changes' })).toBeUndefined();
  expect(await ui.find({ type: 'Text', text: 'Dashboard (read only)' })).toBeDefined(); expect(f.calls).toHaveLength(0);
  await ui.unmount();
});


test('band before host initialization never calls unknown state fresh', async ($, on) => {
  on('ui.render', { component: 'AbovePrompt' }, ($,e) => { const { Text } = $.ui.resolve(e); return h(Text,{},'Other band') as RenderElement; });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 6, bodyColumns: 40, scroll: { offset: 0, bodyRows: 5 }, view: {} } });
  expect(await ui.find({ type: 'Text', text: 'Loading · Last confirmed update: Unknown' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'Fresh · Last confirmed update: Unknown' })).toBeUndefined(); await ui.unmount();
});

test('a slow refresh cannot leave an old snapshot eligible for a button callback', async () => {
  const f = fixture(); await f.c.restore(undefined);
  let release!: (s: Snapshot) => void;
  f.io.report = async () => new Promise<Snapshot>(resolve => { release = resolve; });
  const flight = f.c.refresh(); f.clock(9000);
  await f.c.prepare('approved',binding,'draft'); expect(f.calls).toHaveLength(0); expect(f.c.stale).toBe(true);
  release(snapshot()); await flight; expect(f.c.stale).toBe(true);
});


test('source evidence is paged and keeps its reference and digest', async ($, on) => {
  const s = snapshot(); s.findings![0]!.sources = [{ reference: 'sealed/report.json', digest: 'sha256-evidence', status: 'available', text: 'x'.repeat(3000) + 'end-of-source' }];
  hostFixtures(on,s); await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
  await ui.press({ key: 'view-source' }); expect(await ui.find({ type: 'Text', text: 'sha256-evidence' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'end-of-source' })).toBeUndefined(); await ui.press({ key: 'page-next' });
  expect(await ui.find({ type: 'Text', text: 'end-of-source' })).toBeDefined(); await ui.unmount();
});

test('unsupported snapshot contract refuses decisions and preserves the last confirmation', async () => {
  const f = fixture(); await f.c.restore(undefined);
  f.io.report = async () => ({ ...snapshot(), schema: 'unsupported' } as unknown as Snapshot);
  await f.c.refresh(); expect(f.c.stale).toBe(true); expect(f.c.snapshot?.snapshot_id).toBe('snapshot-1');
  await f.c.prepare('approved',binding,'text'); expect(f.calls).toHaveLength(0);
});


test('changed checkpoint keeps the previous draft inspectable without retargeting it', async ($, on) => {
  const f = hostFixtures(on); await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
  await ui.input({ key: 'revision-comment', text: 'old checkpoint comment', kind: 'change' });
  f.state.value.identity = { ...binding, checkpoint: 'checkpoint-2' }; await ui.press({ key: 'refresh' });
  expect((await ui.find({ key: 'revision-comment' }))?.text).toBe('');
  expect(await ui.find({ key: 'draft-history' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'old checkpoint comment' })).toBeDefined();
  expect(f.calls).toHaveLength(0); await ui.unmount();
});

test('native bridge passes the captured session on report, prepare, confirmation and receipt', async ($, on) => {
  const f = hostFixtures(on);
  await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
  await ui.input({ key: 'revision-comment', text: 'exact native session', kind: 'change' });
  await ui.press({ key: 'prepare-accept' });
  f.state.lost = true;
  await ui.press({ key: 'confirm-accept' });
  await ui.press({ key: 'receipt' });
  expect(f.calls.map(call => call.operation)).toEqual(['prepare','record_ui','receipt']);
  expect(f.calls[0]?.binding).toEqual(binding);
  expect((f.calls[1]?.control as Record<string, unknown>).session).toBe('session-1');
  expect(f.processes[0]?.argv[0]).toBe('python3');
  expect(f.processes[0]?.argv[1]).toMatch('/taskplane/tp.py');
  expect(f.processes[0]?.argv.slice(2)).toEqual(['flow', 'report', '--mods', '--mods-generation', '0', '--workspace', '/fixture']);
  for (const process of f.processes) {
    expect(process.env).toEqual({ TASKPLANE_CLAUDE_SESSION_ID: 'session-1' });
    expect(process.cwd).toBe('/fixture');
    expect(process.argv.slice(-2)).toEqual(['--workspace','/fixture']);
    if (process.argv.includes('--mods-request-json')) expect(process.argv[3]).toBe('decide');
  }
  expect(await ui.find({ type: 'Text', text: 'Decision recorded by Taskplane' })).toBeDefined();
  await ui.unmount();
});

for (const session of ['', '   ']) {
  test(`missing native identity ${JSON.stringify(session)} starts no bridge process`, async ($, on) => {
    const f = hostFixtures(on, snapshot(), session);
    await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
    const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
    await ui.press({ key: 'refresh' });
    expect(f.processes).toHaveLength(0);
    expect(await ui.find({ type: 'Text', text: 'Native session identity unavailable' })).toBeDefined();
    expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
    expect(await ui.find({ type: 'Text', text: 'Fresh · Last confirmed update' })).toBeUndefined();
    await ui.unmount();
  });
}

for (const mismatch of ['workspace','root','identity-workspace','identity-root','identity-run'] as const) {
  test(`native snapshot ${mismatch} mismatch never becomes Fresh or eligible`, async ($, on) => {
    const s = snapshot();
    if (mismatch === 'workspace') s.selection.workspace = '/other';
    else if (mismatch === 'root') s.selection.root = 'local';
    else if (mismatch === 'identity-workspace') s.identity = { ...binding, workspace: '/other' };
    else if (mismatch === 'identity-root') s.identity = { ...binding, root: 'other-session' };
    else s.identity = { ...binding, run: 'other-run' };
    const f = hostFixtures(on,s);
    await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
    const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
    expect(await ui.find({ type: 'Text', text: 'Stale — refresh failed' })).toBeDefined();
    expect(await ui.find({ type: 'Text', text: 'Fresh · Last confirmed update' })).toBeUndefined();
    expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
    expect(f.calls).toHaveLength(0);
    await ui.unmount();
  });
}

test('native no-run state shows unavailable findings and no changed checkpoint', async ($, on) => {
  const s = snapshot();
  s.status = 'no_run'; s.selection.run = null; s.identity = null;
  s.findings = []; s.unresolved_count = null; s.evidence = { status: 'unavailable' };
  s.capabilities.direct_decision = false; s.capabilities.intent = false;
  const f = hostFixtures(on,s);
  await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
  expect(await ui.find({ type: 'Text', text: 'No selected run — checkpoint and findings unavailable' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'Findings unavailable' })).toBeDefined();
  expect(await ui.find({ type: 'Text', text: 'Checkpoint changed' })).toBeUndefined();
  expect(await ui.find({ type: 'Text', text: 'No findings recorded' })).toBeUndefined();
  expect(await ui.find({ type: 'Text', text: 'Fresh · Last confirmed update' })).toBeUndefined();
  expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
  expect(f.processes[0]?.env).toEqual({ TASKPLANE_CLAUDE_SESSION_ID: 'session-1' });
  await ui.unmount();
});

test('explicit foreign historical selection remains read only under the native session', async ($, on) => {
  const f = hostFixtures(on);
  await $.session.start({ cwd: '/fixture', surface: 'terminal', isInteractive: true });
  const ui = await $.ui.mount({ plugin: 'taskplane', surface: 'terminal', component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(80) });
  f.state.value = { ...snapshot(0,'historical-run'), historical: true };
  f.state.value.selection.root = 'other-session';
  f.state.value.identity = { ...binding, root: 'other-session', run: 'historical-run' };
  // Even an incorrectly permissive controller capability cannot transfer ownership.
  await ui.input({ key: 'run', text: 'historical-run', kind: 'submit' });
  expect(await ui.find({ type: 'Text', text: 'Historical run belongs to another session — read only' })).toBeDefined();
  expect(await ui.find({ key: 'prepare-accept' })).toBeUndefined();
  expect(f.processes.at(-1)?.argv.slice(2)).toEqual(['flow', 'report', '--mods', '--mods-generation', '1', '--run', 'historical-run', '--workspace', '/fixture']);
  expect(f.processes.at(-1)?.env).toEqual({ TASKPLANE_CLAUDE_SESSION_ID: 'session-1' });
  expect(f.calls).toHaveLength(0);
  await ui.unmount();
});

test('foreign automatic selection and foreign saved attempts cannot acquire native authority', async () => {
  const f = fixture();
  f.io.report = async (_,generation) => ({ ...snapshot(generation), historical: true, selection: { workspace: '/fixture', root: 'other-session', run: 'run-1', generation }, identity: { ...binding, root: 'other-session' } });
  await f.c.restore({ run: '', drafts: {}, pending: { id: 'foreign-attempt', binding: { ...binding, root: 'other-session' }, choice: 'approved', comment: 'foreign', outcome: 'unknown' } });
  expect(f.c.stale).toBe(true); expect(f.c.snapshot).toBeUndefined();
  expect(f.calls).toHaveLength(0);
});

for (const surface of ['terminal','desktop'] as const) {
  test(`${surface}: same-ID findings retain origin through navigation, reorder and removal`, async ($, on) => {
    const s = snapshot();
    s.findings = [
      { id: 'F-1', origin_run: 'original', title: 'Inherited obligation', disposition: 'unresolved', verification: 'historical', sources: [{ reference: 'old.json', status: 'available', text: 'Original evidence' }] },
      { id: 'F-1', origin_run: 'replacement', title: 'Current obligation', disposition: 'accepted', verification: 'verified', sources: [{ reference: 'new.json', status: 'available', text: 'Replacement evidence' }] },
      { id: 'F-2', origin_run: 'replacement', title: 'Following obligation' }
    ];
    const f = hostFixtures(on, s);
    await $.session.start({ cwd: '/fixture', surface, isInteractive: true });
    const ui = await $.ui.mount({ plugin: 'taskplane', surface, component: 'Pane', requestId: 'taskplane-checkpoint', props: pane(40) });
    expect(await ui.find({ type: 'Text', text: 'Finding 1 of 3 · F-1 · Inherited obligation' })).toBeDefined();
    await ui.press({ key: 'next' });
    expect(await ui.find({ type: 'Text', text: 'Finding 2 of 3 · F-1 · Current obligation' })).toBeDefined();
    await ui.press({ key: 'view-source' });
    expect(await ui.find({ type: 'Text', text: 'Replacement evidence' })).toBeDefined();
    await ui.press({ key: 'next' });
    expect(await ui.find({ type: 'Text', text: 'Finding 3 of 3 · F-2' })).toBeDefined();
    await ui.press({ key: 'previous' });
    expect(await ui.find({ type: 'Text', text: 'Current obligation' })).toBeDefined();
    await ui.select({ key: 'finding-number', value: findingSelection('F-1', 'original') });
    expect(await ui.find({ type: 'Text', text: 'Original evidence' })).toBeDefined();
    await ui.select({ key: 'finding-number', value: findingSelection('F-1', 'replacement') });
    f.state.value.findings = f.state.value.findings!.toReversed();
    await ui.press({ key: 'refresh' });
    expect(await ui.find({ type: 'Text', text: 'Current obligation' })).toBeDefined();
    expect(await ui.find({ type: 'Text', text: 'Replacement evidence' })).toBeDefined();
    f.state.value.findings = f.state.value.findings!.filter(x => x.title !== 'Current obligation');
    await ui.press({ key: 'refresh' });
    expect(await ui.find({ type: 'Text', text: 'Selected finding no longer' })).toBeDefined();
    expect(await ui.find({ type: 'Text', text: 'Original evidence' })).toBeUndefined();
    expect(f.calls).toHaveLength(0);
    await ui.unmount();
  });
}
