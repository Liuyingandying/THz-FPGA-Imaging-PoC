#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v31_failure_boundary.py — Phase V3.1: 16×32 系统失效边界研究

三个变量(全部在数据/标定层注入,RTL/协议/Viewer 零改动):
  1. 目标-背景电流差 ΔI:30 → 2.5 nA(缺陷对比取 ΔI/2,保持"孔洞"语义)
  2. 上下两排 8×32 的标定后漂移失配:gain 漂移 g、offset 漂移 o
     (标定表为标称值,不含漂移 —— 标定后排间温漂的最不利真实场景)
  3. AVG_FRAMES ∈ {1,2,4,8,16}(固定 ΔI=10 nA,位于边界区)

方法:统计用 Python 黄金模型(与 RTL 位级一致性已由 9/9 证明)做蒙特卡洛;
RTL 仿真只在 ΔI 边界代表点做位级抽查确认。

输出:results/v31/{exp1_dI.csv, exp2_bank.csv, exp3_avg.csv, *.png, summary.json}
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import thz_config as C
import reference_model as RM
import scene512_validation as S51

NCH, ROWS, COLS = S51.NCH, S51.ROWS, S51.COLS
BG = S51.BG_NA
OUT = C.ROOT / "results" / "v31"
FIG = OUT / "figures"


def truth_delta(name, d_i, defect_frac=0.5):
    """目标 = BG+ΔI;缺陷孔洞 = BG+ΔI×defect_frac(保持'孔洞'语义)。"""
    img = np.full((ROWS, COLS), float(BG))
    if name == "rect_hole":
        img[4:12, 8:24] = BG + d_i
        img[7:9, 14:18] = BG + d_i * defect_frac
    elif name == "two_blocks":
        img[2:6, 4:12] = BG + d_i
        img[10:14, 20:28] = BG + d_i
    elif name == "letter_T":
        img[2:5, 8:24] = BG + d_i
        img[4:14, 14:18] = BG + d_i
    return img


def gen_raw(truth, dark, gain_q, gn, off, bank_gain=0.0, bank_off=0.0,
            bank_noise=1.0, frames=4, seed=1):
    """Poisson ADC 生成;bank2(rows8-15, ch256-511)叠加标定后漂移。"""
    prng = np.random.default_rng(seed)
    sig_e = truth.reshape(-1) * S51.E_PER_NA
    bank_scale = np.ones(NCH)
    bank_scale[256:] = 1.0 + bank_gain
    raw = np.zeros((frames, NCH), dtype=np.uint16)
    for f in range(frames):
        n = prng.poisson(sig_e)
        noise = prng.normal(0, S51.READ_LSB, NCH)
        noise[256:] *= bank_noise
        ideal = (C.BASELINE + np.asarray(off)
                 + np.asarray(gn) * bank_scale * (S51.LSB_PER_E * n)
                 + bank_off * (np.arange(NCH) >= 256))
        adc = ideal + noise
        raw[f] = np.clip(np.round(adc), 0, 4095).astype(np.uint16)
    return raw


def process(raw, dark, gain_q, avg):
    old = (C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR)
    C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR = NCH, ROWS, COLS, S51.CALIB
    try:
        fr = RM.process_frames(raw, dark, gain_q, polarity=1,
                               avg_frames=avg, cmap=np.arange(NCH))[0]
    finally:
        C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR = old
    return fr.reshape(ROWS, COLS).astype(float)


def seg_metrics(proc, truth, defect_na=None):
    """CNR / IoU / 缺陷双侧余量(按已知电流值域定义掩膜)。"""
    tg_na = float(truth.max())
    tg = np.isclose(truth, tg_na)
    bg = np.isclose(truth, BG)
    df = np.isclose(truth, defect_na) if defect_na is not None         else np.zeros_like(tg, bool)
    bg_std = float(proc[bg].std()) or 1.0
    cnr = abs(float(np.median(proc[tg])) - float(np.median(proc[bg]))) / bg_std
    thr = (float(np.median(proc[tg])) + float(np.median(proc[bg]))) / 2
    rec = proc > thr
    iou = float((rec & tg).sum() / max((rec | tg).sum(), 1))
    m = {"cnr": round(cnr, 3), "iou": round(iou, 4)}
    if df.any():
        m["def_ok"] = int(
            (abs(float(proc[df].mean()) - float(proc[bg].mean())) >= 3 * bg_std)
            and (abs(float(proc[df].mean()) - float(proc[tg].mean())) >= 3 * bg_std))
        m["def_margin_bg"] = round(
            (float(proc[df].mean()) - float(proc[bg].mean())) / bg_std, 2)
        m["def_margin_tg"] = round(
            (float(proc[df].mean()) - float(proc[tg].mean())) / bg_std, 2)
    m["bg_std"] = round(bg_std, 2)
    return m


# ---------------------------------------------------------------- EXP1

def exp1(dark, gain_q, gn, off, d_is=(30, 20, 15, 10, 7.5, 5, 3.5, 2.5),
         trials=8):
    rows = []
    for d_i in d_is:
        for scene in S51.SCENES:
            truth = truth_delta(scene, d_i)
            for t in range(trials):
                raw = gen_raw(truth, dark, gain_q, gn, off,
                              seed=9000 + int(d_i * 10) * 64 + t)
                proc = process(raw, dark, gain_q, avg=C.AVG_FRAMES)
                m = seg_metrics(proc, truth,
                                defect_na=BG + d_i * 0.5)
                rows.append({"d_i": d_i, "scene": scene, "trial": t, **m})
    return rows


# ---------------------------------------------------------------- EXP2

def exp2(dark, gain_q, gn, off, d_i=30, trials=4):
    rows = []
    truth = truth_delta("rect_hole", d_i)
    combos = []
    for g in (0.0, 0.05, 0.10, 0.20):
        for o in (0, 20, 40):
            combos.append((g, o))
    for g, o in combos:
        for t in range(trials):
            raw = gen_raw(truth, dark, gain_q, gn, off, bank_gain=g,
                          bank_off=o, seed=7000 + int(g * 100) * 16
                          + o * 4 + t)
            proc = process(raw, dark, gain_q, avg=C.AVG_FRAMES)
            m = seg_metrics(proc, truth, defect_na=BG + d_i * 0.5)
            # 接缝不连续度:row7 与 row8 的逐列差(跨缝相邻行),以背景 σ 归一
            jump = np.abs(proc[7, :] - proc[8, :])
            ref_jump = np.abs(proc[6, :] - proc[7, :])
            bg_std = m["bg_std"]
            rows.append({"bank_gain": g, "bank_off": o, "trial": t, **m,
                         "seam_jump_lsb": round(float(jump.mean()), 2),
                         "seam_jump_sigma": round(float(jump.mean()) / bg_std, 2),
                         "ref_jump_sigma": round(float(ref_jump.mean()) / bg_std, 2)})
    return rows


# ---------------------------------------------------------------- EXP3

def exp3(dark, gain_q, gn, off, d_i=10, avgs=(1, 2, 4, 8, 16), trials=4):
    rows = []
    UART_BPS, FRAME_BYTES = 115200, 2 + 2 + 2 + 512 * 2 + 2
    for avg in avgs:
        for scene in ("rect_hole", "letter_T"):
            truth = truth_delta(scene, d_i)
            for t in range(trials):
                raw = gen_raw(truth, dark, gain_q, gn, off, frames=avg,
                              seed=5000 + avg * 32 + t)
                proc = process(raw, dark, gain_q, avg=avg)
                m = seg_metrics(proc, truth,
                                defect_na=BG + d_i * 0.5)
                rows.append({"avg": avg, "scene": scene, "trial": t, **m,
                             "acq_frames": avg,
                             "uart_fps": round(11520 / FRAME_BYTES / avg, 2)})
    return rows


# ---------------------------------------------------------------- plots

def plots(r1, r2, r3):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG.mkdir(parents=True, exist_ok=True)

    # EXP1: CNR/IoU/defect-rate vs ΔI
    d_is = sorted({r["d_i"] for r in r1})
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    for scene in S51.SCENES:
        sel = [r for r in r1 if r["scene"] == scene]
        cnr = [np.mean([r["cnr"] for r in sel if r["d_i"] == d]) for d in d_is]
        iou = [np.mean([r["iou"] for r in sel if r["d_i"] == d]) for d in d_is]
        axes[0].plot(d_is, cnr, "o-", label=scene)
        axes[1].plot(d_is, iou, "o-", label=scene)
    sel = [r for r in r1 if r["scene"] == "rect_hole"]
    rate = [np.mean([r["def_ok"] for r in sel if r["d_i"] == d]) for d in d_is]
    axes[2].plot(d_is, rate, "s-", color="#c62828")
    axes[2].set_ylim(-0.05, 1.05)
    axes[2].set_ylabel("defect detection rate")
    axes[0].set_ylabel("CNR (|μ_t−μ_b|/σ_b)")
    axes[1].set_ylabel("target IoU")
    for ax in axes:
        ax.set_xlabel("ΔI = target−background (nA)")
        ax.grid(alpha=0.3); ax.legend(fontsize=7)
        ax.axvspan(2.5, 5, color="red", alpha=0.08)
    fig.suptitle("EXP1: failure boundary vs target-background contrast ΔI "
                 "(16×32, Poisson, AVG=4)", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "exp1_cnr_iou_defect_vs_dI.png", dpi=130)
    plt.close(fig)

    # EXP2: seam jump vs bank mismatch
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for o, mk in zip((0, 20, 40), "o-s-^"):
        xs = sorted({r["bank_gain"] for r in r2 if r["bank_off"] == o})
        ys = [np.mean([r["seam_jump_sigma"] for r in r2
                       if r["bank_gain"] == g and r["bank_off"] == o]) for g in xs]
        axes[0].plot(xs, ys, mk, label=f"offset drift {o} LSB")
    axes[0].set_xlabel("bank2 gain drift"); axes[0].set_ylabel("seam jump (σ_bg)")
    axes[0].legend(fontsize=7); axes[0].grid(alpha=0.3)
    axes[0].set_title("seam discontinuity vs bank drift", fontsize=9)
    for g, mk in zip((0.0, 0.10, 0.20), "o-s-^"):
        xs = sorted({r["bank_off"] for r in r2 if r["bank_gain"] == g})
        ys = [np.mean([r["seam_jump_sigma"] for r in r2
                       if r["bank_off"] == v and r["bank_gain"] == g]) for v in xs]
        axes[1].plot(xs, ys, mk, label=f"gain drift {g:.0%}")
    axes[1].set_xlabel("bank2 offset drift (LSB)"); axes[1].set_ylabel("seam jump (σ_bg)")
    axes[1].legend(fontsize=7); axes[1].grid(alpha=0.3)
    axes[1].set_title("seam discontinuity vs bank offset drift", fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "exp2_seam_vs_bank_mismatch.png", dpi=130)
    plt.close(fig)

    # EXP3: quality vs AVG_FRAMES
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    avgs = sorted({r["avg"] for r in r3})
    for scene, mk in zip(("rect_hole", "letter_T"), "o-s"):
        for ax, key, name in zip(axes, ["cnr", "iou"], ["CNR", "target IoU"]):
            ys = [np.mean([r[key] for r in r3
                           if r["avg"] == a and r["scene"] == scene]) for a in avgs]
            ax.plot(avgs, ys, mk + "-", label=scene)
    for ax in axes:
        ax.set_xlabel("AVG_FRAMES"); ax.grid(alpha=0.3); ax.legend(fontsize=7)
    axes[0].set_ylabel("CNR"); axes[1].set_ylabel("target IoU")
    axes[0].set_title("quality vs averaging", fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "exp3_quality_vs_avg.png", dpi=130)
    plt.close(fig)
    print("figures written")


# ---------------------------------------------------------------- main

def main(argv=None):
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    dark, gain_q, gn, off = S51.make_calib()

    print("EXP1 ΔI sweep ...")
    r1 = exp1(dark, gain_q, gn, off)
    print("EXP2 bank mismatch ...")
    r2 = exp2(dark, gain_q, gn, off)
    print("EXP3 AVG sweep ...")
    r3 = exp3(dark, gain_q, gn, off)

    import csv
    for name, rows, fields in (
            ("exp1_dI", r1, ["d_i", "scene", "trial", "cnr", "iou",
                             "def_ok", "def_margin_bg", "def_margin_tg",
                             "bg_std"]),
            ("exp2_bank", r2, ["bank_gain", "bank_off", "trial", "cnr", "iou",
                               "seam_jump_lsb", "seam_jump_sigma",
                               "ref_jump_sigma", "bg_std"]),
            ("exp3_avg", r3, ["avg", "scene", "trial", "cnr", "iou",
                              "acq_frames", "uart_fps", "bg_std"])):
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})
    plots(r1, r2, r3)

    # 汇总行(EXP1 聚合)
    agg = []
    for d in sorted({r["d_i"] for r in r1}):
        sel = [r for r in r1 if r["d_i"] == d]
        agg.append({
            "d_i": d,
            "cnr_mean": round(float(np.mean([r["cnr"] for r in sel])), 2),
            "iou_mean": round(float(np.mean([r["iou"] for r in sel])), 4),
            "iou_min": round(float(np.min([r["iou"] for r in sel])), 4),
            "def_rate": round(float(np.mean([r["def_ok"] for r in sel
                                             if r["scene"] == "rect_hole"])), 3),
        })
    (OUT / "summary.json").write_text(json.dumps(
        {"exp1_agg": agg, "exp2_rows": r2, "exp3_rows": r3}, indent=1))

    print(f"\n{'ΔI':>6}{'CNR':>8}{'IoU_mean':>10}{'IoU_min':>9}{'def_rate':>9}")
    for a in agg:
        print(f"{a['d_i']:>6}{a['cnr_mean']:>8}{a['iou_mean']:>10}"
              f"{a['iou_min']:>9}{a['def_rate']:>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
