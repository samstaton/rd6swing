"""Run stock RD-6 1.0.6 and rd6swing side by side in Unicorn and compare.

Handlers are called as plain AAPCS functions, one at a time (interrupt preemption is not modelled).
GPIO set/clear-register writes are logged with the writing pc and the current TIMER14 tick, and update
the pin state that code reads back. MIDI out bytes are logged at the stock enqueue body. The I2C EEPROM
is simulated at the stock read/write routines, with injectable failures.

    .venv/bin/python test_emu.py
"""
import collections, os, random
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_PC, UC_ARM_REG_PRIMASK, UC_CPU_ARM_CORTEX_M4)

HERE = os.path.dirname(os.path.abspath(__file__))
BASE, RET = 0x08002800, 0x08001000          # image base; sentinel return address
SW, MAGIC, TAG = 0x20003000, 0x474E5753, 0xA0
SW_TICKS, SW_LAST, SW_PEND, SW_STEP, SW_SAVE = 4, 8, 12, 16, 20
TRANSPORT, PATTERN, MODE, CLK, PAT_SLOT, SEL = 0x20000BB4, 0x20000A9C, 0x20000260, 0x2000110C, 0x20000288, 0x200009BC
KEYS = 0x20000224                            # +8 CLEAR held, +0xC FUNCTION held
DISPATCH, TIMER13, TIMER14, FUNC_STEP, STEP_KEY = 0x08006AE4, 0x08007528, 0x080075F0, 0x08004CC8, 0x08004CEC
ENQ, ENQ_BODY, EE_WRITE, EE_READ, SAVE_FN, BLANK, MIDI_RT = 0x08002DD4, 0x08002DD8, 0x0800383A, 0x0800381C, 0x080072E8, 0x08005BA0, 0x080052F8
SAVEFLAGS = 0x20000200
GPIO_LOG = {0x48000018: 'A_set', 0x48000028: 'A_clr', 0x48000418: 'B_set', 0x48000428: 'B_clr',
            0x48000818: 'C_set', 0x48000828: 'C_clr', 0x48001418: 'F_set', 0x48001428: 'F_clr'}
K = [round((2 * s / 100 - 1) * 256) for s in (50, 54, 58, 62, 66, 71)]   # from the percentages, not from swing.s
PPSTEP = {0: 24, 1: 12, 2: 32, 3: 16}       # TIMER13 ticks per step by scale (stock table at 0x08006E94)
STOCK, SWING = 'cache/stock106.bin', 'out/swing106.bin'
with open(os.path.join(HERE, 'out/swing.elf'), 'rb') as fh:
    SYM = {s.name: s['st_value'] & ~1 for s in ELFFile(fh).get_section_by_name('.symtab').iter_symbols() if s.name}

# Writers whose GPIO writes belong to the dispatch (move with it) or follow it from the trigger countdown.
DISPATCH_PCS = [(0x08006AE4, 0x08006C48), (0x08002EE4, 0x08002F00)]
COUNTDOWN_PCS = [(0x08006DC0, 0x08006EC8), (0x08002E0A, 0x08002E70)]
inside = lambda pc, ranges: any(a <= pc < b for a, b in ranges)


class RD6:
    def __init__(self, image, fail_writes=0, fail_reads=0):
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
        for base, size in ((0x08000000, 0x20000), (0x20000000, 0x10000), (0x40000000, 0x30000), (0x48000000, 0x2000), (0xE0000000, 0x100000)):
            uc.mem_map(base, size)
        uc.mem_write(BASE, open(os.path.join(HERE, image), 'rb').read())
        self.log, self.enq, self.now, self.ee_writes = [], [], 0, []
        self.eeprom = bytearray(0x8000)
        self.fail_writes, self.fail_reads = fail_writes, fail_reads
        uc.hook_add(UC_HOOK_MEM_WRITE, self._gpio, begin=0x48000000, end=0x48002000)
        uc.hook_add(UC_HOOK_CODE, self._enq, begin=ENQ_BODY, end=ENQ_BODY)
        for a in (EE_READ, EE_WRITE):
            uc.hook_add(UC_HOOK_CODE, self._ee, begin=a, end=a)
        # 0x08002A5C walks a pointer list that boot code sets up; not run here, so it returns at once
        uc.hook_add(UC_HOOK_CODE, lambda uc, a, n, d: uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR)), begin=0x08002A5C, end=0x08002A5C)

    def _gpio(self, uc, access, addr, size, value, data):
        port, off = addr & ~0x3FF, addr & 0x3FF
        if off in (0x18, 0x28):                       # set/clear registers drive the output and input state
            out = int.from_bytes(uc.mem_read(port + 0x14, 4), 'little')
            out = ((out | (value & 0xFFFF)) & ~(value >> 16)) if off == 0x18 else out & ~(value & 0xFFFF)
            for o in (0x10, 0x14):
                uc.mem_write(port + o, (out & 0xFFFF).to_bytes(4, 'little'))
        if addr in GPIO_LOG and value:
            self.log.append((self.now, GPIO_LOG[addr], value, uc.reg_read(UC_ARM_REG_PC)))

    def _enq(self, uc, addr, size, data):
        self.enq.append((self.now, uc.reg_read(UC_ARM_REG_R0) & 0xFF))

    def _ee(self, uc, addr, size, data):
        a, p, n = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if addr == EE_WRITE:
            src = bytes(uc.mem_read(p, n))
            self.ee_writes.append((a, src))
            if self.fail_writes: self.fail_writes -= 1
            else: self.eeprom[a:a + n] = src
        elif self.fail_reads:
            self.fail_reads -= 1                       # like the real routine on NACK: buffer untouched
        else:
            uc.mem_write(p, bytes(self.eeprom[a:a + n]))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def w32(self, a, v): self.uc.mem_write(a, (v & 0xFFFFFFFF).to_bytes(4, 'little'))
    def w16(self, a, v): self.uc.mem_write(a, (v & 0xFFFF).to_bytes(2, 'little'))
    def w8(self, a, v): self.uc.mem_write(a, bytes([v & 0xFF]))
    def r32(self, a): return int.from_bytes(self.uc.mem_read(a, 4), 'little')
    def r8(self, a): return self.uc.mem_read(a, 1)[0]

    def call(self, fn, r0=0, r1=0, r2=0, limit=5_000_000):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, 0x20001700)
        uc.reg_write(UC_ARM_REG_LR, RET | 1)
        for r, v in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1), (UC_ARM_REG_R2, r2)):
            uc.reg_write(r, v)
        uc.emu_start(fn | 1, RET, count=limit)
        assert uc.reg_read(UC_ARM_REG_PC) == RET, f'call {fn:#x} did not return'

    def ram(self):
        r = bytearray(self.uc.mem_read(0x20000000, 0x3000))
        r[0x1600:0x1730] = bytes(0x130)               # stack: call residue
        return bytes(r)

    def pattern(self, rows, length=16, scale=0, swing=0, slot=0x1C0):
        data = bytes([0, scale, length, swing]) + b''.join(r.to_bytes(2, 'little') for r in rows)
        self.uc.mem_write(PATTERN, data)
        self.eeprom[slot:slot + 22] = data
        self.w16(PAT_SLOT, slot)
        idx, grp = (slot >> 6) & 15, slot >> 10       # keep the selection consistent with the loaded slot
        for off in (4, 8, 0xC):
            self.w32(SEL + off, idx)
        self.w8(SEL + 0x10, grp)

    def setup(self, rows, length=16, scale=0, swing=0, slot=0x1C0, playing=True):
        self.pattern(rows, length, scale, swing, slot)
        for off, v in ((0, int(playing)), (4, 0), (8, 0), (0xC, PPSTEP[scale]), (0x10, 0), (0x14, 0)):
            self.w32(TRANSPORT + off, v)
        self.w8(MODE, 2)
        self.w8(CLK + 0x84, 0)                        # internal clock

    def tick14(self):
        self.w32(0x40014010, 1)
        self.call(TIMER14)
        self.now += 1

    def tick13(self):
        self.w32(0x40002010, 1)
        self.call(TIMER13)

    def midi(self, status):
        self.call(MIDI_RT, 0x0F | (status << 8))


def rows(seed):
    r = random.Random(seed)
    return [r.getrandbits(16) for _ in range(9)]


failures = []
def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok: failures.append(what)


# ---- identity with stock where rd6swing must not change anything

def test_identity():
    for swing, scale in ((0x00, 0), (0xFF, 0), (TAG, 0), (TAG | 6, 0), (TAG | 7, 0), (0xB3, 0), (0x23, 0),
                         (0x00, 1), (TAG | 5, 2), (TAG | 5, 3)):
        same = True
        for seed in range(16):
            out = []
            for img in (STOCK, SWING):
                m = RD6(img); m.setup(rows(seed), swing=swing, scale=scale)
                for k in range(3):
                    m.w32(TRANSPORT + 8, (seed + k) % 16)
                    m.call(DISPATCH)
                    for _ in range(300): m.tick14()
                out.append((m.log, m.enq, m.ram()))
            same &= out[0] == out[1]
        check(same, f'swing byte {swing:#04x}, scale {scale}: three steps identical to stock (pins, MIDI, RAM)')


# ---- setting the swing through the real key handler, and saving it from the main loop

def press(m, step=3, mode=2, playing=False):
    m.w8(MODE, mode); m.w32(TRANSPORT, int(playing))
    m.w32(KEYS + 0xC, 1); m.w32(KEYS + 8, 0)       # FUNCTION held, CLEAR not
    m.call(STEP_KEY, step)                          # returns into the stock handler, which reads r4/r5


def test_setting():
    for mode, playing, expect in ((2, False, True), (2, True, True), (0, False, True), (0, True, True),
                                  (3, False, True), (3, True, False), (1, False, False), (4, False, False)):
        out = []
        for img in (STOCK, SWING):
            m = RD6(img); m.setup(rows(1), playing=playing)
            press(m, 3, mode, playing)
            out.append(m)
        st, sw = out
        diff = [hex(0x20000000 + i) for i, (a, b) in enumerate(zip(st.ram(), sw.ram())) if a != b]
        got = sw.r8(PATTERN + 3) == TAG | 3 and sw.r32(SW + SW_SAVE) == 0x10000 | 0x1C3
        check(got == expect and diff == ([hex(PATTERN + 3)] if expect else []) and sw.ee_writes == st.ee_writes,
              f'FUNCTION + step 4, mode {mode}, {"playing" if playing else "stopped"}: '
              f'{"sets 0xA3 and queues the save" if expect else "nothing changes"}; EEPROM writes as stock')

    for slot in (0x0000, 0x0400, 0x07C0):
        m = RD6(SWING); m.setup(rows(1), slot=slot, playing=False); press(m, 4); m.call(SAVE_FN)
        check(m.ee_writes == [(slot + 3, bytes([TAG | 4]))] and m.r32(SW + SW_SAVE) == 0, f'slot {slot:#06x}: main loop writes 0xA4 to {slot + 3:#06x} once')
    for slot in (0xFFFF, 0x0800, 0x01C1, 0x3080):
        m = RD6(SWING); m.setup(rows(1), playing=False); m.w16(PAT_SLOT, slot); press(m, 4); m.call(SAVE_FN)
        check(not m.ee_writes and m.r8(PATTERN + 3) == TAG | 4, f'slot value {slot:#06x}: RAM swing set, nothing written')

    for length, step, playing, writes in ((16, 5, True, 1), (16, 12, True, 1), (16, 13, True, 0), (16, 15, True, 0),
                                          (5, 0, True, 0), (16, 15, False, 1)):
        m = RD6(SWING); m.setup(rows(1), length=length, playing=False); press(m, 2)
        m.w32(TRANSPORT, int(playing)); m.w32(TRANSPORT + 8, step)
        m.call(SAVE_FN)
        check(len(m.ee_writes) == writes, f'save while {"playing" if playing else "stopped"}, length {length}, step {step + 1}: '
              f'{"written" if writes else "postponed"}')
    m = RD6(SWING); m.setup(rows(1), playing=False); press(m, 2)
    m.w32(TRANSPORT, 1); m.w32(TRANSPORT + 8, 14); m.call(SAVE_FN)
    m.w32(TRANSPORT + 8, 2); m.call(SAVE_FN)
    check(len(m.ee_writes) == 1 and m.eeprom[0x1C3] == TAG | 2, 'postponed save happens on a later pass, once')

    for fw, n in ((1, 2), (99, 3)):
        m = RD6(SWING, fail_writes=fw); m.setup(rows(1), playing=False)
        press(m, 2); m.call(SAVE_FN)
        check(len(m.ee_writes) == n, f'{fw} failed writes: {n} write attempts')
    for fr, n in ((1, 2), (99, 3)):
        m = RD6(SWING); m.setup(rows(1), playing=False)
        press(m, 2); m.call(SAVE_FN)                  # a good save leaves 0xA2 in the read-back buffer
        m.fail_reads = fr; m.ee_writes.clear()
        press(m, 2); m.call(SAVE_FN)                  # same value again, read fails: must not count as success
        check(len(m.ee_writes) == n, f'same level again with {fr} failed reads: {n} write attempts (stale read-back not trusted)')
    out = []
    for img in (STOCK, SWING):                         # the stock save still runs, after a pending swing save
        m = RD6(img); m.setup(rows(1), playing=False)
        if img == SWING: press(m, 5)
        m.w32(SAVEFLAGS + 0x10, 1); m.w32(PAT_SLOT + 4, 1)
        m.call(SAVE_FN)
        out.append(m.ee_writes)
    check(len(out[1]) == len(out[0]) + 1 and out[1][0] == (0x1C3, bytes([TAG | 5])) and
          [(a, d[:3] + d[4:]) for a, d in out[1][1:]] == [(a, d[:3] + d[4:]) for a, d in out[0]],
          'stock pattern save still runs after the swing save, and writes the same apart from byte 3')


def test_blank():
    out = []
    for img in (STOCK, SWING):
        m = RD6(img); m.setup(rows(9), swing=TAG | 4)
        m.call(BLANK, PATTERN)
        out.append(bytes(m.uc.mem_read(PATTERN, 22)))
    check(out[0][3] == TAG | 4 and out[1][3] == 0 and out[0][:3] + out[0][4:] == out[1][:3] + out[1][4:],
          'blank-pattern init also clears the swing byte; everything else as stock')


def test_enqueue():
    ok = True
    for pm in (0, 1):
        res = []
        for img in (STOCK, SWING):
            m = RD6(img); m.w32(0x20000308 + 0x18, 97)
            m.uc.reg_write(UC_ARM_REG_PRIMASK, pm)
            for b in (0x90, 0x24, 0x7F, 0x80, 0x24, 0x00):
                m.call(ENQ, b)
            res.append((m.ram(), m.uc.reg_read(UC_ARM_REG_PRIMASK)))
        ok &= res[0][0] == res[1][0] and res[1][1] == pm
    check(ok, 'MIDI enqueue: queue identical to stock across the wrap; PRIMASK restored from 0 and 1')


# ---- the deferral itself

def test_deferral():
    for level in range(1, 6):
        m = RD6(SWING); m.setup(rows(7), swing=TAG | level)
        m.w32(TRANSPORT + 8, 3); m.call(DISPATCH)                  # sets up the block
        m.w32(TRANSPORT + 8, 4); m.w32(SW + SW_TICKS, 5000); m.call(DISPATCH)
        m.log.clear(); m.enq.clear()
        m.w32(TRANSPORT + 8, 5); m.w32(SW + SW_TICKS, 5810); m.call(DISPATCH)
        d = 810 * K[level] >> 8
        deferred = not m.enq and m.r32(SW + SW_PEND) == d
        fired = None
        for t in range(1, d + 3):
            n = len(m.enq); m.tick14()
            if len(m.enq) > n and fired is None: fired = t
        check(deferred and fired == d, f'level {level}: odd step after a {810}-tick step plays {d} ticks late (fired on tick {fired})')
    for s in (39, 32768):
        m = RD6(SWING); m.setup(rows(7), swing=TAG | 5)
        m.w32(TRANSPORT + 8, 3); m.call(DISPATCH)
        m.w32(TRANSPORT + 8, 4); m.w32(SW + SW_TICKS, 1000); m.call(DISPATCH)
        m.w32(TRANSPORT + 8, 5); m.w32(SW + SW_TICKS, 1000 + s); m.enq.clear(); m.call(DISPATCH)
        check(m.enq and m.r32(SW + SW_PEND) == 0, f'previous step of {s} ticks (outside 40-32767): plays straight')
    for what in ('stop', 'step'):
        m = RD6(SWING); m.setup(rows(3), swing=TAG | 5)
        for k, t in ((1, 0), (2, 100), (3, 910)):
            m.w32(TRANSPORT + 8, k); m.w32(SW + SW_TICKS, t); m.call(DISPATCH)
        pend = m.r32(SW + SW_PEND)
        if what == 'stop': m.w32(TRANSPORT, 0)
        else: m.w32(TRANSPORT + 8, 4)
        m.enq.clear()
        for _ in range(400): m.tick14()
        check(pend and m.r32(SW + SW_PEND) == 0 and not m.enq, f'{what} while a step waits: it is dropped, no notes')
    m = RD6(SWING); m.setup(rows(4), swing=TAG | 5)
    m.uc.mem_write(SW, bytes([0x5A] * 32))
    m.w32(SW + SW_STEP, 0); m.w32(SW + SW_PEND, 3)       # garbage that would fire if trusted
    for _ in range(50): m.tick14()
    quiet = not m.enq
    m.w32(SW + SW_SAVE, 0x10000 | 0x1C3); m.call(SAVE_FN)
    check(quiet and not m.ee_writes, 'random RAM at power-on: no stray step, no stray EEPROM write')


# ---- whole bars

def run_bars(img, rows_, level, s, length, bars, scale=0, swing_via='panel', start_ticks=None, change=None, seed=0):
    m = RD6(img)
    m.setup(rows_, length=length, scale=scale, swing=(TAG | level) if (img == SWING and swing_via == 'byte') else 0)
    if img == SWING and swing_via == 'panel':
        press(m, level, 2, playing=False); m.call(SAVE_FN); m.w32(TRANSPORT, 1)
    if start_ticks is not None:                       # power-on: the block is not set up yet
        m.uc.mem_write(SW, bytes(32)); m.w32(SW + SW_TICKS, start_ticks)
    if change:                                        # a pattern queued for the end of this one
        slot2, level2 = change
        data = bytes([0, scale, length, (TAG | level2) if img == SWING else 0]) + b''.join(r.to_bytes(2, 'little') for r in rows(seed + 100))
        m.eeprom[slot2:slot2 + 22] = data
        idx2 = (slot2 >> 6) & 15
        m.w32(SEL + 0x14, 1); m.w32(SEL + 0x1C, idx2); m.w32(SEL + 0x20, idx2); m.w8(SEL + 0x24, 0)
    m.ee_writes.clear()
    m.call(DISPATCH)                                  # step 0 at Start
    per = s // PPSTEP[scale]
    state = {0: (0, m.r8(PATTERN + 3), m.r8(PATTERN + 1))}
    for _ in range(s * length * bars - 1):
        m.tick14()
        if m.now % per == 0: m.tick13()
        state[m.now] = (m.r32(TRANSPORT + 8), m.r8(PATTERN + 3), m.r8(PATTERN + 1))
    return m, state


def bar_test(level, s=816, length=16, bars=2, scale=0, seed=11, swing_via='panel', start_ticks=None, change=None, clap_overlap=False):
    assert s % PPSTEP[scale] == 0
    r = rows(seed)
    st, _ = run_bars(STOCK, r, level, s, length, bars, scale, swing_via, start_ticks, change, seed)
    sw, state = run_bars(SWING, r, level, s, length, bars, scale, swing_via, start_ticks, change, seed)

    def delay(t):                                     # rd6swing's expected delay for the step playing at tick t
        step, byte3, sc = state[t]
        lvl = byte3 ^ TAG
        return (s * K[lvl] >> 8) if (step % 2 and sc in (0, 1) and 0 < lvl < 6) else 0
    def moved(t, pc):
        d = delay(t)
        if not d: return t
        if inside(pc, DISPATCH_PCS): return t + d - 1   # replay runs at the end of TIMER14 call D; logs stamp calls by their start
        if inside(pc, COUNTDOWN_PCS): return t + d      # first countdown tick is the call after the replay
        return t
    notes_st = collections.Counter((t + (delay(t) - 1 if delay(t) else 0), b) for t, b in st.enq)
    notes_sw = collections.Counter(sw.enq)
    pins_st = collections.Counter((moved(t, pc), k, v, pc) for t, k, v, pc in st.log)
    pins_sw = collections.Counter(sw.log)
    swung = sum(1 for t in state if t % s == 0 and delay(t))
    tag = (f'L{level} S={s} len {length} scale {scale} x{bars}' + (f' via {swing_via}' if swing_via != 'panel' else '')
           + (f' start {start_ticks} ticks after power-on' if start_ticks is not None else '') + (f' then pattern {change}' if change else ''))
    check(notes_st == notes_sw and len(sw.enq) == len(st.enq), f'{tag}: MIDI notes as stock, {swung} odd steps moved')
    if not clap_overlap:
        check(pins_st == pins_sw, f'{tag}: {sum(pins_sw.values())} GPIO writes as stock (dispatch writes moved with the step, the rest unmoved)')
    else:
        miss, extra = pins_st - pins_sw, pins_sw - pins_st
        check(not extra and all(0x08006E00 <= pc < 0x08006E10 and v in (0x80, 0x100) for _, _, v, pc in miss.elements())
              and sum(miss.values()) <= 3 * length * bars // 2,
              f'{tag}: only clap-envelope pulses differ ({sum(miss.values())}), where a late clap overlaps the next')
    check(not sw.ee_writes, f'{tag}: no EEPROM writes during playback')


def test_bars():
    for level in (0, 1, 3, 5):
        bar_test(level)
    bar_test(3, swing_via='byte')
    for length in (15, 13, 7):
        bar_test(5, length=length, bars=3)
    for scale in (1, 2, 3):
        bar_test(4, s={1: 816, 2: 800, 3: 816}[scale], scale=scale)
    bar_test(3, change=(0x200, 5))
    bar_test(0, change=(0x200, 4))
    bar_test(5, length=15, change=(0x240, 0))
    bar_test(1, s=408)
    bar_test(5, s=408, seed=3, clap_overlap=True)
    bar_test(5, s=600, bars=1)
    bar_test(2, s=3000, bars=1)
    for start in (3000, 20000, 40000):
        bar_test(5, start_ticks=start, bars=1)
    for seed in range(12, 18):
        bar_test(1 + seed % 5, seed=seed, bars=1)


# ---- MIDI clock

def midi_run(img, scale=0, length=16, level=5, plan=(('start', 0), ('clocks', 140)), ticks_per_clock=135):
    m = RD6(img)
    m.setup(rows(11), length=length, scale=scale, swing=TAG | level if img == SWING else 0, slot=0, playing=False)
    m.w8(CLK + 0x84, 2)                               # clock source: USB MIDI
    m.w32(CLK + 8, 777777)
    entered = []                                      # (step, delayed?) each time the step index changes
    last = None
    for what, n in plan:
        if what == 'start': m.midi(0xFA)
        elif what == 'stop': m.midi(0xFC)
        elif what == 'continue': m.midi(0xFB)
        else:
            for _ in range(n):
                m.midi(0xF8)
                step, playing = m.r32(TRANSPORT + 8), m.r32(TRANSPORT)
                if playing and step != last:
                    entered.append((step, bool(m.r32(SW + SW_PEND))))
                    last = step
                for _ in range(ticks_per_clock): m.tick14()
    for _ in range(1000): m.tick14()                  # let a step that is still waiting play
    return m, entered


def test_midi_clock():
    plans = {
        'Start, 140 clocks': (('start', 0), ('clocks', 140)),
        'Stop, 19 clocks while stopped, Continue': (('start', 0), ('clocks', 60), ('stop', 0), ('clocks', 19), ('continue', 0), ('clocks', 120)),
    }
    for name, plan in plans.items():
        for length in (16, 15):
            _, entered = midi_run(SWING, length=length, plan=plan)
            odd = [(s, d) for s, d in entered if s % 2]
            even_delayed = [s for s, d in entered if not s % 2 and d]
            check(odd and all(d for _, d in odd[1:]) and not even_delayed,
                  f'MIDI clock, {name}, length {length}: every odd step after the first is delayed ({sum(d for _, d in odd)}/{len(odd)}), no even step')
    _, entered = midi_run(SWING, scale=2, plan=plans['Start, 140 clocks'])
    check(entered and not any(d for _, d in entered), 'MIDI clock, 8th-triplet scale: nothing delayed')
    _, entered = midi_run(SWING, scale=1, plan=plans['Start, 140 clocks'])
    odd = [(s, d) for s, d in entered if s % 2]
    check(odd and all(d for _, d in odd[1:]) and not any(d for s, d in entered if not s % 2),
          f'MIDI clock, 32nd scale: every odd step after the first is delayed ({sum(d for _, d in odd)}/{len(odd)}), no even step')
    for name, plan in plans.items():
        st, _ = midi_run(STOCK, plan=plan); sw, _ = midi_run(SWING, plan=plan)
        check(sorted(b for _, b in st.enq) == sorted(b for _, b in sw.enq), f'MIDI clock, {name}: the same notes as stock (timing aside)')


if __name__ == '__main__':
    test_identity()
    test_setting()
    test_blank()
    test_enqueue()
    test_deferral()
    test_bars()
    test_midi_clock()
    print('\nFAILURES:', failures if failures else 'none')
