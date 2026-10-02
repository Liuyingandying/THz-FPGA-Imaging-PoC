#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thz_demo_viewer.py — 2×(8×32) Digital Twin Demo Viewer(Phase V4-Demo)

基于既有 PySide6 面板件扩展(复用 HeatPanel 等),独立入口,不改 thz_live_viewer.py
主体。四视图:Ground Truth / Synthetic Random Photocurrent / FPGA Reconstructed /
Difference+Defect Map;顶部状态条:MODEL/MODE/SOURCE/FRAME/AVG/CNR;扫描指示:
Scanning: BANK x ROW y / Global Row z —— 让"32 通道并行 + 16 行扫描"直观看懂。

数据来源(全部离线,来自 demo_2x8x32.py build 的产物,已 RTL 位级验证):
  results/demo_2x8x32/ref/{tag}_truth.npy / _raw.npy / _gold.npy

声明(常显):MODEL: 2×(8×32) ASSUMED | MODE: DIGITAL TWIN | SYNTHETIC THz DATA
用法: python tools/thz_demo_viewer.py --scene tju|metal_defect|moving_target
"""

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib
matplotlib.use("Agg")
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QGridLayout, QLabel, QMainWindow,
                               QWidget)

REFD = Path(__file__).resolve().parents[1] / "results" / "demo_2x8x32" / "ref"
ROWS, COLS = 16, 32
AVG = 4
DECL = ("MODEL: 2×(8×32) ASSUMED   |   MODE: DIGITAL TWIN   |   "
        "SOURCE: SYNTHETIC THz DATA (not real-prototype measurements)")


class Heat(QWidget):
    def __init__(self, title):
        super().__init__()
        self.fig = Figure(figsize=(4.4, 3.2), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QGridLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.im = self.ax.imshow(np.zeros((ROWS, COLS)), cmap="inferno",
                                 vmin=0, vmax=1, interpolation="nearest",
                                 aspect="auto")
        self.fig.colorbar(self.im, ax=self.ax, fraction=0.046)
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.ax.set_title(title, fontsize=9)

    def set(self, img, vmin=None, vmax=None):
        self.im.set_data(img)
        if vmax is not None:
            self.im.set_clim(vmin or 0, vmax)
        self.canvas.draw_idle()


class DemoWindow(QMainWindow):
    def __init__(self, scene="tju", fps=2):
        super().__init__()
        self.setWindowTitle("THz 2×(8×32) Digital Twin Demo — FPGA Imaging")
        self.resize(1280, 760)
        self.scene = scene
        self.t = np.load(REFD / f"{scene}_truth.npy")
        self.r = np.load(REFD / f"{scene}_raw.npy").astype(float)
        self.g = np.load(REFD / f"{scene}_gold.npy").astype(float)
        self.ng = self.g.shape[0]
        self.k = 0

        self.lbl_top = QLabel(DECL)
        self.lbl_top.setStyleSheet("font-weight:bold; padding:3px;")
        from PySide6.QtCore import Qt
        self.lbl_top.setAlignment(Qt.AlignCenter)
        self.scan = QLabel("")
        self.scan.setStyleSheet("padding:2px; color:#1565c0;")

        cw = QWidget(); grid = QGridLayout(cw)
        grid.addWidget(self.lbl_top, 0, 0, 1, 2)
        grid.addWidget(self.scan, 1, 0, 1, 2)
        self.h_gt = Heat("A. Ground Truth 16×32 (nA, assumed)")
        self.h_raw = Heat("B. Synthetic Random Photocurrent (ADC frame)")
        self.h_rec = Heat("C. FPGA Reconstructed Frame (RTL, bit-exact)")
        self.h_dif = Heat("D. Difference / Defect Map (rec vs GT level)")
        grid.addWidget(self.h_gt, 2, 0); grid.addWidget(self.h_raw, 2, 1)
        grid.addWidget(self.h_rec, 3, 0); grid.addWidget(self.h_dif, 3, 1)
        self.lbl_met = QLabel("")
        self.lbl_met.setStyleSheet("padding:4px;")
        grid.addWidget(self.lbl_met, 4, 0, 1, 2)
        self.setCentralWidget(cw)

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / fps))
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self):
        gi = self.k % self.ng
        fi = self.k % self.r.shape[0]
        # 扫描指示动画:一帧内推进 global row(演示"16 行顺序扫描")
        grow = self.k % ROWS
        self.scan.setText(
            f"Scanning:  BANK {'A' if grow < 8 else 'B'}   ROW {grow % 8} / 8"
            f"    |    Global Row {grow} / 16    |    "
            f"32 parallel channels × 16 row scans = 512 samples/frame")
        self.h_gt.set(self.t[min(gi, self.t.shape[0] - 1)],
                      vmin=self.t.min(), vmax=self.t.max())
        self.h_raw.set(self.r[fi], vmin=self.r.min(), vmax=self.r.max())
        self.h_rec.set(self.g[gi], vmin=self.g.min(), vmax=self.g.max())
        # 缺陷图:重建与 GT 电流档位的差(以 GT 三档中值为参照)
        gt = self.t[min(gi, self.t.shape[0] - 1)]
        ref = np.median(np.unique(gt))
        self.h_dif.im.set_cmap("RdBu_r")
        self.h_dif.set(self.g[gi] - ref,
                       vmin=-abs(self.g[gi] - ref).max(),
                       vmax=abs(self.g[gi] - ref).max())
        m = self._metrics(gt, self.g[gi])
        self.lbl_met.setText(
            f"Scene: {self.scene}   Frame {gi+1}/{self.ng}   AVG_FRAMES={AVG}   "
            f"Pearson r={m['r']}   IoU={m['iou']}   CNR={m['cnr']}   "
            f"NRMSE={m['nrmse']}"
            + (f"   Defect Detected: {'YES' if m['def'] else 'NO'}"
               if m["def"] is not None else ""))
        self.k += 1

    @staticmethod
    def _metrics(gt, rec):
        tn = (gt - gt.min()) / (np.ptp(gt) or 1)
        rn = (rec - rec.min()) / (np.ptp(rec) or 1)
        r = float(np.corrcoef(gt.ravel(), rec.ravel())[0, 1])
        nrmse = float(np.sqrt(np.mean((tn - rn) ** 2)))
        bg = gt == gt.min(); tg = gt == gt.max()
        s = float(rec[bg].std()) or 1.0
        cnr = abs(float(np.median(rec[tg])) - float(np.median(rec[bg]))) / s
        thr = (float(np.median(rec[tg])) + float(np.median(rec[bg]))) / 2
        seg = rec > thr
        iou = float((seg & tg).sum() / max((seg | tg).sum(), 1))
        lv = np.unique(gt)
        df = None
        if len(lv) == 3:
            mid = gt == lv[1]
            df = bool(abs(float(rec[mid].mean()) - float(rec[bg].mean())) >= 3 * s
                      and abs(float(rec[mid].mean())
                              - float(rec[tg].mean())) >= 3 * s)
        return {"r": round(r, 4), "nrmse": round(nrmse, 4),
                "iou": round(iou, 4), "cnr": round(cnr, 2), "def": df}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="tju",
                    choices=["tju", "metal_defect", "moving_target"])
    ap.add_argument("--fps", type=int, default=2)
    ap.add_argument("--offscreen-shot", default=None,
                    help="渲染 N 帧后截图保存并退出(演示素材)")
    a = ap.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)
    win = DemoWindow(a.scene, a.fps)
    win.show()
    if a.offscreen_shot:
        for _ in range(6):
            win.tick()
        app.processEvents()
        p = Path(a.offscreen_shot)
        p.parent.mkdir(parents=True, exist_ok=True)
        win.grab().save(str(p))
        print("shot:", p)
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
