#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frame_sources.py — 统一帧数据源接口(Visualization v1)

所有进入可视化工作台的数据都经过 FrameSource 接口,GUI 不依赖具体后端:

    FrameSource
        get_next_frame() -> Frame | None
        reset()          # 回到第一帧
        close()
        datasets()       # 可选择的数据集/输出列表

后端:
    A. DatasetFrameSource — 现有 synthetic dataset(data/raw/*.csv),
       processed 用 reference_model 黄金模型(与 RTL 位级一致);
    B. RTLFileFrameSource — Icarus Verilog 实际仿真输出(results/rtl/*_out.txt);
    C. UARTFrameSource   — 未来 UART/USB 直连 FPGA 的骨架(协议见
       docs/UART_FRAME_PROTOCOL.md),当前未接真实串口,不伪造数据。

每帧至少返回:frame_id, timestamp, raw_channels[32], processed_channels[32],
raw_image[4][8], processed_image[4][8]。
通道→像素映射使用现有 data/calib/channel_map.hex(thz_config 的 ROWS/COLS),
本模块提供正/逆映射函数,GUI 悬停换算必须经此函数,禁止另写一套。
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

import thz_config as C


# ---------------------------------------------------------------- 映射(唯一实现)

def load_channel_map() -> np.ndarray:
    """channel -> 线性像素索引(来自现有标定表)。"""
    cmap = np.array([int(x, 16) for x in
                     (C.CALIB_DIR / "channel_map.hex").read_text().split()],
                    dtype=np.int32)
    assert cmap.size == C.NUM_CHANNELS
    return cmap


def channels_to_images(channels: np.ndarray, cmap: Optional[np.ndarray] = None):
    """32 通道向量 -> (rows, cols) 图像。RTL 语义:pidx = map[ch] 处写入该通道值。"""
    if cmap is None:
        cmap = load_channel_map()
    img = np.zeros((C.ROWS * C.COLS,), dtype=channels.dtype)
    img[np.asarray(cmap)] = channels          # img[map[ch]] = ch
    return img.reshape(C.ROWS, C.COLS)


def pixel_to_channel(row: int, col: int, cmap: Optional[np.ndarray] = None) -> int:
    """(row,col) -> 通道号(逆映射,与 channels_to_images 严格互逆)。"""
    if cmap is None:
        cmap = load_channel_map()
    inv = np.argsort(cmap)                    # inv[pidx] = ch
    return int(inv[row * C.COLS + col])


# ---------------------------------------------------------------- 帧结构

@dataclass
class Frame:
    frame_id: int
    timestamp: float
    raw_channels: Optional[np.ndarray]        # (32,) uint16 | None
    processed_channels: np.ndarray            # (32,) uint16
    raw_image: Optional[np.ndarray]           # (ROWS,COLS) | None
    processed_image: np.ndarray               # (ROWS,COLS)
    meta: Dict = field(default_factory=dict)


class FrameSource:
    name = "abstract"
    source_label = "UNKNOWN SOURCE"
    processed_label = "Processed"

    def get_next_frame(self) -> Optional[Frame]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def datasets(self) -> List[str]:
        return []

    @property
    def info(self) -> Dict:
        return {"name": self.name, "source_label": self.source_label,
                "processed_label": self.processed_label}


def _list_dataset_stems() -> List[str]:
    stems = [f"{num}_{name}" for num, name, _ in C.DATASETS]
    have = {p.stem for p in C.RAW_DIR.glob("*.csv") if not p.stem.endswith("_truth")}
    return [s for s in stems if s in have] or sorted(have)


def resolve_dataset(name: Optional[str]) -> str:
    """'moving_target' / '12_moving_target' / '12' -> 实际数据集 stem。"""
    stems = _list_dataset_stems()
    if name is None:
        return stems[0]
    if name in stems:
        return name
    matches = [s for s in stems if s.endswith("_" + name) or s.split("_", 1)[0] == name]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"dataset {name!r} not found; available: {stems}")


# ---------------------------------------------------------------- A. 数据集源

class DatasetFrameSource(FrameSource):
    """synthetic dataset 播放源:raw 逐帧,processed 为黄金模型运行平均。

    processed_display[i] = (Σ_{f=0..i} processed_f) >> AVG_SHIFT(与 RTL 同样在
    增益后累加、同样截断);i = AVG_FRAMES-1 时即最终黄金输出帧。
    """
    name = "dataset"
    source_label = "SOURCE: SYNTHETIC — Synthetic THz Detector Data (not measured)"

    def __init__(self, dataset: str):
        self.dataset = resolve_dataset(dataset)
        self.cmap = load_channel_map()
        self._load()
        self._cursor = 0
        self._served = 0

    def _load(self):
        df = np.genfromtxt(C.RAW_DIR / f"{self.dataset}.csv", delimiter=",",
                           skip_header=1, dtype=np.int64)
        f = int(df[:, 0].max()) + 1
        raw = np.zeros((f, C.NUM_CHANNELS), dtype=np.int64)
        raw[df[:, 0], df[:, 1]] = df[:, 2]

        from reference_model import load_calib, process_frames
        dark, gain_q, cmap = load_calib()
        pol = -1 if self.dataset.startswith(C.POLARITY_DATASET + "_") else C.POLARITY
        self.polarity = pol
        # 每帧单独校正(avg_frames=1 关闭平均),供运行平均播放
        self.per_frame_processed = process_frames(
            raw, dark, gain_q, polarity=pol, avg_frames=1, cmap=None)  # (F,32)
        self.final_processed = process_frames(
            raw, dark, gain_q, polarity=pol, avg_frames=C.AVG_FRAMES, cmap=None)[0]
        self.raw = raw
        self.n_frames = f
        self.final_range = (int(self.final_processed.min()),
                            int(self.final_processed.max()))

        # ground truth(实际读取文件)
        self.truth_per_frame = None
        tp = C.RAW_DIR / f"{self.dataset}_truth.csv"
        if tp.exists():
            meta_p = C.RAW_DIR / f"{self.dataset}_meta.json"
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            t = np.genfromtxt(tp, delimiter=",", dtype=float)
            if meta.get("per_frame"):
                self.truth_per_frame = t.reshape(-1, C.ROWS, C.COLS)
            else:
                t = t[1:]                                 # 跳过 "c0,c1,.." 列头行
                self.truth_per_frame = np.broadcast_to(
                    t.reshape(C.ROWS, C.COLS), (f, C.ROWS, C.COLS))
        # 保存的黄金参考(实际读取文件,用于 Compare 面板误差)
        rp = C.REF_DIR / f"{self.dataset}_ref.csv"
        self.saved_reference = (np.genfromtxt(rp, delimiter=",", skip_header=1,
                                              dtype=np.int64).reshape(-1, C.NUM_CHANNELS)[0]
                                if rp.exists() else None)
        self.processed_label = "Python Reference (golden model, bit-exact w/ RTL)"

    # -- FrameSource --
    def get_next_frame(self) -> Optional[Frame]:
        if self._cursor >= self.n_frames:
            self._cursor = 0                       # synthetic 循环播放
        i = self._cursor
        done = np.cumsum(self.per_frame_processed[: i + 1], axis=0)[-1]
        if i + 1 >= C.AVG_FRAMES:
            processed = self.final_processed
        else:
            processed = done >> C.AVG_SHIFT        # 与 RTL 同样的截断(部分平均)
        raw_ch = self.raw[i].astype(np.uint16)
        proc_ch = processed.astype(np.uint16)
        truth = (self.truth_per_frame[min(i, len(self.truth_per_frame) - 1)]
                 if self.truth_per_frame is not None else None)
        self._cursor += 1
        self._served += 1
        return Frame(
            frame_id=self._served - 1,
            timestamp=time.time(),
            raw_channels=raw_ch,
            processed_channels=proc_ch,
            raw_image=channels_to_images(raw_ch, self.cmap),
            processed_image=channels_to_images(proc_ch, self.cmap),
            meta={"dataset": self.dataset, "frame_index": i,
                  "n_frames": self.n_frames, "polarity": self.polarity,
                  "truth_image": truth, "reference_channels": self.saved_reference,
                  "averaging_complete": (i + 1) >= C.AVG_FRAMES,
                  "processed_final_range": self.final_range},
        )

    def reset(self):
        self._cursor = 0
        self._served = 0

    def datasets(self):
        return _list_dataset_stems()

    @property
    def info(self):
        d = super().info
        d.update(dataset=self.dataset, n_frames=self.n_frames)
        return d


# ---------------------------------------------------------------- B. RTL 输出源

class RTLFileFrameSource(FrameSource):
    """Icarus Verilog 仿真输出源:直接读 results/rtl/<name>_out.txt。

    RTL 每数据集输出 1 帧(4 帧平均后);frame_id 持续递增循环播放。
    raw 数据不在 RTL 输出文件中 → raw_channels/raw_image 为 None(界面标 N/A)。
    """
    name = "rtl"
    source_label = "SOURCE: RTL SIMULATION (Icarus Verilog output file)"

    def __init__(self, dataset: str, loop: bool = True):
        self.loop = loop
        self.cmap = load_channel_map()
        self.dataset = self._resolve(dataset)
        self._load()
        self._served = 0

    def _resolve(self, dataset):
        want = resolve_dataset(dataset)
        if (C.RTL_OUT_DIR / f"{want}_out.txt").exists():
            return want
        # 允许直接给输出文件名(如 02_single_point_out)
        name = dataset.removesuffix("_out").removesuffix(".txt")
        if (C.RTL_OUT_DIR / f"{name}_out.txt").exists():
            return name
        raise FileNotFoundError(
            f"RTL output for {dataset!r} not found; available: "
            f"{sorted(x.stem.replace('_out.txt','') for x in C.RTL_OUT_DIR.glob('*_out.txt'))}"
            f"\nrun tools/run_all.py first to generate RTL outputs")

    def _load(self):
        p = C.RTL_OUT_DIR / f"{self.dataset}_out.txt"
        if not p.exists():
            avail = sorted(x.stem for x in C.RTL_OUT_DIR.glob("*_out.txt"))
            raise FileNotFoundError(
                f"RTL output not found: {p}\navailable: {avail}\n"
                f"run tools/run_all.py first to generate RTL outputs")
        vals = np.zeros(C.NUM_CHANNELS, dtype=np.int64)
        order = []
        for ln, line in enumerate(p.read_text().splitlines()):
            if not line.strip():
                continue
            r, c, v = (int(x) for x in line.split())
            order.append((r, c))
            vals[r * C.COLS + c] = v
        assert order == [(i // C.COLS, i % C.COLS) for i in range(len(order))], \
            "RTL output is not in row-major order"
        self.processed_channels = vals
        rp = C.REF_DIR / f"{self.dataset}_ref.csv"
        self.saved_reference = (np.genfromtxt(rp, delimiter=",", skip_header=1,
                                              dtype=np.int64).reshape(-1, C.NUM_CHANNELS)[0]
                                if rp.exists() else None)
        self.processed_label = "FPGA Processed Frame (RTL simulation output)"

    def get_next_frame(self) -> Optional[Frame]:
        proc = self.processed_channels
        fid = self._served
        self._served += 1
        return Frame(
            frame_id=fid,
            timestamp=time.time(),
            raw_channels=None,
            processed_channels=proc.astype(np.uint16),
            raw_image=None,
            processed_image=channels_to_images(proc, self.cmap),
            meta={"dataset": self.dataset, "frame_index": 0, "n_frames": 1,
                  "truth_image": None,
                  "reference_channels": self.saved_reference,
                  "averaging_complete": True,
                  "processed_final_range": (int(proc.min()), int(proc.max()))},
        )

    def reset(self):
        self._served = 0

    def datasets(self):
        return sorted(x.stem.replace("_out.txt", "")
                      for x in C.RTL_OUT_DIR.glob("*_out.txt"))


# ---------------------------------------------------------------- C. UART 源

def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC16-CCITT(多项式 0x1021,初值 0xFFFF)——与协议文档及 ARM 端一致。"""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class _FrameParser:
    """协议字节流解析器(docs/UART_FRAME_PROTOCOL.md)。

    A5 5A | FRAME_ID u16LE | PIXEL_COUNT u16LE | N×u16LE | CRC16 u16LE
    纯字节流操作,不依赖串口,便于单元测试(逐字节喂入/噪声/CRC 损坏)。
    """

    HEADER = b"\xA5\x5A"

    def __init__(self, num_channels: int = 32):
        self.n = num_channels
        self.frame_len = 6 + 2 * num_channels + 2
        self.buf = bytearray()
        self.frames_ok = 0
        self.crc_errors = 0
        self.format_errors = 0
        self.resync_count = 0

    def feed(self, chunk: bytes):
        """喂入任意长度字节,返回完成帧列表 [(frame_id, pixels ndarray), ...]。"""
        self.buf.extend(chunk)
        done = []
        while True:
            i = self.buf.find(self.HEADER)
            if i < 0:
                # 无帧头:只保留最后 1 字节(可能是被截断的 0xA5),其余丢弃
                if len(self.buf) > 1:
                    del self.buf[:-1]
                    self.resync_count += 1
                break
            if i > 0:                       # 帧头前有垃圾,丢弃重同步
                del self.buf[:i]
                self.resync_count += 1
            if len(self.buf) < 6:
                break
            cnt = int.from_bytes(self.buf[4:6], "little")
            if cnt != self.n:
                self.format_errors += 1
                del self.buf[:1]
                continue
            if len(self.buf) < self.frame_len:
                break                       # 半帧:等待更多字节
            frame = bytes(self.buf[:self.frame_len])
            rx_crc = int.from_bytes(frame[-2:], "little")
            if crc16_ccitt(frame[:-2]) != rx_crc:
                self.crc_errors += 1
                del self.buf[:1]            # 丢弃该 A5,向后重同步
                continue
            self.frames_ok += 1
            del self.buf[:self.frame_len]
            fid = int.from_bytes(frame[2:4], "little")
            px = np.frombuffer(frame[6:6 + 2 * self.n], dtype="<u2").copy()
            done.append((fid, px))
        return done

    def flush(self):
        """结束捕获时统计未完成的协议帧;普通横幅/垃圾不算格式错误。"""
        if self.buf.startswith(self.HEADER) or self.buf == b"\xA5":
            self.format_errors += 1
        self.buf.clear()


class UARTFrameSource(FrameSource):
    """AX7020 真实串口源(pyserial),协议与 docs/UART_FRAME_PROTOCOL.md 一致。

    第一阶段数据链:PL 管线 -> AXI-Lite -> ARM 组帧 -> PS UART1(板上
    USB-UART)-> PC COM 口。找不到/打不开串口时优雅降级为占位行为
    (get_next_frame 返回 None,info 中说明原因),GUI 无需修改。
    端口选择:显式 port 参数 > 环境变量 THZ_UART_PORT > 唯一 COM 口自动识别。
    """
    name = "uart"
    source_label = "SOURCE: UART FPGA (AX7020 hardware)"
    processed_label = "FPGA Processed Frame (UART)"

    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 num_channels: int = C.NUM_CHANNELS):
        self.port = port or os.environ.get("THZ_UART_PORT") or self._autodetect()
        self.baud = baud
        self.num_channels = num_channels
        self.cmap = load_channel_map()
        self.parser = _FrameParser(num_channels)
        self._ser = None
        self._error = None
        self._open()

    @staticmethod
    def _autodetect() -> Optional[str]:
        try:
            import serial.tools.list_ports as lp
            ports = [p.device for p in lp.comports()]
        except Exception:
            return None
        return ports[0] if len(ports) == 1 else None

    def _open(self):
        try:
            import serial
            if not self.port:
                self._error = ("UART: no serial port specified/found "
                               "(set THZ_UART_PORT or pass port=...)")
                return
            self._ser = serial.Serial(self.port, self.baud, timeout=0)
        except Exception as e:                  # ImportError / SerialException
            self._ser = None
            self._error = f"UART open failed: {e}"

    def get_next_frame(self) -> Optional[Frame]:
        if self._ser is None:
            return None
        try:
            n = max(1, self._ser.in_waiting)
            chunk = self._ser.read(n)
        except Exception as e:
            self._error = f"UART read failed: {e}"
            return None
        frames = self.parser.feed(chunk)
        if not frames:
            return None
        fid, px = frames[-1]                    # 只取最新帧(丢弃式语义)
        return Frame(
            frame_id=int(fid),
            timestamp=time.time(),
            raw_channels=None,
            processed_channels=px.astype(np.uint16),
            raw_image=None,
            processed_image=channels_to_images(px, self.cmap),
            meta={"source": "uart", "pl_frame_id": int(fid),
                  "frame_index": int(fid), "n_frames": 1,
                  "truth_image": None, "reference_channels": None,
                  "averaging_complete": True,
                  "frames_ok": self.parser.frames_ok,
                  "crc_errors": self.parser.crc_errors,
                  "format_errors": self.parser.format_errors,
                  "resync_count": self.parser.resync_count,
                  "processed_final_range": (int(px.min()), int(px.max()))},
        )

    def reset(self):
        self.parser = _FrameParser(self.num_channels)

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    @property
    def info(self):
        d = super().info
        d.update(port=self.port, baud=self.baud,
                 frames_ok=self.parser.frames_ok,
                 crc_errors=self.parser.crc_errors,
                 format_errors=self.parser.format_errors)
        if self._error:
            d["error"] = self._error
        return d


def make_source(source: str, dataset: Optional[str] = None,
                port: Optional[str] = None) -> FrameSource:
    """工厂:GUI/CLI 统一入口。"""
    if source == "dataset":
        ds = dataset or _list_dataset_stems()[0]
        return DatasetFrameSource(ds)
    if source == "rtl":
        ds = dataset or "02_single_point"
        return RTLFileFrameSource(ds)
    if source == "uart":
        return UARTFrameSource(port=port)
    raise ValueError(f"unknown source: {source!r} (dataset|rtl|uart)")
