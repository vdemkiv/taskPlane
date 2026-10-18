"""Measure one explicit brief command. Measurement does not gate workflow progress."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--task')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    command=[sys.executable,str(ROOT/'taskplane/tp.py'),'flow','context','--workspace',str(args.workspace.resolve()),'--run',args.run]
    if args.task:command+=['--task',args.task]
    started=time.perf_counter()
    result=subprocess.run(command,capture_output=True)
    report={'schema':'taskplane.brief-measurement/v1','command':command,'returncode':result.returncode,
            'elapsed_seconds':time.perf_counter()-started,'stdout_bytes':len(result.stdout),
            'stderr_bytes':len(result.stderr),'coverage':'One actual CLI invocation; no attention or live-host certification.',
            'stdout':result.stdout.decode('utf-8',errors='replace'),'stderr':result.stderr.decode('utf-8',errors='replace')}
    raw=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(raw)
    print(raw,end='')
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
