"""RD-6 update-file container: decode to a flat image (and back), following the
format documented by the Scatter project (docs/TECHNICAL.md, updater analyse())."""
import hashlib, sys

PREFIX = bytes([0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x0D, 0x00])

def split(data):
    out, pos = [], 0
    while pos < len(data):
        assert data[pos] == 0xF0, pos
        end = data.index(0xF7, pos)
        out.append(data[pos:end + 1]); pos = end + 1
    return out

def le7(m, o):
    return m[o] | (m[o+1] << 7) | (m[o+2] << 14) | (m[o+3] << 21)

def unpack7(d):
    out = bytearray()
    for p in range(0, len(d), 8):
        g = d[p:p+8]
        for i in range(1, len(g)):
            out.append(g[i] | (((g[0] >> (7 - i)) & 1) << 7))
    return bytes(out)

def mask_of(raw):
    n = len(raw) // 128; best = (0, 0); start = 0
    blk = lambda i: raw[i*128:(i+1)*128]
    for i in range(1, n + 1):
        if i < n and blk(i) == blk(start): continue
        if i - start > best[1]: best = (start, i - start)
        start = i
    return blk(best[0]), best[1]

def decode(syx):
    fr = split(syx)
    assert len(fr) == 383 and all(f[:8] == PREFIX for f in fr)
    raw = b''.join(unpack7(f[14:-1]) for f in fr[6:])
    assert len(raw) == le7(fr[5], 10)
    mask, run = mask_of(raw)
    img = bytes(b ^ mask[i % 128] for i, b in enumerate(raw))
    hdr = fr[5]
    return dict(image=img, mask=mask, run=run, frames=fr,
                version='.'.join(map(str, hdr[29:32])), size=le7(hdr, 10), sum=le7(hdr, 14),
                imgsum=sum(img) & 0xFFFFFFFF)

if __name__ == '__main__':
    for path in sys.argv[1:]:
        d = decode(open(path, 'rb').read())
        img = d['image']
        print(path.split('/')[-1], 'v' + d['version'], 'size', hex(len(img)), 'mask run', d['run'],
              'hdr sum ok', d['sum'] == d['imgsum'] or (d['sum'], d['imgsum']),
              'SP', hex(int.from_bytes(img[0:4], 'little')), 'reset', hex(int.from_bytes(img[4:8], 'little')),
              'sha', hashlib.sha256(img).hexdigest()[:12])
