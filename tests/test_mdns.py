import struct
import unittest

from pkg import mdns


def _rr(name, rtype, rdata, ttl=120):
    return (mdns.encode_name(name) +
            struct.pack('!HHIH', rtype, 0x8001, ttl, len(rdata)) + rdata)


def build_response():
    """A typical ESPHome answer: PTR + SRV + TXT + A in one packet."""
    inst = 'konnected-abc123._konnected._tcp.local.'
    host = 'konnected-abc123.local.'
    txt = b''
    for entry in (b'friendly_name=Garage Door Opener',
                  b'project_name=konnected.garage-door-gdov2-s',
                  b'project_version=1.4.3', b'mac=a1b2c3d4e5f6'):
        txt += struct.pack('B', len(entry)) + entry
    srv = struct.pack('!HHH', 0, 0, 80) + mdns.encode_name(host)
    header = struct.pack('!HHHHHH', 0, 0x8400, 0, 4, 0, 0)
    return (header +
            _rr(mdns.SERVICE, mdns.TYPE_PTR, mdns.encode_name(inst)) +
            _rr(inst, mdns.TYPE_SRV, srv) +
            _rr(inst, mdns.TYPE_TXT, txt) +
            _rr(host, mdns.TYPE_A, bytes([192, 168, 1, 50])))


class MdnsTests(unittest.TestCase):
    def test_query_roundtrip(self):
        q = mdns.build_query()
        name, off = mdns.read_name(q, 12)
        self.assertEqual(name, mdns.SERVICE)
        rtype, rclass = struct.unpack('!HH', q[off:off + 4])
        self.assertEqual(rtype, mdns.TYPE_PTR)
        self.assertEqual(rclass, mdns.CLASS_IN | mdns.QU_BIT)

    def test_parse_response(self):
        records = mdns.parse_response(build_response())
        self.assertEqual([r['type'] for r in records],
                         [mdns.TYPE_PTR, mdns.TYPE_SRV, mdns.TYPE_TXT,
                          mdns.TYPE_A])
        services = mdns.merge_records({}, records)
        self.assertEqual(len(services), 1)
        svc = list(services.values())[0]
        self.assertEqual(svc.name, 'konnected-abc123')
        self.assertEqual(svc.host, '192.168.1.50')
        self.assertEqual(svc.port, 80)
        self.assertEqual(svc.txt['mac'], 'a1b2c3d4e5f6')
        self.assertTrue(mdns.is_gdo_white(svc))
        svc.txt['project_name'] = 'konnected.garage-door-gdov2-q'
        self.assertFalse(mdns.is_gdo_white(svc))
        svc.txt['project_name'] = 'konnected.alarm-panel-pro'
        self.assertFalse(mdns.is_gdo_white(svc))

    def test_compressed_name(self):
        # "local." at offset 12 then a pointer back to it
        packet = b'\x00' * 12 + mdns.encode_name('local.')
        pointer_at = len(packet)
        packet += b'\x03abc' + struct.pack('!H', 0xC000 | 12)
        name, off = mdns.read_name(packet, pointer_at)
        self.assertEqual(name, 'abc.local.')
        self.assertEqual(off, len(packet))

    def test_query_packet_ignored(self):
        self.assertEqual(mdns.parse_response(mdns.build_query()), [])
