"""Minimal mDNS (DNS-SD) discovery for Konnected ESPHome devices.

Konnected firmware advertises ``_konnected._tcp.local.`` with TXT
records ``project_name``, ``project_version``, ``mac``, ``friendly_name``
and the web server port in the SRV record (see packages/mdns.yaml in
konnected-esphome).  A full mDNS stack (zeroconf) drags in compiled
dependencies that have repeatedly broken the arm build matrix, so this
module implements just enough of RFC 6762 / RFC 6763 to send one PTR
query and parse the answers.
"""

import logging
import socket
import struct
import time

MDNS_GROUP = '224.0.0.251'
MDNS_PORT = 5353
SERVICE = '_konnected._tcp.local.'

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
CLASS_IN = 1
QU_BIT = 0x8000   # request a unicast response


class Service:
    """One discovered service instance."""

    def __init__(self, instance):
        self.instance = instance   # konnected-abc123._konnected._tcp.local.
        self.target = None         # hostname from SRV
        self.port = None
        self.address = None        # IPv4 from A record
        self.txt = {}

    @property
    def name(self):
        return self.instance.split('.')[0]

    @property
    def host(self):
        """Best address to talk to: IPv4 if known, else the mDNS hostname."""
        return self.address or self.target

    def __repr__(self):
        return '<Service {} {}:{} {}>'.format(self.name, self.host,
                                              self.port, self.txt)


def encode_name(name):
    out = b''
    for label in name.strip('.').split('.'):
        raw = label.encode('utf-8')
        out += struct.pack('B', len(raw)) + raw
    return out + b'\x00'


def build_query(service=SERVICE, unicast=True):
    header = struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0)
    qclass = CLASS_IN | (QU_BIT if unicast else 0)
    return header + encode_name(service) + struct.pack('!HH', TYPE_PTR,
                                                       qclass)


def read_name(data, offset, depth=0):
    """Decode a possibly-compressed DNS name.  Returns (name, next_offset)."""
    labels = []
    jumped = False
    next_offset = offset
    while True:
        if offset >= len(data):
            raise ValueError('truncated name')
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if depth > 20:
                raise ValueError('compression loop')
            pointer = struct.unpack('!H', data[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                next_offset = offset + 2
            jumped = True
            name, _ = read_name(data, pointer, depth + 1)
            labels.append(name.rstrip('.'))
            break
        offset += 1
        labels.append(data[offset:offset + length].decode('utf-8',
                                                          'replace'))
        offset += length
    if not jumped:
        next_offset = offset
    return '.'.join(labels) + '.', next_offset


def parse_txt(rdata):
    txt = {}
    i = 0
    while i < len(rdata):
        length = rdata[i]
        i += 1
        entry = rdata[i:i + length].decode('utf-8', 'replace')
        i += length
        if not entry:
            continue
        key, sep, value = entry.partition('=')
        txt[key] = value if sep else True
    return txt


def parse_response(data):
    """Parse an mDNS response packet into a list of resource records.

    Each record is a dict with name, type, and type-specific fields.
    """
    if len(data) < 12:
        raise ValueError('short packet')
    _, flags, qd, an, ns, ar = struct.unpack('!HHHHHH', data[:12])
    if not flags & 0x8000:
        return []   # a query, not a response
    offset = 12
    for _ in range(qd):
        _, offset = read_name(data, offset)
        offset += 4
    records = []
    for _ in range(an + ns + ar):
        name, offset = read_name(data, offset)
        rtype, rclass, ttl, rdlen = struct.unpack('!HHIH',
                                                  data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        rec = {'name': name, 'type': rtype, 'ttl': ttl}
        if rtype == TYPE_PTR:
            rec['target'], _ = read_name(data, offset)
        elif rtype == TYPE_SRV:
            prio, weight, port = struct.unpack('!HHH', rdata[:6])
            rec['port'] = port
            rec['target'], _ = read_name(data, offset + 6)
        elif rtype == TYPE_TXT:
            rec['txt'] = parse_txt(rdata)
        elif rtype == TYPE_A and rdlen == 4:
            rec['address'] = socket.inet_ntoa(rdata)
        offset += rdlen
        records.append(rec)
    return records


def merge_records(services, records, service=SERVICE):
    """Fold parsed records into the services dict (instance -> Service)."""
    hosts = {}
    for rec in records:
        if rec['type'] == TYPE_A:
            hosts[rec['name'].lower()] = rec['address']
    for rec in records:
        if rec['type'] == TYPE_PTR and rec['name'].lower() == service.lower():
            services.setdefault(rec['target'], Service(rec['target']))
    for rec in records:
        inst = rec['name']
        if rec['type'] == TYPE_SRV and inst.lower().endswith(service.lower()):
            svc = services.setdefault(inst, Service(inst))
            svc.target = rec['target']
            svc.port = rec['port']
        elif rec['type'] == TYPE_TXT and inst.lower().endswith(
                service.lower()):
            svc = services.setdefault(inst, Service(inst))
            svc.txt.update(rec['txt'])
    for svc in services.values():
        if svc.target and svc.target.lower() in hosts:
            svc.address = hosts[svc.target.lower()]
    return services


def _open_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                         socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    # Binding to 5353 lets us see multicast replies from responders that
    # ignore the unicast-response bit.  If another daemon (avahi) has the
    # port without SO_REUSEADDR this fails; fall back to an ephemeral port
    # and rely on unicast replies.
    try:
        sock.bind(('', MDNS_PORT))
        mreq = struct.pack('4s4s', socket.inet_aton(MDNS_GROUP),
                           socket.inet_aton('0.0.0.0'))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as ex:
        logging.debug('Could not bind mDNS port 5353 (%s); '
                      'using unicast replies only', ex)
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                             socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(('', 0))
    return sock


def discover(service=SERVICE, timeout=3.0, retries=2):
    """Query the LAN for ``service`` and return a list of Service objects."""
    services = {}
    query = build_query(service)
    sock = _open_socket()
    sock.settimeout(0.5)
    try:
        for attempt in range(retries):
            try:
                sock.sendto(query, (MDNS_GROUP, MDNS_PORT))
            except OSError as ex:
                logging.warning('mDNS send failed: %s', ex)
                return []
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(9000)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    merge_records(services, parse_response(data), service)
                except (ValueError, struct.error) as ex:
                    logging.debug('Ignoring bad mDNS packet: %s', ex)
            if services and all(s.host for s in services.values()):
                break
    finally:
        sock.close()
    found = [s for s in services.values() if s.host]
    logging.debug('mDNS discovery found %s', found)
    return found


def is_gdo_white(service):
    """True if the advertised project is a GDO White (not blaQ / panel)."""
    project = str(service.txt.get('project_name', '')).lower()
    if not project.startswith('konnected.garage-door'):
        return False
    return 'gdov2-q' not in project and 'blaq' not in project


def find_gdo_devices(timeout=3.0):
    """Discover GDO White devices.  Returns a list of Service objects."""
    devices = []
    for svc in discover(timeout=timeout):
        if is_gdo_white(svc):
            devices.append(svc)
        else:
            logging.debug('Skipping non-GDO Konnected device %s (%s)',
                          svc.name, svc.txt.get('project_name'))
    return devices


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    for dev in find_gdo_devices():
        print(dev)
