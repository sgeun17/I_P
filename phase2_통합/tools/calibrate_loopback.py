"""Known TCP/UDP loopback traffic; application receipt is NOT packet capture proof."""
import argparse
import json
from pathlib import Path
import socket
import threading


def measure(kind, port, count=10):
    payload=b'ISMS-SYNTHETIC-NETWORK-CALIBRATION'
    received=[]
    errors=[]
    with socket.socket(socket.AF_INET,kind) as server:
        server.bind(('127.0.0.1',port));server.settimeout(5)
        if kind==socket.SOCK_STREAM:server.listen()
        def receive():
            try:
                for _ in range(count):
                    if kind==socket.SOCK_STREAM:
                        conn,_=server.accept()
                        with conn:
                            conn.settimeout(5)
                            body=b''
                            while len(body)<len(payload):
                                part=conn.recv(len(payload)-len(body))
                                if not part:break
                                body+=part
                            received.append(body==payload);conn.sendall(b'OK')
                    else:
                        body,addr=server.recvfrom(256)
                        received.append(body==payload);server.sendto(b'OK',addr)
            except Exception as exc:errors.append(type(exc).__name__)
        thread=threading.Thread(target=receive);thread.start()
        acknowledgements=0
        try:
            for _ in range(count):
                with socket.socket(socket.AF_INET,kind) as client:
                    client.settimeout(5)
                    client.connect(('127.0.0.1',port));client.sendall(payload)
                    acknowledgements+=client.recv(2)==b'OK'
        finally:thread.join(timeout=6)
    return {'protocol':'TCP' if kind==socket.SOCK_STREAM else 'UDP','port':port,
            'sent':count,'verified_receipts':sum(received),'acknowledgements':acknowledgements,
            'errors':errors,'passed':sum(received)==count and acknowledgements==count and not errors}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report={'scope':'LOOPBACK_APPLICATION_RECEIPT_ONLY','packet_capture_verified':False,
            'checks':[measure(socket.SOCK_STREAM,18191),measure(socket.SOCK_DGRAM,18192)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(report))
