#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reference_model.py — Python 黄金参考模型(与 RTL 定点逐位一致)

处理链(与 rtl/thz_imaging_top.sv 严格一致):
    raw ADC
      -> offset 校正:  d = (raw - dark)  若极性 +1;  d = (dark - raw)  若极性 -1
      -> clamp 到 [0, PIXEL_MAX]
      -> gain 校正:    out = (d * gain_q) >> GAIN_FRAC   (Q4.8, 截断取整)
      -> clamp 到 [0, PIXEL_MAX]
      -> 时域平均:     acc[ch] 累加 AVG_FRAMES 帧, 输出 acc >> AVG_SHIFT (截断)
      -> 通道映射:     channel ch -> 线性像素 pidx = channel_map[ch]
                                    row = pidx // COLS, col = pidx % COLS

注意:整型 >> 与 // 在非负数上等价,均为向下取整——与 RTL 逻辑右移一致。
所有参数见 thz_config.py;数据为合成样本,不代表真实探测器测量。
"""

import json
import sys
import numpy as np

import thz_config as C


def load_calib(calib_dir=None):
    calib_dir = calib_dir or C.CALIB_DIR
    dark = np.array([int(x, 16) for x in
                     (calib_dir / "dark.hex").read_text().split()], dtype=np.uint16)
    gain = np.array([int(x, 16) for x in
                     (calib_dir / "gain.hex").read_text().split()], dtype=np.uint16)
    cmap = np.array([int(x, 16) for x in
                     (calib_dir / "channel_map.hex").read_text().split()], dtype=np.uint8)
    assert dark.size == C.NUM_CHANNELS and gain.size == C.NUM_CHANNELS
    assert cmap.size == C.NUM_CHANNELS
    return dark, gain, cmap


def process_frames(raw, dark, gain_q, polarity=1, avg_frames=None, cmap=None):
    """raw: (F, NUM_CHANNELS) uint16 -> 返回 (NUM_CHANNELS,) uint16 平均后校正帧
    与 RTL 位级一致:截断、钳位顺序完全相同。"""
    if avg_frames is None:
        avg_frames = C.AVG_FRAMES
    assert avg_frames & (avg_frames - 1) == 0, "AVG_FRAMES must be power of 2"
    shift = avg_frames.bit_length() - 1
    f = raw.shape[0]
    assert f % avg_frames == 0, "frames must be multiple of AVG_FRAMES"

    out = np.zeros((f, C.NUM_CHANNELS), dtype=np.uint32)
    for i in range(f):
        d = (raw[i].astype(np.int32) - dark.astype(np.int32) if polarity > 0
             else dark.astype(np.int32) - raw[i].astype(np.int32))
        d = np.clip(d, 0, C.PIXEL_MAX)                       # offset 后钳位
        p = (d * gain_q.astype(np.uint32)) >> C.GAIN_FRAC    # Q4.8 乘法 + 截断
        p = np.clip(p, 0, C.PIXEL_MAX)                       # gain 后钳位
        out[i] = p
    groups = f // avg_frames
    out = out.reshape(groups, avg_frames, C.NUM_CHANNELS)
    acc = out.sum(axis=1)                                    # 累加 AVG_FRAMES 帧
    frame = acc >> shift                                     # 截断平均
    frame = np.clip(frame, 0, C.PIXEL_MAX).astype(np.uint16)
    if cmap is not None:
        # RTL: 像素位置 pidx=map[ch] 写入 calibrated[ch] → out[map[ch]] = ch 值
        # 即 out[p] = orig[inv[p]], inv = argsort(map)(恒等映射下两者相同)
        inv = np.argsort(cmap)
        frame = frame[:, inv]                                # 通道 -> 像素线性索引
    return frame                                             # (groups, 32)


def dataset_names():
    names = []
    for p in sorted(C.RAW_DIR.glob("*.csv")):
        if p.stem.endswith("_truth"):
            continue
        names.append(p.stem)
    return names


def run(polarity=1, only=None):
    C.ensure_dirs()
    dark, gain_q, cmap = load_calib()
    rows = []
    for name in dataset_names():
        if only and name not in only:
            continue
        df = np.genfromtxt(C.RAW_DIR / f"{name}.csv", delimiter=",", skip_header=1,
                           dtype=np.uint16)
        frames = df[:, 0]
        chs = df[:, 1]
        adc = df[:, 2]
        f = frames.max() + 1
        raw = np.zeros((f, C.NUM_CHANNELS), dtype=np.uint16)
        raw[frames, chs] = adc

        pol = -1 if name.startswith(C.POLARITY_DATASET + "_") else polarity
        frame = process_frames(raw, dark, gain_q, polarity=pol, cmap=cmap)
        np.savetxt(C.REF_DIR / f"{name}_ref.csv", frame, fmt="%d", delimiter=",",
                   header="processed frame(s) row-major, groups x 32", comments="# ")
        # 第一原始帧也保存为 4x8(仅可视化用)
        raw0 = raw[0][np.argsort(cmap)].reshape(C.ROWS, C.COLS)
        np.savetxt(C.REF_DIR / f"{name}_raw_frame0.csv", raw0, fmt="%d", delimiter=",")
        rows.append({"dataset": name, "polarity": pol,
                     "out_groups": int(frame.shape[0])})
        print(f"reference: {name:<24} pol={pol:+d} groups={frame.shape[0]} -> {name}_ref.csv")
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--polarity", type=int, default=C.POLARITY, choices=(1, -1))
    ap.add_argument("--only", nargs="*", default=None, help="dataset 名单,缺省全部")
    a = ap.parse_args()
    run(polarity=a.polarity, only=a.only)
