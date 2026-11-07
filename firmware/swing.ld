/* Places the swing patch at real RD-6 1.0.6 addresses. Absolute targets carry the
   Thumb bit so the linker emits bl / b.w rather than interworking blx. */
DISPATCH_BODY = 0x08006AE9;   /* stock dispatcher after its push.w */
T14_DISPLACED = 0x08007A75;   /* TIMER14 handler's last call */
ENQ_BODY      = 0x08002DD9;   /* stock enqueue after its two literal loads */
EE_WRITE      = 0x0800383B;   /* stock EEPROM write (address, source, length) + 5 ms wait */
EE_READ       = 0x0800381D;   /* stock EEPROM read (address, destination, length) */
SAVE_BODY     = 0x080072ED;   /* stock main-loop save routine after its first two instructions */
BLANK_REST    = 0x08005BBD;   /* stock blank-pattern init after the two replaced stores */
SYSEX_5C      = 0x0800852D;   /* stock SysEx handler: command 0x5C */
SYSEX_DONE    = 0x08008579;   /* stock SysEx handler: return */
SYX_TO_USB    = 0x080058B9;   /* stock: SysEx bytes to USB-MIDI packets */
USB_SEND      = 0x08007D29;   /* stock: send USB-MIDI packets */

SECTIONS
{
  .hook_set  0x08004CE8 : { *(.hook_set) }
  .hook_disp 0x08006AE4 : { *(.hook_disp) }
  .hook_t14  0x0800760A : { *(.hook_t14) }
  .hook_enq  0x08002DD4 : { *(.hook_enq) }
  .hook_main 0x080072E8 : { *(.hook_main) }
  .hook_blank 0x08005BB8 : { *(.hook_blank) }
  .hook_sysex 0x08008528 : { *(.hook_sysex) }
  .text      0x0800B380 : { *(.text) }
  /DISCARD/ : { *(.ARM.attributes) *(.comment) }
}
