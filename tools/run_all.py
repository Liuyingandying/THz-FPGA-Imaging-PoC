#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_all.py — 一键运行整个 PoC 数据链(Windows / Linux 通用)

Step 1  生成合成样本数据集            tools/generate_thz_dataset.py
Step 2  运行 Python 黄金参考          tools/reference_model.py
Step 3  准备 RTL 输入(CSV -> hex 已由生成器完成)
Step 4  编译 RTL(WSL 内 Icarus Verilog;含 POL_NEG 反极性变体)
Step 5  逐数据集运行 testbench 仿真(vvp)
Step 6  收集 RTL 输出
Step 7  reference vs RTL 自动对比     tools/compare_rtl_reference.py
Step 8  生成图像                      tools/visualize_thz_image.py
Step 9  总结输出 + results/verification_summary.csv

若 RTL 仿真器不可用或编译失败:Python 部分照常完成,
总结中显示 RTL simulation: NOT RUN + 原因,绝不伪造 PASS。
"""

import re
import shutil
import subprocess
import sys

import thz_config as C
from thz_config import wsl_path

TOOLS = C.ROOT / "tools"
POL_DATASET = C.POLARITY_DATASET


def sh(cmd, desc, logfile=None):
    """运行命令,打印状态,返回 (rc, stdout+stderr)。"""
    print(f"[run_all] {desc}\n          $ {cmd}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if logfile:
        with open(logfile, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n==== {desc}\n$ {cmd}\n{out}\n[exit {r.returncode}]\n")
    return r.returncode, out


def wsl(cmd, desc, logfile=None):
    inner = f"cd '{wsl_path(C.ROOT)}' && {cmd}"
    return sh(["wsl", "-e", "bash", "-c", inner], desc, logfile)


def check_wsl_iverilog():
    rc, out = wsl("iverilog -V | head -1", "检查 WSL 中的 Icarus Verilog")
    m = re.search(r"Icarus Verilog version ([\d.]+)", out)
    return (m.group(1) if m else None) if rc == 0 else None


def compile_rtl(log):
    rc, out = wsl("iverilog -g2012 -o sim/thz_sim.vvp -s tb_thz_imaging_top "
                  "rtl/thz_input_adapter.sv rtl/thz_offset_calibrator.sv "
                  "rtl/thz_gain_calibrator.sv rtl/thz_temporal_averager.sv "
                  "rtl/thz_frame_mapper.sv rtl/thz_frame_buffer.sv "
                  "rtl/thz_imaging_top.sv tb/tb_thz_imaging_top.sv",
                  "Step 4a 编译 RTL(默认极性 +1)", logfile=log)
    if rc != 0:
        return None
    rc2, out2 = wsl("iverilog -g2012 -D POL_NEG -o sim/thz_sim_polneg.vvp "
                    "-s tb_thz_imaging_top "
                    "rtl/thz_input_adapter.sv rtl/thz_offset_calibrator.sv "
                    "rtl/thz_gain_calibrator.sv rtl/thz_temporal_averager.sv "
                    "rtl/thz_frame_mapper.sv rtl/thz_frame_buffer.sv "
                    "rtl/thz_imaging_top.sv tb/tb_thz_imaging_top.sv",
                    "Step 4b 编译反极性变体(POL_NEG, dataset 13 用)", logfile=log)
    if rc2 != 0:
        return None
    return "sim/thz_sim.vvp"


def run_sim(vvp_target, logfile):
    rc, out = wsl(f"vvp {vvp_target}", f"Step 5 运行 {vvp_target}", logfile=logfile)
    m = re.search(r"TB_RESULT:(.*)", out)
    verdict = "PASS" if (rc == 0 and "TB: PASS" in out) else "FAIL"
    return verdict, (m.group(1).strip() if m else out.strip()[:200])


def main():
    C.ensure_dirs()
    log = C.LOGS_DIR / "run_all.log"
    log.write_text(f"run_all start\niverilog host: WSL\n")

    print("=" * 70)
    print("THz FPGA Imaging PoC - run_all")
    print("=" * 70)

    # ---- Step 1 数据集 ----
    rc, _ = sh([sys.executable, str(TOOLS / "generate_thz_dataset.py")],
               "Step 1 生成合成样本数据集", logfile=log)
    if rc != 0:
        print("数据生成失败,中止"); return 1

    # ---- Step 2 黄金参考 ----
    rc, _ = sh([sys.executable, str(TOOLS / "reference_model.py")],
               "Step 2 运行 Python 黄金参考模型", logfile=log)
    if rc != 0:
        print("黄金参考失败,中止"); return 1

    # ---- Step 3 RTL 输入准备(生成器已写 data/rtl_in/<name>.hex) ----
    print("[run_all] Step 3 RTL 输入 hex 已由生成器准备于 data/rtl_in/")

    # ---- Step 4 编译 ----
    reason = None
    ver = check_wsl_iverilog()
    if ver is None:
        reason = "WSL 中未找到 Icarus Verilog"
        print(f"[run_all] RTL simulation: NOT RUN — {reason}")
        vvp_ok = False
    else:
        print(f"[run_all] 仿真器: Icarus Verilog {ver} (WSL)")
        vvp_ok = compile_rtl(log) is not None
        if not vvp_ok:
            reason = "RTL 编译失败,见 logs/run_all.log"
            print("[run_all] RTL simulation: NOT RUN — " + reason)

    # ---- Step 5/6 逐数据集仿真 ----
    sim_results = {}
    if vvp_ok:
        for num, name, _ in C.DATASETS:
            stem = f"{num}_{name}"
            shutil.copy(C.RTL_IN_DIR / f"{stem}.hex", C.RTL_IN_DIR / "stim.hex")
            target = "sim/thz_sim_polneg.vvp" if stem.startswith(POL_DATASET) \
                else "sim/thz_sim.vvp"
            verdict, info = run_sim(target, log)
            src = C.RTL_OUT_DIR / "rtl_out.txt"
            if src.exists():
                dst = C.RTL_OUT_DIR / f"{stem}_out.txt"
                shutil.copy(src, dst)
                src.unlink()
            sim_results[stem] = (verdict, info)
            print(f"[run_all]   {stem:<24} TB {verdict}   {info}")

    # ---- Step 7 对比 ----
    rc = subprocess.run([sys.executable, str(TOOLS / "compare_rtl_reference.py")],
                        capture_output=True, text=True)
    print(rc.stdout)
    if rc.returncode != 0:
        print(rc.stderr)

    # ---- Step 8 图像 ----
    rcv, outv, _ = (lambda r: (r.returncode, r.stdout, r.stderr))(
        subprocess.run([sys.executable, str(TOOLS / "visualize_thz_image.py")],
                       capture_output=True, text=True))
    print(outv)
    if rcv != 0:
        print("visualize 失败:", outv[-800:])

    # ---- Step 9 总结 ----
    import csv
    summary = list(csv.DictReader(open(C.RESULTS_DIR / "verification_summary.csv")))
    print("=" * 70)
    print("THz FPGA Imaging PoC — 最终总结")
    print("=" * 70)
    print(f"{'dataset':<26}{'result':<14}")
    print("-" * 40)
    for r in summary:
        print(f"{r['dataset']:<26}{r['result']:<14}")
    print("-" * 40)
    if vvp_ok:
        print("RTL simulation: PASS (Icarus Verilog, WSL)"
              if all(v == "PASS" for v, _ in sim_results.values())
              else "RTL simulation: FAIL — 见 logs/run_all.log")
        npass = sum(1 for r in summary if r["result"] == "PASS")
        print(f"Reference comparison: {'PASS' if npass == len(summary) else 'FAIL'}"
              f" ({npass}/{len(summary)})")
    else:
        print("RTL simulation: NOT RUN")
        print("Reason: " + (reason or "unknown"))
    print(f"\n结果文件: results/verification_summary.csv")
    print(f"图像: results/images/, results/figures/")
    print(f"日志: {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
