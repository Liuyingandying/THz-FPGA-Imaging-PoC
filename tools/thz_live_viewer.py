#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thz_live_viewer.py — THz FPGA Imaging Monitor(实时可视化工作台 v1)

数据源无关:经 tools/frame_sources.py 的 FrameSource 接口取帧。
    --source dataset --dataset moving_target --fps 10
    --source rtl     --dataset 02_single_point
    --source uart            (骨架,未接真实串口,界面明确标注)

PowerShell 示例:
    python tools\thz_live_viewer.py --source dataset --dataset moving_target --fps 10
    python tools\thz_live_viewer.py --source rtl --dataset 02_single_point --scale auto
"""

import argparse
import csv
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import thz_config as C
from frame_sources import (Frame, FrameSource, load_channel_map, make_source,
                           pixel_to_channel)

# offscreen 插件使用基础字体库:必须指向系统字体目录,否则 UI 文字渲染为豆腐块
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import matplotlib
matplotlib.use("Agg")                       # 仅注册;Qt 渲染由 FigureCanvas 完成
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PIL import Image

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCursor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGridLayout, QLabel, QMainWindow, QMessageBox,
    QTabWidget, QToolBar, QToolTip, QWidget)

INTERP_MODES = {"nearest": Image.NEAREST, "bilinear": Image.BILINEAR,
                "bicubic": Image.BICUBIC}
INTERP_SIZE = (320, 160)                    # 显示分辨率(非物理分辨率)
SPEEDS = [1, 5, 10, 30]

APP_TITLE = "THz FPGA Imaging Monitor"
INTERP_NOTICE = ("Interpolation is for visualization only; physical detector "
                 "resolution remains 4\u00d78.")


# ---------------------------------------------------------------- 画布面板

class HeatPanel(QWidget):
    """单个 4×8 热图面板:figure 只建一次,逐帧 set_data/set_clim/文本更新。"""

    def __init__(self, title, cmap="inferno", annotate=True, parent=None):
        super().__init__(parent)
        self.annotate_values = annotate
        self.base_title = title
        self.fig = Figure(figsize=(3.6, 2.9), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.im = self.ax.imshow(np.zeros((C.ROWS, C.COLS)), cmap=cmap,
                                 vmin=0, vmax=1, interpolation="nearest")
        self.cbar = self.fig.colorbar(self.im, ax=self.ax, fraction=0.046, pad=0.03)
        self.ax.set_xticks(range(C.COLS))
        self.ax.set_yticks(range(C.ROWS))
        self.ax.set_xlabel("col", fontsize=7)
        self.ax.set_ylabel("row", fontsize=7)
        self.ax.tick_params(labelsize=6.5)
        self.texts = [[self.ax.text(c, r, "", ha="center", va="center",
                                    fontsize=5.6, color="w")
                       for c in range(C.COLS)] for r in range(C.ROWS)]
        self.notice = None
        self._notice = None
        self.sel_marker = None

    def set_frame(self, img, title, clim=None, notice=None, show_values=True):
        img = np.asarray(img, dtype=float)
        self.im.set_data(img)
        if clim is None:
            vmin, vmax = float(np.nanmin(img)), float(np.nanmax(img))
            clim = (vmin, vmax if vmax > vmin else vmin + 1)
        self.im.set_clim(*clim)
        self.ax.set_title(title, fontsize=8)
        show_txt = show_values and self.annotate_values
        lo, hi = clim if clim else (float(np.nanmin(img)), float(np.nanmax(img)))
        span = (hi - lo) or 1.0
        for r in range(C.ROWS):
            for c in range(C.COLS):
                self.texts[r][c].set_text(f"{int(img[r, c])}" if show_txt else "")
                self.texts[r][c].set_visible(show_txt)
                if show_txt:                       # 亮底用深色注记,保证可读
                    norm = (img[r, c] - lo) / span
                    self.texts[r][c].set_color("k" if norm > 0.6 else "w")
        if notice != self._notice:
            if self.notice is not None:
                self.notice.remove()
                self.notice = None
            if notice:
                self.notice = self.ax.text(
                    0.5, -0.40, notice, transform=self.ax.transAxes, ha="center",
                    va="top", fontsize=6.0, style="italic", color="#333333")
            self._notice = notice
        self.canvas.draw_idle()

    def mark(self, row, col):
        if self.sel_marker is None:
            self.sel_marker = self.ax.plot(col, row, "o", ms=14, mfc="none",
                                           mec="#00e5ff", mew=1.2)[0]
        else:
            self.sel_marker.set_data([col], [row])
        self.canvas.draw_idle()

    def pixel_at(self, x, y):
        if x is None or y is None:
            return None
        c, r = int(np.floor(x + 0.5)), int(np.floor(y + 0.5))
        if 0 <= r < C.ROWS and 0 <= c < C.COLS:
            return r, c
        return None

    def rgb_buffer(self):
        self.fig.canvas.draw()
        return np.asarray(self.fig.canvas.buffer_rgba())


class TemporalPanel(QWidget):
    """时序视图:max / mean / 选中像素,最近 100 帧(GUI 侧曲线)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(3.6, 2.9), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        (self.ln_max,) = self.ax.plot([], [], lw=1.2, color="#c62828", label="max")
        (self.ln_mean,) = self.ax.plot([], [], lw=1.2, color="#1565c0", label="mean")
        (self.ln_sel,) = self.ax.plot([], [], lw=1.2, color="#2e7d32",
                                      label="selected")
        self.legend = self.ax.legend(fontsize=6.5, loc="upper left")
        self.ax.set_xlabel("frame", fontsize=7)
        self.ax.set_ylabel("processed value", fontsize=7)
        self.ax.tick_params(labelsize=6.5)
        self.ax.set_title("Temporal profile (GUI / PC-side)", fontsize=8)
        self.hist_max = deque(maxlen=100)
        self.hist_mean = deque(maxlen=100)
        self.hist_sel = deque(maxlen=100)
        self.hist_fid = deque(maxlen=100)

    def push(self, frame: Frame, sel_value):
        self.hist_max.append(int(frame.processed_image.max()))
        self.hist_mean.append(float(frame.processed_image.mean()))
        self.hist_sel.append(int(sel_value))
        self.hist_fid.append(frame.frame_id)
        x = list(self.hist_fid)
        self.ln_max.set_data(x, list(self.hist_max))
        self.ln_mean.set_data(x, list(self.hist_mean))
        self.ln_sel.set_data(x, list(self.hist_sel))
        self.ax.relim()
        self.ax.autoscale_view()
        self.ax.set_xlabel(f"frame (last {len(x)})", fontsize=7)
        self.canvas.draw_idle()

    def set_selection_label(self, text):
        self.ln_sel.set_label(text)
        self.legend = self.ax.legend(fontsize=6.5, loc="upper left")
        self.canvas.draw_idle()

    def reset(self):
        for h in (self.hist_max, self.hist_mean, self.hist_sel, self.hist_fid):
            h.clear()


class InterpPanel(QWidget):
    """插值显示面板:PIL 重采样到 320×160 —— 仅视觉,不提升物理分辨率。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(3.6, 2.9), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.im = self.ax.imshow(np.zeros((INTERP_SIZE[1], INTERP_SIZE[0])),
                                 cmap="inferno", vmin=0, vmax=1,
                                 interpolation="nearest", aspect="auto")
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.notice = self.ax.text(
            0.5, -0.06, INTERP_NOTICE, transform=self.ax.transAxes,
            ha="center", va="top", fontsize=6.4, style="italic", color="#333333")
        self.mode = "nearest"

    def set_frame(self, img, clim):
        big = Image.fromarray(np.asarray(img, dtype=np.float32), mode="F") \
            .resize(INTERP_SIZE, resample=INTERP_MODES[self.mode])
        self.im.set_data(np.asarray(big))
        self.im.set_clim(*clim)
        self.ax.set_title(f"Interpolated view ({self.mode}, display "
                          f"{INTERP_SIZE[0]}\u00d7{INTERP_SIZE[1]})", fontsize=8)
        self.canvas.draw_idle()


class ComparePanel(QWidget):
    """Ground Truth vs Processed vs Error(误差实际读取参考文件计算)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(7.4, 2.9), constrained_layout=True)
        self.axes = self.fig.subplots(1, 3)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.ims = []
        for ax in self.axes:
            im = ax.imshow(np.zeros((C.ROWS, C.COLS)), cmap="inferno",
                           vmin=0, vmax=1, interpolation="nearest")
            ax.set_xticks(range(C.COLS)); ax.set_yticks(range(C.ROWS))
            ax.tick_params(labelsize=6)
            self.ims.append(im)
        self.ims[2].set_cmap("RdBu_r")

    def set_frame(self, frame: Frame):
        gt = frame.meta.get("truth_image")
        ref = frame.meta.get("reference_channels")
        if gt is None:
            self.ims[0].set_data(np.zeros((C.ROWS, C.COLS)))
            self.ims[0].set_clim(0, 1)
            self.axes[0].set_title("Ground Truth: n/a", fontsize=7)
        else:
            gt = np.asarray(gt, dtype=float)
            self.ims[0].set_data(gt)
            self.ims[0].set_clim(0, max(float(gt.max()), 1.0))
            self.axes[0].set_title("Ground Truth (sim)", fontsize=7)

        self.ims[1].set_data(frame.processed_image)
        self.ims[1].set_clim(0, max(float(frame.processed_image.max()), 1.0))
        self.axes[1].set_title("Processed", fontsize=7)

        if ref is not None:
            err = (frame.processed_channels.astype(np.int64)
                   - np.asarray(ref)).reshape(C.ROWS, C.COLS)
            true_max = int(np.abs(err).max())
            clim_abs = max(true_max, 1)            # 仅用于色标,不冒充真实误差
            self.ims[2].set_data(err)
            self.ims[2].set_clim(-clim_abs, clim_abs)
            self.axes[2].set_title(f"Error = processed \u2212 reference "
                                   f"(max|err|={true_max})", fontsize=7)
        else:
            self.ims[2].set_data(np.zeros((C.ROWS, C.COLS)))
            self.ims[2].set_clim(-1, 1)
            self.axes[2].set_title("Error: reference file n/a", fontsize=7)
        self.canvas.draw_idle()


# ---------------------------------------------------------------- 主窗口

def ensure_app_font():
    """显式指定应用字体(offscreen 基础字体库的默认字体可能缺字形)。"""
    from PySide6.QtGui import QFont
    app = QApplication.instance()
    if app is None:
        return
    for fam in ("Segoe UI", "Arial", "Microsoft YaHei"):
        f = QFont(fam, 9)
        if f.exactMatch():
            app.setFont(f)
            return


class ViewerWindow(QMainWindow):
    def __init__(self, source_name, dataset, fps, interp, scale, offscreen=False):
        super().__init__()
        ensure_app_font()
        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 660)
        self.offscreen = offscreen

        self.source_kind = source_name
        self.source: FrameSource = make_source(source_name, dataset)
        self.playing = True
        self.fps = fps
        self.scale_mode = scale                      # hold | auto
        self.hold_raw = None
        self.hold_proc = None
        self.selected = (1, 3)
        self.tick_times = deque(maxlen=20)
        self.current: Frame | None = None
        self.recording = False
        self.record_dir = None
        self.record_images = []
        self.record_csv = None
        self.uart_note_shown = False
        self._interp0 = interp

        self._build_controls()
        self._build_panels()
        self._build_statusbar()
        self._connect_canvas_events()

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / self.fps))
        self.timer.timeout.connect(self.on_tick)
        self.timer.start()

    # ---------------- UI 构建 ----------------
    def _build_controls(self):
        tb = QToolBar("controls")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel(" Source: "))
        self.cmb_source = QComboBox()
        self.cmb_source.addItems(["dataset", "rtl", "uart"])
        self.cmb_source.setCurrentText(self.source_kind)
        tb.addWidget(self.cmb_source)

        tb.addWidget(QLabel(" Dataset: "))
        self.cmb_dataset = QComboBox()
        tb.addWidget(self.cmb_dataset)

        self.act_toggle = QAction("Pause", self)
        self.act_toggle.triggered.connect(self.toggle_play)
        tb.addAction(self.act_toggle)
        act_next = QAction("Next Frame", self)
        act_next.setShortcut(QKeySequence("N"))
        act_next.triggered.connect(self.next_frame)
        tb.addAction(act_next)
        act_reset = QAction("Reset", self)
        act_reset.setShortcut(QKeySequence("R"))
        act_reset.triggered.connect(self.reset_view)
        tb.addAction(act_reset)

        tb.addWidget(QLabel(" Speed: "))
        self.cmb_speed = QComboBox()
        for s in SPEEDS:
            self.cmb_speed.addItem(f"{s} FPS", s)
        self.cmb_speed.setCurrentIndex(SPEEDS.index(self.fps) if self.fps in SPEEDS else 2)
        self.cmb_speed.currentIndexChanged.connect(self.on_speed_change)
        tb.addWidget(self.cmb_speed)

        tb.addWidget(QLabel(" Scale: "))
        self.cmb_scale = QComboBox()
        self.cmb_scale.addItem("Hold first frame", "hold")
        self.cmb_scale.addItem("Auto (per frame)", "auto")
        self.cmb_scale.setCurrentIndex(0 if self.scale_mode == "hold" else 1)
        self.cmb_scale.currentIndexChanged.connect(lambda _: self._clear_hold())
        tb.addWidget(self.cmb_scale)

        tb.addWidget(QLabel(" Interp: "))
        self.cmb_interp = QComboBox()
        self.cmb_interp.addItems(list(INTERP_MODES))
        self.cmb_interp.setCurrentText(self._interp0)
        self.cmb_interp.currentTextChanged.connect(self.on_interp_change)
        tb.addWidget(self.cmb_interp)

        act_save = QAction("Save Frame", self)
        act_save.triggered.connect(self.save_current_frame)
        tb.addAction(act_save)
        self.act_record = QAction("Record", self)
        self.act_record.triggered.connect(self.toggle_record)
        tb.addAction(self.act_record)

        self.cmb_source.currentTextChanged.connect(self.on_source_change)
        self.cmb_dataset.currentTextChanged.connect(self.on_dataset_change)
        self._reload_datasets()

    def _reload_datasets(self):
        self.cmb_dataset.blockSignals(True)
        self.cmb_dataset.clear()
        self.cmb_dataset.addItems(self.source.datasets() or ["(none)"])
        want = getattr(self.source, "dataset", None)
        if want:
            i = self.cmb_dataset.findText(want)
            if i >= 0:
                self.cmb_dataset.setCurrentIndex(i)
        self.cmb_dataset.blockSignals(False)
        self.cmb_dataset.setEnabled(self.source_kind != "uart")

    def _build_panels(self):
        central = QWidget()
        grid = QGridLayout(central)
        grid.setContentsMargins(6, 6, 6, 2)
        self.panel_raw = HeatPanel("Raw ADC 4\u00d78")
        self.panel_proc = HeatPanel("FPGA Processed Frame")
        self.panel_interp = InterpPanel()
        self.panel_temporal = TemporalPanel()
        self.panel_compare = ComparePanel()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.panel_temporal, "Temporal")
        self.tabs.addTab(self.panel_compare, "Compare (GT / Error)")
        grid.addWidget(self.panel_raw, 0, 0)
        grid.addWidget(self.panel_proc, 0, 1)
        grid.addWidget(self.panel_interp, 1, 0)
        grid.addWidget(self.tabs, 1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        self.setCentralWidget(central)

    def _build_statusbar(self):
        self.lbl_source = QLabel(self.source.source_label)
        self.lbl_source.setStyleSheet("font-weight: bold; padding: 2px 8px;")
        self.lbl_stats = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.lbl_stats)
        sb.addPermanentWidget(self.lbl_source)

    def _connect_canvas_events(self):
        self.panel_proc.canvas.mpl_connect("motion_notify_event", self._on_hover_proc)
        self.panel_raw.canvas.mpl_connect("motion_notify_event", self._on_hover_raw)
        self.panel_proc.canvas.mpl_connect("button_press_event", self._on_click_proc)

    # ---------------- 数据/状态 ----------------
    def set_source(self, kind, dataset=None):
        was_active = self.timer.isActive()       # 保留定时器状态(离屏驱动时已被停)
        self.timer.stop()
        try:
            self.source.close()
        except Exception:
            pass
        self.source_kind = kind
        self.source = make_source(kind, dataset)
        self.current = None                      # 防止渲染旧来源的陈旧帧
        self.lbl_source.setText(self.source.source_label)
        self.uart_note_shown = False
        self._reload_datasets()
        self._clear_hold()
        self.panel_temporal.reset()
        if was_active:
            self.timer.start(int(1000 / self.fps))

    def _clear_hold(self):
        self.hold_raw = None
        self.hold_proc = None
        self.scale_mode = self.cmb_scale.currentData()

    def set_playing(self, on):
        self.playing = on
        self.act_toggle.setText("Run" if not on else "Pause")

    def toggle_play(self):
        self.set_playing(not self.playing)

    def next_frame(self):
        """暂停状态下手动步进一帧。"""
        self.set_playing(False)
        frame = self.source.get_next_frame()
        if frame is None:
            self._handle_no_data()
            return
        self.current = frame
        self._render(frame)
        self._update_status(0.0)

    def reset_view(self):
        self.source.reset()
        self.panel_temporal.reset()
        self._clear_hold()
        self.set_playing(True)

    def on_speed_change(self, _):
        self.fps = self.cmb_speed.currentData()
        self.timer.setInterval(int(1000 / self.fps))

    def on_interp_change(self, mode):
        self.panel_interp.mode = mode
        if self.current:
            clim = self.hold_proc or self._auto_clims(self.current)[1]
            self.panel_interp.set_frame(self.current.processed_image, clim)

    def on_source_change(self, kind):
        self.set_source(kind, None)

    def on_dataset_change(self, name):
        if getattr(self.source, "dataset", None) != name and name \
                and not name.startswith("("):
            self.set_source(self.source_kind, name)

    # ---------------- 量程 ----------------
    def _auto_clims(self, frame, prefer_final=False):
        raw = frame.raw_image
        cr = ((float(raw.min()), max(float(raw.max()), float(raw.min()) + 1))
              if raw is not None else (0.0, 1.0))
        fin = frame.meta.get("processed_final_range")
        if prefer_final and fin:
            cp = (float(fin[0]), max(float(fin[1]), float(fin[0]) + 1))
        else:
            cp = (float(frame.processed_image.min()),
                  max(float(frame.processed_image.max()),
                      float(frame.processed_image.min()) + 1))
        return cr, cp

    def _clims(self, frame):
        if self.scale_mode == "auto":
            return self._auto_clims(frame)
        if self.hold_raw is None:
            # Hold 模式:processed 用最终平均帧量程(播放早期不跳动)
            self.hold_raw, self.hold_proc = self._auto_clims(frame, prefer_final=True)
        return self.hold_raw, self.hold_proc

    # ---------------- 主循环 ----------------
    def on_tick(self):
        if not self.playing:
            self._update_status(0.0)
            return
        now = time.perf_counter()
        frame = self.source.get_next_frame()
        if frame is None:                            # UART 骨架等无数据情形
            self._handle_no_data()
            return
        self.current = frame
        self._render(frame)
        dt = (now - self.tick_times[-1]) if self.tick_times else 1.0
        self.tick_times.append(now)
        fps = 1.0 / dt if dt > 0 else 0.0
        self._update_status(min(fps, 999.0))
        if self.recording:
            self._record_frame(frame)

    def _handle_no_data(self):
        self._update_status(0.0)
        if not self.uart_note_shown:
            msg = getattr(self.source, "info", {}).get("error", "no data")
            self.lbl_stats.setText(f"Frame: -   {msg}")
            self.uart_note_shown = True

    def _render(self, frame: Frame):
        clim_raw, clim_proc = self._clims(frame)
        fi = frame.meta.get("frame_index", frame.frame_id)
        nf = frame.meta.get("n_frames", "?")
        if frame.raw_image is not None:
            self.panel_raw.set_frame(
                frame.raw_image, f"Raw ADC 4\u00d78  (frame {fi}/{nf})",
                clim=clim_raw,
                notice="Raw data view \u2014 no interpolation applied")
        else:
            self.panel_raw.set_frame(
                np.full((C.ROWS, C.COLS), np.nan), "Raw ADC 4\u00d78  (n/a)",
                clim=(0, 1), show_values=False,
                notice="This source carries no raw ADC data\n"
                       "(RTL output file stores the processed frame only)")
        # Processed:标题严格区分 FPGA / Python Reference,不混淆;
        # 帧号统一用输入帧序号(1-based),避免与状态栏全局 Frame 混淆
        avg_note = "" if frame.meta.get("averaging_complete", True) \
            else f"  (averaging {fi + 1}/{nf})"
        self.panel_proc.set_frame(
            frame.processed_image,
            f"{self.source.processed_label}\ninput frame {fi + 1}/{nf}{avg_note}",
            clim=clim_proc)
        self.panel_proc.mark(*self.selected)
        self.panel_interp.set_frame(frame.processed_image, clim_proc)
        r, c = self.selected
        self.panel_temporal.set_selection_label(f"CH{pixel_to_channel(r, c)} (r{r},c{c})")
        self.panel_temporal.push(frame, int(frame.processed_image[r, c]))
        self.panel_compare.set_frame(frame)

    def _update_status(self, fps):
        f = self.current
        if f is None:
            return
        img = f.processed_image
        r, c = np.unravel_index(int(np.argmax(img)), img.shape)
        ch = pixel_to_channel(int(r), int(c))
        note = "" if self.playing else "   [PAUSED]"
        self.lbl_stats.setText(
            f"Frame: {f.frame_id}   GUI FPS: {fps:5.1f} (PC display only, not FPGA "
            f"throughput)   Min: {int(img.min())}   Max: {int(img.max())}   "
            f"Mean: {img.mean():.1f}   Peak: CH{ch} (row{r},col{c}){note}")

    # ---------------- 鼠标 ----------------
    def _hover_text(self, x, y, panel):
        pix = panel.pixel_at(x, y)
        f = self.current
        if pix is None or f is None:
            return
        r, c = pix
        ch = pixel_to_channel(r, c)
        raw = "-" if f.raw_channels is None else f"{int(f.raw_channels[ch])}"
        QToolTip.showText(QCursor.pos(),
                          f"Row {r}  Col {c}  |  CH{ch}  |  Raw {raw}  |  "
                          f"Processed {int(f.processed_image[r, c])}")

    def _on_hover_proc(self, ev):
        if ev.inaxes is self.panel_proc.ax:
            self._hover_text(ev.xdata, ev.ydata, self.panel_proc)

    def _on_hover_raw(self, ev):
        if ev.inaxes is self.panel_raw.ax and self.current is not None \
                and self.current.raw_image is not None:
            self._hover_text(ev.xdata, ev.ydata, self.panel_raw)

    def _on_click_proc(self, ev):
        pix = self.panel_proc.pixel_at(ev.xdata, ev.ydata)
        if pix:
            self.selected = pix
            self.panel_proc.mark(*pix)

    # ---------------- 保存/录制 ----------------
    def save_current_frame(self, target_dir=None):
        f = self.current
        if f is None:
            return None
        out = Path(target_dir) if target_dir \
            else C.RESULTS_DIR / "visualization" / "saves"
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{self.source.name}_{f.meta.get('dataset', 'frame')}_{f.frame_id:06d}"
        cmap = load_channel_map()
        inv = np.argsort(cmap)
        csvp = out / f"{stem}.csv"
        with open(csvp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["row", "col", "channel", "raw_adc", "processed"])
            for r in range(C.ROWS):
                for c in range(C.COLS):
                    ch = int(inv[r * C.COLS + c])
                    raw = "-" if f.raw_channels is None else int(f.raw_channels[ch])
                    w.writerow([r, c, ch, raw, int(f.processed_image[r, c])])
        pngp = out / f"{stem}.png"
        self.grab().save(str(pngp))
        return csvp, pngp

    def toggle_record(self):
        if not self.recording:
            ts = time.strftime("%Y%m%d_%H%M%S")
            self.record_dir = C.RESULTS_DIR / "visualization" / f"record_{ts}"
            self.record_dir.mkdir(parents=True, exist_ok=True)
            self.record_images = []
            self.record_csv = open(self.record_dir / "frames.csv", "a", newline="")
            self.recording = True
            self.act_record.setText("Stop Record")
            self.lbl_source.setText(self.lbl_source.text() + "   [REC]")
        else:
            self.recording = False
            self.act_record.setText("Record")
            self.record_csv.close()
            self.lbl_source.setText(self.lbl_source.text().replace("   [REC]", ""))
            try:
                self._finalize_record()
            except Exception as e:                   # GIF 导出尽力而为,不拖垮 GUI
                QMessageBox.warning(self, "Record", f"GIF export failed: {e}")

    def _record_frame(self, frame: Frame):
        buf = self.panel_proc.rgb_buffer()
        self.record_images.append(Image.fromarray(buf[:, :, :3].copy()))
        w = csv.writer(self.record_csv)
        for r in range(C.ROWS):
            for c in range(C.COLS):
                w.writerow([frame.frame_id, r, c,
                            int(frame.processed_image[r, c])])

    def _finalize_record(self):
        if self.record_images:
            np.save(self.record_dir / "frames.npy",
                    np.stack([np.asarray(im) for im in self.record_images]))
            self.record_images[0].save(
                self.record_dir / "sequence.gif", save_all=True,
                append_images=self.record_images[1:],
                duration=int(1000 / self.fps), loop=0)

    # ---------------- 离屏驱动(测试/自动截图) ----------------
    def drive(self, n=1, pause_after=False):
        for _ in range(n):
            self.set_playing(True)
            self.on_tick()
        if pause_after:
            self.set_playing(False)

    def screenshot(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.grab().save(str(path))
        return path


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument("--source", choices=["dataset", "rtl", "uart"], default="dataset")
    ap.add_argument("--dataset", default=None,
                    help="数据集名,如 moving_target / 02_single_point;rtl 源为输出名")
    ap.add_argument("--fps", type=int, default=10, choices=SPEEDS)
    ap.add_argument("--interpolation", choices=list(INTERP_MODES), default="nearest")
    ap.add_argument("--scale", choices=["hold", "auto"], default="hold")
    ap.add_argument("--offscreen", action="store_true", help="离屏运行(测试/自动截图)")
    a = ap.parse_args(argv)

    if a.offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    app = QApplication.instance() or QApplication(sys.argv)
    win = ViewerWindow(a.source, a.dataset, a.fps, a.interpolation, a.scale,
                       offscreen=a.offscreen)
    win.show()
    if a.offscreen:
        win.drive(3)
        app.processEvents()
    else:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
