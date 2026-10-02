#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thz_config.py — 中央参数配置(全工程唯一来源)

所有 Python 工具(generate_thz_dataset / reference_model / compare_rtl_reference /
visualize_thz_image / run_all)以及 RTL testbench 的默认参数都从这里读取。
RTL 侧参数在 thz_imaging_top.sv / tb_thz_imaging_top.sv 中保持数值一致,
scripts/run_all.py 会自动核对两侧参数。

注意:以下所有信号参数均为【仿真假设】,不代表真实太赫兹探测器实测参数。
"""

from pathlib import Path

# ---------------------------------------------------------------- 阵列几何
NUM_CHANNELS = 32          # 通道数(未来对应 32 路探测器)
ROWS = 4                   # PoC 默认空间排布 4 x 8 = 32(仿真假设,可参数化)
COLS = 8

# ---------------------------------------------------------------- ADC / 像素
ADC_WIDTH = 12             # ADC 位宽(假设)
ADC_MAX = 4095
BASELINE = 2048            # 零偏 TIA + VREF 基准对应的 ADC 中点码(假设)
PIXEL_WIDTH = 16           # 校正后像素位宽(无符号)
PIXEL_MAX = 65535

# ---------------------------------------------------------------- 增益标定(Q4.8)
GAIN_FRAC = 8              # 定点小数位数: gain_q = round(gain * 2^8)
GAIN_ONE = 1 << GAIN_FRAC  # = 256, 表示增益 1.0

# ---------------------------------------------------------------- 帧与时域平均
AVG_FRAMES = 4             # 时域平均帧数(2 的幂);1 = 关闭
AVG_SHIFT = 2              # log2(AVG_FRAMES)
FRAMES_PER_DATASET = 4     # 每个数据集生成的原始帧数

# ---------------------------------------------------------------- 极性
POLARITY = +1              # +1: signal = raw - dark ; -1: signal = dark - raw
                           # 由探测器/前端接法决定,不得写死

# ---------------------------------------------------------------- 数据集非理想因素(仿真假设)
NOISE_SIGMA = 8            # 每帧每通道高斯噪声 sigma (ADC LSB)
LOW_SNR_SIGMA = 25         # low_snr 数据集噪声 sigma
SPIKE_PROB = 0.005         # 偶发尖峰概率(每采样点)
SPIKE_AMP = 800            # 尖峰幅度 (ADC LSB)
DEFAULT_CH_OFFSET = 60     # 通道 offset mismatch 幅度上限 (ADC LSB)
DEFAULT_CH_GAIN_SPREAD = 0.25  # 通道 gain mismatch 相对幅度 (±25%)

# ------------------------------------------------- 随机光电流模型参数(仿真假设,非实测)
# 物理原理真实:光子到达为 Poisson 过程,散粒噪声方差 = 平均计数;
# 以下数值(K、暗计数、读噪)均为量级假设,需真实器件实测后替换。
DARK_PHOTONS = 20          # 暗计数:每帧每通道平均暗光电子数(假设)
LSB_PER_PHOTON = 0.8       # TIA+ADC 链路增益:每光电子对应的 ADC LSB(假设)
PHOTON_READ_NOISE_LSB = 4.0  # TIA/ADC 读出噪声(1σ,ADC LSB,假设)
SNR_SWEEP_PHOTONS = [10, 30, 100, 300, 1000]  # sweep: 峰值平均光电子数/帧

# ---------------------------------------------------------------- 可复现性
SEED = 20260916            # 全局固定随机种子,保证数据集可复现

# ---------------------------------------------------------------- 目录结构
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CALIB_DIR = DATA_DIR / "calib"
RTL_IN_DIR = DATA_DIR / "rtl_in"
RESULTS_DIR = ROOT / "results"
REF_DIR = RESULTS_DIR / "reference"
RTL_OUT_DIR = RESULTS_DIR / "rtl"
IMAGES_DIR = RESULTS_DIR / "images"
FIGURES_DIR = RESULTS_DIR / "figures"
LOGS_DIR = ROOT / "logs"
SIM_DIR = ROOT / "sim"
RTL_DIR = ROOT / "rtl"
TB_DIR = ROOT / "tb"

# 数据集清单: (编号, 名称, 说明)
DATASETS = [
    ("01", "dark",            "无目标:仅 offset/gain 失配 + 噪声"),
    ("02", "single_point",    "单热点 (1,3)"),
    ("03", "two_points",      "双热点 (0,1)/(3,6)"),
    ("04", "horizontal_bar",  "水平条带 rows 1-2"),
    ("05", "vertical_bar",    "垂直条带 cols 3-4"),
    ("06", "cross",           "十字目标 row1 + col4"),
    ("07", "gaussian_blob",   "二维高斯斑"),
    ("08", "low_snr",         "弱信号(40 LSB)+ 强噪声(sigma=25)"),
    ("09", "dead_pixel",      "含坏点:通道5 停在 4095"),
    ("10", "gain_mismatch",   "强通道增益不一致(±45%)"),
    ("11", "offset_mismatch", "强通道偏置不一致(±120 LSB)"),
    ("12", "moving_target",   "多帧移动热点"),
    ("13", "inverted_polarity", "反极性系统验证(polarity=-1),需 polneg 仿真变体"),
    ("14", "random_photocurrent", "随机光电流: Poisson 高斯斑, 峰值300光电子/帧"),
]
# SNR sweep:双热点场景,峰值光电子数从 10 到 1000(Poisson 散粒噪声主导)
for _i, _n in enumerate(SNR_SWEEP_PHOTONS):
    DATASETS.append((f"{15 + _i}", f"snr_N{_n}",
                     f"SNR sweep: Poisson 双热点, 峰值{_n}光电子/帧"))
POLARITY_DATASET = "13"    # 该数据集使用 POLARITY=-1 的 RTL 变体与参考模型


def wsl_path(p) -> str:
    """把 Windows 路径转换为 WSL 内 /mnt/... 路径。"""
    p = Path(p).resolve()
    drive = p.drive.rstrip(":").lower()          # "e"
    rest = p.as_posix()[len(p.drive):]           # "/Research/image system/..."
    return f"/mnt/{drive}{rest}"


def ensure_dirs() -> None:
    for d in (RAW_DIR, CALIB_DIR, RTL_IN_DIR, REF_DIR, RTL_OUT_DIR,
              IMAGES_DIR, FIGURES_DIR, LOGS_DIR, SIM_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"PoC root      : {ROOT}")
    print(f"channels      : {NUM_CHANNELS}  ({ROWS}x{COLS})")
    print(f"ADC           : {ADC_WIDTH}-bit, baseline {BASELINE}")
    print(f"pixel         : {PIXEL_WIDTH}-bit unsigned, gain Q{GAIN_FRAC}")
    print(f"avg frames    : {AVG_FRAMES}, frames/dataset {FRAMES_PER_DATASET}")
    print(f"polarity      : {POLARITY:+d}")
    print(f"seed          : {SEED}")
