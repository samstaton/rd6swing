"""xref.py IMG TARGET...: linear-sweep the image for bl/b/b.w to TARGET and literal-pool words equal to TARGET."""
import sys, capstone
from rdis import load, BASE
img = load(sys.argv[1]); targets = {int(t, 16) & ~1 for t in sys.argv[2:]}
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB); md.skipdata = True
end = BASE + len(img)
for ins in md.disasm(img, BASE):
    if ins.mnemonic in ('bl', 'b', 'b.w', 'blx', 'bne', 'beq', 'bne.w', 'beq.w') and ins.op_str.startswith('#'):
        t = int(ins.op_str[1:], 16)
        if t in targets: print(f'{ins.address:08x}: {ins.mnemonic} {ins.op_str}')
for off in range(0, len(img) - 3, 4):
    w = int.from_bytes(img[off:off+4], 'little')
    if (w & ~1) in targets: print(f'{BASE+off:08x}: .word {w:#010x}')
