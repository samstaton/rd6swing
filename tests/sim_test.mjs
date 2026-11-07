// Loader tests against the simulated RD-6: node tests/sim_test.mjs
// Needs firmware/cache/RD-6_V1.0.6.syx (firmware/build.py fetches it).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Rd6, FakeRd6, BrowserPort, Refusal, buildInstall, buildOfficial, readUpdateFile, digest, swingPercent, PORT_NAME } from '../js/rd6swing.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const base = new Uint8Array(readFileSync(join(root, 'firmware/cache/RD-6_V1.0.6.syx')));
const patch = JSON.parse(readFileSync(join(root, 'patches/rd6swing-v4.json'), 'utf8'));
const recorded = JSON.parse(readFileSync(join(root, 'tests/fixtures/rd6_protocol.json'), 'utf8'));
const FAST = 20;   // simulator and loader waits both run 20x faster

let failed = 0;
const check = (ok, what) => { console.log((ok ? 'ok   ' : 'FAIL ') + what); if (!ok) failed++; };
const equal = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const fake = opts => new FakeRd6({ speedUp: FAST, recorded, ...opts });   // answers as recorded on hardware
const link = dev => new Rd6(dev, () => {}, { speedUp: FAST });
async function outcome(fn) { try { return { value: await fn() }; } catch (error) { return { error }; } }

async function update(dev, file, rd6 = link(dev)) {
  await rd6.connect();
  await rd6.toUpdateMode();
  await rd6.send(file);
  return rd6.confirmRestart(file.version);
}

// ---- files
const swing = await buildInstall(base, patch);
const stock = await buildOfficial(base);
check(swing.version === '1.0.6' && swing.frames.length === 383, 'patched file: 383 messages, header version 1.0.6');
for (const [what, bad] of [
  ['one changed byte', (() => { const b = base.slice(); b[5000] ^= 1; return b; })()],
  ['a truncated file', base.subarray(0, 100000)],
]) check((await outcome(() => buildInstall(bad, patch))).error instanceof Refusal, `refuses a base file with ${what}`);
{
  const p = structuredClone(patch); p.runs[0][1] = btoa('xx');
  check((await outcome(() => buildInstall(base, p))).error instanceof Refusal, 'refuses a tampered patch');
  const q = structuredClone(patch); q.base.sha256 = '00'.repeat(32);
  check((await outcome(() => buildInstall(base, q))).error instanceof Refusal, 'refuses a patch that names another base file');
}
check((await outcome(() => readUpdateFile(swing.bytes.subarray(0, swing.bytes.length - 300)))).error instanceof Refusal, 'parser refuses missing messages');

// ---- complete updates
{
  const dev = fake({ version: [1, 0, 5] });
  const r = await outcome(() => update(dev, swing));
  check(r.value === '1.0.6', 'install from 1.0.5: restarts reporting 1.0.6');
  check(dev.got.length === 383 && dev.got.every((m, i) => equal(m, swing.frames[i])), 'install: the 383 messages arrived byte for byte, in order');
  check(dev.gaps.length === 1 && Math.abs(dev.gaps[0] - recorded.ms_from_setup_answer_to_header[0]) < 60, `install: ${Math.round(dev.gaps[0])} ms between the last setup answer and the header (recorded: ${recorded.ms_from_setup_answer_to_header.join(', ')} ms)`);
}
{
  const dev = fake();
  const r = await outcome(() => update(dev, stock));
  check(r.value === '1.0.6' && dev.got.every((m, i) => equal(m, stock.frames[i])), 'restore 1.0.6: all messages byte for byte');
}
{
  const dev = fake(); dev.mode = 'update';
  const r = await outcome(() => update(dev, swing));
  check(r.value === '1.0.6' && dev.got.length === 383, 'starting in update mode: no switch, full update');
}
{
  const dev = fake({ version: [1, 0, 7] });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && /1\.0\.7/.test(r.error.message) && dev.mode === 'app' && !dev.got.length, 'RD-6 reporting 1.0.7: refuses before switching, nothing sent');
}

// ---- failures: stop at once, never retry, report honestly
for (const [what, faults, limit] of [
  ['no answer to the header', { noAnswerAt: 5 }, 6],
  ['no answer mid-firmware', { noAnswerAt: 200 }, 201],
  ['a refusal', { refuseAt: 100 }, 101],
  ['a second answer', { doubleAnswerAt: 50 }, 51],
  ['a refused first setup message', { refuseAt: 0 }, 1],
]) {
  const dev = fake({ faults });
  const rd6 = link(dev);
  const r = await outcome(() => update(dev, swing, rd6));
  check(r.error && dev.got.length <= limit && rd6.partial === (limit > 4),
    `${what}: stops (${r.error?.message}); ${dev.got.length} sent; partial=${rd6.partial}`);
}
{
  const dev = fake({ faults: { ignoreSwitch: true } });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && !dev.got.length, 'RD-6 ignores the switch: stops, nothing sent');
}
{
  const dev = fake({ faults: { noUsbChange: true } });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && !dev.got.length, 'RD-6 stays in its application after the switch: stops, nothing sent');
}
{
  const dev = fake({ faults: { silentAfter: true } });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && /Reload/.test(r.error.message) && dev.got.length === 383, 'no answer after the restart: says so and suggests a reload; nothing resent');
}
{
  const dev = fake({ faults: { restartsInUpdateMode: true } });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && /update mode/.test(r.error.message), 'restarts in update mode: reported as the firmware not starting');
}
{
  const dev = fake({ faults: { reportsVersion: [1, 0, 4] } });
  const r = await outcome(() => update(dev, swing));
  check(r.error instanceof Refusal && /1\.0\.4/.test(r.error.message), 'restart reporting another version: flagged');
}
{
  const dev = fake();
  const rd6 = link(dev);
  await rd6.connect(); await rd6.toUpdateMode();
  const r = await outcome(() => rd6.send(swing, () => {}, () => dev.got.length >= 40));
  check(r.error instanceof Refusal && dev.got.length === 40 && rd6.partial, 'Stop: no further messages after the current one; marked partial');
}
{
  // a failed update, then a retry that fails before any firmware: still reported as partial
  const dev = fake({ faults: { refuseAt: 150 } });
  const rd6 = link(dev);
  await outcome(() => update(dev, swing, rd6));
  dev.faults = { refuseAt: 0 };
  const r = await outcome(async () => { await rd6.connect(); await rd6.toUpdateMode(); await rd6.send(swing); });
  check(r.error && rd6.partial, 'retry after a failed update fails early: still reported as partial');
  dev.faults = {};
  const again = await outcome(async () => { await rd6.connect(); await rd6.toUpdateMode(); await rd6.send(swing); return rd6.confirmRestart('1.0.6'); });
  check(again.value === '1.0.6' && !rd6.partial, 'a clean retry from update mode finishes and clears the partial flag');
}

// ---- pattern backup
{
  const dev = fake();
  dev.patterns[3][3] = 0xA3; dev.patterns[7][3] = 0xFF;
  const rd6 = link(dev); await rd6.connect();
  const b = await rd6.readPatterns();
  check(b.patterns.every((p, i) => p.data === Buffer.from(dev.patterns[i]).toString('hex')) && b.settings.join() === '0,0,1,2,0,96',
    'pattern backup: 32 patterns and 6 settings read back exactly');
  check(b.patterns[3].swing === 62 && b.patterns[7].swing === null && b.patterns[0].swing === null && swingPercent(0xA0) === 50,
    'pattern backup reads swing from header byte 3 (62% on 1-4; none for 00 or FF)');
}
{
  const dev = fake(); dev.mode = 'update';
  const rd6 = link(dev); await rd6.connect();
  const r = await outcome(() => rd6.readPatterns());
  check(r.error && !dev.got.length, 'pattern requests to the bootloader get no answer and are not taken as firmware');
}

// ---- choosing the MIDI ports
const port = (id, name, state = 'connected') => ({ id, name, state, open: async () => {}, send() {} });
const access = (ins, outs) => ({ inputs: new Map(ins.map(p => [p.id, p])), outputs: new Map(outs.map(p => [p.id, p])) });
{
  const b = new BrowserPort(access([port('i1', 'IAC RD-6 sync'), port('i2', PORT_NAME)], [port('o2', PORT_NAME), port('o1', 'IAC RD-6 sync')]));
  const { input, output } = b.pick();
  check(input.id === 'i2' && output.id === 'o2', 'prefers the ports named exactly like the RD-6 over other "RD-6" ports');
}
{
  const b = new BrowserPort(access([port('a', PORT_NAME), port('b', PORT_NAME)], [port('c', PORT_NAME), port('d', PORT_NAME)]));
  check((await outcome(() => b.open())).error instanceof Refusal, 'two RD-6s connected: refuses to pick one');
}
{
  const b = new BrowserPort(access([port('a', PORT_NAME, 'disconnected')], [port('c', PORT_NAME)]));
  check(!b.present() && (await outcome(() => b.open())).error instanceof Refusal, 'a disconnected input is not used');
}

console.log(`\nFAILURES: ${failed || 'none'}`);
process.exit(failed ? 1 : 0);
