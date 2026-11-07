# rd6swing

Per-pattern swing for the Behringer RD-6 via  a firmware patch. Unofficial; not made or supported by Behringer or Music Tribe.

As ever, updating firmware can leave a device unusable if it fails, and may affect the warranty. The loader has been used on one RD-6 (6 November 2026), and also tested against a simulated RD-6. Use at your own risk. 

Heavily inspired by tracelistener's <a href="https://github.com/tracelistener/rd6-scatter">Scatter</a>, which is a different firmware mod for the RD-6.

## Swing

Hold FUNCTION and press a step button. The swing is stored in the loaded pattern.

| Step | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Swing | off | 54% | 58% | 62% | 66% | 71% |

Every other step plays late by the chosen amount: the off-beat 16ths, or in the 32nd scale the off-beat 32nds. The step clock, Sync Out and MIDI clock are unchanged. Patterns without a stored swing, and patterns in the triplet scales, play straight. Clearing a pattern clears its swing.

## Install

Open the loader in Chrome or Edge: **https://samstaton.github.io/rd6swing/**

Add `?sim=1` to the address for a simulated RD-6. To run the loader from a copy of this repository instead, use `./serve.sh` and open http://127.0.0.1:8771/.

The loader needs Behringer's 1.0.6 update file. It downloads the copy kept by the [Scatter](https://github.com/tracelistener/rd6-scatter) project, or uses a file you choose, and checks its SHA-256 (`4446914d…`). It then applies `patches/rd6swing-v4.json` and checks the result (`f8b01169…`) before sending anything. The loader only replaces firmware 1.0.5 or 1.0.6.

## Build and test

```sh
cd firmware
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build.py        # needs clang and Homebrew llvm (ld.lld)
.venv/bin/python test_emu.py     # stock and patched firmware side by side in Unicorn
cd .. && node tests/sim_test.mjs # the loader against the simulated RD-6
```

`build.py` downloads the official file into `firmware/cache/` (git-ignored) and writes the patch.

## Files

- `index.html`, `js/rd6swing.js`: the loader.
- `patches/rd6swing-v4.json`: the patch, as byte runs on the official update file.
- `tests/fixtures/rd6_protocol.json`: replies recorded from a real RD-6, used by the simulated one.
- `firmware/`: patch source, build and emulator tests.
- `docs/FIRMWARE.md`: how the patch works.


## Licence

MIT ([LICENSE](LICENSE)), for this repository's own files. Behringer's firmware is not included and is not covered.
