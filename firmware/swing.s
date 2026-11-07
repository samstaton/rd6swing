@ rd6swing v4: a patch on Behringer RD-6 firmware 1.0.6 (decoded image loaded at 0x08002800).
@
@ Pattern header byte 3 holds the swing: 0xA0 | level, level 0-5 = 50 (off), 54, 58, 62, 66, 71 %.
@ Any other value means no swing. Patterns in the 16th and 32nd scales (byte 1 = 0, 1) swing every
@ other step; the triplet scales (2, 3) play straight.
@ FUNCTION + step 1-6 sets the loaded pattern's swing; the main loop then writes that byte to the
@ EEPROM slot the pattern was loaded from, outside the last steps of the pattern.
@
@ Each odd step is played late by (2s - 1) x the measured length of the previous step. Step
@ boundaries stay where they are; the stock dispatcher (triggers, accent, clap, MIDI notes) runs
@ later, from TIMER14.

        .syntax unified
        .thumb
        .cpu cortex-m4

        .equ SW,          0x20003000    @ state block (no stock code uses RAM above 0x20001730)
        .equ SW_MAGIC,    0             @ u32 MAGIC once the block is set up
        .equ SW_TICKS,    4             @ u32 TIMER14 ticks, counted here (the stock counter is reset by MIDI clock)
        .equ SW_LAST,     8             @ u32 SW_TICKS at the previous step
        .equ SW_PEND,     12            @ u32 TIMER14 ticks until the delayed step, 0 = none
        .equ SW_STEP,     16            @ u32 step index of the delayed step
        .equ SW_SAVE,     20            @ u32 0, or 0x10000 | EEPROM address of a swing byte to write
        .equ SW_WBYTE,    24            @ u8  the byte to write
        .equ SW_RBYTE,    25            @ u8  the byte read back
        .equ MAGIC,       0x474E5753    @ "SWNG"
        .equ TAG,         0xA0

        .equ PATTERN,     0x20000A9C    @ loaded pattern: +1 scale, +2 length, +3 swing
        .equ PAT_SLOT,    0x20000288    @ u16 EEPROM address the loaded pattern came from
        .equ MODE,        0x20000260    @ u8 0 track play, 1 track write, 2 pattern play, 3 pattern write
        .equ TRANSPORT,   0x20000BB4    @ +0 playing, +8 step index
        .equ MIDIQ,       0x20000308
        .equ MIDIQ_BUF,   0x200010A7
        .equ SAVEFLAGS,   0x20000200    @ literal of the stock main-loop save routine

        .equ MIN_STEP,    40            @ plausible step lengths, TIMER14 ticks
        .equ MAX_STEP,    32767
        .equ EE_PATTERNS, 0x800         @ 32 pattern slots of 64 bytes

@ ---- hooks: 4 bytes each

        .section .hook_set, "ax"        @ 0x08004CE8 FUNCTION + step fallthrough; was bx lr + padding
        b.w     sw_set
        .section .hook_disp, "ax"       @ 0x08006AE4 step dispatcher entry; was push.w {r4-r11, ip, lr}
        b.w     sw_disp
        .section .hook_t14, "ax"        @ 0x0800760A end of TIMER14 handler; was bl 0x08007A74
        bl      sw_tick
        .section .hook_enq, "ax"        @ 0x08002DD4 MIDI out enqueue; was two literal loads
        b.w     sw_enq
        .section .hook_main, "ax"       @ 0x080072E8 main-loop save routine; was ldr r0, =0x20000200; push {r4, lr}
        b.w     sw_main
        .section .hook_blank, "ax"      @ 0x08005BB8 blank-pattern init; was strb r1, [r0, #1]; strb r2, [r0, #2]
        b.w     sw_blank

@ ---- new code

        .section .text, "ax"
        .align  2

@ FUNCTION + step (main loop). r0 = step index 0..15. The stock key handler reads r4 and r5
@ after this returns.
        .thumb_func
sw_set:
        cmp     r0, #6
        bhs     9f
        ldr     r1, =MODE
        ldrb    r1, [r1]
        cmp     r1, #1                  @ track write, and modes above pattern write: leave alone
        beq     9f
        cmp     r1, #3
        bhi     9f
        orr     r0, r0, #TAG
        ldr     r3, =SW
        mrs     r12, primask            @ slot and pattern byte together: no pattern load in between
        cpsid   i
        ldr     r1, [r3, #SW_MAGIC]
        ldr     r2, =MAGIC
        cmp     r1, r2
        beq     2f
        push    {r0, lr}
        mov     r0, r3
        bl      sw_init                 @ swing set before the first step since power-on
        pop     {r0, lr}
2:      ldr     r1, =PAT_SLOT
        ldrh    r1, [r1]
        ldr     r2, =PATTERN
        strb    r0, [r2, #3]
        cmp     r1, #EE_PATTERNS        @ 0xFFFF: reload pending; the RAM copy is about to be replaced
        bhs     1f
        tst     r1, #63
        bne     1f
        adds    r1, r1, #3
        orr     r1, r1, #0x10000
        strb    r0, [r3, #SW_WBYTE]
        str     r1, [r3, #SW_SAVE]      @ the main loop writes it (sw_main)
1:      msr     primask, r12
9:      bx      lr

@ Main loop, once per pass, before the stock save routine. Writes a pending swing byte unless the
@ pattern is playing near its end, where the stock firmware may load the next pattern from EEPROM
@ inside a timer interrupt.
        .thumb_func
sw_main:
        push    {r4, r5, r6, lr}
        ldr     r6, =SW
        ldr     r0, [r6, #SW_MAGIC]
        ldr     r1, =MAGIC
        cmp     r0, r1
        bne     8f
        ldr     r5, [r6, #SW_SAVE]
        cmp     r5, #0
        beq     8f
        ldr     r0, =TRANSPORT
        ldr     r1, [r0]
        cmp     r1, #0
        beq     2f                      @ stopped: safe
        ldr     r0, [r0, #8]            @ step
        ldr     r1, =PATTERN
        ldrb    r1, [r1, #2]            @ length
        cmp     r1, #6
        blo     8f                      @ short pattern while playing: wait for stop
        subs    r1, r1, #4
        cmp     r0, r1
        bhi     8f                      @ last three steps: try again later
2:      movs    r0, #0
        str     r0, [r6, #SW_SAVE]
        uxth    r5, r5                  @ EEPROM address of the swing byte
        movs    r4, #3                  @ attempts
3:      mov     r0, r5
        add     r1, r6, #SW_WBYTE
        movs    r2, #1
        bl      EE_WRITE                @ (address, source, length), then 5 ms
        ldrb    r0, [r6, #SW_WBYTE]
        mvns    r0, r0
        strb    r0, [r6, #SW_RBYTE]     @ a failed read leaves the buffer as it was
        mov     r0, r5
        add     r1, r6, #SW_RBYTE
        movs    r2, #1
        bl      EE_READ                 @ (address, destination, length)
        ldrb    r0, [r6, #SW_WBYTE]
        ldrb    r1, [r6, #SW_RBYTE]
        cmp     r0, r1
        beq     8f
        subs    r4, r4, #1
        bne     3b
8:      pop     {r4, r5, r6, lr}
        ldr     r0, =SAVEFLAGS          @ the two instructions the hook replaced
        push    {r4, lr}
        b.w     SAVE_BODY

@ Set up the state block. r0 = SW. Clobbers r1, r2. MAGIC goes last: sw_tick and sw_main ignore
@ the block until it is there.
        .thumb_func
sw_init:
        ldr     r1, [r0, #SW_TICKS]
        str     r1, [r0, #SW_LAST]
        movs    r1, #0
        str     r1, [r0, #SW_PEND]
        str     r1, [r0, #SW_STEP]
        str     r1, [r0, #SW_SAVE]
        ldr     r2, =MAGIC
        str     r2, [r0, #SW_MAGIC]
        bx      lr

@ Blank-pattern init: also clear the swing byte. r0 = PATTERN, r1 = 0, r2 = 16.
        .thumb_func
sw_blank:
        strb    r1, [r0, #1]
        strb    r2, [r0, #2]
        strb    r1, [r0, #3]
        b.w     BLANK_REST

@ Dispatcher entry. The dispatcher takes no arguments; r0-r3 and ip are free.
        .thumb_func
sw_disp:
        ldr     r0, =SW
        ldr     r1, [r0, #SW_MAGIC]
        ldr     r2, =MAGIC
        ldr     r3, [r0, #SW_TICKS]     @ now
        cmp     r1, r2
        beq     1f
        push    {lr}                    @ first step since power-on: set up the block
        bl      sw_init
        pop     {lr}                    @ lr is the dispatcher caller's return address
        b       sw_straight
1:      ldr     r1, [r0, #SW_LAST]
        str     r3, [r0, #SW_LAST]
        subs    r2, r3, r1              @ S
        movs    r1, #0
        str     r1, [r0, #SW_PEND]      @ a new step cancels a delayed one still waiting
        ldr     r3, =TRANSPORT
        ldr     r3, [r3, #8]            @ step index
        tst     r3, #1
        beq     sw_straight
        ldr     r1, =PATTERN
        ldrb    ip, [r1, #1]            @ scale: 16ths and 32nds swing, triplets don't
        cmp     ip, #1
        bhi     sw_straight
        ldrb    r1, [r1, #3]
        eor     r1, r1, #TAG
        cmp     r1, #6
        bhs     sw_straight
        cmp     r1, #0
        beq     sw_straight
        cmp     r2, #MIN_STEP
        blo     sw_straight
        movw    ip, #MAX_STEP
        cmp     r2, ip
        bhi     sw_straight
        adr     ip, sw_k
        ldrb    r1, [ip, r1]
        muls    r2, r1, r2
        lsrs    r2, r2, #8              @ D = S * k / 256
        beq     sw_straight
        str     r3, [r0, #SW_STEP]
        str     r2, [r0, #SW_PEND]      @ last: TIMER14 may read it at once
        bx      lr
        .thumb_func
sw_straight:
        push.w  {r4-r11, ip, lr}        @ the instruction the hook replaced
        b.w     DISPATCH_BODY

        .align  2
sw_k:   .byte   0, 20, 41, 61, 82, 108  @ round((2s - 1) * 256) for s = 50 54 58 62 66 71 %
        .align  1

@ End of the TIMER14 handler.
        .thumb_func
sw_tick:
        push    {r4, lr}
        bl      T14_DISPLACED           @ the call the hook replaced
        ldr     r4, =SW
        ldr     r0, [r4, #SW_TICKS]
        adds    r0, r0, #1
        str     r0, [r4, #SW_TICKS]
        ldr     r0, [r4, #SW_MAGIC]
        ldr     r1, =MAGIC
        cmp     r0, r1
        bne     9f
        ldr     r0, [r4, #SW_PEND]
        cmp     r0, #0
        beq     9f
        ldr     r2, =TRANSPORT
        ldr     r1, [r2]                @ stopped: drop it
        cmp     r1, #0
        beq     8f
        ldr     r1, [r2, #8]            @ the step moved on: drop it
        ldr     r3, [r4, #SW_STEP]
        cmp     r1, r3
        bne     8f
        subs    r0, r0, #1
        str     r0, [r4, #SW_PEND]
        bne     9f
        bl      sw_straight             @ the stock dispatcher, now
        b       9f
8:      movs    r0, #0
        str     r0, [r4, #SW_PEND]
9:      pop     {r4, pc}

@ MIDI out enqueue, now also reached from TIMER14: run the stock body with interrupts masked.
        .thumb_func
sw_enq:
        push    {r4, lr}
        mrs     r4, primask
        cpsid   i
        ldr     r2, =MIDIQ
        ldr     r3, =MIDIQ_BUF
        bl      ENQ_BODY
        msr     primask, r4
        pop     {r4, pc}

        .ltorg
