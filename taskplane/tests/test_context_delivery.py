"""Frozen transport benchmarks. Bytes are not native tokens or model quality.

The comparison deliberately reports failing ratios instead of padding a baseline.
The oracle is available only to this evaluator, never production view selection.
"""
from __future__ import annotations
def engine_comparison_entry():
    """Supplemental old/new shipped-engine replay. Fixture decisions are test data."""
    import argparse
    from collections import defaultdict
    import hashlib
    import json
    import os
    from pathlib import Path
    import shlex
    import shutil
    import subprocess
    import sys
    import time
    import zipfile
    
    REPO = Path(__file__).resolve().parents[2]
    BASE = Path('/private/tmp/taskplane-engine-context-repair1')
    OLD = Path('/Users/vdemkiv/.codex/plugins/cache/openai-curated-remote/taskplane/2.28.0')
    ARCHIVE = Path('/private/tmp/taskplane-context-optimization-repair/taskplane-2.29.0-openai.zip')
    ARCHIVE_SHA = '142f4be851c979174758e0cc661e04a5878b93a4b7805feb863b4b84ec8a7094'
    PHASES = ['product','design','plan','build','evaluate','engineering','retro']
    ROOT = 'fixture-context-comparison'
    REQUEST = ('Add WELCOME 10, LOYALTY 15 and STAFF 50 percent discounts before 20 percent tax; '
               'unknown or absent code leaves subtotal unchanged. Preserve basket and catalog behavior.')
    
    def enc(value):
        return json.dumps(value, sort_keys=True, separators=(',',':'), ensure_ascii=False, allow_nan=False).encode()
    
    def sha(raw): return hashlib.sha256(raw).hexdigest()
    
    def write_json(target, value):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    
    def files(root):
        return {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted(root.rglob('*'))
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    
    class Replay:
        def __init__(self, arm, repetition):
            self.arm, self.repetition = arm, repetition
            self.engine = BASE/'engines'/arm
            self.workspace = BASE/'workspaces'/f'{arm}-{repetition}'
            self.output = BASE/'results'/f'{arm}-{repetition}'
            assert not self.workspace.exists(), 'Never overwrite an earlier execution.'
            self.workspace.mkdir(parents=True)
            self.output.mkdir(parents=True)
            self.env = {k:v for k,v in os.environ.items() if k not in {
                'CODEX_THREAD_ID','CODEX_SESSION_ID','TASKPLANE_CLAUDE_SESSION_ID','CLAUDE_SESSION_ID','PYTHONPATH'}}
            self.env.update(CODEX_THREAD_ID=ROOT, PLUGIN_ROOT=str(self.engine), CLAUDE_PLUGIN_ROOT=str(self.engine),
                            PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0')
            # Isolate the fixture driver's identity and verification environment too.
            os.environ.clear()
            os.environ.update(self.env)
            self.phase='product'
            self.events=[]
            self.records=[]
            self.prior=[]
            self.receipt=None
            self.driver_sha256=sha(Path(__file__).read_bytes())
            self.hooks=json.loads((self.engine/'hooks/hooks.json').read_text())['hooks']
            sys.path.insert(0,str(self.engine))
            from taskplane import workflow as w, workflow_host as h
            self.w, self.h = w,h
            self.controller=h.Controller(self.workspace,ROOT,h.installed_adapter('codex'))
    
        def capture(self, kind, label, argv, *, stdin=None, shell=False):
            start=time.monotonic()
            result=subprocess.run(argv, cwd=self.workspace, env=self.env, input=stdin,
                                  capture_output=True, timeout=120, shell=shell)
            index=len(self.events)
            prefix=self.output/f'{index:04d}-{self.phase}-{kind}'
            prefix.with_suffix('.stdout').write_bytes(result.stdout)
            prefix.with_suffix('.stderr').write_bytes(result.stderr)
            self.events.append({'phase':self.phase,'kind':kind,'label':label,
                'command':argv,'exit_code':result.returncode,'stdout_bytes':len(result.stdout),
                'stderr_bytes':len(result.stderr),'input_bytes':len(result.stdout)+len(result.stderr),
                'stdout_sha256':sha(result.stdout),'stderr_sha256':sha(result.stderr),
                'stdout':str(prefix.with_suffix('.stdout')),'stderr':str(prefix.with_suffix('.stderr')),
                'duration_seconds':round(time.monotonic()-start,4)})
            write_json(self.output/'events.json',self.events)
            return result
    
        def hook(self, name, argv, **extra):
            event={'hook_event_name':name,'cwd':str(self.workspace),'session_id':ROOT,'thread_id':ROOT,
                   'tool_name':'exec_command','tool_input':{'cmd':shlex.join(argv)},**extra}
            command=self.hooks[name][0]['hooks'][0]['command']
            result=self.capture('hook',name,command,stdin=enc(event),shell=True)
            assert result.returncode==0,(name,result.stderr.decode())
            return json.loads(result.stdout)
    
        def cli(self, *args, kind='lifecycle', code=0, flow=True):
            argv=[sys.executable,str(self.engine/'taskplane/tp.py')]
            argv += (['flow',*args,'--workspace',str(self.workspace)] if flow else list(args))
            before=self.hook('PreToolUse',argv)
            if before.get('hookSpecificOutput',{}).get('permissionDecision')=='deny':
                assert code==2, before
                return {'hook_refused':True,'detail':before}
            result=self.capture(kind,' '.join(args[:2]),argv)
            self.hook('PostToolUse',argv,tool_response={'exit_code':result.returncode})
            assert result.returncode==code,(args,result.returncode,result.stdout.decode()[-3000:],result.stderr.decode())
            value=json.loads(result.stdout)
            if code==0 and self.arm=='new' and value.get('schema')=='taskplane.command-summary/v1':
                assert not value.get('errors',{}).get('transport'),value
                assert value.get('context',{}).get('status')!='unavailable',value
            return value
    
        def read_file(self, relative, kind='additional_read'):
            target=self.workspace/relative
            r=self.capture(kind,relative,['cat',str(target)])
            assert r.returncode==0
            return r.stdout
    
        def state(self):
            # Evaluator-only metadata for bindings/assertions, never a consumer payload.
            return self.controller.report(getattr(self, 'run_id', None))
    
        def fetch(self, reference):
            key=reference['sha256']
            if key in self.nodes: return self.nodes[key]
            first=self.cli('context','--read',key,kind='additional_read')
            self.receipt=first['context_receipt']
            self.remaining=first['remaining_required']
            page=first['page']; data=page['data']; form=page['form']
            for number in range(1,page['pages']):
                returned=self.cli('context','--read',key,'--page',str(number),kind='additional_read')
                self.receipt=returned['context_receipt']; self.remaining=returned['remaining_required']
                data += returned['page']['data']
            if form=='value' or form=='entries': result=data
            else:
                if form=='groups' or form.endswith('-groups'):
                    data=[item for group in data for item in self.fetch(group)]
                    if form=='groups':
                        self.nodes[key]=data
                        return data
                    form=form.removesuffix('-groups')
                if form=='dict': result={k:self.fetch(v) for k,v in data}
                else:
                    values=[self.fetch(v) for v in data]
                    if form=='dict-chunks': result={k:v for chunk in values for k,v in chunk.items()}
                    elif form=='list-chunks': result=[v for chunk in values for v in chunk]
                    elif form=='text': result=''.join(values)
                    else: result=values
            self.nodes[key]=result
            return result
    
        def context(self, report=None, renewal=False):
            if self.arm=='old':
                state=report['workflow']
                assert state['phase']==self.phase and state['goal']==REQUEST
                predecessor=[]
                artifacts={}
                for visit in state['visits'][:state['index']]:
                    assert visit['decision']=='approved' and not visit['superseded']
                    packet=visit['packet']; predecessor.append(packet['output'])
                    for relative in sorted(packet['manifest']):
                        if relative==packet['context']['tasks_path']: continue
                        if relative in artifacts: continue  # One read per distinct artifact in each consumer.
                        document=json.loads((self.workspace/relative).read_bytes()) if relative.endswith('.json') else None
                        if isinstance(document,dict) and document.get('schema')=='taskplane.phase-output/v1': continue
                        artifacts[relative]=self.read_file(relative)
                sources={relative:self.read_file(relative) for relative in self.pricing
                         if (self.workspace/relative).exists()}
                return {'goal':state['goal'],'previous':predecessor,'sources':sources,'artifacts':artifacts,
                        'graph_available':bool(report.get('graph'))}
            self.nodes={}
            kind='receipt_renewal' if renewal else 'initial_context'
            descriptor=self.cli('context',kind=kind)
            returned=self.cli('context','--consume',descriptor['handoff_ref']['sha256'],kind=kind)
            self.receipt=returned['context_receipt']; self.remaining=returned['remaining_required']
            view=returned['view']; bodies=dict(view['inline'])
            if self.remaining:
                required=self.fetch(view['required_inputs']['details'])
                for entry in required:
                    if entry['id'] not in bodies: bodies[entry['id']]=self.fetch(entry['ref'])
            wanted=['requirements-and-scope','source-graph']
            wanted += ['source/'+p for p in self.pricing if (self.workspace/p).exists()]
            if any(key not in bodies for key in wanted):
                index=self.fetch(view['references']['details'])
                references={entry['id']:entry['ref'] for entry in index}
                for key in wanted:
                    if key not in bodies: bodies[key]=self.fetch(references[key])
            assert self.remaining==0
            requirement=bodies['requirements-and-scope']
            assert requirement['phase']==self.phase and requirement['criteria']==['AC-DISCOUNT']
            assert requirement['goal']==REQUEST
            sources={v['path']:v['text'].encode() for k,v in bodies.items() if k.startswith('source/')}
            artifacts={v['path']:v['text'].encode() for k,v in bodies.items() if k.startswith('artifact/')}
            previous=[v for k,v in bodies.items() if k.startswith('accepted/')]
            return {'goal':requirement['goal'],'previous':previous,'sources':sources,'artifacts':artifacts,
                    'graph_available':bool(bodies['source-graph']),
                    'verifications':[v for k,v in bodies.items() if k.startswith('verification/')]}
    
        def normalize(self, output):
            return {k:v for k,v in output.items() if k not in ('run','visit','context_receipt')}
    
        def validate_input(self, consumed):
            assert consumed['graph_available']
            by_phase={out['phase']:self.normalize(out) for out in consumed['previous']}
            for prior in self.prior:
                assert by_phase[prior['phase']]==self.normalize(prior),prior['phase']
            for relative, raw in consumed['sources'].items():
                assert raw==(self.workspace/relative).read_bytes(),relative
            assert len(consumed['sources'])==len([p for p in self.pricing if (self.workspace/p).exists()])
            if self.phase in ('evaluate','engineering','retro'):
                assert consumed['artifacts']['.taskplane/check.txt']==(self.workspace/'.taskplane/check.txt').read_bytes()
            if self.arm=='new' and self.phase in ('evaluate','engineering'):
                assert consumed['verifications'] and all(v['status']=='reused' for v in consumed['verifications'])
    
        def run(self):
            fixture=REPO/'evals/fixture-repo'
            for p in (fixture/'tree-a').rglob('*.py'):
                target=self.workspace/p.relative_to(fixture/'tree-a')
                target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(p.read_bytes())
            self.pricing=sorted(str(p.relative_to(fixture/'tree-b')) for p in (fixture/'tree-b').rglob('*.py'))
            scope={'criteria':['AC-DISCOUNT'],'paths':{p:[f'.taskplane/{p}.json'] for p in PHASES},'verification_inputs':self.pricing}
            scope['paths']['build'] += self.pricing+['.taskplane/check.txt']
            tasks={'tasks':[{'id':'DISCOUNT','phase':'build','dependencies':[],'owner':ROOT,
                'criteria':['AC-DISCOUNT'],'paths':scope['paths']['build'],
                'verification':'Frozen pricing tests and exact before-tax arithmetic'}]}
            write_json(self.workspace/'.taskplane/scope.json',scope)
            write_json(self.workspace/'.taskplane/tasks.json',tasks)
            self.cli('start','--scope','.taskplane/scope.json','--tasks','.taskplane/tasks.json',
                     '--request-reference','fixture-labelled/user-request','--goal',REQUEST)
            self.run_id=self.state()['run']
            check_executions=0
            for i,phase in enumerate(PHASES):
                self.phase=phase
                first=len(self.events) if i else 0
                report=self.cli('report',kind='initial_context')
                consumed=self.context(report)
                self.validate_input(consumed)
                state=self.state(); assert self.w.current(state)['phase']==phase
                request=consumed['goal']
                if phase=='build':
                    for relative in self.pricing:
                        target=self.workspace/relative;target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes((fixture/'tree-b'/relative).read_bytes())
                    self.cli('graph','--workspace',str(self.workspace),'scan','--decompose','--strict',flow=False,kind='verification')
                    command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
                    test=self.capture('verification','pricing tests',command)
                    check_executions+=1
                    assert test.returncode==0 and b'Ran 8 tests' in test.stderr
                    (self.workspace/'.taskplane/check.txt').write_bytes(test.stdout+test.stderr)
                    if self.arm=='new':
                        from taskplane.context_reuse import key, record
                        from taskplane.context import Store
                        # Same real test already executed; wrap that observed result without rerunning.
                        provenance=key(self.workspace, paths=[p for p in self.pricing if p.startswith('pricing/')],
                            tests=[p for p in self.pricing if p.startswith('tests/')], criteria=['AC-DISCOUNT'],
                            command=command, tool='python-unittest', contract='discount-before-tax/v1')
                        self.reuse_ref=record(Store(self.workspace),provenance,status='pass',producer='fixture/build',
                            result={'returncode':test.returncode,'stdout':test.stdout.decode(),'stderr':test.stderr.decode()})
                        renewed=self.context(renewal=True)
                        self.validate_input(renewed)
                out={'schema':'taskplane.phase-output/v1','run':state['run'],
                     'visit':self.w.current(state)['id'],'phase':phase,'criteria':['AC-DISCOUNT'],
                     **{field:request for field in self.w.OUTPUT_FIELDS[phase]}}
                if self.arm=='new': out['context_receipt']=self.receipt
                if phase=='product': out['acceptance_criteria']=[{'id':'AC-DISCOUNT','statement':request}]
                if phase=='design': out['acceptance_test_map']={'AC-DISCOUNT':'Frozen fixture pricing tests'}
                if phase=='plan': out.update(task_dag=tasks['tasks'],ownership={'DISCOUNT':ROOT},
                    write_scope=scope['paths']['build'],acceptance_coverage={'AC-DISCOUNT':['DISCOUNT']},integration_order=['DISCOUNT'])
                if phase=='build':
                    check={'name':'Frozen pricing tests','status':'pass','evidence':'.taskplane/check.txt'}
                    if self.arm=='new': check['reuse_ref']=self.reuse_ref
                    out.update(change_inventory=self.pricing,task_acceptance_map={'AC-DISCOUNT':['DISCOUNT']},build_checks=[check])
                if phase=='evaluate': out['criterion_results']={'AC-DISCOUNT':{'status':'pass','evidence':'.taskplane/check.txt',
                    'explanation':'Unchanged matching Build verification'}}
                if phase=='engineering': out.update(findings=[],lens_coverage=[{'lens':'backend','reviewer':'fixture',
                    'rationale':'Discount arithmetic and caller preservation'}],requirements_comparison={'AC-DISCOUNT':'Pinned tree-b tests passed'})
                target=f'.taskplane/{phase}.json'; write_json(self.workspace/target,out)
                self.cli('submit','--output',target,'--tasks','.taskplane/tasks.json','--expected-revision',str(state['revision']))
                state=self.state()
                action='finish' if phase=='retro' else 'advance'
                args=['--phase',PHASES[min(i+1,6)]] if action=='advance' else []
                refused=self.cli(action,*args,'--expected-revision',str(state['revision']),code=2)
                assert refused.get('hook_refused') or refused.get('reason')=='approval_required',refused
                self.cli('present','--evidence','.taskplane/dashboard.html','--presentation','linked',
                    '--note','Fixture-labelled artifact link; no live host display or human approval claim.')
                binding=self.w.binding(state,self.w.current(state)['packet'])
                decision={'schema':'taskplane.observed-decision/v1','event_id':'fixture-human-'+phase,
                    'choice':'approved','binding':binding,'excerpt':'Approved','recorder':'root_orchestrator',
                    'source':{'kind':'conversation','reference':'fixture-human-'+phase,'conversation':ROOT,
                              'actor':'user','automatic':False,'observed_at':'2026-09-23T00:00:02+00:00'},
                    'presentation':{'checkpoint':binding['checkpoint'],'reference':'fixture/presentation-'+phase,
                                    'at':'2026-09-23T00:00:01+00:00'}}
                self.cli('decide','--decision-json',json.dumps(decision),'--expected-revision',str(state['revision']))
                state=self.state()
                self.cli(action,*args,'--expected-revision',str(state['revision']))
                self.prior.append(out)
                selected=self.events[first:]
                categories=defaultdict(int)
                for event in selected: categories[event['kind']]+=event['input_bytes']
                row={'phase':phase,'bytes_by_category':dict(categories),'input_bytes':sum(categories.values()),
                     'response_count':len(selected),'prior_phases_received':len(consumed['previous']),
                     'source_files_received':len(consumed['sources']),'gate_refused_before_approval':True,
                     'substantive_output':self.normalize(out)}
                self.records.append(row)
                write_json(self.output/'phases.json',self.records)
                print(json.dumps({'arm':self.arm,'repetition':self.repetition,'phase':phase,'input_bytes':row['input_bytes']}),flush=True)
            final=self.state()
            assert final['finished'] and len(final['decisions'])==7 and check_executions==1
            expected={p:sha((fixture/'tree-b'/p).read_bytes()) for p in self.pricing}
            actual={p:sha((self.workspace/p).read_bytes()) for p in self.pricing}
            assert actual==expected
            result={'arm':self.arm,'repetition':self.repetition,'version':json.loads((self.engine/'.codex-plugin/plugin.json').read_text())['version'],
                    'driver_sha256':self.driver_sha256,
                    'phase_records':self.records,'input_bytes':sum(e['input_bytes'] for e in self.events),
                    'quality':{'finished':True,'human_fixture_gates':7,'checks_executed':check_executions,
                               'final_source_sha256':actual,'prior_outputs_preserved':True},
                    'events':str(self.output/'events.json'),'native_model_tokens':'not_measured'}
            write_json(self.output/'result.json',result)
    
    def prepare():
        engines=BASE/'engines'; engines.mkdir(parents=True, exist_ok=True)
        if not (engines/'old').exists(): shutil.copytree(OLD,engines/'old',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        assert sha(ARCHIVE.read_bytes())==ARCHIVE_SHA
        if not (engines/'new').exists():
            (engines/'new').mkdir()
            with zipfile.ZipFile(ARCHIVE) as z: z.extractall(engines/'new')
        contract=json.loads((REPO/'.taskplane/context-optimization-2026-09-22/benchmark-contract.json').read_text())
        for entry in contract['input_files']:
            raw=(REPO/entry['path']).read_bytes()
            assert len(raw)==entry['bytes'] and sha(raw)==entry['sha256']
        identity={'old':{'origin':str(OLD),'files':files(engines/'old')},
                  'new':{'origin':str(ARCHIVE),'archive_sha256':ARCHIVE_SHA,'files':files(engines/'new')},
                  'python':sys.version,'executable':sys.executable,'fixture_inputs':contract['input_files'],
                  'driver_sha256':sha(Path(__file__).read_bytes())}
        write_json(BASE/'identity.json',identity)
    
    if __name__=='__main__':
        parser=argparse.ArgumentParser()
        parser.add_argument('--arm',choices=['old','new'])
        parser.add_argument('--repetition',type=int,default=0)
        parser.add_argument('--prepare',action='store_true')
        args=parser.parse_args()
        if args.prepare: prepare()
        else: Replay(args.arm,args.repetition).run()


if __name__ == '__main__' and '--compare-engines' in __import__('sys').argv:
    __import__('sys').argv.remove('--compare-engines')
    engine_comparison_entry()
    raise SystemExit(0)


import argparse
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from taskplane import depgraph, flow, workflow as w, workflow_host as h, workflow_local as local
from taskplane.context import Store, encode, digest
from taskplane.context_handoff import Session
from taskplane.context_reuse import key, run_check, lookup, record
from taskplane.context_views import view
from taskplane.tests.test_workflow_local import decision

ROOT = Path(__file__).resolve().parents[2]


def size(value): return len(encode(value))


def review(workspace):
    fixture = json.loads((ROOT/'evals/frozen-pr-9464/fixture.json').read_text())
    store = Store(workspace)
    delivered = []
    resolved = []
    baseline = 0
    for lens in ('architecture', 'security', 'backend', 'code-quality'):
        # The same frozen relevant/shared bundle is delivered to every baseline
        # consumer. No upstream checkout, generated padding or HTML is appended.
        baseline += size({'lens': lens, 'input': fixture})
        inputs = [{'id': 'requirement', 'body': fixture['requirement']},
                  {'id': 'changed-source', 'body': {'files': fixture['changed_files'],
                                                   'symbols': fixture['changed_symbols']}},
                  {'id': 'dependency-and-error-paths', 'body': fixture['snapshot']},
                  {'id': 'coverage', 'body': {'graph': fixture['graph'], 'impact': fixture['impact']}}]
        result = view(store, {'workspace': str(workspace), 'root': 'fixture-root',
                             'run': 'fixture-review', 'visit': lens, 'revision': 0,
                             'scope_digest': digest({'criteria': ['safe-input-boundary']})}, 'engineering',
                      [lens], ['safe-input-boundary'], {'authority': 'none'}, inputs,
                      {'module_confidence': fixture['impact']['module_confidence']})
        delivered.append(result)
        # All these small bodies must really enter the consumer, not only exist on disk.
        assert all(item['id'] in result['inline'] for item in inputs)
        resolved.append(result['inline'])
    oracle = json.loads((ROOT/'evals/frozen-pr-9464/oracle.json').read_text())
    edges = resolved[2]['dependency-and-error-paths']['symbol_edges']
    callers = {edge['caller'] for edge in edges}
    expected = set(oracle['required_callers']) if 'required_callers' in oracle else set(oracle['callers'])
    assert expected <= callers
    paths = oracle.get('required_paths', oracle.get('paths', []))
    pairs = {(edge['caller'], edge['callee']) for edge in edges}
    assert all(all(pair in pairs for pair in zip(path, path[1:])) for path in paths)
    # This leg retains a known supplied result; it does not claim LLM discovery.
    supplied = {'fixture_label': 'prior-review result supplied by evaluator', 'finding': oracle['blocker']}
    baseline += size({'input': fixture, 'prior_result': supplied})
    delivered.append(supplied)
    prior = record(store, {'eligible': False, 'coverage': 'low_confidence'}, status='unknown',
                   producer='fixture/prior-review', result={'returncode': None}, findings=[oracle['blocker']])
    followup = lookup(store, prior, {'eligible': False, 'coverage': 'low_confidence'})
    delivered.append(followup)
    assert followup['findings'] == [oracle['blocker']] and followup['status'] == 'miss'
    # Removing either incoming route must fail the structural preservation oracle.
    failures = []
    for name in ('missing-nodeclass-validation', 'missing-provisioning'):
        mutation = json.loads((ROOT/f'evals/frozen-pr-9464/mutations/{name}.json').read_text())
        removed = set(mutation.get('remove_callers', []))
        if not removed:
            removed = set(mutation.get('remove_symbols', []))
        remaining = [edge for edge in edges if edge['caller'] not in removed]
        assert not expected <= {edge['caller'] for edge in remaining}, mutation
        failures.append(name)
    return {'baseline_bytes': baseline, 'optimized_bytes': sum(map(size, delivered)),
            'quality': {'callers': len(expected), 'paths': len(paths), 'known_blocker_retained': True,
                        'mutations_rejected': failures, 'module_confidence': 'low',
                        'live_finding_discovery': 'not_measured'},
            'returned_responses': len(delivered)}


def consume(controller, state):
    """Fresh client calls only the public controller context operations."""
    session = Session(controller.workspace, state)
    returned = [controller.context(state['run'])]
    returned.append(controller.context(state['run'], consume=returned[0]['handoff_ref']['sha256']))
    for required in session.required:
        for sha in sorted(session.store.descendants(required['ref'])):
            if sha in session.ledger()['seen']: continue
            first = controller.context(state['run'], read=sha)
            returned.append(first)
            for page in range(1, first['page']['pages']):
                returned.append(controller.context(state['run'], read=sha, page=page))
    receipt = returned[-1]['context_receipt']
    session.validate(receipt)
    assert returned[-1]['remaining_required'] == 0
    return receipt, returned, session


def delivery(workspace, host='codex'):
    workspace.mkdir(parents=True, exist_ok=True)
    fixture = ROOT/'evals/fixture-repo'
    for source in (fixture/'tree-a').rglob('*.py'):
        target = workspace/source.relative_to(fixture/'tree-a')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (workspace/'.taskplane').mkdir(exist_ok=True)
    pricing = sorted(p.relative_to(fixture/'tree-b').as_posix() for p in (fixture/'tree-b').rglob('*.py'))
    scope = {'criteria': ['AC-DISCOUNT'], 'paths': {phase: [f'.taskplane/{phase}.json'] for phase in w.PHASES},
             'verification_inputs': pricing}
    scope['paths']['build'] += pricing + ['.taskplane/check.txt']
    tasks = {'tasks': [{'id': 'DISCOUNT', 'phase': 'build', 'dependencies': [], 'owner': 'fixture-root',
                       'criteria': ['AC-DISCOUNT'], 'paths': scope['paths']['build'],
                       'verification': 'Frozen pricing tests and exact before-tax arithmetic'}]}
    (workspace/'.taskplane/tasks.json').write_text(json.dumps(tasks))
    request = ('Add WELCOME 10, LOYALTY 15 and STAFF 50 percent discounts before 20 percent tax; '
               'unknown or absent code leaves subtotal unchanged. Preserve basket and catalog behavior.')
    c = h.Controller(workspace, 'fixture-root', h.installed_adapter(host))
    state = c.start({'scope': scope, 'request_reference': 'fixture-labelled/user-request', 'goal': request})
    depgraph.scan(str(workspace), decompose=True, strict=True)
    flow.publish_dashboard(workspace, state['run'], governor=c, select=True)
    outputs, responses, baselines = [], [], []
    verification = None
    checks = 0
    reused = []
    for number, phase in enumerate(w.PHASES):
        assert w.current(state)['phase'] == phase
        receipt, returned, session = consume(c, state)
        responses.extend(returned)
        baselines.append({'binding': session.binding, 'inputs': session.items})
        # The consumer uses the delivered requirement, not the driver's earlier
        # request variable. Resolution is after the public receipt proved every
        # byte of this required immutable body was returned and counted.
        requirement = next(item for item in session.required if item['id'] == 'requirements-and-scope')
        received = session.store.resolve(requirement['ref'])
        assert received['phase'] == phase and received['criteria'] == ['AC-DISCOUNT']
        request = received['goal']
        assert all(fact in request for fact in ('WELCOME 10', 'LOYALTY 15', 'STAFF 50', 'before 20 percent tax'))
        if phase == 'build':
            for relative in pricing:
                target = workspace/relative; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((fixture/'tree-b'/relative).read_bytes())
            depgraph.scan(str(workspace), decompose=True, strict=True)
            provenance = key(workspace, paths=[p for p in pricing if p.startswith('pricing/')],
                             tests=[p for p in pricing if p.startswith('tests/')], criteria=['AC-DISCOUNT'],
                             command=[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                             tool='python-unittest', contract='discount-before-tax/v1')
            verification = run_check(workspace, provenance, producer='fixture/build')
            checks += 1
            check = Store(workspace).resolve(verification)
            assert check['status'] == 'pass', check
            (workspace/'.taskplane/check.txt').write_text(check['result']['stdout']+check['result']['stderr'])
            # Source changed during the authorized Build. Renew the current
            # receipt and count that overhead; do not pad the baseline with an
            # extra consumer that its schedule did not require.
            receipt, returned, session = consume(c, state)
            responses.extend(returned)
        if phase in ('evaluate', 'engineering'):
            reused_body = [item['body'] for item in session.items if item['kind'] == 'verification']
            assert reused_body and reused_body[0]['status'] == 'reused', reused_body
            reused.append(phase)
        out = {'schema': 'taskplane.phase-output/v1', 'run': state['run'], 'visit': w.current(state)['id'],
               'phase': phase, 'criteria': ['AC-DISCOUNT'], 'context_receipt': receipt,
               **{field: request for field in w.OUTPUT_FIELDS[phase]}}
        if phase == 'product': out['acceptance_criteria'] = [{'id': 'AC-DISCOUNT', 'statement': request}]
        if phase == 'design': out['acceptance_test_map'] = {'AC-DISCOUNT': 'Frozen fixture pricing tests'}
        if phase == 'plan':
            out.update(task_dag=tasks['tasks'], ownership={'DISCOUNT': 'fixture-root'},
                       write_scope=scope['paths']['build'], acceptance_coverage={'AC-DISCOUNT': ['DISCOUNT']},
                       integration_order=['DISCOUNT'])
        if phase == 'build':
            out.update(change_inventory=pricing, task_acceptance_map={'AC-DISCOUNT': ['DISCOUNT']},
                       build_checks=[{'name': 'Frozen pricing tests', 'status': 'pass',
                                      'evidence': '.taskplane/check.txt', 'reuse_ref': verification}])
        if phase == 'evaluate':
            out['criterion_results'] = {'AC-DISCOUNT': {'status': 'pass', 'evidence': '.taskplane/check.txt',
                                                       'explanation': 'Unchanged matching Build verification'}}
        if phase == 'engineering':
            out.update(findings=[], lens_coverage=[{'lens': 'backend', 'reviewer': 'fixture',
                                                    'rationale': 'Discount arithmetic and caller preservation'}],
                       requirements_comparison={'AC-DISCOUNT': 'Pinned tree-b tests passed'})
        target = f'.taskplane/{phase}.json'; (workspace/target).write_text(json.dumps(out))
        state = c.apply('submit', state['run'], expected_revision=state['revision'], output=target,
                        tasks='.taskplane/tasks.json')
        blocked = False
        try:
            c.apply('advance' if phase != 'retro' else 'finish', state['run'],
                    expected_revision=state['revision'], phase=w.PHASES[min(number+1, 6)])
        except w.Refusal: blocked = True
        assert blocked, 'Fixture phase advanced without a human gate'
        flow.publish_dashboard(workspace, state['run'], governor=c, select=True)
        local.Harness(workspace, c.root).present(state, '.taskplane/dashboard.html', 'linked',
                                                'Fixture-labelled link, not live UI verification.')
        state = c.apply('decide', state['run'], expected_revision=state['revision'],
                        native_reference=json.dumps(decision(state, event='fixture-human-'+phase)))
        outputs.append(out)
        if phase != 'retro':
            state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=w.PHASES[number+1])
    state = c.apply('finish', state['run'], expected_revision=state['revision'])
    assert state['finished'] and len(state['decisions']) == 7
    full = flow.report(workspace, state['run'], governor=c)
    # Controller-only fixture actions intentionally do not create an observation
    # journal. Bind the explicit finished run when the CLI projection is absent.
    if not full:
        full = c.report(state['run'])
    stream = io.StringIO()
    with redirect_stdout(stream): flow.emit(full, workspace, 'report')
    compact_bytes = len(stream.getvalue().encode())
    return {'baseline_bytes': sum(map(size, baselines)), 'optimized_bytes': sum(map(size, responses)),
            'quality': {'human_gates': 7, 'phase_receipts': 7, 'checks_executed': checks,
                        'checks_reused': reused, 'finished': True, 'native_host': host},
            'returned_responses': len(responses), 'full_report_bytes': size(full),
            'compact_report_bytes': compact_bytes}


def benchmark(contract_path, output_path):
    contract = json.loads(contract_path.read_text())
    pinned = []
    for expected in contract['input_files']:
        raw = (ROOT/expected['path']).read_bytes()
        assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
        pinned.append(expected)
    records = []
    with tempfile.TemporaryDirectory(prefix='taskplane-context-bench-') as tmp:
        for repetition in range(contract['measurement']['repetitions']):
            for fixture, execute in [('CTX-BENCH-REVIEW', review), ('CTX-BENCH-DELIVERY', delivery)]:
                path = Path(tmp)/f'{fixture}-{repetition}'
                cold = execute(path)
                # A separate fresh run with unchanged fixture bodies is the warm
                # execution; canonical store objects are retained, gates are new.
                warm_path = Path(tmp)/f'{fixture}-{repetition}-warm'
                warm_path.mkdir()
                if (path/'.taskplane/context-v1').exists():
                    shutil.copytree(path/'.taskplane/context-v1/objects', warm_path/'.taskplane/context-v1/objects')
                warm = execute(warm_path)
                baseline = cold['baseline_bytes']+warm['baseline_bytes']
                optimized = cold['optimized_bytes']+warm['optimized_bytes']
                ratio = optimized/baseline
                records.append({'fixture': fixture, 'repetition': repetition+1,
                                'cold': cold, 'warm': warm, 'ratio': ratio,
                                'threshold': 0.5, 'status': 'pass' if ratio <= 0.5 else 'fail'})
    result = {'schema': 'taskplane.context-benchmark-results/v1', 'pinned_inputs': pinned,
              'unit': 'UTF-8 consumer input bytes', 'records': records,
              'status': 'pass' if all(row['status'] == 'pass' for row in records) else 'fail',
              'native_tokens': {'status': 'not_comparable', 'reason': 'No matched real model replay'},
              'limitations': ['Deterministic consumers prove transport and gate composition, not model attention.',
                              'Known review blocker is supplied by evaluator, not newly discovered.',
                              'Warm transport repeats fresh gates; immutable object storage reuse is not a token discount.']}
    output_path.write_text(json.dumps(result, indent=2)+'\n')
    return result


def test_review_fixture_transport_and_oracle(tmp_path):
    result = review(tmp_path)
    assert result['quality']['known_blocker_retained']


def test_delivery_fixture_has_seven_receipts_and_gates(tmp_path):
    result = delivery(tmp_path)
    assert result['quality']['human_gates'] == 7 and result['compact_report_bytes'] <= 16384


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--benchmark-contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = benchmark(args.benchmark_contract, args.output)
    print(json.dumps({'status': result['status'], 'records': len(result['records'])}))
    raise SystemExit(0 if result['status'] == 'pass' else 1)
