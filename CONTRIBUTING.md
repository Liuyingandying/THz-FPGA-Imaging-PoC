# Contributing

## Core RTL Changes

Any modification to files in `rtl/` must pass:

1. **Legacy regression**: `python tools/run_all.py` (19/19 PASS)
2. **Scene512 regression**: `python tools/scene512_validation.py` (9/9 BIT-EXACT)
3. **Digital twin**: `python tools/demo_2x8x32.py build` (3 scenes BIT-EXACT)

These tests verify that RTL output matches the Python golden reference
bit-for-bit. Breaking any existing test blocks the merge.

## Python Tool Changes

Run the relevant test suite:
- `python tools/test_visualization.py` (11 tests)
- `python tools/test_uart_backend.py` (12 tests)

## Board-Level Changes

Board tests require an AX7020 + JTAG. If you make board-level changes:
1. Run the 500-image gate for all three scenes
2. Attach the `stageA_stats.json` output as evidence

## Style

- SystemVerilog: Verilog-2001 compatible for module-reference top files
- Python: PEP 8, type hints encouraged
- Docs: Markdown, one topic per file

## Submitting

1. Fork, branch, commit
2. Ensure all regressions pass
3. Open a Pull Request with test evidence
