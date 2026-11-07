"""Build rd6swing: assemble, link, patch Behringer's RD-6 1.0.6, verify, re-encode, make the browser patch.

    .venv/bin/python build.py   -> ../patches/rd6swing-v4.json  (committed: the loader applies it)
                                   out/RD6_Swing_v4.syx, out/swing106.bin, cache/stock106.bin  (git-ignored)

Behringer's official 1.0.6 update file is not in this repository. It is fetched into cache/ from the
Scatter project's unmodified mirror (the same source the loader uses) and checked by SHA-256.
"""
import base64, hashlib, json, os, subprocess, sys, urllib.request
import capstone
from elftools.elf.elffile import ELFFile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rd6syx import decode, le7, unpack7

BASE = 0x08002800
CACHE = os.path.join(HERE, 'cache')
STOCK_SYX = os.path.join(CACHE, 'RD-6_V1.0.6.syx')
STOCK_URL = 'https://tracelistener.github.io/rd6-scatter/firmware/RD-6_V1.0.6.syx'
PATCH = os.path.join(HERE, '..', 'patches', 'rd6swing-v4.json')
STOCK_SYX_SHA = '4446914dffce8152dc7f7c0065de52d266f062a6499a22a8fef36b221982cfc3'
STOCK_BIN_SHA = 'e20968a504ccf4302039cb99cfe647acab4f8bd12515b87a8828fd61a46d1656'
LLVM = '/opt/homebrew/opt/llvm/bin/'
OUT = os.path.join(HERE, 'out')

# hook site -> stock bytes it must replace, and the branch target it must get
HOOKS = {
    '.hook_set':  (0x08004CE8, bytes.fromhex('70470000'), 'b.w', 'sw_set'),
    '.hook_disp': (0x08006AE4, bytes.fromhex('2de9f05f'), 'b.w', 'sw_disp'),
    '.hook_t14':  (0x0800760A, bytes.fromhex('00f033fa'), 'bl',  'sw_tick'),
    '.hook_enq':  (0x08002DD4, bytes.fromhex('064a054b'), 'b.w', 'sw_enq'),
    '.hook_main': (0x080072E8, bytes.fromhex('074810b5'), 'b.w', 'sw_main'),
    '.hook_blank': (0x08005BB8, bytes.fromhex('41708270'), 'b.w', 'sw_blank'),
}
# absolute branches inside the new code: symbol -> (mnemonic, target)
ABS = {'DISPATCH_BODY': 0x08006AE8, 'T14_DISPLACED': 0x08007A74, 'ENQ_BODY': 0x08002DD8,
       'EE_WRITE': 0x0800383A, 'EE_READ': 0x0800381C, 'SAVE_BODY': 0x080072EC, 'BLANK_REST': 0x08005BBC}


def pack7(raw):
    out = bytearray()
    for p in range(0, len(raw), 7):
        g = raw[p:p + 7]
        hdr = 0
        for i, x in enumerate(g, start=1):
            hdr |= ((x >> 7) & 1) << (7 - i)
        out.append(hdr)
        out.extend(x & 0x7F for x in g)
    return bytes(out)


def enc7(v):
    return bytes([(v >> s) & 0x7F for s in (0, 7, 14, 21)])


def encode(template_syx, image):
    """Re-encode `image` into the frame layout of `template_syx` (same size)."""
    d = decode(template_syx)
    assert len(image) == len(d['image'])
    mask = d['mask']
    raw = bytes(x ^ mask[i % 128] for i, x in enumerate(image))
    frames = [bytearray(f) for f in d['frames']]
    hdr = frames[5]
    assert le7(hdr, 10) == len(image)
    hdr[14:18] = enc7(sum(image) & 0xFFFFFFF)
    assert sum(image) < 1 << 28
    pos = 0
    for f in frames[6:]:
        n = len(unpack7(bytes(f[14:-1])))
        f[14:-1] = pack7(raw[pos:pos + n])
        pos += n
    assert pos == len(raw)
    return b''.join(bytes(f) for f in frames)


def syms_lit(sec, ta):
    """Start of the literal pool: the first 4-aligned word equal to the SW state address."""
    _, data = sec
    return [ta + o for o in range(0, len(data) - 3, 4) if data[o:o + 4] == (0x20003000).to_bytes(4, 'little')]


def build_elf():
    os.makedirs(OUT, exist_ok=True)
    obj, elf = os.path.join(OUT, 'swing.o'), os.path.join(OUT, 'swing.elf')
    subprocess.run(['clang', '--target=thumbv7em-none-eabi', '-mcpu=cortex-m4', '-c',
                    os.path.join(HERE, 'swing.s'), '-o', obj], check=True)
    subprocess.run([LLVM + 'ld.lld', '-T', os.path.join(HERE, 'swing.ld'), obj, '-o', elf], check=True)
    return elf


def stock_syx():
    if not os.path.exists(STOCK_SYX):
        os.makedirs(CACHE, exist_ok=True)
        data = urllib.request.urlopen(STOCK_URL, timeout=30).read()
        assert hashlib.sha256(data).hexdigest() == STOCK_SYX_SHA, 'downloaded file is not official 1.0.6'
        open(STOCK_SYX, 'wb').write(data)
    return open(STOCK_SYX, 'rb').read()


def syx_patch(base, target, gap=8):
    """Byte runs that turn `base` into `target` (runs closer than `gap` bytes are merged)."""
    runs, i = [], 0
    while i < len(base):
        if base[i] == target[i]:
            i += 1
            continue
        j = i
        while j < len(base) and (base[j] != target[j] or base[j:j + gap] != target[j:j + gap]):
            j += 1
        runs.append([i, base64.b64encode(target[i:j]).decode()])
        i = j
    out = bytearray(base)
    for off, b64 in runs:
        d = base64.b64decode(b64)
        out[off:off + len(d)] = d
    assert bytes(out) == target
    return runs


def main():
    syx = stock_syx()
    assert hashlib.sha256(syx).hexdigest() == STOCK_SYX_SHA, 'not official 1.0.6'
    stock = decode(syx)
    img = bytearray(stock['image'])
    assert hashlib.sha256(img).hexdigest() == STOCK_BIN_SHA
    assert encode(syx, bytes(img)) == syx, 'encoder does not round-trip the official file'

    with open(build_elf(), 'rb') as fh:
        elf = ELFFile(fh)
        syms = {s.name: s['st_value'] & ~1 for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name}
        secs = {s.name: (s['sh_addr'], s.data()) for s in elf.iter_sections() if s.name.startswith(('.hook', '.text'))}

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    checks = []
    for name, (addr, old, mnem, target) in HOOKS.items():
        a, data = secs[name]
        assert a == addr and len(data) == 4, name
        off = addr - BASE
        assert bytes(img[off:off + 4]) == old, f'{name}: stock bytes differ'
        ins = next(md.disasm(data, addr))
        assert ins.mnemonic == mnem and int(ins.op_str[1:], 16) == syms[target], (name, ins.mnemonic, ins.op_str)
        img[off:off + 4] = data
        checks.append(f'{name} @ {addr:#010x}: {ins.mnemonic} {ins.op_str} = {target}')

    ta, text = secs['.text']
    assert ta == 0x0800B380 and len(text) <= 0x200, len(text)
    off = ta - BASE
    assert not any(img[off:off + len(text)]), 'target region not blank'
    img[off:off + len(text)] = text
    # every branch in the new code must stay inside it or hit a known absolute target
    inside = range(ta, ta + len(text))
    known = set(ABS.values()) | {syms['sw_straight']}
    for ins in md.disasm(text, ta):
        if ins.mnemonic in ('bl', 'b.w', 'blx') or (ins.mnemonic.startswith('b') and ins.op_str.startswith('#')):
            t = int(ins.op_str[1:], 16)
            assert ins.mnemonic != 'blx', ins
            assert t in inside or t in known, f'{ins.address:#x}: {ins.mnemonic} {ins.op_str}'
            if t not in inside:
                checks.append(f'  {ins.address:#010x}: {ins.mnemonic} {ins.op_str}')
    code_end = min(a for a in syms_lit(secs['.text'], ta))
    decoded = list(md.disasm(text[:code_end - ta], ta))
    assert decoded and decoded[-1].address + decoded[-1].size == code_end, 'capstone did not decode all code'
    for sym, t in ABS.items():
        assert any(ins.mnemonic in ('bl', 'b.w') and int(ins.op_str[1:], 16) == t
                   for ins in md.disasm(text, ta) if ins.op_str.startswith('#')), sym

    open(os.path.join(CACHE, 'stock106.bin'), 'wb').write(stock['image'])
    out_bin = os.path.join(OUT, 'swing106.bin')
    open(out_bin, 'wb').write(img)
    new_syx = encode(syx, bytes(img))
    back = decode(new_syx)
    assert back['image'] == bytes(img) and back['version'] == '1.0.6' and back['sum'] == back['imgsum']
    assert back['run'] >= 64, back['run']
    out_syx = os.path.join(OUT, 'RD6_Swing_v4.syx')
    open(out_syx, 'wb').write(new_syx)

    runs = syx_patch(syx, new_syx)
    patch = {
        'format': 'rd6swing-syx-patch-v1',
        'name': 'rd6swing v4',
        'note': 'Byte runs to apply to Behringer\'s official RD-6 1.0.6 SysEx update file. Contains only '
                'rd6swing\'s changes; the base file is Behringer\'s and is not included.',
        'base': {'name': 'Behringer RD-6 firmware 1.0.6', 'size': len(syx), 'sha256': STOCK_SYX_SHA},
        'target': {'name': 'rd6swing v4 (reports 1.0.6)', 'size': len(new_syx),
                   'sha256': hashlib.sha256(new_syx).hexdigest(),
                   'image_sha256': hashlib.sha256(img).hexdigest()},
        'runs': runs,
    }
    os.makedirs(os.path.dirname(PATCH), exist_ok=True)
    json.dump(patch, open(PATCH, 'w'), indent=1)
    open(PATCH, 'a').write('\n')
    checks.append(f'patch: {len(runs)} runs, {sum(len(base64.b64decode(b)) for _, b in runs)} bytes -> {os.path.relpath(PATCH, HERE)}')

    print('\n'.join(checks))
    print(f'code {ta:#010x}-{ta + len(text):#010x} ({len(text)} bytes); mask run {back["run"]} blocks')
    print('image sha256', hashlib.sha256(img).hexdigest())
    print('syx   sha256', hashlib.sha256(new_syx).hexdigest(), len(new_syx), 'bytes')


if __name__ == '__main__':
    main()
