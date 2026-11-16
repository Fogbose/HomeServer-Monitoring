"""Explicit LAN wake only, bound to a configured local private IPv4 interface."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket

ROOT=Path(__file__).resolve().parents[1]


def validate(config):
    mac=config['mac']
    if not re.fullmatch(r'(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}',mac):
        raise ValueError('Configure a valid MAC')
    network=ipaddress.IPv4Network(config['network'],strict=True)
    source=ipaddress.IPv4Address(config['source'])
    if not network.is_private or source not in network or source in (network.network_address,network.broadcast_address):
        raise ValueError('Private LAN interface/subnet required')
    return source,network.broadcast_address,b'\xff'*6+bytes.fromhex(mac.replace(':',''))*16


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--send',action='store_true')
    args=parser.parse_args()
    config=json.loads((ROOT/'private/monitoring/site.json').read_text())
    source,broadcast,payload=validate(config['lan_wake'])
    if not args.send:
        print('Preview only; no packet sent')
        return
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.bind((str(source),0))
        udp.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
        udp.sendto(payload,(str(broadcast),9))
    print('LAN packet sent; actual wake and power state are not confirmed')


if __name__=='__main__':
    main()
