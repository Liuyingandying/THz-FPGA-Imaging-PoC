# 工程调试规则(累积)

本文件沉淀跨阶段验证过的调试纪律。每条规则附来源案例。

## R1:AXI-Lite / MMIO 寄存器窗口不得映射为普通 Normal 内存(2026-10-02,V4.1 Stage A)

**规则**:对 AXI-Lite 外设寄存器窗口,MMU/页属性必须使用平台规定的
强序外设语义(本平台:`STRONG_ORDERED`;通用:Device /
Strongly-Ordered / 对应 MMIO attribute)。禁止默认沿用
`NORM_NONCACHE`,禁止用 sleep/delay/nop 掩盖由此产生的排序问题。

**案例**(docs/FULL_STACK_AUDIT_REPORT.md):
- 现象:板上每图固定 42 像素失配 = **前三列读到上一行**的 ROWSEL 窗口
  数据;505/512 像素正确、协议层零错误、RTL 仿真位级正确。
- 根因:`NORM_NONCACHE` 下 `write ROWSEL → read PIXEL0..2` 无强排序,
  CPU 前几次读发生在 ROWSEL 写入对外设生效之前。
- 决定性 A/B(同位流/同 ELF 逻辑/同硬件):NORM_NONCACHE 20/20 FAIL
  vs STRONG_ORDERED 20/20 BIT-EXACT。
- 最小修复(main_2x8x32.c,commit cc6214e):`Xil_SetTlbAttributes
  (base, STRONG_ORDERED)`;修复后 500/500 门禁 ×2 次独立 PASS。
- 反面教训:该问题曾被误诊为"仿真/综合语义分歧 + row_buf 分布式
  RAM"(见 V41_STAGE_A_GATE_REPORT.md 的 SUPERSEDED 标注)——
  **先怀疑软件内存语义,再怀疑综合器**。

## R2:固定模式探针优先于深层寄存器分析(2026-10-01,UART TX 案例)

见 docs/UART_DIAGNOSIS_POSTMORTEM.md:诊断失败 ≠ 无信号;物理结论
必须有物理测量或通过的探针支撑。

## R3:Windows 构建主机卫生(2026-10-02,Vivado)

- 僵尸 vivado/cscript/bash 会锁 run 目录 → rm 失败 → create_project
  -force 失败;<0.5GB 可用内存会导致 Vivado 随机 elaboration 失败
  ("Could not open 'C'")。
- 处理:构建前跑 scripts/preflight_vivado.ps1(RAM/僵尸/锁/文件齐备
  /版本/ROM 哈希),不满足即 FAIL FAST。
- 路径含空格("image system")时,参考已验证脚本模式
  (`add_files ... [list $path]`),勿改动引号结构。
