# AX7020 Board Demo Validation

## Verified Setup

- Board: ALINX AX7020 (Zynq-7020, xc7z020clg400-2)
- PL: 32ch imaging core (7 modules, zero modification) + row scanner
- PS: ARM Cortex-A9 bare-metal, STRONG_ORDERED MMIO
- Link: UART 115200, 72B frame (A5 5A | frame_id | count | 32xu16 | CRC16)
- PC: Python pyserial + RowAssembler + golden bit-exact comparison

## Results

| Scene | Images | BIT-EXACT | CRC | Row Errors | Key Metric |
|---|---|---|---|---|---|
| TJU pattern | 500 | 500/500 | 0 | 0 | image_id continuous |
| Hidden defect | 500 | 500/500 | 0 | 0 | Defect=YES, IoU 0.9195 |
| Moving target | 500 | 500/500 | 0 | 0 | 4-image loop, no mixing |

## Source-Map Identity

100 images, 512/512 coordinate identity per image, zero mismatches.

## Data Authenticity

Real hardware: AX7020 FPGA execution, ARM readout, UART, PC reconstruction.
Synthetic input: Poisson photocurrent digital twin, not real detector.
