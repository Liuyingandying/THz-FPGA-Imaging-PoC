#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_reconstruction.py — Phase V1 成像质量评价

回答:随机光电流数据经 FPGA 处理后,空间信息恢复得如何?

指标(全部实际读取文件计算,不写死):
  1. bit_exact      RTL 输出 vs Python 黄金参考 逐位一致(应全 PASS)
  2. pearson_r      处理后 4x8 图与 ground-truth 光子速率图的 Pearson 相关
  3. nrmse          归一化均方根误差(双方 min-max 归一后)
  4. localization   top-2 像素定位(双热点)是否与真值一致
  5. out_snr        输出图像峰值对比信噪比 (peak - bg_mean) / bg_std [dB]
  6. in_snr_theory  输入单帧理论信噪比 K·N/sqrt(K²(N+D)+σ_read²) [dB]

输出:results/snr_sweep.csv + results/figures/09_snr_sweep.png + 终端表
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import thz_config as C

POISSON_DATASETS = ["14_random_photocurrent"] + \
                   [f"{15 + i}_snr_N{n}" for i, n in enumerate(C.SNR_SWEEP_PHOTONS)]


def load_truth(name):
    import io
    p = C.RAW_DIR / f"{name}_truth.csv"
    lines = [l for l in p.read_text().splitlines() if not l.startswith("#")]
    t = np.genfromtxt(io.StringIO("\n".join(lines[1:])), delimiter=",")  # 跳过 c0.. 列头
    return t.reshape(C.ROWS, C.COLS)


def load_processed(name):
    ref = np.genfromtxt(C.REF_DIR / f"{name}_ref.csv", delimiter=",",
                        skip_header=1, dtype=np.int64).reshape(-1, C.NUM_CHANNELS)[0]
    return ref.reshape(C.ROWS, C.COLS)


def load_rtl(name):
    p = C.RTL_OUT_DIR / f"{name}_out.txt"
    vals = np.array([int(l.split()[2]) for l in p.read_text().splitlines() if l.strip()],
                    dtype=np.int64)
    return vals.reshape(C.ROWS, C.COLS)


def top2_cells(img):
    flat = img.flatten()
    idx = np.argsort(flat)[::-1]
    return {(int(i) // C.COLS, int(i) % C.COLS) for i in idx[:2]}


def evaluate_one(name):
    truth = load_truth(name)
    proc = load_processed(name)
    rtl = load_rtl(name)
    meta = json.loads((C.RAW_DIR / f"{name}_meta.json").read_text())
    pm = meta.get("photon_model", {})
    N = float(pm.get("peak_photons_per_frame", 0))
    K = float(pm.get("lsb_per_photon", C.LSB_PER_PHOTON))
    D = float(pm.get("dark_photons_per_frame", C.DARK_PHOTONS))
    sr = float(pm.get("read_noise_lsb", C.PHOTON_READ_NOISE_LSB))

    bit_exact = int(np.abs(rtl - proc).max()) == 0

    tn = (truth - truth.min()) / (np.ptp(truth) or 1)
    pn = (proc - proc.min()) / (np.ptp(proc) or 1)
    r = float(np.corrcoef(truth.flatten(), proc.flatten())[0, 1])
    nrmse = float(np.sqrt(np.mean((tn - pn) ** 2)))

    peaks = sorted(top2_cells(truth)) if N else []
    loc_ok = None
    out_snr = None
    if peaks and len(peaks) == 2:
        loc_ok = top2_cells(proc) == set(peaks)
        bg = np.array([proc[rr, cc] for rr in range(C.ROWS) for cc in range(C.COLS)
                       if (rr, cc) not in peaks], dtype=float)
        vals = [float(proc[p]) for p in peaks]
        out_snr = (min(vals) - bg.mean()) / max(bg.std(), 1e-9)   # 较弱峰的对比SNR
        out_snr_db = 20 * np.log10(out_snr) if out_snr > 0 else float("nan")
    else:
        out_snr_db = float("nan")

    in_snr_db = 20 * np.log10(K * N / np.sqrt(K * K * (N + D) + sr * sr)) if N else float("nan")

    return {"dataset": name, "N_photons": N, "bit_exact": bit_exact,
            "pearson_r": round(r, 4), "nrmse": round(nrmse, 4),
            "localization": ("PASS" if loc_ok else "FAIL") if loc_ok is not None else "n/a",
            "out_snr_db": round(out_snr_db, 2) if out_snr_db == out_snr_db else "n/a",
            "in_snr_db": round(in_snr_db, 2) if N else "n/a"}


def main():
    C.ensure_dirs()
    rows = [evaluate_one(n) for n in POISSON_DATASETS]

    hdr = f"{'dataset':<24}{'N':>6}{'bit':>5}{'pearson_r':>10}{'nrmse':>7}" \
          f"{'loc':>6}{'outSNR dB':>10}{'inSNR dB':>10}"
    print(hdr)
    print("-" * len(hdr))
    for x in rows:
        print(f"{x['dataset']:<24}{x['N_photons']:>6.0f}{str(x['bit_exact']):>5}"
              f"{x['pearson_r']:>10}{x['nrmse']:>7}{x['localization']:>6}"
              f"{str(x['out_snr_db']):>10}{str(x['in_snr_db']):>10}")

    with open(C.RESULTS_DIR / "snr_sweep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- SNR sweep 图(仅双热点 15..19;14 为扩展场景不入线) ----
    sweep = [x for x in rows if x["N_photons"] and x["localization"] != "n/a"]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4))
    ns = [x["N_photons"] for x in sweep]
    axes[0].plot(ns, [x["out_snr_db"] for x in sweep], "o-", label="output image SNR (measured)")
    axes[0].plot(ns, [x["in_snr_db"] for x in sweep], "s--", label="single-frame input SNR (theory)")
    axes[0].plot(ns, [x["in_snr_db"] + 6.0 for x in sweep], ":", color="gray",
                 label="input + 6 dB (4-frame avg bound)")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("mean photo-electrons per frame at hotspot N")
    axes[0].set_ylabel("SNR (dB)")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)
    axes[0].set_title("Contrast SNR vs photon budget", fontsize=10)
    axes[1].plot(ns, [x["pearson_r"] for x in sweep], "o-", color="#2e7d32")
    axes[1].set_xscale("log")
    axes[1].set_ylim(0, 1.05)
    axes[1].set_xlabel("mean photo-electrons per frame at hotspot N")
    axes[1].set_ylabel("Pearson r(processed, truth)")
    axes[1].grid(alpha=0.3)
    axes[1].set_title("Spatial recovery vs photon budget", fontsize=10)
    fig.suptitle("Random photocurrent (Poisson) — FPGA pipeline reconstruction sweep "
                 "(simulation)", fontsize=10)
    fig.tight_layout()
    fig.savefig(C.FIGURES_DIR / "09_snr_sweep.png", dpi=130)
    print(f"\nwritten: {C.RESULTS_DIR / 'snr_sweep.csv'}")
    print(f"written: {C.FIGURES_DIR / '09_snr_sweep.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
