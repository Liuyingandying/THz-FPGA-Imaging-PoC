#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visualize_thz_image.py — 太赫兹 4x8 图像可视化

每个数据集生成(results/images/):
  <name>_raw.png           原始 4x8 热图(第一帧)
  <name>_nearest.png       最近邻放大(仅视觉)
  <name>_interpolated.png  双三次插值平滑图
插值只是视觉展示,不提高物理分辨率 —— 图上明确标注。

showcase(results/figures/):任务书 §26 的 7 张总图。
"""

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

import thz_config as C

UPSCALE = 16  # 最近邻放大倍数(仅视觉)

plt.rcParams["figure.dpi"] = 120


def _nearest(img, k=UPSCALE):
    return np.repeat(np.repeat(img, k, axis=0), k, axis=1)


def _interpolated(img, k=UPSCALE):
    from scipy.ndimage import zoom
    return zoom(img, k, order=3)


def _save_heat(fig, ax, img, title, cmap="inferno"):
    im = ax.imshow(img, cmap=cmap, aspect="equal")
    ax.set_xticks(range(C.COLS)); ax.set_yticks(range(C.ROWS))
    ax.set_title(title, fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046)
    return im


def dataset_figures(name):
    raw = np.genfromtxt(C.REF_DIR / f"{name}_raw_frame0.csv",
                        delimiter=",", dtype=float)
    refp = np.genfromtxt(C.REF_DIR / f"{name}_ref.csv", delimiter=",",
                         skip_header=1, dtype=float).reshape(-1, C.NUM_CHANNELS)[0]
    refp = refp.reshape(C.ROWS, C.COLS)

    for tag, img, note in (
            ("raw", raw, "raw ADC frame 0 (4x8)"),
            ("processed", refp, "processed (dark/gain calib + 4-frame avg)")):
        fig, ax = plt.subplots(figsize=(4.6, 3.4))
        _save_heat(fig, ax, img, f"{name}\n{note}")
        fig.tight_layout()
        fig.savefig(C.IMAGES_DIR / f"{name}_{tag}.png")
        plt.close(fig)

    # 最近邻 + 插值对比(处理后的帧)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    _save_heat(fig, axes[0], _nearest(refp),
               f"{name} nearest-neighbor x{UPSCALE} (visual only)")
    im = _interpolated(refp)
    _save_heat(fig, axes[1], im,
               f"{name} interpolated (visual only)\n"
               "Interpolated visualization - does NOT represent\n"
               "increased physical detector resolution")
    fig.tight_layout()
    fig.savefig(C.IMAGES_DIR / f"{name}_interpolated.png")
    plt.close(fig)


def showcase_figures():
    C.FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    # 01 系统数据链框图
    fig, ax = plt.subplots(figsize=(11.5, 4.2))
    ax.axis("off")
    boxes = [
        ("THz detector\narray (32ch)\n[FUTURE hardw.]", "#d9d9d9"),
        ("TIA analog\nfront-end\n[FUTURE hardw.]", "#d9d9d9"),
        ("ADC 12-bit\n[ASSUMED]", "#d9d9d9"),
        ("Input\nadapter", "#bcd4ec"),
        ("Offset calib\n(dark+pol.)", "#bcd4ec"),
        ("Gain calib\nQ4.8 fixed-pt", "#bcd4ec"),
        ("Temporal avg\n4 frames", "#bcd4ec"),
        ("Channel->pixel\nmapping", "#bcd4ec"),
        ("Frame\nbuffer", "#bcd4ec"),
        ("Host interface\n(sim files now;\nUART/AXIS later)", "#bcd4ec"),
        ("Python viz\n& verification", "#c9e7c9"),
    ]
    n = len(boxes)
    w, h, x0, y0 = 0.083, 0.42, 0.01, 0.30
    for i, (label, color) in enumerate(boxes):
        x = x0 + i * (w + 0.006)
        ax.add_patch(FancyBboxPatch((x, y0), w, h, boxstyle="round,pad=0.004",
                                    fc=color, ec="k", lw=0.8,
                                    transform=ax.transAxes))
        ax.text(x + w / 2, y0 + h / 2, label, ha="center", va="center",
                fontsize=6.2, transform=ax.transAxes)
        if i:
            ax.add_patch(FancyArrowPatch((x - 0.006, y0 + h / 2),
                                         (x, y0 + h / 2), transform=ax.transAxes,
                                         arrowstyle="-|>", mutation_scale=8, color="k"))
    ax.text(0.5, 0.06, "Detector/TIA/ADC are future physical hardware - currently "
            "simulated. RTL stages run in Icarus Verilog simulation.",
            ha="center", fontsize=7.5, style="italic", transform=ax.transAxes)
    ax.set_title("THz FPGA Imaging PoC - data pipeline", fontsize=11)
    fig.savefig(C.FIGURES_DIR / "01_system_pipeline.png", bbox_inches="tight")
    plt.close(fig)

    names = [p.stem for p in sorted(C.RAW_DIR.glob("*.csv"))
             if not p.stem.endswith("_truth")]

    # 02 ground truth 蒙太奇
    k = len(names)
    fig, axes = plt.subplots(2, (k + 1) // 2, figsize=(2.0 * ((k + 1) // 2), 5.4))
    for ax in axes.flat:
        ax.axis("off")
    for j, name in enumerate(names):
        meta_p = C.RAW_DIR / f"{name}_meta.json"
        import json
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        if meta.get("per_frame"):
            truth = np.genfromtxt(C.RAW_DIR / f"{name}_truth.csv",
                                  delimiter=",", dtype=float)[:C.ROWS]
        else:
            try:
                truth = np.genfromtxt(C.RAW_DIR / f"{name}_truth.csv",
                                      delimiter=",", skip_header=1, dtype=float)
            except Exception:
                truth = np.zeros((C.ROWS, C.COLS))
        r, c = divmod(j, (k + 1) // 2)
        axes[r][c].imshow(truth, cmap="inferno")
        axes[r][c].set_title(name, fontsize=7)
    fig.suptitle("Ground-truth scenes (sim assumptions, ADC LSB)", fontsize=10)
    fig.savefig(C.FIGURES_DIR / "02_ground_truth.png", bbox_inches="tight")
    plt.close(fig)

    show = "02_single_point"
    raw = np.genfromtxt(C.REF_DIR / f"{show}_raw_frame0.csv", delimiter=",", dtype=float)
    refp = np.genfromtxt(C.REF_DIR / f"{show}_ref.csv", delimiter=",", skip_header=1,
                         dtype=float).reshape(-1, C.NUM_CHANNELS)[0].reshape(C.ROWS, C.COLS)

    # 03 raw
    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    _save_heat(fig, ax, raw, f"{show} raw ADC frame (with FPN/noise, sim)")
    fig.savefig(C.FIGURES_DIR / "03_raw_adc_frame.png", bbox_inches="tight"); plt.close(fig)

    # 04 reference processed
    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    _save_heat(fig, ax, refp, f"{show} Python golden reference (processed)")
    fig.savefig(C.FIGURES_DIR / "04_reference_processed.png", bbox_inches="tight"); plt.close(fig)

    # 05 RTL processed(仅当真实仿真输出存在)
    rtlp = None
    p = C.RTL_OUT_DIR / f"{show}_out.txt"
    if p.exists():
        vals = [int(l.split()[2]) for l in p.read_text().splitlines() if l.strip()]
        if len(vals) == C.NUM_CHANNELS:
            rtlp = np.array(vals, float).reshape(C.ROWS, C.COLS)
            fig, ax = plt.subplots(figsize=(4.6, 3.4))
            _save_heat(fig, ax, rtlp, f"{show} FPGA RTL output (Icarus Verilog sim)")
            fig.savefig(C.FIGURES_DIR / "05_rtl_processed.png", bbox_inches="tight")
            plt.close(fig)

    # 06 final heatmap(单点目标,处理前后)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    _save_heat(fig, axes[0], raw, "raw 4x8 ADC (fixed-pattern + noise)")
    _save_heat(fig, axes[1], refp, "after FPGA pipeline (dark/gain calib + avg)")
    fig.suptitle(f"Final THz heatmap - {show} (synthetic scene)", fontsize=11)
    fig.savefig(C.FIGURES_DIR / "06_final_heatmap.png", bbox_inches="tight")
    plt.close(fig)

    # 07 reference vs RTL(全部数据集误差条 + 单点目标并排)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    if rtlp is not None:
        _save_heat(fig, axes[0], refp, "Python golden reference")
        _save_heat(fig, axes[1], rtlp, "RTL simulation output")
        fig.suptitle(f"Reference vs RTL - {show} (bit-exact expected)", fontsize=11)
    else:
        axes[0].text(0.5, 0.5, "RTL output not available\n(simulation NOT RUN)",
                     ha="center", va="center", transform=axes[0].transAxes)
        axes[0].axis("off"); axes[1].axis("off")
    fig.savefig(C.FIGURES_DIR / "07_reference_vs_rtl.png", bbox_inches="tight")
    plt.close(fig)

    # 附:全部数据集 PASS/FAIL 总览图
    import csv
    summ = C.RESULTS_DIR / "verification_summary.csv"
    if summ.exists():
        rows = list(csv.DictReader(open(summ)))
        fig, ax = plt.subplots(figsize=(7, 0.42 * len(rows) + 1.4))
        ypos = range(len(rows))[::-1]
        colors = {"PASS": "#4caf50", "FAIL": "#e53935", "NOT_RUN": "#9e9e9e",
                  "NO_REFERENCE": "#ffb300"}
        ax.barh(list(ypos), [1] * len(rows),
                color=[colors.get(r["result"], "#999") for r in rows])
        ax.set_yticks(list(ypos))
        ax.set_yticklabels([f"{r['dataset']}  {r['result']}" for r in rows], fontsize=7.5)
        ax.set_xticks([])
        ax.set_title("RTL vs Python verification (per dataset)", fontsize=10)
        fig.savefig(C.FIGURES_DIR / "08_verification_summary.png", bbox_inches="tight")
        plt.close(fig)


def main():
    C.ensure_dirs()
    names = sys.argv[1:] or [p.stem for p in sorted(C.RAW_DIR.glob("*.csv"))
                             if not p.stem.endswith("_truth")]
    for n in names:
        dataset_figures(n)
        print("images:", n)
    showcase_figures()
    print("figures ->", C.FIGURES_DIR)
    print("images  ->", C.IMAGES_DIR)


if __name__ == "__main__":
    main()
