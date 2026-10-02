# Case Study: Zynq AXI MMIO Memory-Attribute Ordering Bug

## Symptom

42 wrong pixels per 16x32 image, consistently in first 3 columns of each
row (reading previous row's window). RTL simulation bit-exact. Protocol
layer zero errors.

## Root Cause

PL AXI register window mapped as NORM_NONCACHE. CPU write-ROWSEL then
read-PIXEL sequence lacked strict MMIO ordering. First reads executed
before ROWSEL write took effect.

## Decisive A/B

Same bitstream, same hardware, only MMU attribute varied:
- NORM_NONCACHE: 20/20 FAIL
- STRONG_ORDERED: 20/20 BIT-EXACT

## Fix

Xil_SetTlbAttributes(base, STRONG_ORDERED). After: 500/500 bit-exact.

## Source-Map Confirmation

Invertible 512-unique-code pattern. Before fix: first-3-columns
permutation visible. After: 100 images x 512/512 identity, zero mismatches.

## Lessons

1. AXI-Lite MMIO must use strongly-ordered memory attributes
2. Do not mask ordering bugs with sleep/delay
3. "Sim passes but board fails" can be software memory-model issue
