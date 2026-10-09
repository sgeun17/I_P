"""Python-process loopback-only socket policy; not an OS firewall.

Native libraries/other processes are outside this control. Install before clients
are created. A separate hook is required in every Python worker.
"""
import ipaddress
import os
import sys


def is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode('ascii')
    if host == 'localhost':
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or bool(getattr(ip, 'ipv4_mapped', None) and ip.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def install():
    # Prevent inherited HTTP clients from routing loopback LLM requests via proxies.
    for key in list(os.environ):
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            del os.environ[key]
    os.environ['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    def check(event, args):
        host = None
        if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
            host = args[0]
        elif event == 'socket.getnameinfo':
            host = args[0][0]
        elif event == 'socket.connect' and isinstance(args[1], tuple):
            host = args[1][0]
        elif event in ('socket.sendto', 'socket.sendmsg') and isinstance(args[-1], tuple):
            host = args[-1][0]
        if host is not None and not is_loopback(host):
            raise PermissionError('OFFLINE_EXTERNAL_CONNECTION_BLOCKED')
    sys.addaudithook(check)
