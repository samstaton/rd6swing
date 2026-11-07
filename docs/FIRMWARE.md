# rd6swing firmware patch

rd6swing v4 is Behringer RD-6 firmware 1.0.6 with a patch. It reports version 1.0.6. `firmware/build.py` produces it (SysEx SHA-256 `f8b01169…`) and the loader's patch file. (v3 left the 32nd scale straight.)

## Behaviour

FUNCTION + step 1–6 sets the swing of the loaded pattern to 50 (off), 54, 58, 62, 66 or 71%. It works in Pattern Play, Track Play and, while stopped, Pattern Write: the modes in which the stock firmware routes FUNCTION + step to 0x08004CC8 with a pattern loaded.

Each odd (off-beat) step of a pattern in the 16th or 32nd scale (header byte 1 = 0 or 1) is played (2s − 1) × S late, where S is the measured length of the previous step: swing of the step grid, as on Elektron sequencers. Patterns in the two triplet scales play straight. The step clock, step lights, Sync Out and MIDI clock are not delayed.

## Storage

The swing is stored in byte 3 of the 22-byte pattern as `0xA0 + level`, level 0–5. Any other value means no swing.

- **Stock routines:**
  - The pattern load (0x08003630), save (0x080037C0) and SysEx pattern routines move all 22 bytes.
  - The post-load check (0x08005B70) changes only bytes 1 and 2.
  - No other stock routine was found that reads or writes byte 3 on its own.
- **Clearing:** the blank-pattern initialization (0x08005BA0), which clearing a pattern uses, now also sets byte 3 to 0.
- **Setting:** FUNCTION + step sets byte 3 of the RAM pattern and records the EEPROM address (u16 at 0x20000288) + 3, both with interrupts masked, so a pattern load cannot come between them.
- **Writing:** the main loop then writes the byte with the stock routines (write 0x0800383A, read 0x0800381C), reads it back into a buffer that was first set to a different value, and retries up to three times.
  - The write waits while the pattern is playing in its last three steps, or while a pattern shorter than six steps is playing. At those times the stock firmware may load the next pattern from EEPROM inside TIMER13, and the I2C routines have no lock.
  - No write is recorded if 0x20000288 does not hold a pattern-slot address.

The stock save path (dirty flag 0x2000028C) is not used, because it writes to the slot of the selected pattern (0x08005CD0), which can differ from the loaded one while a pattern change is queued.

## Image and container

The decoded image loads at 0x08002800, above a 10 KB bootloader. The update file's data messages carry the image XOR a repeating 128-byte mask, 7-bit packed. The header holds the image size and byte sum. `firmware/rd6syx.py` decodes; `firmware/build.py` re-encodes, and reproduces the official 1.0.6 file byte for byte from its decoded image.

## Hooks

Six 4-byte sites. `build.py` asserts the stock bytes at each before patching.

| Site | Stock | Patched |
|---|---|---|
| 0x08004CE8, FUNCTION + step fallthrough | `bx lr` and 2 bytes of padding | `b.w sw_set` |
| 0x08006AE4, step dispatcher entry | `push.w {r4-r11, ip, lr}` | `b.w sw_disp` |
| 0x0800760A, end of the TIMER14 handler | `bl 0x08007A74` | `bl sw_tick` |
| 0x08002DD4, MIDI out enqueue | two 16-bit literal loads | `b.w sw_enq` |
| 0x080072E8, main-loop save routine | `ldr r0, =0x20000200; push {r4, lr}` | `b.w sw_main` |
| 0x08005BB8, blank-pattern initialization | `strb r1, [r0, #1]; strb r2, [r0, #2]` | `b.w sw_blank` |

- **`sw_tick`** counts TIMER14 ticks in its own counter, because the stock counter at 0x2000110C + 8 is reset by the MIDI clock routine. When a delayed step's countdown ends, it runs the stock dispatcher. It drops the step if the transport has stopped or the step index has changed.
- **`sw_disp`** measures S on that counter. On an odd step of a swung pattern it stores D = S·k/256 and returns.
- **`sw_enq`** runs the stock enqueue with interrupts masked, because notes are now also queued from TIMER14. Scatter v1.4 takes the same approach at the same site.

The state block lives at 0x20003000 behind a magic word. It is set up by the first step or the first swing setting after power-on, with the previous-step time set to the current tick. No stock code references RAM above the initial stack pointer, 0x20001730. The new code is 456 bytes at 0x0800B380, in the blank region the loader recovers the mask from; 149 of its 153 blocks remain.

## Checks

`firmware/test_emu.py` runs both images in Unicorn and calls the real handlers.

- **Emulator setup:**
  - GPIO set and clear writes update the pin state that code reads back, and are logged with the writing address.
  - The EEPROM is simulated, with injectable write and read failures. A failed read leaves the buffer untouched, as the real routine does.
  - TIMER13 runs at the stock 96 ppqn.
- **Identity:** with swing bytes 00, FF, A0, A6, A7, B3 and 23, with 00 in scale 1, and with A5 in the triplet scales 2 and 3, the pins, MIDI bytes and RAM are identical to stock over three steps.
- **Setting through the real key handler:**
  - In modes 0, 2 and 3 (stopped), FUNCTION + step 4 sets 0xA3 and records the save, and RAM otherwise matches stock.
  - In modes 1, 3 (playing) and 4, nothing changes.
  - EEPROM writes match stock in every case.
- **Saving:**
  - The byte is written at slot + 3 for slots 0, 0x400 and 0x7C0, and nothing is written for invalid slot values.
  - While playing, the write waits for steps outside the last three, or for stop with short patterns.
  - Failed writes are retried. A failed read is not taken as success, even when the stale buffer holds the same value.
  - The stock pattern save still runs afterwards and writes the same bytes, apart from byte 3.
- **Clearing:** the blank-pattern initialization clears byte 3 and is otherwise as stock.
- **Enqueue:** the queue is identical to stock across the ring wrap, and PRIMASK is restored.
- **Deferral:**
  - At each level, an odd step is played D ticks late.
  - Previous steps under 40 or over 32767 ticks play straight.
  - A stop or a step change drops a waiting step.
  - Random RAM at power-on causes no stray step and no EEPROM write.
- **Whole bars:** one to three bars, through pattern wraps, with lengths 16, 15, 13 and 7 and scales 0–3. They include a queued change to a pattern with different swing, Start 3000, 20000 or 40000 ticks after power-on, step lengths of 408–3000 ticks, swing set from the panel and from the pattern byte, and random patterns.
  - MIDI notes equal stock's, with each odd step of a swung pattern in scale 0 or 1 D later.
  - The dispatch's GPIO writes move with it and the trigger countdown's follow; all other writes are unchanged.
  - No EEPROM writes happen during playback.
- **MIDI clock:** the stock MIDI realtime handler is driven with Start, clock, Stop and Continue.
  - Every odd step is delayed, including after Stop, 19 clocks while stopped, then Continue, and with length 15.
  - In the 32nd scale every odd step is delayed; in the 8th-triplet scale, nothing is.
  - The notes are the same as stock.

Calls run one at a time, so interrupt preemption is not modelled. Sync In (TIMER2) and Track mode are not exercised.

## Known limits

- **Timer rates are not measured.** TIMER14's period register is 16799. That gives about 6.4 kHz at a 108 MHz timer clock, or 10 kHz at 168 MHz.
- **Late steps can be dropped.** A delayed step is dropped if the next step arrives first (a jump in an external clock, the Sync In catch-up loop) or if the transport stops.
- **Trigger countdown start.** After a delayed dispatch, the countdown starts about one TIMER14 tick later. After a TIMER13 dispatch, the gap varies between 0 and 1 tick.
- **Clap envelope.** The clap's envelope lasts 340 TIMER14 ticks. When (1 − D/S) × S is under 340 ticks, a clap on the step after an off-beat clap loses one envelope pulse. Stock firmware does the same when S itself is under 340 ticks.
- **Claps busy-wait.** The clap path busy-waits about 200 µs when it is accented or when PB2 reads high. Inside TIMER14 this can delay the next tick.
- **Saving the swing** blocks the main loop for about 5 ms per attempt, up to three attempts, plus up to 5 ms of read retries each.
- **Short patterns.** A swing set while a pattern of fewer than six steps is playing is saved at stop. It is lost if the RD-6 is switched off first.
- **No level indication.** The firmware gives no light or other feedback for the current swing level.
