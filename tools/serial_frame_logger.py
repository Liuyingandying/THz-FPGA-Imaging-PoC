#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serial_frame_logger.py — 板级联调取证工具(§六 证据 4/5 采集)

打开 COM 口,按 docs/UART_FRAME_PROTOCOL.md 解析 AX7020 发来的 72 字节帧,
实时打印统计 + 帧内容,并与 Python 黄金参考逐位比对。同时保存:
  fpga_ax7020/logs/uart_capture_<时间戳>.bin    原始字节(取证)
  fpga_ax7020/logs/uart_capture_<时间戳>.csv    解析后帧(取证)

用法:
  python tools/serial_frame_logger.py --port <PORT> --seconds 10 --verify
  python tools/serial_frame_logger.py --list           # 列出可用串口
"""
import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from frame_sources import _FrameParser, crc16_ccitt
import thz_config as C

GOLDEN = np.array([299, 0, 0, 5, 5, 1, 2, 8,
                   12, 1, 303, 6, 7, 3, 6, 2,
                   0, 3, 5, 6, 299, 1, 3, 3,
                   0, 1, 0, 7, 0, 2, 297, 5], dtype=np.uint16)


def list_ports():
    import serial.tools.list_ports as lp
    for p in lp.comports():
        print(f"  {p.device:10s} {p.description}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--verify", action="store_true",
                    help="与黄金参考逐位比对(12_moving_target)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_ports()
        return 0

    import serial
    port = args.port
    if port is None:
        try:
            import serial.tools.list_ports as lp
            ports = [p.device for p in lp.comports()]
        except Exception:
            ports = []
        if len(ports) == 1:
            port = ports[0]
        else:
            print("ERROR: 未指定 --port,可用串口:")
            list_ports()
            return 2

    log_dir = C.ROOT / "fpga_ax7020" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bin_path = log_dir / f"uart_capture_{ts}.bin"
    csv_path = log_dir / f"uart_capture_{ts}.csv"

    ser = serial.Serial(port, args.baud, timeout=0.2)
    print(f">> opened {port} @ {args.baud}, capturing {args.seconds}s ...")

    parser = _FrameParser(32)
    raw_all = bytearray()
    rows = []
    t_end = time.time() + args.seconds
    mismatches = 0
    verified_frames = 0

    while time.time() < t_end:
        chunk = ser.read(256)
        if not chunk:
            continue
        raw_all.extend(chunk)
        for fid, px in parser.feed(chunk):
            hotspot_ok = (px[0] > 200 and px[10] > 200
                          and px[20] > 200 and px[30] > 200)
            line_ok = ""
            if args.verify:
                ok = np.array_equal(px, GOLDEN)
                if not ok:
                    mismatches += 1
                else:
                    verified_frames += 1
                line_ok = "GOLD-MATCH" if ok else "GOLD-DIFF"
            rows.append([fid] + [int(v) for v in px] + [line_ok])
            print(f"frame_id={fid:5d} hotspots="
                  f"[{px[0]},{px[10]},{px[20]},{px[30]}] {line_ok}")
    ser.close()

    bin_path.write_bytes(raw_all)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_id"] + [f"px{i}" for i in range(32)] + ["verify"])
        w.writerows(rows)

    print("\n==== 串口捕获统计 ====")
    print(f"bytes raw        : {len(raw_all)}")
    print(f"frames_ok        : {parser.frames_ok}")
    print(f"crc_errors       : {parser.crc_errors}")
    print(f"resync_count     : {parser.resync_count}")
    if args.verify and parser.frames_ok:
        print(f"golden verified  : {verified_frames}/{parser.frames_ok} 帧"
              f"  mismatches={mismatches}")
    print(f"capture saved    : {bin_path.name}, {csv_path.name}")

    ok = parser.frames_ok > 0 and parser.crc_errors == 0
    if args.verify and parser.frames_ok:
        ok = ok and mismatches == 0
    print(f"\n==== SERIAL CAPTURE: {'PASS' if ok else 'FAIL'} ====")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
