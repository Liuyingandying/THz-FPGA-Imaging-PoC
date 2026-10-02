#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_rtl_reference.py — RTL 仿真输出 vs Python 黄金参考 逐像素对比

读取:
  results/reference/<name>_ref.csv   (参考: groups x 32, 行主序)
  results/rtl/<name>_out.txt         (RTL: 每行 "row col value")
对比:逐像素严格相等(定点链路本应位级一致,容差默认 0)。
输出:results/verification_summary.csv + 终端表格。
RTL 输出缺失 → NOT RUN(不造假 PASS)。
"""

import csv
import sys
import numpy as np

import thz_config as C


def load_reference(name):
    p = C.REF_DIR / f"{name}_ref.csv"
    if not p.exists():
        return None
    ref = np.genfromtxt(p, delimiter=",", skip_header=1, dtype=np.int64)
    return ref.reshape(-1, C.NUM_CHANNELS)


def load_rtl(name):
    p = C.RTL_OUT_DIR / f"{name}_out.txt"
    if not p.exists():
        return None
    rows = []
    order_ok = True
    for ln, line in enumerate(p.read_text().splitlines()):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            return None
        r, c, v = (int(x) for x in parts)
        if r != (ln // C.COLS) or c != (ln % C.COLS):
            order_ok = False
        rows.append(v)
    if not rows:
        return None
    return np.array(rows, dtype=np.int64), order_ok


def compare_one(name, tol=0):
    ref = load_reference(name)
    rtl = load_rtl(name)
    if ref is None:
        return {"dataset": name, "result": "NO_REFERENCE", "max_err": "",
                "mean_err": "", "failed_pixels": ""}
    if rtl is None:
        return {"dataset": name, "result": "NOT_RUN", "max_err": "",
                "mean_err": "", "failed_pixels": ""}
    rtl_vals, order_ok = rtl
    frame = ref[0]                      # 第一组平均帧(每组4帧 -> 1组)
    n = min(frame.size, rtl_vals.size)
    diff = np.abs(frame[:n] - rtl_vals[:n])
    max_err = int(diff.max())
    mean_err = float(diff.mean())
    failed = int((diff > tol).sum())
    count_ok = (rtl_vals.size == frame.size)
    ok = (max_err <= tol) and count_ok and order_ok and frame.size == C.NUM_CHANNELS
    return {"dataset": name,
            "result": "PASS" if ok else "FAIL",
            "max_err": max_err, "mean_err": round(mean_err, 4),
            "failed_pixels": failed,
            "count_ok": count_ok, "order_ok": order_ok}


def main(names=None):
    C.ensure_dirs()
    if names is None:
        names = [p.stem for p in sorted(C.RAW_DIR.glob("*.csv"))
                 if not p.stem.endswith("_truth")]
    rows = [compare_one(n) for n in names]
    with open(C.RESULTS_DIR / "verification_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dataset", "result", "max_err",
                                          "mean_err", "failed_pixels"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    print(f"\n{'dataset':<26}{'result':<14}{'max_err':>8}{'mean_err':>10}{'bad_px':>8}")
    print("-" * 66)
    for r in rows:
        print(f"{r['dataset']:<26}{r['result']:<14}{str(r['max_err']):>8}"
              f"{str(r['mean_err']):>10}{str(r['failed_pixels']):>8}")
    npass = sum(1 for r in rows if r["result"] == "PASS")
    print("-" * 66)
    print(f"PASS {npass}/{len(rows)}   (NOT_RUN = RTL 仿真未运行; NO_REFERENCE = 参考缺失)")
    return 0 if npass == len(rows) and rows else 1


if __name__ == "__main__":
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(only))
