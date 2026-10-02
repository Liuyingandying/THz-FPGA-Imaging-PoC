# Reproducibility

## Environment

- Python 3.11+ with numpy, matplotlib, scipy, pandas
- Icarus Verilog 12.0 (iverilog + vvp)
- Windows: conda Python + WSL (Ubuntu) for iverilog
- Vivado/Vitis 2025.2 (board builds only)

## Simulation

```bash
# Full 19-dataset regression (RTL vs Python golden)
python tools/run_all.py

# 16x32 scene validation (9 datasets, 512ch)
python tools/scene512_validation.py

# Failure boundary study
python tools/v31_failure_boundary.py

# Digital twin demo (3 scenes + figures)
python tools/demo_2x8x32.py build
```

## Board (optional)

1. Build bitstream: vivado batch with fpga_ax7020/scripts/build_2x8x32.tcl
2. Build ARM app: bash fpga_ax7020/sw/build_gcc_2x8x32.sh
3. Download: xsct fpga_ax7020/scripts/run_board_2x8x32.tcl
4. Verify: python tools/board_2x8x32_loop.py --gate 500

## Determinism

All datasets use fixed seeds (see tools/thz_config.py SEED). Identical
inputs produce identical outputs across runs.
