# Kurzweil 1000 GX firmware notes (first pass, 2026-11-09)

Dumps: `../dumps/` (two matching reads each). Joined, HI = even byte: `../pro_pgm_v22_joined.bin`, `../gx_setup_v25_joined.bin`.
Disassembler: `kdis.py` -> `pro_pgm.lst` (recursive, ~54 KB of 128 KB covered). `sweep.py` -> `test_sweep.lst` (linear, test code from 0x12342, unreliable where data).

## Chips
- U21/U32 PRO PGM HI/LO V2.2, U22/U33 GX SETUP HI/LO V2.5: TMM27512AD-20. T48: `AM27512@DIP28 -y -r` (ID 0x9815 mismatch expected).
- CPU MC68000P10. Arnold 0/1 = U16/U29 custom (VC2708-0001, KMS66000101A). AMELIA U008 = probably PAL (unconfirmed).
- Sound ROM U50-U61: firmware names X0/Y0/Z0 .. X3/Y3/Z3 (4 banks of 3).
- NVRAM TC55257 x2; empty VRAM footprints U20/U31 next to them (purpose unconfirmed).

## Memory map (from code)
| Addr | What | Evidence |
|---|---|---|
| 0x000000 | PGM image, 128 KB | vectors, SSP 0x8003F0, reset 0x404 |
| 0x008FFC | "EROM" object tag | boot check 0x4B2 |
| 0x020000 | SETUP image ("SROM") | boot check 0x4DA |
| 0x600001/3 | LCD data / command (HD44780-style init 38/06/0C/01) | 0x10B24 |
| 0x680001 | output latch | boot |
| 0x700001 | front panel buttons, active low | 0xADC |
| 0x780001 | written 0x03 at init, unknown | 0x5A0 |
| 0x800000 | RAM, stack top 0x8003F0 | |
| 0x808000-0x80DFFF | user RAM, 24 KB, cleared by hard reset | 0xB16 |
| 0x880000 | read at reset (tst.w), unknown | 0x404 |

## Boot (0xADC), buttons at 0x700001
- 0xFF: normal boot.
- 0x3F held (bits 6,7 pressed = VALUE YES+NO per manual): clear user RAM = Hard Reset.
- 0xCF held (bits 4,5 pressed): jump to 0x10B0A = TEST SUPERVISOR. Probably both PROGRAM buttons (assumes bit order PLAY, MODE, CHANNEL x2, PROGRAM x2, VALUE x2). Not tried on hardware.
- 0x5602 finds objects inside a ROM by type word; 0x4B2 checks OS and SETUP version fields against each other (+8 / +0xA), errors "SVMM" on mismatch. Matters when pairing a SETUP with an OS.

## Test Supervisor menus
Main: RUN ALL TESTS, RUN SELF TESTS, RUN LCD TEST, RUN BUTTON TEST, RUN MIDI TEST, RUN SINE WAVE, RUN BATTERY TST, TEST SEL MENU, CONFIGURE MENU, ERROR STATS, EXIT SUPERVISOR.
Select: LCD, BUTTON, PROGRAM ROM, SETUP ROM, RAM, BATTERY BAK, TIMER, UART, ARNOLD 1, ARNOLD 0, SOUND ROM, ONE SAMPLE, MIDI LOOP, SINE WAVE, SEVEN SAMP, LCD SCOPE.
Configure: SIG ANALYSIS, LOOP ON ERROR, DISP ERR TEXT/DATA, DISP BAD BITS, SCOPE LOOP, ERR MESS FAST/SLOW/HOLD.
Menu keys read at 0x1101C: 0x04/0x08 and 0x80/0x40.
Test code also searches SETUP (0x20000-0x40000) for object 0x420D "DIAGOB".

## Open
- ROM checksum routine not found yet; not a plain byte sum. "SIG ANALYSIS" hints at a signature (LFSR/CRC). Fastest proof of intact ROMs: run the Test Supervisor's PROGRAM ROM / SETUP ROM tests.
- Sound ROM format, Arnold register interface.

## Wave ROMs (service manual p.8-20, scan is poor; + firmware strings)
- 12 sockets, 8 bits each, schematic note "VCC:32 GND:16", labelled "1-8 MEG ROM" (per k1000.net; the scan is hard to read). CE/OE on pins 22/24 = JEDEC 32-pin layout, same as 27C010/040. NOT SST39SF040: its pin 1 = A18, pin 31 = WE#, so SST needs a remap adapter. W27C040 is the drop-in 512K part. Site: all-27C040 = JP7 open, JP5 open reported working.
- Banks (firmware): X0 U58, Y0 U50, Z0 U54 | X1 U59, Y1 U51, Z1 U55 | X2 U60, Y2 U52, Z2 U56 | X3 U61, Y3 U53, Z3 U57.
- Each bank has its own select (SFROM0-3 on the schematic). JP5-JP7 + an etch link route ARN ADDR/+5V to upper pins (ROM size option; exact settings not legible).
- Data: three 74LS257 (U41, U45, U42), 12 outputs to Arnold's data bus (label looks like ARLD 15:4). INFERENCE: 24 bits across X/Y/Z hold two 12-bit words, muxed. Must be confirmed with a dump. How that squares with "16-bit floating-point" is open.
- Needed to crack the format: a dump of one full bank (U58, U50, U54), plus the part number for size.

## SETUP container (OS 0x5602, 0x6374, 0x64A6)
- Starts "SROM", block size at +0x20 as a negative long (GX: 0xFFFE9CBC = 0x16344 bytes... block ends 0x16364), objects tagged by first byte 0x40-0x58. Object 0x420D = "DIAGOB", 0x420E/F looked up at boot.
- Object length: tag 0x40 long at +4; 0x52/53/56/57 = 8 bytes; 0x55 = 16; others word at +2, rounded up to even.

## Second pass (from test code, see test_sweep.lst)
**ROM checksums.** Per chip: 16-bit sum of bytes [0..N-3], stored big-endian in last 2 bytes (test 0x12DA4, N tried 0x8000 then 0x10000). `ck.py`: GX SETUP U22 = 169F OK, U33 = 8C4A OK, both match. PGM U21/U32 do not use this layout; PGM check not located, so PGM is NOT proven.
User RAM test sums 0x808000 same way, 0x3000 per lane. RAM test range is 0x800000-0x80FFFE only (64 KB), so nothing there uses the empty U20/U31 footprints.

**Buttons (bit order, table at 0x12C28):** PLAY/EDIT, MODE/LAYER, CHAN/MENU UP, CHAN/MENU DOWN, PGM/PARAM UP, PGM/PARAM DOWN, DEC/NO, INC/YES. So 0xCF = both PGM buttons = Test Supervisor (still untried on hw).
23-button panel names: ACOUSTIC BASS, VIBES, CHOIR, STRINGS, PIANO, VIBRATO, TREMOLO, CHORUS, WAVESHAPES, ORGAN, CLARINET, BARITONE HORN, TRUMPET, BANK A/B/C, STORE, ENTER.

**Other hardware.** 0x500001.. timer (silkscreen TIMER U48, 0x50000D/F control). 0x580001 and 0x780001 take 0x03 at init = probably 6850-type UART resets.

**Arnold.** Arnold 0 at 0x400000, Arnold 1 at 0x480000. Regions +0x000 (voice regs), +0x180, +0x200 (write 7), +0x400 (mode: 6/2 on Arnold 0, E/A on Arnold 1).
Voice words seen in sound ROM test: +0x02 = (addr bits 23:16 << 8) | flags, +0x0A = addr 15:0, +0x0C, +0x0E, +0x10, +0x12; read back through +0x18..+0x1E.

**Wave data layout, from the sound ROM test at 0x13566.** Each read word is 12 bits in bits 15:4. Test adds high byte of word 0 to X sum, high byte of word 1 to Y sum, bits 7:4 of both to Z sum. So 3 bytes X/Y/Z = 2 words: word0 = X<<4 | one Z nibble, word1 = Y<<4 | other Z nibble (which nibble goes where still open). This confirms the 74LS257 inference. Nonzero words get +0x10 before summing (read offset quirk, unexplained). Address steps by 8 for 4 words = 2 per word (ARN ADDR 23:1).
SETUP holds an "SROM" object with start/end regions and expected sums for this test.
Still unknown: whether the 12 bits are linear or a floating-point encoding, sample rate.

## From k1000.net chipdata.htm (user group, includes a Kurzweil engineer's account)
- Factory sound ROMs are all 512K x 8 (27C040 class). Stock straps: JP7 open (Arnold's 1 MB address line NOT connected), JP5 open (pin 1 floating), JP11 closed + JP9 open (512K line used).
- 27C080 upgrades hold the same 512K twice; with JP7 open they add NO capacity. Practical max = 12 x 512K = 6 MB. Going past that means closing JP7, untested by us and not documented as working.
- 27C040/W27C040 is the right part. 27C080 needs JP5 closed (A19 to +5 V).
- Group's bit layout agrees with firmware: U50 8 bits + U54 4 bits = one word; U58 8 bits + other 4 bits of U54 = the other.
- Adding sound ROMs requires matching OS/SETUP or the unit won't see them. OS chip pairs should be the same speed.
- DAC filters: PX rolled off at 10.25 kHz, most other expanders at 12.5 kHz.
- Site terms: ROMs from its data are personal use only, no selling.

## Wave format, cracked from k1000.net GX data (dec.py)
One bank = X U58, Y U50, Z U54 (512K each). Each address gives two consecutive samples: word0 = X<<4 | (Z & 0xF), word1 = Y<<4 | (Z >> 4).
Words are 12-bit SIGN-MAGNITUDE: bit 11 = sign, bits 10:0 = magnitude. Decoded data forms clean waveforms; two's complement doesn't. Low-nibble-to-X fits slightly better than the reverse (2nd-difference 96 vs 102), so nibble order is likely but not certain.
So 1 bank = 1,048,576 samples. WAV renders in ../audition at an assumed 25 kHz (real rate unknown).
No exponent in the word; "16-bit floating point" must be gain applied elsewhere, unresolved.
Zips: GX set is OS V5.10, differs from the user's V2.2/V2.5 chips; no zip file matches any of our four dumps, so U21/U32 remain unverified.

## SETUP objects, cracked (sp.py, sf.py, sp5.py)
Chain: Program 0x50xx -> layer 0x51 (first data byte = keymap number) -> Keymap 0x46xx (+0x10 = soundfile number, +0x20 name, then key table) -> Soundfile 0x45xx -> 32-byte sample entries.
Other tags: 0x44xx 256-point LFO/waves (Sine, Saw...), 0x47xx program maps, 0x4Bxx tunings, 0x4Dxx velocity curves, 0x42xx tables. 95 programs, 101 keymaps, 18 soundfiles in the GX.
Soundfile: +6 count, +8 header length, name at +0x10, entries at 8+header length. Count is not always the full row count (0x4504 has count 1 and 2 rows).
Entry (32 bytes): root key, 0, int16 unknown (tune/gain?), pitch in cents (root*100 for a sample at base rate), 0, start, alt start, loop, end (u32 each), m1, m2 (unknown, copied), 0, 0.
Addresses are 2 per sample. Banks: 0x800000 (U58/U50/U54), 0xA00000 (U59/U51/U55), 0xC00000 (U60/U52/U56), 0xE00000 (U61/U53/U57). Pro 1 SETUP uses all four (checked).
Proof: factory "Sine Wave" entries decode to exact single sine cycles (824, 347, 146, 61, 26, 11 samples). Their periods and pitch values give a base rate of about 30.2 kHz, assuming 6900 cents = A440.

## k1000_wave.py
WAV -> U60/U52/U56 bank 2 images, and U22/U33 SETUP with soundfile 0x4504 entry 0 ("Noises") repointed, lane checksums fixed. Asserts round trip, checksum, and that only the entry changed.
../build_test holds a generated test tone. NOT tried on hardware. Patch to audition: Choppers (uses Low Noise + Noise 1/4 Tr). Also changes Surf/Seagulls, Log Drums.
Open risks: OS may gate bank 2 somewhere not found yet; int16 field set to 0; base rate approximate.

## Adding new patches (2026-11-10, firmware read + k1000engine.py)
OS object index is built at boot by walking SETUP blocks (0x565C, 0x5706), per tag and ID range, so appended objects with new IDs get indexed. Block chain: +0x20 long = -(length), next long 0 ends it.
Keep program IDs 160-192: map 0x4702 "Presets" sends MIDI program 95-127 there and the GX has none. IDs 64-127 look reserved for RAM programs (unconfirmed).
Keymap: +4 low key, +6 key count, +8 header length, +0x0C base cents, +0x0E cents per key (0 = fixed pitch, 100 = normal), +0x10 soundfile, name +0x20, table is 1-based entry numbers.
Noise keymaps are fixed pitch, so the old Choppers test in k1000_wave.py would not have tracked the keyboard. Superseded.
Entry m1/m2 are self-relative pointers to tables after the rows (probably natural envelope). Cloning 0x4504 keeps them valid and flat.
Layer data: byte 0 keymap, 1 low key, 2 high key, byte 6 looks like level trim (Tuning Note = -10).
Templates: program 0x509F, keymap 0x4679, soundfile 0x4504. Entry pitch = root*100 + 1200*log2(rate divider).
k1000studio.pyw = GUI. NOT tried on hardware. Open: panel stepping past 159, boot heap for the larger index, a Hard Reset may be needed after the swap.
