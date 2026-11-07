"""dis.py IMG ADDR [COUNT]: Thumb disassembly of a decoded RD-6 image (loaded at 0x08002800)."""
import os, sys, capstone
BASE = 0x08002800
def load(name):
    here = os.path.dirname(os.path.abspath(__file__))
    paths = {'stock106': 'cache/stock106.bin', 'swing106': 'out/swing106.bin'}
    return open(os.path.join(here, paths.get(name, name)), 'rb').read()
def dis(img, addr, count=40):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    addr &= ~1
    out = []
    off = addr - BASE
    for ins in md.disasm(img[off:off + count * 4], addr):
        extra = ''
        if ins.mnemonic.startswith('ldr') and '[pc' in ins.op_str:
            try:
                disp = int(ins.op_str.split('#')[-1].rstrip(']'), 0)
                lit = ((ins.address + 4) & ~3) + disp
                extra = f'   ; ={int.from_bytes(img[lit-BASE:lit-BASE+4], "little"):#010x}'
            except Exception: pass
        out.append(f'{ins.address:08x}: {ins.bytes.hex():<10} {ins.mnemonic:<8} {ins.op_str}{extra}')
        if len(out) >= count: break
    return '\n'.join(out)
if __name__ == '__main__':
    print(dis(load(sys.argv[1]), int(sys.argv[2], 16), int(sys.argv[3]) if len(sys.argv) > 3 else 40))
