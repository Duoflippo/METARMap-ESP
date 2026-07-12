# captive_dns.py — minimal wildcard DNS server for the setup captive portal.
#
# Answers EVERY DNS query with the AP's own IP (192.168.4.1). Combined with the
# HTTP server answering the OS connectivity-probe URLs, this makes phones pop up
# the "sign in to network" sheet automatically instead of the user having to
# type the IP. Best-effort: if it can't bind, provisioning still works via the
# manual-IP fallback.
#
# Non-blocking: poll() is called from the provisioning loop alongside the web
# server, so it never stalls the setup UI.


def build_response(data, n, ip_bytes):
    """Build a DNS answer that points the queried name at ip_bytes. Pure byte
    work (no sockets) so it can be unit-tested on desktop."""
    if n < 12:
        return None
    if data[2] & 0x80:          # QR bit set -> already a response, ignore
        return None
    # Walk the question's QNAME labels to find where the question section ends.
    i = 12
    while i < n:
        length = data[i]
        if length == 0:
            i += 1
            break
        i += length + 1
    qend = i + 4                 # + QTYPE(2) + QCLASS(2)
    if qend > n:
        return None
    r = bytearray()
    r += data[0:2]              # transaction ID (echo)
    r += b"\x81\x80"           # flags: response, recursion available, no error
    r += b"\x00\x01"           # QDCOUNT = 1
    r += b"\x00\x01"           # ANCOUNT = 1
    r += b"\x00\x00\x00\x00"   # NSCOUNT = 0, ARCOUNT = 0
    r += data[12:qend]         # echo the original question
    r += b"\xc0\x0c"           # answer name = pointer to the question at offset 12
    r += b"\x00\x01"           # TYPE  = A
    r += b"\x00\x01"           # CLASS = IN
    r += b"\x00\x00\x00\x3c"   # TTL   = 60s
    r += b"\x00\x04"           # RDLENGTH = 4
    r += ip_bytes              # the AP IP
    return bytes(r)


class CaptiveDNS:
    def __init__(self, pool, ap_ip):
        self.ip_bytes = bytes(int(o) for o in ap_ip.split("."))
        self.sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind((ap_ip, 53))
        self._buf = bytearray(512)

    def poll(self):
        try:
            n, addr = self.sock.recvfrom_into(self._buf)
        except OSError:
            return              # nothing waiting (non-blocking) or transient error
        resp = build_response(self._buf, n, self.ip_bytes)
        if resp:
            try:
                self.sock.sendto(resp, addr)
            except OSError:
                pass

    def stop(self):
        try:
            self.sock.close()
        except Exception:
            pass


# --- desktop self-test: `python captive_dns.py` -----------------------------
if __name__ == "__main__":
    # A standard query for "captive.apple.com" A/IN.
    query = (b"\xab\xcd" + b"\x01\x00" + b"\x00\x01" + b"\x00\x00" +
             b"\x00\x00" + b"\x00\x00" +
             b"\x07captive\x05apple\x03com\x00" + b"\x00\x01" + b"\x00\x01")
    ip = bytes((192, 168, 4, 1))
    resp = build_response(query, len(query), ip)
    assert resp is not None
    assert resp[0:2] == b"\xab\xcd"          # echoed transaction ID
    assert resp[2:4] == b"\x81\x80"          # response flags
    assert resp[6:8] == b"\x00\x01"          # one answer
    assert resp[-4:] == ip                    # answer points at the AP IP
    assert resp[-6:-4] == b"\x00\x04"         # RDLENGTH 4
    print("captive_dns build_response OK — %d-byte reply -> %s"
          % (len(resp), ".".join(str(b) for b in resp[-4:])))
