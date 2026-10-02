# Case Study: XUartPs_Send Short-Write UART Frame Truncation

## Symptom

AX7020 board UART stream showed continuous byte flood with zero valid
protocol frames. RTL simulation was bit-exact; PL pipeline confirmed
running via JTAG.

## Root Cause

XUartPs_Send() is non-blocking: fills the 64-byte TX FIFO, returns bytes
accepted. Application sent 72-byte frame in one call without checking
return value. FIFO accepted 65 bytes; remaining 7 silently dropped.

## Fix

Chunked reliable send with return-value checking.

After fix: 1602/1602 frames golden-verified, 0 CRC errors.

## Lessons

1. Non-blocking driver APIs: always check return values
2. "No valid frames" does not equal "no data"
3. A fixed probe beats register scanning for isolating software vs hardware
