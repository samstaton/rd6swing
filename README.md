# rd6swing

Firmware patch that gives swing to the Behringer RD-6. (Unofficial and unsupported!)

Loader (Chrome or Edge): **https://samstaton.github.io/rd6swing/**

As ever, updating firmware can leave a device unusable if it fails, and may affect the warranty, etc.. The loader has been used on my RD-6 (6 November 2026), and also tested against a simulated RD-6, but **use at your own risk**. 

Heavily inspired by tracelistener's <a href="https://github.com/tracelistener/rd6-scatter">Scatter</a>, which is a different firmware mod for the RD-6. I learnt a lot from that. 

## Swing

Hold FUNCTION and press a step button. 

| Step | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Swing | off | 54% | 58% | 62% | 66% | 71% |

The swing setting is stored in the loaded pattern. Patterns in the triplet scales will still play straight. 

https://github.com/user-attachments/assets/a14f1c9a-1a05-4dc8-8fa5-30ad81f0c26c

## Install

Open the loader in a midi compatible browser, e.g. Chrome or Edge: **https://samstaton.github.io/rd6swing/**

You can also run it from a copy of this repository instead. Use `./serve.sh` and open http://127.0.0.1:8771/.

The loader needs Behringer's 1.0.6 update file. You can download the copy kept by the [Scatter](https://github.com/tracelistener/rd6-scatter) project, or use a file you choose. It will checks the SHA-256 (`4446914d…`). It then applies `patches/rd6swing-v5.json` and checks the result (`5468b29a…`) before sending it across. The loader will only work with firmware 1.0.5 or 1.0.6. After the RD-6 restarts, the loader will check that it answers as rd6swing v5.

Add `?sim=1` to the address for a simulated RD-6. 

## Repo structure

### Build and test scripts

```sh
cd firmware
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build.py        # I used clang and Homebrew llvm (ld.lld)
.venv/bin/python test_emu.py     # stock and patched firmware side by side in Unicorn
cd .. && node tests/sim_test.mjs # runs the loader against the simulated RD-6
```

### Other files

- `index.html`, `js/rd6swing.js`: loader.
- `patches/rd6swing-v5.json`: the patch (byte runs on the official update file).
- `tests/fixtures/rd6_protocol.json`: replies recorded from my RD-6.
- `firmware/`: patch source, build and emulator tests.
- `docs/FIRMWARE.md`: details about the patch.


## Licence

MIT ([LICENSE](LICENSE)), for this repository's files. (Behringer's firmware isn't included and is not covered of course.)
