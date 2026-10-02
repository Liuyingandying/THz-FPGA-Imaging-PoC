#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AX7020 UART gate: >=500 ordered complete, bit-exact 16x32 images.

Wire frame_id=(image_id<<4)|row; image_id wraps at 4096. Capture joins
at the first row0, with startup partial rows recorded separately.
Raw UART and per-image comparisons are always saved; --save saves every
compared image, including failures.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thz_config as C
from frame_sources import _FrameParser

ROWS, COLS = 16, 32
OUTDIR = C.ROOT / "results" / "v41_board"
GOLD_PATH = C.ROOT / "results/demo_2x8x32/ref/tju_gold.npy"
GOLD = np.load(GOLD_PATH)[0]  # historical import API


class RowAssembler:
    """One bounded image; duplicate/order faults taint that image.

    Modulo-4096 forward distances 1..2047 are accepted. Older IDs and
    the ambiguous half-range are rejected. A reset requires a new run.
    Startup rows before the first row0 are outside the formal gate window.
    """
    def __init__(self):
        self.imgs = {}
        self.last_row_seen = {}
        self.missing = self.duplicate = self.out_of_order = 0
        self.published = self.stale_dropped = self.stale_rows = 0
        self.startup_partial_rows = 0
        self.synced = False
        self._last_imgid = -1
        self._active = None
        self._seen = 0
        self._expected = 0
        self._tainted = False

    def _finish(self):
        if self._active is None:
            return None
        img_id = self._active
        image = self.imgs[img_id]
        out = None
        if self._seen == 0xFFFF and not self._tainted:
            self.published += 1
            out = img_id, np.array([image[r] for r in range(ROWS)],
                                   dtype=np.int64)
        else:
            self.missing += ROWS - self._seen.bit_count()
            self.stale_dropped += 1
        self.imgs.clear()
        self._active = None
        return out

    def flush(self):
        """Count unfinished images once, including all absent rows."""
        self._finish()

    def feed(self, frame_id, pixels):
        if not 0 <= int(frame_id) <= 0xFFFF or np.shape(pixels) != (COLS,):
            raise ValueError("Expected u16 frame_id and exactly 32 pixels")
        img_id, row = int(frame_id) >> 4, int(frame_id) & 0xF
        if not self.synced:
            if row != 0:
                self.startup_partial_rows += 1
                return None
            self.synced = True
        if self._last_imgid != -1 and img_id != self._last_imgid:
            delta = (img_id - self._last_imgid) & 0xFFF
            if delta >= 2048:
                self.stale_rows += 1
                self.out_of_order += 1
                return None
            self._finish()
            self.stale_dropped += delta - 1
            self.missing += (delta - 1) * ROWS
        if img_id != self._last_imgid:
            self._last_imgid = img_id
            self._active = img_id
            self._seen = self._expected = 0
            self._tainted = False
            self.imgs[img_id] = {}
            self.last_row_seen = {img_id: -1}
        if self._active is None:
            # Never reopen the last published/dropped image.
            if self._seen & (1 << row):
                self.duplicate += 1
            else:
                self.out_of_order += 1
                self.stale_rows += 1
                self._seen |= 1 << row
            return None
        if self._seen & (1 << row):
            self.duplicate += 1
            self._tainted = True
            return None
        if row != self._expected:
            self.out_of_order += 1
            self._tainted = True
        self._expected = row + 1
        self._seen |= 1 << row
        self.last_row_seen[img_id] = row
        self.imgs[img_id][row] = np.array(pixels, copy=True)
        return self._finish() if self._seen == 0xFFFF else None


def _clean(parser, asm):
    return not any((parser.crc_errors, parser.format_errors, asm.missing,
                    asm.duplicate, asm.out_of_order, asm.stale_dropped))


def _load_gold(path):
    gold = np.load(path).astype(np.int64)
    if gold.ndim == 2:
        gold = gold[None, ...]
    if gold.ndim != 3 or gold.shape[1:] != (ROWS, COLS) or not len(gold):
        raise ValueError("gold must have shape (16,32) or (N,16,32)")
    return gold


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
        help="Serial port (auto-detected if not specified)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--gate", type=int, default=500)
    ap.add_argument("--max-images", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--outdir", type=Path, default=OUTDIR)
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--save", action="store_true",
                    help="Save all compared images; raw UART is always saved")
    a = ap.parse_args(argv)
    if a.gate < 1 or a.timeout <= 0:
        ap.error("gate and timeout must be positive")
    a.max_images = max(a.gate, 1000) if a.max_images is None else a.max_images
    if a.max_images < a.gate:
        ap.error("max-images must be >= gate")
    try:
        gold = _load_gold(a.gold)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))

    import serial
    parser, asm = _FrameParser(COLS), RowAssembler()
    consec = consec_best = images_checked = images_exact = 0
    max_abs_err = pixel_mismatches = 0
    first_bad = None
    saved, comparisons = [], []
    t0 = time.monotonic()
    a.outdir.mkdir(parents=True, exist_ok=True)
    gate_boundary = False
    gate_start_image_id = None
    raw_bytes = 0
    serial_error = None
    print(f">> {a.port}@{a.baud}: {a.gate} consecutive bit-exact images")
    try:
        with (a.outdir / "uart_capture.bin").open("wb") as raw, \
                serial.Serial(a.port, a.baud, timeout=0.05) as ser:
            while (consec < a.gate and images_checked < a.max_images
                   and time.monotonic() - t0 < a.timeout):
                buf = ser.read(4096)
                if not buf:
                    continue
                raw.write(buf)
                raw_bytes += len(buf)
                for fid, pixels in parser.feed(buf):
                    if not asm.synced and (fid & 15) == 0:
                        gate_start_image_id = fid >> 4
                    out = asm.feed(fid, pixels)
                    if out is None:
                        continue
                    img_id, image = out
                    expected = gold[img_id % len(gold)]
                    mismatches = int(np.count_nonzero(image != expected))
                    mae = int(np.abs(image - expected).max())
                    exact = mismatches == 0
                    images_checked += 1
                    images_exact += int(exact)
                    pixel_mismatches += mismatches
                    max_abs_err = max(max_abs_err, mae)
                    comparisons.append((img_id, img_id % len(gold),
                                        int(exact), mismatches, mae))
                    if a.save:
                        saved.append(image)
                    if exact and _clean(parser, asm):
                        consec += 1
                        consec_best = max(consec_best, consec)
                    else:
                        consec = 0
                        if first_bad is None:
                            first_bad = {"img_id": img_id, "max_abs_err": mae,
                                         "pixel_mismatches": mismatches,
                                         "exact": exact}
                    if images_checked % 20 == 0:
                        print(f"   images={images_checked} consec={consec} "
                              f"crc={parser.crc_errors} format={parser.format_errors} "
                              f"miss={asm.missing} dup={asm.duplicate} "
                              f"ooo={asm.out_of_order} drop={asm.stale_dropped}")
                    if consec >= a.gate or images_checked >= a.max_images:
                        gate_boundary = True
                        break
    except (OSError, serial.SerialException) as exc:
        serial_error = str(exc)
    except KeyboardInterrupt:
        serial_error = "capture interrupted"
    if not gate_boundary:
        parser.flush()
    asm.flush()
    stats = {
        "elapsed_s": round(time.monotonic() - t0, 1),
        "images_published": asm.published, "images_checked": images_checked,
        "images_bit_exact": images_exact, "pixel_mismatches": pixel_mismatches,
        "consecutive_pass": consec, "consecutive_best": consec_best,
        "crc_errors": parser.crc_errors, "format_errors": parser.format_errors,
        "missing_rows": asm.missing, "duplicate_rows": asm.duplicate,
        "out_of_order_rows": asm.out_of_order, "image_drops": asm.stale_dropped,
        "stale_rows": asm.stale_rows,
        "startup_partial_rows": asm.startup_partial_rows,
        "gate_synced": asm.synced, "max_abs_error": max_abs_err,
        "gate_start_image_id": gate_start_image_id,
        "gate_end_image_id": comparisons[-1][0] if comparisons else None,
        "raw_bytes": raw_bytes, "frames_parsed": parser.frames_ok,
        "bit_exact_all": images_checked > 0 and images_exact == images_checked,
        "first_bad": first_bad, "serial_error": serial_error,
        "gold": str(a.gold.resolve()), "gold_period": len(gold),
        "gate_required": a.gate, "max_images": a.max_images,
        "timeout_s": a.timeout,
    }
    gate_ok = (not serial_error and consec >= a.gate
               and images_checked >= a.gate and images_exact == images_checked
               and pixel_mismatches == 0 and max_abs_err == 0
               and _clean(parser, asm))
    stats["gate_pass"] = bool(gate_ok)
    (a.outdir / "stageA_stats.json").write_text(json.dumps(stats, indent=2),
                                               encoding="utf-8")
    with (a.outdir / "image_compare.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("image_id", "gold_index", "bit_exact",
                         "pixel_mismatches", "max_abs_error"))
        writer.writerows(comparisons)
    if a.save:
        images = np.asarray(saved, dtype=np.int64).reshape((-1, ROWS, COLS))
        np.save(a.outdir / "board_images.npy", images)
        np.save(a.outdir / "sample_images.npy", images)
    print("\n==== Stage A gate ====")
    for key, value in stats.items():
        print(f"  {key:<20}: {value}")
    print("  GATE:", "PASS" if gate_ok else "FAIL")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
