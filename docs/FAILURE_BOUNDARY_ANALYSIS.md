# Failure Boundary Analysis

Systematic Monte Carlo study of imaging quality vs key parameters.

## Contrast (Delta-I) Boundary

| Delta-I (nA) | CNR | IoU | Defect Detection |
|---|---|---|---|
| 30 | 37.1 | 0.989 | 100% |
| 10 | 12.5 | 0.989 | 100% |
| 7.5 | 9.5 | 0.991 | 100% |
| 5 | 6.3 | 0.987 | 37.5% (transition) |
| 3.5 | 4.4 | 0.919 | 0% |
| 2.5 | 3.3 | 0.775 | 0% (collapse) |

Boundary: defect detection fails below ~7.5 nA contrast; target
segmentation collapses below ~3 nA.

## Dual-Bank Mismatch

Combined 20% gain drift + 40 LSB offset drift causes IoU to drop to 0.662.
Single-dimension drift is robust.

## AVG_FRAMES

CNR grows ~sqrt(N) but hits FPN floor at N>=8. AVG=4 recommended.

## Reproduce

```bash
python tools/v31_failure_boundary.py
```
