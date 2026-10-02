#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_thz_dataset.py — 生成合成 32 通道太赫兹探测数据集

全部信号参数(噪声、失配、基线等)均为【仿真假设】,
不代表真实太赫兹探测器实测参数,详见 docs/ASSUMPTIONS.md。

输出:
  data/raw/NN_name.csv          frame,channel,raw_adc 长表(原始 ADC)
  data/raw/NN_name_truth.csv    4x8 理想信号图(ground truth,ADC LSB 单位)
  data/raw/NN_name_meta.json    元数据
  data/calib/dark.hex           逐通道暗场标定表(12-bit hex,RTL $readmemh)
  data/calib/gain.hex           逐通道增益标定表(Q4.8,16-bit hex)
  data/calib/channel_map.hex    通道→像素线性索引映射表(8-bit hex)
  data/rtl_in/NN_name.hex       RTL 仿真激励(12-bit hex,每行一个采样)
"""

import json
import sys
import numpy as np

from thz_config import (
    NUM_CHANNELS, ROWS, COLS, ADC_WIDTH, ADC_MAX, BASELINE,
    GAIN_FRAC, GAIN_ONE, FRAMES_PER_DATASET,
    NOISE_SIGMA, LOW_SNR_SIGMA, SPIKE_PROB, SPIKE_AMP,
    DEFAULT_CH_OFFSET, DEFAULT_CH_GAIN_SPREAD, SEED,
    DARK_PHOTONS, LSB_PER_PHOTON, PHOTON_READ_NOISE_LSB, SNR_SWEEP_PHOTONS,
    RAW_DIR, CALIB_DIR, RTL_IN_DIR, ensure_dirs,
)

CH_W = 12  # hex 位宽(12-bit ADC)


def _frames_csv(name, raw):
    """raw: (F, 32) uint16 -> 长表 CSV"""
    lines = ["frame,channel,raw_adc"]
    for f in range(raw.shape[0]):
        for ch in range(raw.shape[1]):
            lines.append(f"{f},{ch},{raw[f, ch]}")
    (RAW_DIR / f"{name}.csv").write_text("\n".join(lines) + "\n")


def _truth_csv(name, truth):
    """truth: (4,8) float -> CSV(含表头)"""
    lines = [f"# ground-truth scene signal (ADC LSB, sim assumption) name={name}"]
    lines.append(",".join(f"c{c}" for c in range(COLS)))
    for r in range(ROWS):
        lines.append(",".join(f"{truth[r, c]:.2f}" for c in range(COLS)))
    (RAW_DIR / f"{name}_truth.csv").write_text("\n".join(lines) + "\n")


def build_scenes():
    """返回 name -> (truth (4,8) float 或 per-frame list, meta dict)"""
    r, c = np.mgrid[0:ROWS, 0:COLS]
    scenes = {}

    scenes["01_dark"] = (np.zeros((ROWS, COLS)), {"desc": "无目标,仅非理想因素"})
    t = np.zeros((ROWS, COLS)); t[1, 3] = 1500
    scenes["02_single_point"] = (t, {"desc": "单热点 (1,3), 1500 LSB"})
    t = np.zeros((ROWS, COLS)); t[0, 1] = 1200; t[3, 6] = 1400
    scenes["03_two_points"] = (t, {"desc": "双热点 (0,1)=1200, (3,6)=1400"})
    t = np.zeros((ROWS, COLS)); t[1:3, :] = 800
    scenes["04_horizontal_bar"] = (t, {"desc": "水平条带 rows1-2 =800"})
    t = np.zeros((ROWS, COLS)); t[:, 3:5] = 800
    scenes["05_vertical_bar"] = (t, {"desc": "垂直条带 cols3-4 =800"})
    t = np.zeros((ROWS, COLS)); t[1, :] = 800; t[:, 4] = 800; t[1, 4] = 1000
    scenes["06_cross"] = (t, {"desc": "十字 row1+col4, 交点 1000"})
    t = 1600 * np.exp(-((r - 1.5) ** 2 + (c - 3.5) ** 2) / 1.8)
    scenes["07_gaussian_blob"] = (t, {"desc": "高斯斑 center(1.5,3.5) sigma^2=1.8 峰值1600"})
    t = np.zeros((ROWS, COLS)); t[2, 4] = 40
    scenes["08_low_snr"] = (t, {"desc": "弱信号 (2,4)=40 LSB, sigma=25", "sigma": LOW_SNR_SIGMA})
    t = np.zeros((ROWS, COLS)); t[1, 1] = 1000
    scenes["09_dead_pixel"] = (t, {"desc": "单热点(1,1); 通道5(row1,col5)坏点 stuck-at 4095",
                                   "dead_channels": [5]})
    t = np.zeros((ROWS, COLS)); t[2, 2] = 1200
    scenes["10_gain_mismatch"] = (t, {"desc": "热点(2,2); 通道增益失配加宽到±45%",
                                      "gain_spread": 0.45})
    t = np.full((ROWS, COLS), 400.0)
    scenes["11_offset_mismatch"] = (t, {"desc": "均匀场景400; 通道offset失配加宽到±120 LSB",
                                        "offset_spread": 120})
    scenes["12_moving_target"] = (
        [_spot(rr, cc, 1200) for rr, cc in [(0, 0), (1, 2), (2, 4), (3, 6)]],
        {"desc": "移动热点 1200 LSB, 帧序 (0,0)->(1,2)->(2,4)->(3,6)", "per_frame": True})
    t = np.zeros((ROWS, COLS)); t[1, 4] = 1200
    scenes["13_inverted_polarity"] = (t, {"desc": "反极性: 场景编码为 baseline-signal, 系统极性=-1",
                                          "polarity": -1})

    # ---- Phase V1: 随机光电流场景(Poisson 光子到达;truth = 光子速率图) ----
    # 光子到达计数 ~ Poisson(λ),散粒噪声(方差=均值)为计数固有属性,非加性高斯。
    blob = np.exp(-((r - 2.0) ** 2 + (c - 3.0) ** 2) / 1.5)
    scenes["14_random_photocurrent"] = (
        blob * 300.0,
        {"desc": "Poisson 高斯斑 center(2,3) 峰值300光电子/帧", "poisson": True})
    two = np.zeros((ROWS, COLS)); two[0, 2] = 1.0; two[3, 5] = 1.0
    for i, n_ph in enumerate(SNR_SWEEP_PHOTONS):
        scenes[f"{15 + i}_snr_N{n_ph}"] = (
            two * float(n_ph),
            {"desc": f"Poisson 双热点 (0,2)/(3,5) 峰值{n_ph}光电子/帧",
             "poisson": True})
    return scenes


def _spot(rr, cc, amp):
    t = np.zeros((ROWS, COLS))
    t[rr, cc] = amp
    return t


def main():
    ensure_dirs()
    rng = np.random.default_rng(SEED)

    # 通道非理想因素(全局基础失配,仿真假设)
    ch_offset = rng.uniform(-DEFAULT_CH_OFFSET, DEFAULT_CH_OFFSET, NUM_CHANNELS)
    ch_gain = 1.0 + rng.uniform(-DEFAULT_CH_GAIN_SPREAD, DEFAULT_CH_GAIN_SPREAD, NUM_CHANNELS)
    ch_offset[0] = 0.0     # 保持通道0干净,便于人工核对
    ch_gain[0] = 1.0

    # 标定表:dark = 基线+offset 的期望读数;gain_q = 1/gain 的 Q4.8 定点
    dark = np.clip(np.round(BASELINE + ch_offset), 0, ADC_MAX).astype(np.uint16)
    gain_q = np.clip(np.round(GAIN_ONE / ch_gain), 1, 65535).astype(np.uint16)

    identity = np.arange(NUM_CHANNELS, dtype=np.uint8)

    (CALIB_DIR / "dark.hex").write_text(
        "".join(f"{v:0{CH_W // 4}x}\n" for v in dark))
    (CALIB_DIR / "gain.hex").write_text(
        "".join(f"{v:04x}\n" for v in gain_q))
    (CALIB_DIR / "channel_map.hex").write_text(
        "".join(f"{v:02x}\n" for v in identity))

    scenes = build_scenes()
    print(f"{'dataset':<24}{'frames':>7}{'raw ADC range':>18}   desc")
    for name, (truth, meta) in scenes.items():
        sigma = meta.get("sigma", NOISE_SIGMA)
        offset_spread = meta.get("offset_spread")
        gain_spread = meta.get("gain_spread")
        off = (rng.uniform(-offset_spread, offset_spread, NUM_CHANNELS)
               if offset_spread else ch_offset)
        gn = (1.0 + rng.uniform(-gain_spread, gain_spread, NUM_CHANNELS)
              if gain_spread else ch_gain)

        truth_list = truth if meta.get("per_frame") else [truth] * FRAMES_PER_DATASET
        assert len(truth_list) == FRAMES_PER_DATASET, name

        raw = np.zeros((FRAMES_PER_DATASET, NUM_CHANNELS), dtype=np.uint16)
        ds_num = int(name.split("_")[0])
        for f, tmap in enumerate(truth_list):
            signal = tmap.reshape(-1).astype(float)
            if meta.get("poisson"):
                # ---- 随机光电流物理模型(仿真) ----
                # 光子到达计数 ~ Poisson(λ):散粒噪声 = 计数固有方差,非加性高斯。
                # 每数据集每帧独立派生种子:全局可复现,数据集间统计独立。
                prng = np.random.default_rng(SEED + 1000 * ds_num + f)
                n_sig = prng.poisson(np.clip(signal, 0, None))       # 信号光电子
                n_dark = prng.poisson(DARK_PHOTONS, NUM_CHANNELS)    # 暗计数
                ideal = BASELINE + off + gn * (LSB_PER_PHOTON * (n_sig + n_dark))
                adc = ideal + prng.normal(0, PHOTON_READ_NOISE_LSB, NUM_CHANNELS)
            else:
                ideal = BASELINE + off + gn * signal          # 模拟前端输出
                noise = rng.normal(0, sigma, NUM_CHANNELS)
                adc = ideal + noise
                # 反极性场景:信号使 ADC 读数下降(编码已在 truth 为正,此处翻转信号方向)
                if meta.get("polarity", 1) == -1:
                    adc = adc - 2 * gn * signal
                spike = rng.random(NUM_CHANNELS) < SPIKE_PROB
                adc[spike] += SPIKE_AMP
                # 坏点:固定输出满量程
                for dc in meta.get("dead_channels", []):
                    adc[dc] = ADC_MAX
            raw[f] = np.clip(np.round(adc), 0, ADC_MAX).astype(np.uint16)

        stem = name  # e.g. 01_dark
        _frames_csv(stem, raw)
        if meta.get("per_frame"):
            truth_all = np.stack(truth_list)
            np.savetxt(RAW_DIR / f"{stem}_truth.csv", truth_all.reshape(-1, COLS),
                       fmt="%.2f", delimiter=",", comments="")
        elif meta.get("poisson"):
            lines = [f"# ground-truth photon rate (photo-electrons per frame, "
                     f"sim assumption) name={stem}"]
            lines.append(",".join(f"c{c}" for c in range(COLS)))
            for rr in range(ROWS):
                lines.append(",".join(f"{truth[rr, cc]:.2f}" for cc in range(COLS)))
            (RAW_DIR / f"{stem}_truth.csv").write_text("\n".join(lines) + "\n")
        else:
            _truth_csv(stem, truth)
        meta_out = {"name": stem, **meta, "sigma": sigma,
                    "frames": FRAMES_PER_DATASET, "num_channels": NUM_CHANNELS,
                    "rows": ROWS, "cols": COLS,
                    "note": "simulation assumptions, NOT measured THz detector data"}
        if meta.get("poisson"):
            meta_out["photon_model"] = {
                "arrival": "Poisson (shot noise = intrinsic count variance)",
                "peak_photons_per_frame": float(truth.max()),
                "dark_photons_per_frame": DARK_PHOTONS,
                "lsb_per_photon": LSB_PER_PHOTON,
                "read_noise_lsb": PHOTON_READ_NOISE_LSB}
        (RAW_DIR / f"{stem}_meta.json").write_text(json.dumps(meta_out, indent=1))
        (RTL_IN_DIR / f"{stem}.hex").write_text(
            "".join(f"{v:03x}\n" for v in raw.reshape(-1)))

        print(f"{stem:<24}{FRAMES_PER_DATASET:>7}{str((raw.min(), raw.max())):>18}   {meta['desc']}")

    print("\ncalib: dark.hex / gain.hex / channel_map.hex written to", CALIB_DIR)
    print("channel offset (sim assumption, LSB):",
          np.array2string(ch_offset, precision=1, max_line_width=100))
    print("channel gain   (sim assumption):",
          np.array2string(ch_gain, precision=3, max_line_width=100))
    print(f"gain calib Q{GAIN_FRAC} residual check ch0..7:", gain_q[:8].tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
