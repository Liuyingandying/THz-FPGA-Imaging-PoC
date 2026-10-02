#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_2x8x32.py — 2×(8×32) THz 数字样机演示主工具(Phase V4-Demo)

声明(必须随产物传播):
  This is an engineering digital twin based on an ASSUMED 2×(8×32)
  detector topology. The detector topology and numerical photocurrent
  values are NOT claimed to be measurements from the real prototype.
  本演示为基于假设型 2×(8×32) 探测拓扑的工程数字样机;探测拓扑与
  光电流数值均非真实样机实测。

假设读出模型:32 列方向并行模拟通道 × 16 行顺序扫描(Bank A rows0-7,
Bank B rows8-15)= 512 空间采样/帧;每行 32 采样流进现有 32 通道成像核
(RTL 零修改,经 thz_row_scanner 包装)。

场景:metal_defect(隐藏金属+内部缺陷) / moving_target(左→右移动) /
      tju(天津大学 TJU 字母图案)。

用法:
  python tools/demo_2x8x32.py build          # 数据+RTL仿真+黄金比对+素材图
  python tools/demo_2x8x32.py gif            # 动图(PIL, 失败不阻塞)
  python tools/demo_2x8x32.py all
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import thz_config as C
import reference_model as RM

# ---------------- 假设参数(数字样机,非实测) ----------------
ROWS, COLS = 16, 32
NCH = COLS                       # 32 并行通道
BG_NA = 50.0
LEVELS = {"weak": 65.0, "normal": 100.0, "strong": 300.0}  # 展示默认 normal
DEFECT_NA = 80.0                 # 缺陷孔洞(弱目标档)
E_PER_NA = 60.0
LSB_PER_E = 0.04
READ_LSB = 3.0
CH_OFFSET = 60
CH_GAIN_SPREAD = 0.25
AVG = 4
SEED = C.SEED + 2024
N_MOV_FRAMES = 16                # 动态演示帧数(10~20)

ROOT = C.ROOT
DDIR = ROOT / "data" / "demo2x8x32"
RDIR = ROOT / "results" / "demo_2x8x32"
FIGD = RDIR / "figures"
RIND, ROUTD = DDIR / "rtl_in", DDIR / "rtl_out"
REFD = RDIR / "ref"

DECL = ("ASSUMED PROTOTYPE MODEL | SYNTHETIC THz DATA — "
        "digital twin, not real-prototype measurements")


# ---------------- 场景真值(nA 强度图) ----------------

def scene_metal(level=None):
    """隐藏金属目标(矩形主体+凸缘)+ 内部缺陷孔洞。"""
    lv = LEVELS["normal"]
    img = np.full((ROWS, COLS), BG_NA)
    img[3:13, 6:20] = lv          # 金属主体 10×14
    img[5:8, 20:23] = lv          # 右侧凸缘
    img[7:10, 10:14] = DEFECT_NA  # 内部缺陷孔洞 3×4
    return img


def scene_tju(level=None):
    """16×32 可辨 block 字母 TJU。"""
    lv = LEVELS["normal"]
    img = np.full((ROWS, COLS), BG_NA)
    # T (cols 2-9)
    img[2:4, 2:10] = lv
    img[4:13, 5:7] = lv
    # J (cols 12-19)
    img[2:3, 17:20] = lv
    img[3:11, 17:19] = lv
    img[11:13, 13:20] = lv
    # U (cols 22-29)
    img[2:12, 22:24] = lv
    img[2:12, 27:29] = lv
    img[11:13, 22:29] = lv
    return img


def scene_moving(k, n=N_MOV_FRAMES):
    """移动目标:3×5 块从左向右扫过,居中高度。"""
    img = np.full((ROWS, COLS), BG_NA)
    span = COLS - 5
    c0 = int(round(k / (n - 1) * span)) if n > 1 else 0
    img[6:9, c0:c0 + 5] = LEVELS["normal"]
    return img


SCENES = {"metal_defect": lambda: [scene_metal()] * AVG,
          "tju": lambda: [scene_tju()] * AVG,
          "moving_target": lambda: [scene_moving(k) for k in range(N_MOV_FRAMES)]}


# ---------------- 光电流 → ADC(逐行流 = 32 通道) ----------------

def make_row_calib():
    """32 通道标定(与既有 4×8 同机制、独立表,不覆盖既有文件)。"""
    rng = np.random.default_rng(SEED)
    off = rng.uniform(-CH_OFFSET, CH_OFFSET, NCH)
    gn = 1.0 + rng.uniform(-CH_GAIN_SPREAD, CH_GAIN_SPREAD, NCH)
    off[0], gn[0] = 0.0, 1.0
    dark = np.clip(np.round(C.BASELINE + off), 0, 4095).astype(np.uint16)
    gain_q = np.clip(np.round(C.GAIN_ONE / gn), 1, 65535).astype(np.uint16)
    RIND.mkdir(parents=True, exist_ok=True)
    (DDIR / "dark32.hex").write_text("".join(f"{v:03x}\n" for v in dark))
    (DDIR / "gain32.hex").write_text("".join(f"{v:04x}\n" for v in gain_q))
    (DDIR / "cmap32.hex").write_text("".join(f"{v:02x}\n" for v in range(NCH)))
    return dark, gain_q, gn, off


def synth_adc(truth_frames, gn, off, seed):
    """整帧真值 → 每帧 512 ADC 码(行主序;每行 32 通道并行的等效串行流)。"""
    rng = np.random.default_rng(seed)
    out = []
    for truth in truth_frames:
        sig_e = truth.reshape(-1) * E_PER_NA          # 512(行主序=行流拼接)
        n = rng.poisson(sig_e)
        row_off = np.tile(off, ROWS)                   # 每行共用同 32 通道失配
        row_gn = np.tile(gn, ROWS)
        adc = (C.BASELINE + row_off + row_gn * (LSB_PER_E * n)
               + rng.normal(0, READ_LSB, NCH * ROWS))
        out.append(np.clip(np.round(adc), 0, 4095).astype(np.uint16)
                   .reshape(ROWS, COLS))
    return np.array(out)          # (F,16,32)


def golden_rows(raw_frames, dark, gain_q):
    """逐行喂黄金模型(与 RTL 行流语义一致):每行 AVG 帧平均 → 16×32 帧。"""
    frames = []
    for g in range(raw_frames.shape[0] // AVG):
        blk = raw_frames[g * AVG:(g + 1) * AVG]      # (AVG,16,32)
        rows_out = np.zeros((ROWS, COLS), dtype=np.int64)
        for r in range(ROWS):
            rows_raw = blk[:, r, :]                  # (AVG,32) 行流帧
            old = (C.NUM_CHANNELS, C.ROWS, C.COLS)
            C.NUM_CHANNELS, C.ROWS, C.COLS = NCH, 1, COLS
            try:
                fr = RM.process_frames(rows_raw, dark, gain_q, polarity=1,
                                       avg_frames=AVG, cmap=np.arange(NCH))[0]
            finally:
                C.NUM_CHANNELS, C.ROWS, C.COLS = old
            rows_out[r] = fr
        frames.append(rows_out)
    return np.array(frames)        # (G,16,32)


# ---------------- RTL 行扫描链 ----------------

def run_rtl(stim_hex_text, n_groups, tag):
    """编译一次,跑一次 TB;返回 RTL 帧数组 (G,16,32) 或 None。"""
    ROUTD.mkdir(parents=True, exist_ok=True)
    (RIND / "stim.hex").write_text(stim_hex_text)
    vvp = ROOT / "sim" / "tb_row_scanner.vvp"
    if not vvp.exists():
        rc = subprocess.run(
            ["wsl", "-e", "bash", "-c",
             f"cd '{C.wsl_path(ROOT)}' && iverilog -g2012 -o sim/tb_row_scanner.vvp "
             f"-s tb_row_scanner rtl/thz_input_adapter.sv rtl/thz_offset_calibrator.sv "
             f"rtl/thz_gain_calibrator.sv rtl/thz_temporal_averager.sv "
             f"rtl/thz_frame_mapper.sv rtl/thz_frame_buffer.sv "
             f"rtl/thz_imaging_top.sv rtl/thz_row_scanner.sv tb/tb_row_scanner.sv"],
            capture_output=True, text=True, errors="replace")
        if rc.returncode != 0:
            print("COMPILE FAIL:", rc.stdout[-500:], rc.stderr[-500:])
            return None
    cmd = (f"cd '{C.wsl_path(ROOT)}' && vvp sim/tb_row_scanner.vvp "
           f"+define+DUMMY=1")
    # N_GROUPS 经 -DN_GROUPS 传递:重编译轻量,直接带 define 跑
    calib = (f'-DDARK_FILE=\\"data/demo2x8x32/dark32.hex\\" '
             f'-DGAIN_FILE=\\"data/demo2x8x32/gain32.hex\\" '
             f'-DMAP_FILE=\\"data/demo2x8x32/cmap32.hex\\"')
    subprocess.run(
        ["wsl", "-e", "bash", "-c",
         f"cd '{C.wsl_path(ROOT)}' && iverilog -g2012 -o sim/tb_row_scanner.vvp "
         f"-s tb_row_scanner -DN_GROUPS={n_groups} {calib} "
         f"rtl/thz_input_adapter.sv rtl/thz_offset_calibrator.sv "
         f"rtl/thz_gain_calibrator.sv rtl/thz_temporal_averager.sv "
         f"rtl/thz_frame_mapper.sv rtl/thz_frame_buffer.sv "
         f"rtl/thz_imaging_top.sv rtl/thz_row_scanner.sv tb/tb_row_scanner.sv && "
         f"vvp sim/tb_row_scanner.vvp"],
        capture_output=True, text=True, errors="replace", timeout=1800)
    of = ROUTD / "rtl_out.txt"
    if not of.exists():
        return None
    lines = [l.split() for l in of.read_text().splitlines() if l.strip()]
    vals = np.array([int(l[2]) for l in lines], dtype=np.int64)
    import os as _os
    _os.replace(of, ROUTD / f"rtl_out_{tag}.txt")
    return vals.reshape(n_groups, ROWS, COLS) if vals.size == n_groups * 512 else None


# ---------------- 指标 ----------------

def metrics(gt, rec):
    tn = (gt - gt.min()) / (np.ptp(gt) or 1)
    rn = (rec - rec.min()) / (np.ptp(rec) or 1)
    r = float(np.corrcoef(gt.flatten(), rec.flatten())[0, 1])
    nrmse = float(np.sqrt(np.mean((tn - rn) ** 2)))
    bg = gt == BG_NA
    tg = gt == gt.max()
    thr = (float(np.median(rec[tg])) + float(np.median(rec[bg]))) / 2
    seg = rec > thr
    iou = float((seg & tg).sum() / max((seg | tg).sum(), 1))
    s = float(rec[bg].std()) or 1.0
    cnr = abs(float(np.median(rec[tg])) - float(np.median(rec[bg]))) / s
    out = {"pearson_r": round(r, 4), "nrmse": round(nrmse, 4),
           "iou": round(iou, 4), "cnr": round(cnr, 2)}
    df = gt == DEFECT_NA
    if df.any():
        out["defect_detected"] = bool(
            abs(float(rec[df].mean()) - float(rec[bg].mean())) >= 3 * s
            and abs(float(rec[df].mean()) - float(rec[tg].mean())) >= 3 * s)
    return out


# ---------------- 图 ----------------

def _heat(ax, img, title, cmap="inferno"):
    im = ax.imshow(img, cmap=cmap, aspect="auto")
    ax.set_title(title, fontsize=8)
    ax.set_xticks([]); ax.set_yticks([])
    ax.figure.colorbar(im, ax=ax, fraction=0.046)


def save_panels(tag, gt, raw0, rec, m, declaration=DECL):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                              "DejaVu Sans"]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6))
    _heat(axes[0], gt, f"{tag} — Ground Truth (nA, assumed)")
    _heat(axes[1], raw0, "Synthetic Random Photocurrent → ADC frame0 "
                         "(Poisson+read+FPN)")
    ttl = (f"FPGA-Reconstructed Frame (RTL row-scan)\n"
           f"r={m['pearson_r']} IoU={m['iou']} CNR={m['cnr']} "
           f"NRMSE={m['nrmse']}")
    if "defect_detected" in m:
        ttl += f" | Defect: {'YES' if m['defect_detected'] else 'NO'}"
    _heat(axes[2], rec, ttl)
    fig.suptitle(f"2×(8×32) Digital Twin — {tag}   [{declaration}]",
                 fontsize=9.5)
    fig.tight_layout()
    fig.savefig(FIGD / f"{tag}_panels.png", dpi=130)
    plt.close(fig)


def architecture_figure():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei",
                                              "DejaVu Sans"]
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    fig, ax = plt.subplots(figsize=(12.5, 4.8))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.text(0.5, 0.96, "2×(8×32) THz Digital Twin — FPGA Imaging Demonstrator",
            ha="center", fontsize=13, weight="bold")
    ax.text(0.5, 0.905, f"[{DECL}]", ha="center", fontsize=8.5, color="#8b1a1a")
    boxes = [
        ("Assumed 2×(8×32)\nDetector Array\n(BankA r0-7 / BankB r8-15)", "#ffe9a8"),
        ("Synthetic Poisson\nPhotocurrent\n(50/100/300 nA assumed)", "#ffe9a8"),
        ("Row Scanner\n32 parallel ch\n× 16 row scans", "#bcd4ec"),
        ("thz_row_scanner (NEW)\nrow stream + bank/row tags", "#bcd4ec"),
        ("EXISTING 32-ch core\n(7 modules, ZERO MOD\n1×32 instance)", "#bfe3bf"),
        ("Frame Builder 16×32\n→ reconstructed frame", "#bfe3bf"),
        ("Viewer\nGT / photocurrent /\nFPGA frame / defect map", "#bfe3bf"),
    ]
    n = len(boxes); w, h, y = 0.125, 0.34, 0.36
    for i, (t, c) in enumerate(boxes):
        x = 0.015 + i * (w + 0.005)
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.006",
                                    fc=c, ec="k", lw=0.8))
        ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=7)
        if i:
            ax.add_patch(FancyArrowPatch((x - 0.005, y + h / 2),
                                         (x, y + h / 2),
                                         arrowstyle="-|>", mutation_scale=10))
    ax.text(0.5, 0.22, "Bank A rows 0-7 + Bank B rows 8-15 → 16×32 frame "
            "(512 samples/frame, 32-ch parallel × 16 row scans)",
            ha="center", fontsize=9)
    ax.text(0.5, 0.15, "Green = verified | Blue = new in this phase "
            "(wrapper only, core untouched) | Yellow = assumed model",
            ha="center", fontsize=8.5, color="#333333")
    ax.text(0.5, 0.07, "Demo: A hidden metal+internal defect | B moving "
            "target (16 frames) | C TJU pattern", ha="center", fontsize=9)
    fig.savefig(FIGD / "01_system_architecture.png", dpi=140,
                bbox_inches="tight")
    plt.close(fig)


# ---------------- 主流程 ----------------

def build():
    FIGD.mkdir(parents=True, exist_ok=True)
    REFD.mkdir(parents=True, exist_ok=True)
    dark, gain_q, gn, off = make_row_calib()
    report = {}

    for idx, tag in enumerate(("metal_defect", "tju", "moving_target")):
        truths = SCENES[tag]()
        raw = synth_adc(truths, gn, off, seed=SEED + 101 * (idx + 1))
        gold = golden_rows(raw, dark, gain_q)
        # 汇出(供 Viewer/GIF 复用)
        np.save(REFD / f"{tag}_truth.npy", np.array(truths))
        np.save(REFD / f"{tag}_raw.npy", raw)
        np.save(REFD / f"{tag}_gold.npy", gold)
        # 行驻留物理模型:每行驻留 AVG 帧再前进(与真实扫描读出一致);
        # 核按 32 采样/帧逐块平均 → 恰为"行 r 的 AVG 个时间帧"。
        n_groups = raw.shape[0] // AVG
        stim_rows = []
        for g in range(n_groups):
            for r in range(ROWS):
                for f in range(AVG):
                    stim_rows.append(raw[g * AVG + f, r, :])
        stim = "".join(f"{v:03x}\n"
                       for v in np.concatenate(stim_rows))
        n_groups = gold.shape[0]
        rtl = run_rtl(stim, n_groups, tag)
        if rtl is None:
            report[tag] = {"rtl": "SIM-FAIL"}
            print(f"{tag}: RTL FAIL")
            continue
        exact = bool(np.array_equal(rtl, gold))
        report[tag] = {"rtl_bit_exact": exact,
                       "groups": int(n_groups)}
        print(f"{tag}: RTL {'BIT-EXACT' if exact else 'MISMATCH'} "
              f"({n_groups} 组 16×32 帧)")
        m = metrics(np.array(truths)[0 if tag != 'moving_target' else 0],
                    gold[0])
        report[tag]["metrics_frame0"] = m
        save_panels(tag, np.array(truths)[0], raw[0].astype(float),
                    gold[0].astype(float), m)

    architecture_figure()
    (RDIR / "report.json").write_text(json.dumps(
        {"declaration": DECL,
         "assumed_params": {"bg_na": BG_NA, "levels_na": LEVELS,
                            "defect_na": DEFECT_NA, "avg_frames": AVG,
                            "e_per_na": E_PER_NA, "lsb_per_e": LSB_PER_E,
                            "read_lsb": READ_LSB, "seed": SEED},
         "results": report}, indent=1, ensure_ascii=False))
    print("report ->", RDIR / "report.json")
    return report


def gif():
    from PIL import Image
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for tag in ("moving_target", "tju"):
        truth = np.load(REFD / f"{tag}_truth.npy")
        gold = np.load(REFD / f"{tag}_gold.npy")
        raw = np.load(REFD / f"{tag}_raw.npy")
        frames = []
        for g in range(gold.shape[0]):
            fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
            _heat(axes[0], truth[min(g, truth.shape[0] - 1)],
                  f"{tag} GT frame {g}")
            _heat(axes[1], gold[g], f"FPGA reconstruction {g}")
            fig.suptitle(DECL, fontsize=7)
            fig.tight_layout()
            tmp = FIGD / "_gif_tmp.png"
            fig.savefig(tmp, dpi=72); plt.close(fig)
            frames.append(Image.open(tmp).convert("P"))
        frames[0].save(FIGD / f"{tag}.gif", save_all=True,
                       append_images=frames[1:], duration=420, loop=0)
        print("gif:", FIGD / f"{tag}.gif")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["build", "gif", "all"])
    a = ap.parse_args(argv)
    if a.mode in ("build", "all"):
        build()
    if a.mode in ("gif", "all"):
        try:
            gif()
        except Exception as e:
            print("gif skipped:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
