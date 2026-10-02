#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_uart_backend.py — UARTFrameSource / _FrameParser 单元测试(不依赖真实串口)

覆盖:
  1. CRC16-CCITT 已知向量(与 ARM 端 C 实现一致性由联调复核)
  2. 正常帧解析:单帧、连续多帧、frame_id 与像素逐位一致
  3. 分片喂入(1 字节一片)——状态机跨 chunk 断点重组
  4. 帧前垃圾字节重同步(resync_count 统计)
  5. CRC 损坏帧被拒绝并继续同步(crc_errors 统计)
  6. PIXEL_COUNT 不符被拒绝
  7. UARTFrameSource 无串口环境降级不崩溃(GUI 兼容)
  8. 与黄金参考的往返:用 DatasetFrameSource 的一帧构造字节流 → 解析还原

运行: python tools/test_uart_backend.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from frame_sources import (UARTFrameSource, _FrameParser, crc16_ccitt,
                           make_source)
import thz_config as C


def build_frame(frame_id: int, pixels: np.ndarray) -> bytes:
    """按协议组帧(与 ARM 端 main.c 完全一致的格式)。"""
    n = len(pixels)
    body = b"\xA5\x5A" + int(frame_id).to_bytes(2, "little") \
        + int(n).to_bytes(2, "little") \
        + b"".join(int(v).to_bytes(2, "little") for v in pixels)
    return body + crc16_ccitt(body).to_bytes(2, "little")


def expect(cond, msg, results):
    results.append((bool(cond), msg))


def main() -> int:
    results = []

    # 1. CRC 已知向量("123456789" -> 0x29B1)
    expect(crc16_ccitt(b"123456789") == 0x29B1, "CRC16-CCITT check value", results)

    # 2. 正常帧
    p = _FrameParser(32)
    px = np.arange(32, dtype=np.uint16) * 7 + 3
    frames = p.feed(build_frame(42, px))
    expect(len(frames) == 1 and frames[0][0] == 42
           and np.array_equal(frames[0][1], px), "single frame roundtrip", results)
    expect(p.frames_ok == 1 and p.crc_errors == 0 and p.resync_count == 0,
           "single frame stats", results)

    # 3. 连续多帧 + 分片喂入
    p = _FrameParser(32)
    ids = [0, 1, 2, 0xFFFF]  # 含 16-bit 回绕值
    stream = b"".join(build_frame(i, (np.arange(32) + i).astype(np.uint16))
                      for i in ids)
    got = []
    for b in stream:                          # 逐字节喂入
        got += p.feed(bytes([b]))
    expect([f[0] for f in got] == ids, "4 frames byte-by-byte, ids ok", results)
    expect(all(np.array_equal(f[1], (np.arange(32) + i).astype(np.uint16))
               for f, i in zip(got, ids)), "byte-by-byte pixel values", results)

    # 4. 前导垃圾重同步
    p = _FrameParser(32)
    frames = p.feed(b"\x00\x11garbage" + build_frame(5, px))
    expect(len(frames) == 1 and frames[0][0] == 5 and p.resync_count >= 1,
           "garbage prefix resync", results)

    # 5. CRC 损坏
    p = _FrameParser(32)
    good = build_frame(9, px)
    bad = bytearray(good)
    bad[10] ^= 0xFF
    frames = p.feed(bytes(bad) + good)
    expect(len(frames) == 1 and frames[0][0] == 9 and p.crc_errors == 1,
           "corrupted frame rejected, next frame recovered", results)

    # 6. PIXEL_COUNT 不符
    p = _FrameParser(32)
    wrong = b"\xA5\x5A" + (1).to_bytes(2, "little") + (31).to_bytes(2, "little") \
        + b"\x00" * 62 + (0).to_bytes(2, "little")
    frames = p.feed(wrong + build_frame(3, px))
    expect(len(frames) == 1 and frames[0][0] == 3, "wrong count dropped", results)

    # 7. 无串口降级(强制指向不存在的口,不抛异常)
    src = UARTFrameSource(port="COM_NOT_EXIST_99")
    expect(src.get_next_frame() is None, "degraded source returns None", results)
    expect("error" in src.info, "degraded source reports error in info", results)
    src.close()

    # 8. 与黄金参考往返:黄金帧 -> 字节流 -> 解析 == 黄金值
    ref = (C.REF_DIR / "12_moving_target_ref.csv").read_text().splitlines()
    golden = np.array([int(float(x))
                       for x in ref[1].split(",")], dtype=np.uint16)
    p = _FrameParser(32)
    frames = p.feed(build_frame(0, golden))
    expect(np.array_equal(frames[0][1], golden),
           "golden reference frame roundtrip", results)

    # 9. make_source 工厂兼容(GUI 路径)
    s = make_source("uart", port="COM_NOT_EXIST_99")
    expect(isinstance(s, UARTFrameSource), "make_source('uart') factory", results)
    s.close()

    # ---- 汇总 ----
    fails = [m for ok, m in results if not ok]
    for ok, m in results:
        print(f"{'PASS' if ok else 'FAIL'}: {m}")
    print(f"\n==== test_uart_backend: {len(results) - len(fails)}/{len(results)} PASS ====")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
