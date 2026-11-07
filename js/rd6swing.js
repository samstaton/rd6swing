// rd6swing loader core. No DOM code: the page and the Node tests both import this module.
//
// RD-6 SysEx: every message starts F0 00 20 32 00 01 0D 00. The update sequence below is the one
// recorded in two transfers to a real RD-6 (Nov 2026); the Scatter project's notes describe the same.

export const HEAD = [0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x0D, 0x00];
export const OFFICIAL_106 = {
  size: 116186,
  sha256: '4446914dffce8152dc7f7c0065de52d266f062a6499a22a8fef36b221982cfc3',
};
export const MIRROR_106 = 'https://tracelistener.github.io/rd6-scatter/firmware/RD-6_V1.0.6.syx';
export const SUPPORTED_VERSIONS = ['1.0.5', '1.0.6'];   // what the loader will replace
export const PORT_NAME = 'RHYTHM DESIGNER RD-6';

const OK = [0x01, 0x00, 0x00];
const REFUSED = [0x01, 0x01, 0x00];
const SETUP_ANSWERS = [[0x05, 0x50, 0x30, 0x45, 0x34, 0x47, 0x00], [0x07, 0x52, 0x48, 0x59, 0x54, 0x00], OK, OK, OK];
const FILE_COMMANDS = [0x04, 0x06, 0x08, 0x08, 0x03, 0x0A];
const FIRST_WRITE = 4;            // frame 4 (03 30) precedes a ~0.5 s pause on hardware; treat the
                                  // application as possibly damaged from here until a verified restart
const AFTER_SETUP_MS = 500;       // gap before the header frame, as in both recorded transfers
const FRAME_TIMEOUT_MS = 2000;
const SETTLE_MS = 4;              // time allowed for a duplicate reply to show up

export const sysex = (...body) => Uint8Array.from([...HEAD, ...body, 0xF7]);
export const ASK = {
  version: sysex(0x08),           // application answers 09 00 major minor patch
  updateMode: sysex(0x08, 0x40),  // only the bootloader acknowledges this
  toUpdate: sysex(0x03, 0x30),    // application acknowledges, then re-enumerates as the bootloader
  settings: sysex(0x75),
  pattern: (g, p) => sysex(0x77, g, p),
};

export const toHex = (b, sep = ' ') => [...b].map(x => x.toString(16).padStart(2, '0')).join(sep);
export const digest = async b => toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', b)), '');
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

// Bytes between the RD-6 header and F7, or null for anything else.
export function bodyOf(m) {
  if (!m || m.length < HEAD.length + 2 || m.at(-1) !== 0xF7) return null;
  return HEAD.every((x, i) => m[i] === x) ? [...m.subarray(HEAD.length, m.length - 1)] : null;
}

export class Refusal extends Error {}   // a message meant for the user, as written

const wait = ms => new Promise(r => setTimeout(r, ms));
// Short pauses without setTimeout, which browsers stretch to 1 s in background tabs.
function pause(ms) {
  if (ms >= 50 || typeof MessageChannel === 'undefined' || typeof performance === 'undefined') return wait(ms);
  const until = performance.now() + ms;
  const ch = new MessageChannel();
  return new Promise(done => {
    ch.port1.onmessage = () => (performance.now() >= until ? (ch.port1.close(), done()) : ch.port2.postMessage(0));
    ch.port2.postMessage(0);
  });
}

// ==== update files ====

function messages(bytes) {
  const out = [];
  let start = -1;
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i];
    if (start < 0) {
      if (b !== 0xF0) throw new Refusal(`Not a SysEx file (byte ${i}).`);
      start = i;
    } else if (b === 0xF7) {
      out.push(bytes.subarray(start, i + 1));
      start = -1;
    } else if (b & 0x80) {
      throw new Refusal(`Damaged SysEx (byte ${i}).`);
    }
  }
  if (start >= 0) throw new Refusal('The file ends in the middle of a SysEx message.');
  return out;
}

const septets = (m, at) => { let v = 0; for (let k = 3; k >= 0; k--) v = v * 128 + m[at + k]; return v; };
const packedToBytes = n => 7 * Math.floor(n / 8) + Math.max(0, (n % 8) - 1);

// An RD-6 update file: 5 setup messages, a header, 377 numbered data blocks.
export function readUpdateFile(bytes) {
  const frames = messages(bytes);
  if (frames.length !== 383) throw new Refusal(`An RD-6 update file has 383 messages; this one has ${frames.length}.`);
  if (!frames.every(f => HEAD.every((x, i) => f[i] === x))) throw new Refusal('This file is not addressed to an RD-6.');
  if (!same(frames.slice(0, 6).map(f => f[8]), FILE_COMMANDS)) throw new Refusal('This file does not start like an RD-6 update.');
  let total = 0;
  frames.slice(6).forEach((f, n) => {
    if (f[8] !== 0x0B || f[9] !== 0 || septets(f, 10) !== n) throw new Refusal(`Data block ${n} is missing or out of order.`);
    total += packedToBytes(f.length - 15);
  });
  const header = frames[5];
  if (septets(header, 10) !== total) throw new Refusal('The header and the data blocks disagree about the size.');
  if (String.fromCharCode(...header.subarray(18, 29)) !== 'P0E4G\0RD-6\0') throw new Refusal('The header is not for an RD-6.');
  return { frames, version: [...header.subarray(29, 32)].join('.') };
}

export function patchFile(base, patch) {
  if (patch.format !== 'rd6swing-syx-patch-v1' || base.length !== patch.base.size) throw new Refusal('This patch does not fit this file.');
  const out = base.slice();
  let pos = 0;
  for (const [at, b64] of patch.runs) {
    const run = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    if (!Number.isInteger(at) || at < pos || !run.length || at + run.length > out.length) throw new Refusal('The patch is malformed.');
    out.set(run, at);
    pos = at + run.length;
  }
  return out;
}

async function requireOfficial(base) {
  if (base.length !== OFFICIAL_106.size || await digest(base) !== OFFICIAL_106.sha256) {
    throw new Refusal("This is not Behringer's official RD-6 1.0.6 update file (checksum mismatch).");
  }
}

export async function buildInstall(base, patch) {
  await requireOfficial(base);
  if (patch.base.sha256 !== OFFICIAL_106.sha256) throw new Refusal('The patch is for a different base file.');
  const bytes = patchFile(base, patch);
  if (await digest(bytes) !== patch.target.sha256) throw new Refusal('The patched firmware failed its checksum. Nothing was sent.');
  return { bytes, label: patch.name, ...readUpdateFile(bytes) };
}

export async function buildOfficial(base) {
  await requireOfficial(base);
  return { bytes: base, label: 'Behringer 1.0.6', ...readUpdateFile(base) };
}

// ==== talking to the RD-6 ====

// The link needs a port object with: present() -> bool, open(), close(), send(bytes), and a
// `deliver` callback slot that it fills in; the port calls deliver(message) for incoming SysEx.
export class Rd6 {
  constructor(port, note = () => {}, { speedUp = 1 } = {}) {
    this.port = port;
    this.note = note;
    this.speedUp = speedUp;        // > 1 only in tests against the simulator
    this.inbox = [];
    this.waiting = null;
    this.partial = false;          // update started and not yet confirmed by a clean restart
    port.deliver = m => {
      if (m[0] !== 0xF0) return;
      this.note('rx', { hex: toHex(m) });
      this.inbox.push(m);
      this.waiting?.();
    };
  }

  delay(ms) { return ms / this.speedUp >= 50 ? wait(ms / this.speedUp) : pause(ms / this.speedUp); }

  async connect() { await this.port.open(); this.inbox = []; this.note('ports', this.port.describe?.() ?? {}); }
  disconnect() { this.port.close(); }

  async receive(ms) {
    if (!this.inbox.length) {
      await new Promise(go => {
        const t = setTimeout(() => { this.waiting = null; go(); }, ms / this.speedUp);
        this.waiting = () => { clearTimeout(t); this.waiting = null; go(); };
      });
    }
    return this.inbox.shift() ?? null;
  }

  transmit(bytes) {
    this.note('tx', { hex: bytes.length > 24 ? `${toHex(bytes.subarray(0, 16))} … ${bytes.length} bytes` : toHex(bytes) });
    this.port.send(bytes);
  }

  async ask(bytes, ms = FRAME_TIMEOUT_MS) {
    if (this.inbox.length) throw new Error(`reply nobody asked for: ${toHex(this.inbox[0])}`);
    this.transmit(bytes);
    const r = await this.receive(ms);
    if (!r) throw new Error('no reply');
    return bodyOf(r) ?? [];
  }

  async expectSilence(ms) {
    const r = await this.receive(ms);
    if (r) throw new Error(`unexpected reply: ${toHex(r)}`);
  }

  async askQuietly(bytes, ms) {
    try { return await this.ask(bytes, ms); } catch (e) { if (e.message === 'no reply') return null; throw e; }
  }

  async until(present, ms) {
    const end = Date.now() + ms / this.speedUp;
    while (Date.now() < end) { if (this.port.present() === present) return true; await this.delay(50); }
    return false;
  }

  async flushAfter(ms) { await this.delay(ms); this.inbox = []; }

  // Read-only. { mode: 'app', version } | { mode: 'update' } | { mode: 'silent' }
  async identify() {
    this.inbox = [];
    let r = await this.askQuietly(ASK.version, 1000);
    await this.flushAfter(200);
    if (r?.[0] === 0x09 && r.length === 5) return { mode: 'app', version: r.slice(2).join('.') };
    r = await this.askQuietly(ASK.updateMode, 1000);
    await this.flushAfter(200);
    if (r && same(r, OK)) return { mode: 'update' };
    if (r?.[0] === 0x09 && r.length === 5) return { mode: 'app', version: r.slice(2).join('.') };
    return { mode: 'silent' };
  }

  // Application -> bootloader. Writes nothing.
  async toUpdateMode(progress = () => {}) {
    const now = await this.identify();
    if (now.mode === 'update') { this.note('already_in_update_mode'); return; }
    if (now.mode !== 'app') throw new Refusal("The RD-6 didn't answer. Press STOP, check the cable and connect again.");
    if (!SUPPORTED_VERSIONS.includes(now.version)) throw new Refusal(`The RD-6 reports ${now.version}. This loader only replaces 1.0.5 or 1.0.6, so it stopped. Nothing was written.`);
    progress('Asking the RD-6 to switch to update mode...');
    this.transmit(ASK.toUpdate);
    let acks = 0;
    for (let m; (m = await this.receive(1000));) {
      if (!same(bodyOf(m) ?? [], OK)) throw new Error(`unexpected reply to the switch: ${toHex(m)}`);
      acks++;
    }
    if (!acks) throw new Refusal("The RD-6 didn't accept the switch to update mode. Press STOP and try again. Nothing was written.");
    this.note('switch_acknowledged', { acks });
    if (await this.until(false, 15000)) {
      this.note('usb_gone');
      this.disconnect();
      if (!(await this.until(true, 15000))) throw new Refusal("The RD-6 left USB and hasn't come back. Nothing was written. Reconnect it, reload this page and connect again.");
      this.note('usb_back');
    } else {
      this.note('usb_change_not_seen');
    }
    await this.delay(500);
    await this.connect();
    await this.delay(250);
    const r = await this.askQuietly(ASK.updateMode, 1500);
    await this.flushAfter(100);
    if (!r) throw new Refusal('Lost contact with the RD-6 during the switch. Nothing was written. Reload this page and connect: if it says update mode, carry on from there.');
    if (!same(r, OK)) throw new Refusal("The RD-6 didn't switch to update mode. Press STOP and try again. Nothing was written.");
    this.note('update_mode_confirmed');
  }

  // One message at a time, each answered exactly as on hardware.
  async send(file, progress = () => {}, stopRequested = () => false) {
    this.inbox = [];
    if (!same(await this.ask(ASK.updateMode), OK)) throw new Refusal('The RD-6 is not in update mode. Nothing was written.');
    const { frames } = file;
    for (let i = 0; i < frames.length; i++) {
      if (stopRequested()) throw new Refusal(this.partial ? 'Stopped.' : 'Stopped before anything was written.');
      if (i === FIRST_WRITE) this.partial = true;
      if (i === 5) await this.expectSilence(AFTER_SETUP_MS);
      const want = i < 5 ? SETUP_ANSWERS[i] : OK;
      const r = await this.ask(frames[i]);
      if (same(r, REFUSED)) throw new Error(`the RD-6 refused message ${i}`);
      if (!same(r, want)) throw new Error(`message ${i}: expected ${toHex(want)}, got ${toHex(r)}`);
      await this.delay(SETTLE_MS);
      if (this.inbox.length) throw new Error(`message ${i}: second reply ${toHex(this.inbox[0])}`);
      progress(i + 1, frames.length);
    }
    await this.expectSilence(500);
    this.note('all_acknowledged', { messages: frames.length });
  }

  // The RD-6 restarts after the last block; it must come back reporting the file's version.
  async confirmRestart(version) {
    this.disconnect();
    if (await this.until(false, 6000)) await this.until(true, 20000); else await this.delay(4000);
    await this.delay(800);
    let lastMode = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        try { await this.connect(); } catch { await this.delay(1500); continue; }   // port not back yet
        const id = await this.identify();
        lastMode = id.mode;
        if (id.mode === 'app') {
          if (id.version !== version) throw new Refusal(`After restarting, the RD-6 reports ${id.version}, not ${version}.`);
          this.partial = false;
          this.note('restarted', { version: id.version });
          return id.version;
        }
        if (id.mode === 'update') {
          throw new Refusal('All messages were acknowledged, but the RD-6 restarted in update mode: the new firmware did not start. Keep it connected and run Restore official 1.0.6.');
        }
      } catch (e) { if (e instanceof Refusal) throw e; }
      await this.delay(1500);
    }
    throw new Refusal(`All messages were acknowledged, but the RD-6 hasn't answered since${lastMode ? '' : ' (its USB port has not reappeared)'}. ` +
      'Reload this page and connect; Chrome sometimes keeps a stale connection after the RD-6 restarts.');
  }

  // Read-only: 32 patterns of 22 bytes (as 44 nibbles, high first) and six settings.
  async readPatterns(progress = () => {}) {
    this.inbox = [];
    const patterns = [];
    for (let g = 0; g < 2; g++) {
      for (let p = 0; p < 16; p++) {
        const r = await this.ask(ASK.pattern(g, p));
        if (r.length !== 47 || r[0] !== 0x78 || r[1] !== g || r[2] !== p || r.slice(3).some(x => x > 15)) {
          throw new Error(`pattern ${g + 1}-${p + 1}: unexpected reply ${toHex(r)}`);
        }
        const bytes = Array.from({ length: 22 }, (_, k) => r[3 + 2 * k] * 16 + r[4 + 2 * k]);
        patterns.push({ group: g + 1, pattern: p + 1, data: toHex(bytes, ''), swing: swingPercent(bytes[3]) });
        progress(patterns.length, 33);
      }
    }
    const s = await this.ask(ASK.settings);
    if (s.length !== 7 || s[0] !== 0x76) throw new Error(`settings: unexpected reply ${toHex(s)}`);
    progress(33, 33);
    return { patterns, settings: s.slice(1) };
  }
}

// Header byte 3 of a pattern: 0xA0 + level (0-5) is rd6swing's swing; anything else means none.
export const SWING_STEPS = [50, 54, 58, 62, 66, 71];
export const swingPercent = b => (b >= 0xA0 && b <= 0xA5 ? SWING_STEPS[b - 0xA0] : null);

// ==== Web MIDI ====

export class BrowserPort {
  constructor(access) { this.access = access; this.input = this.output = null; this.deliver = null; }

  pick() {
    const live = map => [...map.values()].filter(p => p.state === 'connected' && /RD-6/.test(p.name ?? ''));
    const choose = (ports, kind) => {
      const exact = ports.filter(p => p.name === PORT_NAME);
      const list = exact.length ? exact : ports;
      if (list.length > 1) throw new Refusal(`More than one RD-6 ${kind} is connected. Connect only the RD-6 you want to update.`);
      return list[0] ?? null;
    };
    return { input: choose(live(this.access.inputs), 'input'), output: choose(live(this.access.outputs), 'output') };
  }

  present() { try { const { input, output } = this.pick(); return !!(input && output); } catch { return true; } }

  async open() {
    this.close();
    const { input, output } = this.pick();
    if (!input || !output) throw new Refusal('No RD-6 found. Connect it by USB, switch it on, and close other music software.');
    await input.open();
    await output.open();
    input.onmidimessage = e => this.deliver?.(e.data);
    Object.assign(this, { input, output });
  }

  close() { if (this.input) this.input.onmidimessage = null; this.input = this.output = null; }
  send(bytes) { if (!this.output) throw new Error('not connected'); this.output.send(bytes); }
  describe() { return { input: this.input && [this.input.id, this.input.name], output: this.output && [this.output.id, this.output.name] }; }
}

// ==== simulated RD-6 (tests, ?sim=1) ====

// Follows the recorded transfers where they say something, and is strict where they don't: the
// bootloader answers only the update commands, in order. `faults` makes it misbehave on purpose:
// noAnswerAt, refuseAt, doubleAnswerAt (message numbers 0-382), ignoreSwitch, noUsbChange,
// silentAfter (never restarts), restartsInUpdateMode, reportsVersion (after the restart).
// `recorded` is tests/fixtures/rd6_protocol.json: when given, the fake answers with the bytes a real
// RD-6 sent rather than with this module's own expectations.
export class FakeRd6 {
  constructor({ version = [1, 0, 6], faults = {}, speedUp = 1, recorded = null } = {}) {
    Object.assign(this, { version, faults, speedUp, mode: 'app', plugged: true, opened: false, deliver: null });
    const rec = recorded?.answers;
    const bytes = h => h.split(' ').map(x => parseInt(x, 16));
    this.setupAnswers = rec ? ['update:04', 'update:06', 'update:08 40', 'update:08 00', 'update:03 30'].map(k => bytes(rec[k][0])) : SETUP_ANSWERS;
    this.ok = rec ? bytes(rec['update:0b'][0]) : OK;
    this.switchAnswers = rec ? rec['app:03 30'][0].split(' | ').map(bytes) : [OK, OK];
    this.versionPrefix = rec ? bytes(rec['app:08'][0]).slice(0, 2) : [0x09, 0x00];
    this.got = [];           // update-file messages received, in order
    this.next = 0;           // which update-file message the bootloader expects
    this.gaps = [];          // ms between the answer to message 4 and the arrival of message 5
    this.patterns = Array.from({ length: 32 }, (_, i) => { const d = Array(22).fill(0); d[2] = 16; d[4] = i; return d; });
    this.settings = [0, 0, 1, 2, 0, 96];
  }
  present() { return this.plugged; }
  async open() { if (!this.plugged) throw new Refusal('No RD-6 found.'); this.opened = true; }
  close() { this.opened = false; }
  describe() { return { input: ['sim-in', PORT_NAME], output: ['sim-out', PORT_NAME] }; }
  after(ms, fn) { setTimeout(fn, ms / this.speedUp); }
  answer(body, ms = 2) { this.after(ms, () => this.opened && this.plugged && this.deliver?.(sysex(...body))); }
  replug(mode, at, gap = 250) {
    this.after(at, () => { this.plugged = false; this.opened = false; });
    this.after(at + gap, () => { this.mode = mode; this.plugged = true; });
  }

  send(bytes) {
    if (!this.opened || !this.plugged) return;
    const b = bodyOf(bytes);
    if (!b) return;
    return this.mode === 'app' ? this.application(b) : this.bootloader(b, bytes);
  }

  application([c, a, ...rest]) {
    if (c === 0x08) return this.answer([...this.versionPrefix, ...this.version]);
    if (c === 0x03 && a === 0x30 && !rest.length) {
      if (this.faults.ignoreSwitch) return;
      this.switchAnswers.forEach((ans, k) => this.answer(ans, 2 + k * 0.1));
      if (!this.faults.noUsbChange) this.replug('update', 2700);
      return;
    }
    if (c === 0x77) return this.answer([0x78, a, rest[0], ...this.patterns[a * 16 + rest[0]].flatMap(x => [x >> 4, x & 15])]);
    if (c === 0x75) return this.answer([0x76, ...this.settings]);
  }

  bootloader(b, raw) {
    const n = this.next;
    const [c, a] = b;
    if (c === 0x08 && a === 0x40 && b.length === 2 && n !== 2) return this.answer(this.setupAnswers[2]);   // the loader's probe
    const expected = n < 6 ? FILE_COMMANDS[n] : 0x0B;
    if (c === 0x04 && b.length === 1 && n !== 0) { this.next = 0; return this.bootloader(b, raw); }   // a fresh start
    if (c !== expected) return;                                                                         // anything else: ignored
    if (n === 5 && this.answeredFour) this.gaps.push((performance.now() - this.answeredFour) * this.speedUp);
    this.got.push(Uint8Array.from(raw));
    this.next++;
    const f = this.faults;
    if (f.noAnswerAt === n) return;
    if (f.refuseAt === n) return this.answer(REFUSED);
    const ms = n === 5 ? 45 : n >= 6 && n % 4 === 2 ? 40 : 5;
    this.answer(n < 5 ? this.setupAnswers[n] : this.ok, n < 5 ? 1 : ms);
    if (n === 4) this.after(1, () => { this.answeredFour = performance.now(); });
    if (f.doubleAnswerAt === n) this.answer(this.ok, ms + 1);
    if (n === 382) {
      const v = [...this.got[5].subarray(29, 32)];
      this.after(200, () => { this.plugged = false; this.opened = false; });
      if (f.silentAfter) return;
      this.after(700, () => {
        this.plugged = true; this.next = 0;
        if (f.restartsInUpdateMode) return;
        this.mode = 'app'; this.version = f.reportsVersion ?? v;
      });
    }
  }
}
