import type { EngineInterface, Register, SessionContextUsage, UiPressArgument } from 'claude-code';

// The controller owns authority. This store contains only display state and retry correlation.
export type Binding = Readonly<{
  workspace: string; root: string; run: string; revision: number; visit: string;
  checkpoint: string; manifest_digest: string; scope_digest: string;
}>;
type RecordData = Record<string, unknown>;
export type Finding = RecordData & { id: string; title?: string; sources?: RecordData[] };
export type Snapshot = {
  schema: 'taskplane.claude-mods-snapshot/v1';
  selection: { workspace: string; root: string; run: string | null; generation: number };
  status: 'ready' | 'no_run' | 'selection_unavailable'; identity: Binding | null;
  historical?: boolean;
  phase: unknown; work: unknown; decision: unknown; revision: unknown; packet_revision: unknown;
  evidence: RecordData; findings: Finding[] | null; current_checks: RecordData[];
  unresolved_count: number | null; workers: { live: number | null; pending?: number; status?: string } | null;
  run_usage: { tokens: Record<string, number | null> | null; coverage: unknown; measurement: unknown };
  confirmed_at: string; snapshot_id: string;
  capabilities: { snapshot: boolean; evidence: boolean; intent: boolean; direct_decision: boolean; native_confirmation: boolean; preview: boolean; reasons: string[] };
  fallback: { dashboard: string; decision: string };
};
type Choice = 'approved' | 'changes_requested';
type Intent = { intent_id: string; binding: Binding; choice: Choice; comment: string; payload_digest: string; created_at?: string };
type Reply = { schema: string; status: string; intent?: Intent; presentation?: { text: string }; receipt?: RecordData; reason?: string; detail?: unknown };
type Pending = { id: string; binding: Binding; choice: Choice; comment: string; intent?: Intent; text?: string; outcome: 'preparing' | 'prepared' | 'unknown' | 'recorded' | 'not_recorded' };
type Saved = { run: string; drafts: Record<string, string>; pending?: Pending };
type Transport = { identity: { workspace: string; root: string }; report: (run: string, generation: number) => Promise<Snapshot>; decide?: (request: RecordData) => Promise<Reply>; save: (state: Saved) => Promise<void>; now: () => Promise<number>; changed: () => void; id: () => string };
const PANE = 'taskplane-checkpoint';
const REQUEST = 'taskplane.claude-mods-request/v1';
const display = (value: unknown): string => value === undefined || value === null ? 'Unknown' : typeof value === 'string' ? value : JSON.stringify(value);
const bindingKey = (binding: Binding | null | undefined): string => binding ? JSON.stringify([binding.workspace,binding.root,binding.run,binding.revision,binding.visit,binding.checkpoint,binding.manifest_digest,binding.scope_digest]) : '';
const percent = (value: number | undefined) => value === undefined ? 'Unknown' : `${value}%`;
const runTokens = (s?: Snapshot) => display(s?.run_usage.tokens?.total_tokens);
const coverage = (s?: Snapshot) => { const c = s?.run_usage.coverage; if (!c || typeof c !== 'object') return display(c); const v = c as RecordData; return Number(v.unmeasured_sessions) > 0 || Number(v.partial_sessions) > 0 ? 'Partial coverage' : typeof v.measured_sessions === 'number' && v.measured_sessions > 0 ? `${v.measured_sessions} measured sessions` : 'Unknown coverage'; };
const equalBinding = (a: Binding | null | undefined, b: Binding | null | undefined): boolean => !!a && !!b && Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([k,v]) => (b as unknown as RecordData)[k] === v);
const freeze = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

export class Coordinator {
  snapshot?: Snapshot;
  run = '';
  runInput = '';
  generation = 0;
  confirmed = 0;
  now = 0;
  stale = true;
  refreshing = false;
  message = 'Loading Taskplane';
  persistenceError = '';
  drafts: Record<string, string> = {};
  pending?: Pending;
  finding?: string;
  view: 'finding' | 'source' | 'verification' = 'finding';
  page = 0;
  historyKey = '';
  historyPage = 0;
  context?: SessionContextUsage;
  contextAt?: string;
  private flight?: Promise<void>;
  private queued = false;
  private busy = false;
  constructor(readonly io: Transport, readonly preview = false) {}
  async restore(saved: unknown) {
    if (saved && typeof saved === 'object') {
      const s = saved as Saved;
      if (typeof s.run === 'string') this.run = this.runInput = s.run;
      if (s.drafts && typeof s.drafts === 'object') this.drafts = Object.fromEntries(Object.entries(s.drafts).filter(([,v]) => typeof v === 'string'));
      if (s.pending && typeof s.pending.id === 'string' && s.pending.binding && typeof s.pending.comment === 'string') {
        // Preparation cannot record a decision. Only a sent confirmation needs
        // authoritative reconciliation; restoring state never replays a write.
        const outcome = s.pending.outcome === 'preparing' || s.pending.outcome === 'not_recorded' ? 'not_recorded' : 'unknown';
        this.pending = { ...s.pending, outcome };
      }
    }
    await this.refresh();
    if (this.pending) await this.reconcile();
  }
  async persist() {
    try { await this.io.save({ run: this.run, drafts: this.drafts, pending: this.pending }); this.persistenceError = ''; return true; }
    catch { this.persistenceError = 'Draft retained in memory; persistence unavailable. Keep this pane open.'; return false; }
  }
  async select(run: string) {
    this.generation++; this.run = this.runInput = run; this.snapshot = undefined;
    this.stale = true; this.finding = undefined; this.page = 0;
    await this.persist(); this.io.changed(); await this.refresh();
  }
  refresh(): Promise<void> {
    if (this.flight) { this.queued = true; return this.flight; }
    this.flight = this.refreshLoop().finally(() => { this.flight = undefined; });
    return this.flight;
  }
  private async refreshLoop() {
    do {
      this.queued = false;
      const generation = this.generation, run = this.run;
      this.refreshing = true;
      try {
        const s = await this.io.report(run, generation);
        if (generation !== this.generation) { this.queued = true; continue; }
        if (s.schema !== 'taskplane.claude-mods-snapshot/v1' || s.selection.generation !== generation || (run && s.selection.run !== run) || !s.capabilities?.snapshot) throw Error('Unsupported or mismatched snapshot contract');
        const expected = this.io.identity;
        if (!expected.root?.trim() || !expected.workspace?.trim()) throw Error('Native session identity unavailable');
        if (s.selection.workspace !== expected.workspace || !s.selection.root?.trim()) throw Error('Snapshot workspace or session mismatch');
        // Explicit historical selection may inspect another owner, but never
        // changes the native principal or grants decision authority.
        if (s.selection.root !== expected.root && !(run && s.status === 'ready' && s.historical === true)) throw Error('Snapshot session mismatch');
        if (!['ready','no_run','selection_unavailable'].includes(s.status)
          || (s.status === 'ready' && !s.selection.run)
          || (s.status === 'no_run' && (s.selection.run !== null || s.identity !== null))
          || (s.status === 'selection_unavailable' && (!run || s.identity !== null))
          || (s.identity && (s.identity.workspace !== expected.workspace || s.identity.root !== s.selection.root || s.identity.run !== s.selection.run))) throw Error('Snapshot identity does not match its selection');
        const now = await this.io.now();
        if (generation !== this.generation) { this.queued = true; continue; }
        this.snapshot = freeze(s); this.now = now;
        const captured = Date.parse(s.confirmed_at);
        this.confirmed = Number.isFinite(captured) && captured <= this.now ? captured : 0;
        this.stale = !this.confirmed || this.now - this.confirmed >= 5000;
        if (!run && s.selection.run) { this.run = this.runInput = s.selection.run; await this.persist(); }
        if (generation !== this.generation) { this.queued = true; continue; }
        if (!this.finding && s.findings?.length) this.finding = s.findings[0]?.id;
        if (this.stale) this.message = 'Stale — controller confirmation is old or invalid';
        else if (!this.pending) this.message = s.status === 'ready' ? 'Fresh' : s.status === 'no_run' ? 'No selected run' : 'Selected run unavailable';
      } catch (error) {
        if (generation === this.generation) { this.stale = true; this.message = `Stale — refresh failed: ${errorText(error)}`; }
      } finally { this.refreshing = false; this.io.changed(); }
    } while (this.queued);
  }
  async age() {
    this.now = await this.io.now();
    if (!this.stale && this.now - this.confirmed >= 5000) { this.stale = true; this.message = 'Stale — last confirmation is over five seconds old'; this.io.changed(); }
  }
  async tick() { await this.age(); await this.refresh(); }
  disconnect() { this.stale = true; this.message = 'Stale — host disconnected'; this.io.changed(); }
  draft(binding = this.snapshot?.identity) { return this.drafts[bindingKey(binding)] ?? ''; }
  async edit(value: string, binding: Binding | null | undefined) {
    if (!binding) return;
    this.drafts[bindingKey(binding)] = value; await this.persist(); this.io.changed();
  }
  eligibility(binding = this.snapshot?.identity): string | undefined {
    if (this.preview || this.snapshot?.capabilities.preview) return 'Preview — decisions unavailable';
    if (!this.io.decide) return 'Decision transport unavailable; use the conversation';
    if (this.stale || !this.snapshot || this.now - this.confirmed >= 5000) return 'Refresh and review the current checkpoint before deciding';
    if (this.snapshot.status === 'no_run') return 'No selected run — checkpoint and findings unavailable';
    if (this.snapshot.status === 'selection_unavailable') return 'Selected run unavailable — choose another run';
    if (this.snapshot.selection.root !== this.io.identity.root) return 'Historical run belongs to another session — read only';
    if (this.snapshot.historical) return 'Historical run — read only';
    if (!this.snapshot.identity) return 'No pending checkpoint';
    if (!binding || !equalBinding(binding, this.snapshot.identity)) return 'Checkpoint changed — review current evidence';
    if (!this.snapshot.capabilities.direct_decision || !this.snapshot.capabilities.intent || !this.snapshot.capabilities.native_confirmation) return this.snapshot.capabilities.reasons.join('; ') || 'Direct decisions unavailable; use the conversation';
    if (this.busy) return 'Recording decision…';
    return undefined;
  }
  async prepare(choice: Choice, binding: Binding, comment: string) {
    await this.age();
    const reason = this.eligibility(binding); if (reason) { this.message = reason; this.io.changed(); return; }
    if (this.pending && this.pending.outcome !== 'recorded' && this.pending.outcome !== 'not_recorded') { this.message = 'Reconcile or cancel the outstanding attempt before starting another'; this.io.changed(); return; }
    const pending: Pending = { id: this.io.id(), binding: freeze(binding), choice, comment, outcome: 'preparing' };
    const request = { schema: REQUEST, operation: 'prepare', intent_id: pending.id, binding: pending.binding, choice, comment };
    if (new TextEncoder().encode(JSON.stringify(request)).length > 16384 || [...comment].length > 4096) { this.message = 'Not recorded — comment exceeds the complete payload limit; text retained'; this.io.changed(); return; }
    this.pending = pending; this.busy = true; this.message = 'Preparing exact decision for confirmation'; this.io.changed();
    try {
      if (!await this.persist()) { pending.outcome = 'not_recorded'; this.message = 'Not recorded — cannot persist retry identity; draft retained'; return; }
      const reply = await this.io.decide!(request);
      if (reply.status !== 'prepared' || !reply.intent || !reply.presentation?.text || !equalBinding(reply.intent.binding, binding) || reply.intent.choice !== choice || reply.intent.comment !== comment || reply.intent.intent_id !== pending.id) throw Error('Preparation did not confirm the exact displayed payload');
      pending.intent = freeze(reply.intent); pending.text = reply.presentation.text; pending.outcome = 'prepared';
      this.message = 'Review the exact decision below, then confirm explicitly';
    } catch(error) { pending.outcome = 'not_recorded'; this.message = `Not recorded — preparation failed: ${errorText(error)}. Text retained; revise or retry.`; }
    finally { this.busy = false; await this.persist(); this.io.changed(); }
  }
  async confirm(attempt: Pending, control: UiPressArgument, session: string) {
    await this.age();
    const reason = this.eligibility(attempt.binding);
    if (reason) { this.message = reason; this.io.changed(); return; }
    if (this.pending !== attempt || attempt.outcome !== 'prepared' || !attempt.intent || control.plugin !== 'taskplane' || control.component !== 'Pane' || control.requestId !== PANE || !['terminal','desktop'].includes(control.surface) || control.element !== (attempt.choice === 'approved' ? 'confirm-accept' : 'confirm-changes') || !session || session !== attempt.binding.root) {
      this.message = 'Not recorded — confirmation no longer matches this pane or owning session'; this.io.changed(); return;
    }
    this.busy = true; attempt.outcome = 'unknown'; this.message = 'Recording decision…'; this.io.changed();
    try {
      if (!await this.persist()) { attempt.outcome = 'prepared'; this.message = 'Not recorded — cannot persist retry identity; draft retained'; return; }
      const reply = await this.io.decide!({ schema: REQUEST, operation: 'record_ui', intent_id: attempt.id, payload_digest: attempt.intent.payload_digest, control: { plugin: control.plugin, component: 'checkpoint', element: control.element, request_id: control.requestId, surface: control.surface, session } });
      this.acceptReceipt(reply, attempt);
    } catch(error) { this.message = `Outcome unknown — ${errorText(error)}. Check receipt before retrying.`; }
    finally { this.busy = false; await this.persist(); this.io.changed(); await this.refresh(); }
  }
  private acceptReceipt(reply: Reply, attempt: Pending) {
    if (reply.status === 'recorded' && validReceipt(reply.receipt, attempt) && reply.intent?.intent_id === attempt.id && equalBinding(reply.intent.binding, attempt.binding) && reply.intent.choice === attempt.choice && reply.intent.comment === attempt.comment) {
      attempt.outcome = 'recorded'; this.message = 'Decision recorded by Taskplane';
    } else if (reply.status === 'prepared' && reply.intent?.payload_digest) {
      if (!equalBinding(reply.intent.binding, attempt.binding) || reply.intent.choice !== attempt.choice || reply.intent.comment !== attempt.comment || reply.intent.intent_id !== attempt.id) throw Error('Receipt payload mismatch');
      attempt.intent = freeze(reply.intent); attempt.text = reply.presentation?.text ?? attempt.text; attempt.outcome = 'prepared'; this.message = 'Not recorded — exact attempt is ready for explicit confirmation';
    } else if (reply.status === 'not_recorded') { attempt.outcome = 'not_recorded'; this.message = 'Not recorded — text retained'; }
    else throw Error('No matching authoritative receipt');
  }
  async reconcile() {
    if (!this.pending || this.busy || this.preview || !this.io.decide) return;
    if (this.pending.binding.root !== this.io.identity.root || this.pending.binding.workspace !== this.io.identity.workspace) { this.message = 'Attempt belongs to another workspace or session — receipt unavailable'; this.io.changed(); return; }
    if (this.pending.outcome === 'not_recorded') { this.message = 'Not recorded — text retained; revise or retry'; this.io.changed(); return; }
    this.busy = true;
    try { this.acceptReceipt(await this.io.decide({ schema: REQUEST, operation: 'receipt', intent_id: this.pending.id }), this.pending); }
    catch(error) { this.message = `Outcome unknown — ${errorText(error)}. Text retained.`; }
    finally { this.busy = false; await this.persist(); this.io.changed(); }
  }
  cancel() {
    if (this.busy || this.pending?.outcome === 'unknown') { this.message = 'Check receipt before discarding an uncertain attempt'; }
    else { this.pending = undefined; this.message = 'Confirmation cancelled; draft retained'; }
    this.io.changed(); return this.persist();
  }
  pick(id: string) { this.finding = id; this.page = 0; this.io.changed(); }
  move(delta: number) {
    const items = this.snapshot?.findings ?? [], index = items.findIndex(x => x.id === this.finding);
    const next = items[Math.max(0, Math.min(items.length - 1, index + delta))];
    if (next) this.pick(next.id);
  }
}
function validReceipt(receipt: RecordData | undefined, attempt: Pending): boolean {
  if (!receipt || typeof receipt.confirmed_revision !== 'number' || !Number.isSafeInteger(receipt.confirmed_revision) || receipt.confirmed_revision < attempt.binding.revision || typeof receipt.confirmed_at !== 'string' || !Number.isFinite(Date.parse(receipt.confirmed_at))) return false;
  const decision = receipt.decision as RecordData | undefined;
  const provenance = decision?.provenance as RecordData | undefined;
  return !!decision && decision.event_id === `mods/${attempt.id}` && decision.human === true && decision.automatic === false && decision.choice === attempt.choice && equalBinding(decision.binding as Binding, attempt.binding) && provenance?.comment === attempt.comment;
}
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

async function bridge($: EngineInterface, workspace: string, session: string, args: string[]): Promise<unknown> {
  if (!session?.trim() || !workspace?.trim()) throw Error('Native session identity unavailable');
  const result = await $.process.run(['python3', `${$.plugin.root}/taskplane/tp.py`, 'flow', ...args, '--workspace', workspace], { cwd: workspace, env: { TASKPLANE_CLAUDE_SESSION_ID: session }, timeoutMs: 8000 });
  let value: unknown;
  try { value = JSON.parse(result.stdout); } catch { throw Error('Taskplane returned an unreadable response'); }
  if (result.exitCode !== 0) { const failure = value as Reply; throw Error([failure.reason, failure.detail].filter(x => x !== undefined).map(display).join(': ') || result.stderr); }
  return value;
}
async function openPane($: EngineInterface) { await $.ui.open({ id: PANE, title: 'Taskplane checkpoint', focus: true, closeOnEscape: true }); }
function lines(data: unknown): string {
  if (data === undefined || data === null) return 'Unavailable';
  return typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}
function pageText(text: string, page: number) { const chunks = Array.from(text); return chunks.slice(page * 2400, (page + 1) * 2400).join(''); }
function freshness(c?: Coordinator) {
  return !c ? 'Loading' : c.stale ? 'Stale' : !c.snapshot ? 'Loading' : c.snapshot.status === 'no_run' ? 'No selected run' : c.snapshot.status === 'selection_unavailable' ? 'Selected run unavailable' : c.refreshing ? 'Refreshing' : 'Fresh';
}

export const register: Register = (on, options) => {
  let store: Coordinator | undefined;
  let starting: Promise<void> | undefined;
  let session = '';
  let observed: UiPressArgument | undefined;
  let eventQueue = Promise.resolve();
  const preview = options.preview === true;
  on('session.start', async ($, e, next) => {
    const result = await next(e);
    await $.command.register({ name: 'taskplane-status', description: 'Open the Taskplane checkpoint and evidence' });
    // Start only after host identity exists. The timer belongs to this module instance;
    // Claude cancels it on hot reload. No decisions are resumed here.
    if (!starting) {
      const workspace = await $.session.root(); session = await $.session.id();
      const key = `taskplane-mods/v1:${workspace}:${session}`;
      store = new Coordinator({
        identity: { workspace, root: session },
        report: async (run, generation) => await bridge($, workspace, session, ['report', '--mods', '--mods-generation', String(generation), ...(run ? ['--run', run] : []), ...(preview ? ['--mods-preview'] : [])]) as Snapshot,
        ...(preview ? {} : { decide: async (request: RecordData) => await bridge($, workspace, session, ['decide', '--mods-request-json', JSON.stringify(request)]) as Reply }),
        save: async state => { await $.store.set(key, state); }, now: () => $.clock.now(), changed: () => $.ui.invalidate('ui.render'), id: () => crypto.randomUUID()
      }, preview);
      const coordinator = store;
      starting = (async () => {
        try { await coordinator.restore(await $.store.get(key)); }
        catch(error) { coordinator.message = `State unavailable — ${errorText(error)}`; coordinator.stale = true; }
        $.clock.every(1000, () => coordinator.age());
        $.clock.every(2000, async () => {
          await coordinator.tick();
          try { coordinator.context = (await $.session.usage()).context; coordinator.contextAt = new Date(await $.clock.now()).toISOString(); }
          catch { coordinator.context = undefined; coordinator.contextAt = undefined; }
          $.ui.invalidate('ui.render');
        });
      })();
      await starting;
    }
    return result;
  });
  on('session.detach', async ($, e, next) => { store?.disconnect(); return next(e); });
  on('command.run', { command: 'taskplane-status' }, async ($) => { await openPane($); return { text: 'Taskplane checkpoint opened. Ctrl+X then Tab focuses the pane.' }; });
  // Observe the actual event, then let the host invoke the rendered closure. A
  // serialized fold prevents one press from borrowing another press's metadata.
  on('ui.press', { plugin: 'taskplane' }, async ($, e, next) => {
    const task = eventQueue.then(async () => { observed = e; try { return await next(e); } finally { observed = undefined; } });
    eventQueue = task.then(() => undefined, () => undefined); return task;
  });
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const rest = await next(e);
    if (e.surface !== 'terminal' && e.surface !== 'desktop') return rest;
    const { Box, Text, Button } = $.ui.resolve(e), s = store?.snapshot;
    return <Box flexDirection="column">
      {rest}
      <Text wrap="wrap">{`Taskplane · ${s?.status === 'no_run' ? 'No selected run' : s?.status === 'selection_unavailable' ? 'Selected run unavailable' : display(s?.phase)} · Decision: ${display(s?.decision)} · Live workers: ${display(s?.workers?.live)} · Unresolved findings: ${display(s?.unresolved_count)}`}</Text>
      <Text wrap="wrap">{`Run tokens: ${runTokens(s)} (${coverage(s)}) · Context window: ${display(store?.context?.tokens)} / ${display(store?.context?.window)} tokens; ${percent(store?.context?.percent)}`}</Text>
      <Text wrap="wrap">{`${freshness(store)} · Last confirmed update: ${s?.confirmed_at ?? 'Unknown'}`}</Text>
      <Button key="open-checkpoint" label="Open checkpoint" onPress={() => openPane($)} />
    </Box>;
  });
  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e);
    if (e.surface !== 'terminal' && e.surface !== 'desktop') {
      const { Text } = $.ui.resolve(e);
      return <Text>{`Taskplane native controls unavailable on this surface. Dashboard: ${store?.snapshot?.fallback.dashboard ?? '.taskplane/dashboard.html'}. Use the conversation for decisions.`}</Text>;
    }
    const { Box, Text, Button, Input, Select } = $.ui.resolve(e);
    const c = store, s = c?.snapshot, binding = s?.identity;
    if (!c) return <Text>Taskplane is loading. Use .taskplane/dashboard.html if host startup is unavailable.</Text>;
    const attempt = c.pending;
    const reason = c.eligibility(binding);
    const finding = s?.findings?.find(x => x.id === c.finding);
    const index = s?.findings?.findIndex(x => x.id === c.finding) ?? -1;
    const content = finding ? c.view === 'finding' ? lines(Object.fromEntries(Object.entries(finding).filter(([key]) => key !== 'sources' && key !== 'checks'))) : c.view === 'source' ? (finding.sources?.length ? finding.sources.map(source => `${lines(Object.fromEntries(Object.entries(source).filter(([key]) => key !== 'text')))}\n${source.status === 'available' ? display(source.text ?? 'Evidence body unavailable') : 'Evidence unavailable'}${source.truncated ? '\n[Controller excerpt truncated]' : ''}`).join('\n\n') : 'Source evidence unavailable') : `Current checks\n${lines(s?.current_checks)}\nFinding verification / historical records\n${lines(finding.checks ?? finding.verification)}` : '';
    const previousDrafts = Object.entries(c.drafts).filter(([key,text]) => key !== bindingKey(binding) && text.length > 0);
    const oldDraft = previousDrafts.find(([key]) => key === c.historyKey) ?? previousDrafts.at(-1);
    const historyPages = Math.max(1, Math.ceil(Array.from(oldDraft?.[1] ?? '').length / 2400));
    c.historyPage = Math.min(c.historyPage, historyPages - 1);
    const pages = Math.max(1, Math.ceil(Array.from(content).length / 2400));
    c.page = Math.min(c.page, pages - 1);
    return <Box flexDirection="column" minWidth={0}>
      <Box flexDirection={e.props.bodyColumns < 72 ? 'column' : 'row'} flexWrap="wrap" gap={1}>
        <Button key="refresh" label="Refresh" autoFocus onPress={() => c.refresh()} />
        <Button key="close" label="Close checkpoint" onPress={() => $.ui.close({ id: PANE })} />
      </Box>
      <Text wrap="wrap">{`Tab: controls · Enter: activate · Escape: close · Ctrl+X then Tab: focus pane.`}</Text>
      <Input key="run" label="Run ID (empty selects current)" value={c.runInput} onInput={value => { c.runInput = value; }} onSubmit={value => c.select(value)} submitLabel="select run" />
      <Button key="select-run" label="Select run" onPress={() => c.select(c.runInput)} />
      <Text wrap="wrap">{`${freshness(c)} · Last confirmed update: ${s?.confirmed_at ?? 'Unknown'}`}</Text>
      <Text wrap="wrap">{c.message}</Text>
      {c.persistenceError ? <Text wrap="wrap">{c.persistenceError}</Text> : null}
      <Text wrap="wrap">{`Phase: ${display(s?.phase)}\nWork: ${display(s?.work)}\nEvidence validation: ${display(s?.evidence.status)}\nUnresolved findings: ${display(s?.unresolved_count)}\nDecision: ${display(s?.decision)}\nWorkflow revision: ${display(s?.revision)} · Packet revision: ${display(s?.packet_revision)}`}</Text>
      <Text wrap="wrap">{`Run tokens: ${runTokens(s)} · Counters: ${display(s?.run_usage.tokens)} · Coverage: ${display(s?.run_usage.coverage)} · Measured: ${display(s?.run_usage.measurement)}\nContext window: ${display(c.context?.tokens)} / ${display(c.context?.window)} tokens; ${percent(c.context?.percent)} · Source: Claude session · Measured: ${c.contextAt ?? 'Unknown'}`}</Text>
      <Text wrap="wrap">{`Identifiers\n${lines(binding ?? s?.selection ?? 'No selected run')}`}</Text>
      <Text wrap="wrap">{`Dashboard (read only): ${s?.fallback.dashboard ?? '.taskplane/dashboard.html'}`}</Text>
      <Text wrap="wrap">{reason ?? 'Choose an action, then review its exact confirmation. Evidence validation does not resolve findings.'}</Text>
      {binding ? <Input key="revision-comment" label="Revision comment" value={c.draft(binding)} onInput={value => c.edit(value, binding)} onSubmit={value => c.edit(value, binding)} submitLabel="keep draft" /> : null}
      {!reason && binding && (!attempt || ['recorded','not_recorded'].includes(attempt.outcome)) ? <Box flexDirection="column">
        <Button key="prepare-accept" label="Accept" onPress={() => c.prepare('approved', binding, c.draft(binding))} />
        <Button key="prepare-changes" label="Request changes" onPress={() => c.prepare('changes_requested', binding, c.draft(binding))} />
      </Box> : null}
      {attempt ? <Box flexDirection="column">
        <Text wrap="wrap">{`Attempt ${attempt.id} · ${attempt.outcome}\n${attempt.text ?? lines({ binding: attempt.binding, choice: attempt.choice, comment: attempt.comment })}`}</Text>
        {attempt.outcome === 'prepared' && !reason ? <Button key={attempt.choice === 'approved' ? 'confirm-accept' : 'confirm-changes'} label={attempt.choice === 'approved' ? 'Confirm Accept' : 'Confirm Request changes'} onPress={() => { const event = observed; if (event) return c.confirm(attempt, event, session); }} /> : null}
        <Button key="receipt" label="Check decision receipt" onPress={() => c.reconcile()} />
        <Button key="cancel-confirmation" label="Cancel confirmation (retain draft)" onPress={() => c.cancel()} />
      </Box> : null}
      {oldDraft ? <Box flexDirection="column">
        <Text>Previous checkpoint drafts (read only; never submitted to this checkpoint)</Text>
        <Select key="draft-history" label="Saved draft" value={oldDraft[0]} options={previousDrafts.map(([key],i) => ({ value: key, label: `Previous checkpoint draft ${i+1}` }))} onSelect={key => { c.historyKey=key; c.historyPage=0; $.ui.invalidate('ui.render'); }} />
        <Text wrap="wrap">{`Draft binding: ${oldDraft[0]}\nPage ${c.historyPage+1} of ${historyPages}\n${pageText(oldDraft[1],c.historyPage)}`}</Text>
        {historyPages > 1 ? <Box flexDirection="column">
          <Button key="draft-page-back" label="Previous draft page" onPress={() => { c.historyPage=Math.max(0,c.historyPage-1); $.ui.invalidate('ui.render'); }} />
          <Button key="draft-page-next" label="Next draft page" onPress={() => { c.historyPage=Math.min(historyPages-1,c.historyPage+1); $.ui.invalidate('ui.render'); }} />
        </Box> : null}
      </Box> : null}
      <Text bold>Evidence browser</Text>
      {s?.status !== 'ready' || !s.findings ? <Text>Findings unavailable</Text> : s.findings.length === 0 ? <Text>No findings recorded</Text> : <Box flexDirection="column">
        <Select key="finding-number" label="Numbered findings" value={finding?.id} options={s.findings.map((f,i) => ({ value: f.id, label: `${i+1}. ${f.title ?? f.id}` }))} onSelect={id => c.pick(id)} />
        <Box flexDirection={e.props.bodyColumns < 72 ? 'column' : 'row'} flexWrap="wrap">
          <Button key="previous" label="Previous finding" onPress={() => c.move(-1)} />
          <Button key="next" label="Next finding" onPress={() => c.move(1)} />
        </Box>
        <Text wrap="wrap">{finding ? `Finding ${index+1} of ${s.findings.length} · ${finding.id} · ${finding.title ?? 'Untitled'}\nDisposition: ${display(finding.disposition)} · Verification: ${display(finding.verification)}\nRelated tasks: ${display(finding.related_tasks ?? finding.tasks)} · Review lenses: ${display(finding.review_lenses ?? finding.lenses)}` : 'Selected finding no longer in this snapshot — choose a numbered finding'}</Text>
        <Box flexDirection="column">
          <Button key="view-finding" label="Finding" onPress={() => { c.view='finding'; c.page=0; $.ui.invalidate('ui.render'); }} />
          <Button key="view-source" label="Source evidence" onPress={() => { c.view='source'; c.page=0; $.ui.invalidate('ui.render'); }} />
          <Button key="view-verification" label="Verification" onPress={() => { c.view='verification'; c.page=0; $.ui.invalidate('ui.render'); }} />
        </Box>
        <Text wrap="wrap">{`${c.view} · Page ${c.page+1} of ${pages}\n${pageText(content,c.page)}`}</Text>
        {pages > 1 ? <Box flexDirection="column">
          <Button key="page-back" label="Previous evidence page" onPress={() => { c.page=Math.max(0,c.page-1); $.ui.invalidate('ui.render'); }} />
          <Button key="page-next" label="Next evidence page" onPress={() => { c.page=Math.min(pages-1,c.page+1); $.ui.invalidate('ui.render'); }} />
        </Box> : null}
      </Box>}
    </Box>;
  });
};
