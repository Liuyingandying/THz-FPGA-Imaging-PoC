#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scene512_validation.py — 16×32 阵列场景级验证(Phase: 场景级验证)

阵列建模声明(工程假设,非最终硬件结构结论):
    老师样机信道结构按"两排 8×32 上下拼接为一个 16×32 阵列"建模,
    通道 ch → (row=ch//32, col=ch%32),排 1 占 rows 0..7、排 2 占 rows 8..15,
    拼接缝位于 row7/row8 之间。若老师确认为"双独立 8×32 通道",可扩展为
    双通道模式(见验证文档"板级扩展映射"一节)。

场景(16×32 ground truth 强度图,单位 nA):
    scn1  矩形目标 + 中心缺陷孔洞
    scn2  两个分离目标块
    scn3  字母 T
    电流:背景 50 nA,目标区随动态范围档位(100/300/500 nA),缺陷孔洞 120 nA

随机光电流模型(与既有 Poisson 路线同构):
    光子/光电子计数 N ~ Poisson(I_nA × E_PER_NA)   ← 散粒噪声=计数固有方差
    adc = clip(round(BASELINE + off_ch + gain_ch × LSB_PER_E × N + N(0,READ_LSB)))
    通道失配(offset/gain)与 4×8 基线假设同分布;固定派生种子可复现。

验证链:场景真值 → 随机电流数据 → ADC 码 → (a) Python 黄金处理(复用
reference_model.process_frames,几何参数就地覆盖为 512/16/32)
(b) 既有 RTL 经新增 TB(tb/tb_scn512.sv)在 512 通道下仿真 → 位级比对。

物理机制与工程假设参数的区分见验证文档;本文件头部常量全部为工程假设。
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import thz_config as C

# ---------------- 阵列几何(工程建模假设:两排 8×32 拼接) ----------------
NCH, ROWS, COLS = 512, 16, 32

# ---------------- 电流模型参数(工程假设,非实测) ----------------
BG_NA = 50                     # 背景等效电流
DEFECT_NA = 120                # 缺陷孔洞等效电流
LEVELS_NA = [100, 300, 500]    # 动态范围 sweep:目标区电流
E_PER_NA = 60.0                # 每帧每 nA 等效光电子数(含积分时间/量子效率)
LSB_PER_E = 0.04               # ADC 每光电子 LSB 数(TIA 增益×ADC 满量程折算)
READ_LSB = 3.0                 # 读出噪声 1σ (ADC LSB)
CH_OFFSET_LSB = 60             # 通道 offset 失配上限(与 4×8 基线假设同分布)
CH_GAIN_SPREAD = 0.25          # 通道 gain 失配幅度
FRAMES = 4                     # 每数据集帧数(与 AVG_FRAMES=4 匹配 → 1 输出帧)
ADC_MAX = 4095

SEED = C.SEED + 777

# ---------------- 目录(独立于 4×8 数据,零覆盖) ----------------
D = C.ROOT / "data" / "scn512"
R = C.ROOT / "results" / "scn512"
CALIB = D / "calib"
RAW = D / "raw"
RTL_IN = D / "rtl_in"
REF = R / "ref"
RTL_OUT = R / "rtl_out"
FIG = R / "figures"

SCENES = ["rect_hole", "two_blocks", "letter_T"]


# ---------------- 场景真值(nA 强度图) ----------------

def build_scene(name: str, target_na: float) -> np.ndarray:
    img = np.full((ROWS, COLS), float(BG_NA))
    if name == "rect_hole":
        img[4:12, 8:24] = target_na                       # 矩形目标 8×16
        img[7:9, 14:18] = float(DEFECT_NA)                # 中心缺陷孔洞 2×4
    elif name == "two_blocks":
        img[2:6, 4:12] = target_na                        # 块 A 4×8
        img[10:14, 20:28] = target_na                     # 块 B 4×8(对角分离)
    elif name == "letter_T":
        img[2:5, 8:24] = target_na                        # 横杠 3×16
        img[4:14, 14:18] = target_na                      # 竖杠 10×4
    else:
        raise ValueError(name)
    return img


# ---------------- 数据生成 ----------------

def make_calib():
    rng = np.random.default_rng(SEED)
    off = rng.uniform(-CH_OFFSET_LSB, CH_OFFSET_LSB, NCH)
    gn = 1.0 + rng.uniform(-CH_GAIN_SPREAD, CH_GAIN_SPREAD, NCH)
    off[0] = 0.0
    gn[0] = 1.0
    dark = np.clip(np.round(C.BASELINE + off), 0, ADC_MAX).astype(np.uint16)
    gain_q = np.clip(np.round(C.GAIN_ONE / gn), 1, 65535).astype(np.uint16)
    CALIB.mkdir(parents=True, exist_ok=True)
    (CALIB / "dark.hex").write_text("".join(f"{v:03x}\n" for v in dark))
    (CALIB / "gain.hex").write_text("".join(f"{v:04x}\n" for v in gain_q))
    (CALIB / "channel_map.hex").write_text(
        "".join(f"{v:03x}\n" for v in range(NCH)))
    return dark, gain_q, gn, off


def gen_dataset(name, level_na, truth, dark, gain_q, gn, off):
    """生成 FRAMES 帧 Poisson ADC 数据 + 黄金处理帧。返回字典。"""
    stem = f"scn512_{name}_L{level_na}"
    prng = np.random.default_rng(SEED + 1000 * (SCENES.index(name) + 1)
                                 + LEVELS_NA.index(level_na) * 10)
    raw = np.zeros((FRAMES, NCH), dtype=np.uint16)
    sig_e = truth.reshape(-1) * E_PER_NA           # 每通道每帧等效光电子(行主序=通道序)
    for f in range(FRAMES):
        n = prng.poisson(sig_e)                    # Poisson 计数(散粒噪声固有)
        ideal = C.BASELINE + np.asarray(off) + np.asarray(gn) * (LSB_PER_E * n)
        adc = ideal + prng.normal(0, READ_LSB, NCH)
        raw[f] = np.clip(np.round(adc), 0, ADC_MAX).astype(np.uint16)

    # 黄金处理(复用已验证数学,几何参数就地覆盖)
    import reference_model as RM
    old = (C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR)
    C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR = NCH, ROWS, COLS, CALIB
    try:
        frame = RM.process_frames(raw, dark, gain_q,
                                  polarity=1, avg_frames=C.AVG_FRAMES,
                                  cmap=np.arange(NCH))[0]
    finally:
        C.NUM_CHANNELS, C.ROWS, C.COLS, C.CALIB_DIR = old

    RAW.mkdir(parents=True, exist_ok=True)
    RTL_IN.mkdir(parents=True, exist_ok=True)
    REF.mkdir(parents=True, exist_ok=True)
    np.savetxt(RAW / f"{stem}.csv", raw, fmt="%d", delimiter=",")
    (RTL_IN / f"{stem}.hex").write_text(
        "".join(f"{v:03x}\n" for v in raw.reshape(-1)))
    np.savetxt(REF / f"{stem}_ref.txt", frame, fmt="%d", delimiter=",")
    np.save(REF / f"{stem}_truth.npy", truth)
    np.save(REF / f"{stem}_raw0.npy", raw[0].reshape(ROWS, COLS))
    return {"stem": stem, "truth": truth, "raw0": raw[0].reshape(ROWS, COLS),
            "proc": frame.reshape(ROWS, COLS)}


# ---------------- 指标 ----------------

def metrics(d):
    truth, proc = d["truth"], d["proc"].astype(float)
    bg_mask = truth == BG_NA
    df_mask = truth == DEFECT_NA                       # 缺陷孔洞区
    tg_mask = (truth != BG_NA) & ~df_mask              # 目标区

    tn = (truth - truth.min()) / np.ptp(truth)
    pn = (proc - proc.min()) / np.ptp(proc)
    r = float(np.corrcoef(truth.flatten(), proc.flatten())[0, 1])
    nrmse = float(np.sqrt(np.mean((tn - pn) ** 2)))

    # 目标掩膜 IoU(自适应阈值:背景与目标处理值的中点)
    thr = (np.median(proc[tg_mask]) + np.median(proc[bg_mask])) / 2
    rec = proc > thr
    iou = float((rec & tg_mask).sum() / max((rec | tg_mask).sum(), 1))

    # 缺陷孔洞分辨:与背景、与目标的双侧对比余量(单位:背景像素 σ)
    bg_std = float(proc[bg_mask].std()) or 1.0
    d_vs_bg = (float(proc[df_mask].mean()) - float(proc[bg_mask].mean())) / bg_std
    d_vs_tg = (float(proc[df_mask].mean()) - float(proc[tg_mask].mean())) / bg_std

    # 对比 SNR:目标相对背景(处理域, dB)
    snr_db = 20 * np.log10(abs(float(np.median(proc[tg_mask]))
                               - float(np.median(proc[bg_mask]))) / bg_std)
    return {"pearson_r": round(r, 4), "nrmse": round(nrmse, 4),
            "iou_target": round(iou, 4),
            "defect_vs_bg_sigma": round(d_vs_bg, 2),
            "defect_vs_tg_sigma": round(d_vs_tg, 2),
            "contrast_snr_db": round(snr_db, 2),
            "thr_used": float(thr), "rec_mask": rec}


# ---------------- 可视化 ----------------

def figures(per_level):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG.mkdir(parents=True, exist_ok=True)

    for si, scene in enumerate(SCENES):
        fig, axes = plt.subplots(3, 3, figsize=(13, 8.2))
        for li, level in enumerate(LEVELS_NA):
            d = per_level[(scene, level)]
            m = metrics(d)
            for ax, img, ttl in zip(
                    axes[li],
                    [d["truth"], d["raw0"], d["proc"]],
                    [f"GT intensity (nA, target {level})",
                     f"noisy ADC frame 0 (shot+read+FPN)",
                     f"processed (r={m['pearson_r']}, IoU={m['iou_target']})"]):
                im = ax.imshow(img, cmap="inferno", aspect="auto")
                ax.set_title(f"{scene} L{level} — {ttl}", fontsize=7.5)
                ax.set_xticks([]); ax.set_yticks([])
                fig.colorbar(im, ax=ax, fraction=0.046)
        fig.suptitle(f"Scene: {scene} — 16×32 (工程假设: 两排 8×32 拼接)",
                     fontsize=11)
        fig.tight_layout()
        fig.savefig(FIG / f"scn512_{scene}_panels.png", dpi=130)
        plt.close(fig)
        print("figure:", f"scn512_{scene}_panels.png")


def sweep_figure(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for scene in SCENES:
        xs = [r["level_na"] for r in rows if r["scene"] == scene]
        for ax, key, name in zip(
                axes, ["pearson_r", "nrmse", "iou_target"],
                ["Pearson r", "NRMSE", "target IoU"]):
            ax.plot(xs, [r[key] for r in rows if r["scene"] == scene], "o-",
                    label=scene)
            ax.set_xlabel("target current (nA)"); ax.set_ylabel(name)
            ax.set_xscale("log"); ax.grid(alpha=0.3); ax.legend(fontsize=7)
    fig.suptitle("16×32 scene recovery vs target current level (Poisson sim)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(FIG / "scn512_snr_sweep.png", dpi=130)
    plt.close(fig)
    print("figure: scn512_snr_sweep.png")


# ---------------- 主流程 ----------------

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["gen", "all"], default="all")
    ap.add_argument("--no-sim", action="store_true")
    a = ap.parse_args(argv)

    dark, gain_q, gn, off = make_calib()
    out = []
    for scene in SCENES:
        for level in LEVELS_NA:
            truth = build_scene(scene, level)
            d = gen_dataset(scene, level, truth, dark, gain_q, gn, off)
            d["scene"], d["level_na"] = scene, level
            out.append(d)
            print(f"gen: {d['stem']}")

    # 指标
    rows = []
    for d in out:
        m = metrics(d)
        rows.append({"scene": d["scene"], "level_na": d["level_na"],
                     **{k: v for k, v in m.items()
                        if not k.endswith("_mask")}})
    R.mkdir(parents=True, exist_ok=True)
    import csv
    with open(R / "metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # RTL 仿真(新增 TB,既有 RTL 零改动)
    if not a.no_sim:
        import subprocess
        RTL_OUT.mkdir(parents=True, exist_ok=True)
        ok = 0
        for d in out:
            stem = d["stem"]
            src_hex = RTL_IN / f"{stem}.hex"
            (D / "rtl_in" / "stim.hex").write_text(src_hex.read_text())
            r = subprocess.run(
                ["wsl", "-e", "bash", "-c",
                 f"cd '{C.wsl_path(C.ROOT)}' && vvp sim/tb_scn512.vvp"],
                capture_output=True, text=True, errors="replace", timeout=600)
            of = RTL_OUT / f"{stem}_out.txt"
            if of.exists():
                of.unlink()
            (R / "rtl_out" / "rtl_out.txt").rename(of)
            gold = d["proc"].flatten().astype(int)
            rtl = [int(l.split()[2]) for l in of.read_text().splitlines()
                   if l.strip()]
            exact = (len(rtl) == len(gold)
                     and all(a == b for a, b in zip(rtl, gold)))
            ok += exact
            print(f"rtl: {stem}  {'BIT-EXACT' if exact else 'MISMATCH'} "
                  f"({len(rtl)} px)")
        print(f"RTL bit-exact: {ok}/{len(out)}")
        rows_rtl = {"rtl_bit_exact": f"{ok}/{len(out)}"}
    else:
        rows_rtl = {"rtl_bit_exact": "skipped"}

    figures({(d["scene"], d["level_na"]): d for d in out})
    sweep_figure(rows)
    (R / "summary.json").write_text(json.dumps(
        {"rows": rows, **rows_rtl,
         "assumption": "16×32 = 两排 8×32 上下拼接(工程建模假设,非最终硬件结论)",
         "params": {"bg_na": BG_NA, "defect_na": DEFECT_NA,
                    "levels_na": LEVELS_NA, "e_per_na": E_PER_NA,
                    "lsb_per_e": LSB_PER_E, "read_lsb": READ_LSB,
                    "frames": FRAMES, "seed": SEED}}, indent=1))
    print(f"summary -> {R / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
