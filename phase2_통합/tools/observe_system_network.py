"""Read-only Windows TCP sampling during one actual integrated synthetic run."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import httpx
from check_system_demo import run

HERE=Path(__file__).resolve().parent


def observe(private, output):
    work=private/('network_'+uuid.uuid4().hex[:8]);work.mkdir()
    stop=work/'stop';raw=work/'tcp.jsonl'
    with (work/'server.log').open('w') as log:
        app=subprocess.Popen([sys.executable,'-B','-X','utf8',str(HERE/'run_system_demo.py'),
            '--config',str(private/'system_config.json'),'--port','18084'],stdout=log,stderr=log)
        monitor=None
        try:
            with httpx.Client(timeout=2,trust_env=False) as client:
                for _ in range(40):
                    if app.poll() is not None:raise RuntimeError('Server startup failed')
                    try:
                        if client.get('http://127.0.0.1:18084/health').status_code==401:break
                    except httpx.TransportError:pass
                    time.sleep(.5)
                else:raise RuntimeError('Server startup timeout')
            monitor=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'observe_system_network.ps1'),
                '-RootProcessId',str(app.pid),'-StopFile',str(stop),'-OutputFile',str(raw)],stdout=log,stderr=log)
            for _ in range(30):
                if monitor.poll() is not None:raise RuntimeError('TCP observer startup failed; see server.log')
                if raw.exists():break
                time.sleep(.5)
            else:raise RuntimeError('TCP observer startup timeout')
            run(private,work/'e2e.json',True,'http://127.0.0.1:18084')
        finally:
            stop.touch()
            if monitor:
                try:monitor.wait(timeout=30)
                except subprocess.TimeoutExpired:monitor.terminate();monitor.wait()
            app.terminate();app.wait(timeout=20)
    samples=[json.loads(line) for line in raw.read_text(encoding='utf-8-sig').splitlines()]
    connections={}
    for sample in samples:
        for row in sample.get('connections',[]):
            if row['remote_port']==0:continue
            key=(row['tunnel'],row['remote_address'],row['remote_port'])
            connections[key]={'tunnel':row['tunnel'],'remote_address':row['remote_address'],
                              'remote_port':row['remote_port'],'loopback':ipaddress.ip_address(row['remote_address']).is_loopback}
    report={'date':'2026-10-10','method':'Windows Get-NetTCPConnection periodic snapshots',
        'sample_count':len(samples),'sampling_errors':sum('error' in s for s in samples),
        'observed_endpoints':list(connections.values()),'e2e':json.loads((work/'e2e.json').read_text(encoding='utf-8')),
        'limits':['TCP only; short-lived connections between samples and UDP can be missed.',
                  'Scope: launched application descendants and process listening on SSH tunnel port 11435.',
                  'Remote Ollama server processes not observed; not a packet capture or complete no-egress proof.']}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='e2e'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();observe(a.private.resolve(),a.output)
