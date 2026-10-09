import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from offline_network import is_loopback


class NetworkTests(unittest.TestCase):
    def test_hosts(self):
        for host in ('127.0.0.1','::1','localhost','::ffff:127.0.0.1'):
            self.assertTrue(is_loopback(host))
        for host in ('203.0.113.1','example.com','127.0.0.1.example.com','0.0.0.0'):
            self.assertFalse(is_loopback(host))

    def test_hook_blocks_before_external_connect(self):
        code='''
import socket, os
from offline_network import install
os.environ['HTTP_PROXY']='http://127.0.0.1:9999'
os.environ['https_proxy']='http://example.invalid:9999'
os.environ['ALL_PROXY']='socks5://127.0.0.1:9999'
install()
assert not any(k.lower() in ('http_proxy','https_proxy','all_proxy') for k in os.environ)
for operation in (lambda: socket.getaddrinfo('example.com',443),
                  lambda: socket.gethostbyname('example.invalid'),
                  lambda: socket.gethostbyaddr('203.0.113.1'),
                  lambda: socket.getnameinfo(('203.0.113.1',443),0),
                  lambda: socket.socket().connect(('203.0.113.1',443)),
                  lambda: socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'x',('203.0.113.1',53))):
    try: operation()
    except PermissionError as e: assert str(e)=='OFFLINE_EXTERNAL_CONNECTION_BLOCKED'
    else: raise AssertionError('external operation allowed')
assert socket.getaddrinfo('127.0.0.1',12345)
'''
        env=dict(os.environ,PYTHONPATH=str(ROOT))
        result=subprocess.run([sys.executable,'-B','-c',code],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_external_llm_config_rejected(self):
        sys.path.insert(0,str(ROOT/'tools'))
        from run_system_demo import validate_config
        for url in ('https://example.com/v1','http://127.0.0.1.evil/v1','http://user:pass@localhost/v1'):
            with self.assertRaises(ValueError): validate_config({'llm_base_url':url})

    def test_actual_http_loopback_and_external_redirect(self):
        code='''
import socket, threading, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from offline_network import install
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path=='/redirect':
            self.send_response(302)
            self.send_header('Location','http://203.0.113.1/blocked')
        else:
            self.send_response(200)
        self.send_header('Content-Length','0'); self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
os.environ['HTTP_PROXY']='http://127.0.0.1:1'
os.environ['NO_PROXY']=''
install()
try:
    with httpx.Client(timeout=3,follow_redirects=True) as client:
        assert client.get(f'http://127.0.0.1:{server.server_port}/ok').status_code==200
        try: client.get(f'http://127.0.0.1:{server.server_port}/redirect')
        except Exception as exc:
            chain=[]
            while exc:
                chain.append(str(exc)); exc=exc.__cause__ or exc.__context__
            assert any('OFFLINE_EXTERNAL_CONNECTION_BLOCKED' in s for s in chain),chain
        else: raise AssertionError('redirect escaped policy')
finally:
    server.shutdown(); server.server_close()
'''
        result=subprocess.run([sys.executable,'-B','-c',code],
            env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
