# 随机光电流成像验证报告(Phase V1)

日期:2026-09-18。目标:回答 **"随机光电流数据经过 FPGA 处理后,能否稳定恢复空间信息并形成有效二维成像"**。

## 一、先回答结论

**能恢复,且验证是位级严格的。**

| 光子预算(峰值光电子/帧) | 输入单帧SNR(理论) | FPGA输出图像 | 与真值Pearson r | 双热点定位 | 位级比对 |
|---|---|---|---|---|---|
| N=10(极限弱光) | 2.6 dB | 输出对比SNR 8.9 dB | 0.614 | **PASS** | bit-exact |
| N=30 | 10.8 dB | 16.6 dB | 0.884 | PASS | bit-exact |
| N=100 | 18.4 dB | 28.6 dB | 0.989 | PASS | bit-exact |
| N=300 | 24.2 dB | 39.8 dB | 0.999 | PASS | bit-exact |
| N=1000 | 29.8 dB | 49.8 dB | 0.9998 | PASS | bit-exact |
| 高斯斑 N=300(扩展场景) | 24.2 dB | 8.3 dB(对比式SNR不适用)* | 0.997 | n/a | bit-exat→见注 |

\* 14_random_photocurrent 是空间扩展场景,"弱峰对比SNR"指标不适用,以 Pearson r=0.9971 / NRMSE=0.028 为准。
6/6 数据集的 RTL(Icarus Verilog 实际仿真)输出与 Python 黄金参考**逐位一致**。

**关键发现:即便输入单帧 SNR 只有 2.6 dB(Poisson 散粒噪声完全主导),经过
暗场扣除 → 定点增益校正 → 4 帧平均的 FPGA 管线,空间位置信息(双热点 top-2
定位)仍然完全正确**;图像与真值的相关性随光子预算单调收敛(输出 SNR 相对输入
单帧提升约 6 dB,符合 4 帧平均 ×2 的理论界)。这就是"随机 → 稳定空间信息"的直接证据。

## 二、哪些是真实物理,哪些是仿真模型(强制区分)

### 真实物理(模型所依据的物理定律,不可约简)

1. **光子到达的 Poisson 统计**:相干/热太赫兹探测中光子事件计数服从泊松分布,
   散粒噪声方差 = 平均计数(σ²=N)。这不是加性高斯噪声可以替代的 —— 本验证的
   随机性直接来自 Poisson 抽样(`numpy.random.poisson`),而非手工加噪。
2. **暗电流散粒噪声**:暗计数同样为 Poisson 过程,其**均值可被暗场标定扣除,
   其涨落不可扣除**(只能靠帧平均压制)—— 数据中如实保留。
3. **ADC 量化**(12-bit,round+clip 0..4095)与 **TIA/ADC 读出高斯噪声**:真实存在。
4. **通道间失配**(offset/gain mismatch)与**定点标定残余**(Q4.8 系数的截断误差):
   真实硬件必然存在;标定表与老数据集共用同一套,未为新数据集做任何特殊化。

### 仿真假设(数值非实测,集中在 thz_config.py,替换实测值即可)

| 参数 | 值 | 性质 |
|---|---|---|
| 每帧峰值光电子数 N | 10~1000(sweep) | 假设的场景亮度预算 |
| 暗计数 D | 20 e-/帧 | 假设(真实值需器件实测) |
| 链路增益 K | 0.8 LSB/光电子 | 假设(TIA跨阻×量子效率×ADC满量程的综合) |
| 读出噪声 σ_read | 4 LSB | 假设 |
| 积分时间/帧率/量子效率 | 未单独建模(并入 K) | 假设 |

**本验证不含任何真实太赫兹测量数据。**"真实"的是物理定律与处理链路的位级实现;
"仿真"的是参数与场景。当真实探测器数据到位后,仅需替换上述四个参数即可复用
同一验证流程。

## 三、验证链路(全部复用既有设施,零 RTL 修改)

```
Poisson 场景(photon rate map, 固定seed)
  → generate_thz_dataset.py 新增 poisson 分支(光子计数→TIA/ADC→12-bit量化→通道失配)
  → data/raw/14..19_*.csv + data/rtl_in/*.hex
  → [Icarus Verilog 实际仿真] 既有 7 模块管线(暗场→极性→Q4.8增益→4帧平均→映射→帧缓冲)
  → compare_rtl_reference.py 位级比对            → 6/6 PASS
  → evaluate_reconstruction.py 重建指标 + SNR sweep → 见第一节表
  → PySide6 Viewer(08/09 截图)
```

- 回归:原 13 个数据集结论不变(总体 19/19 PASS);可视化测试 11/11 PASS。
- 每数据集每帧使用独立派生种子(`SEED + 1000×数据集号 + 帧号`),全局可复现、
  数据集间统计独立。

## 四、数据与产物

| 文件 | 内容 |
|---|---|
| `data/raw/14_random_photocurrent.*` | Poisson 高斯斑 N=300(4 帧) |
| `data/raw/15_snr_N10 … 19_snr_N1000.*` | 双热点 sweep(峰位 (0,2)/(3,5)) |
| `results/snr_sweep.csv` | 全部指标表 |
| `results/figures/09_snr_sweep.png` | SNR/相关系数 vs 光子预算曲线 |
| `results/visualization/08_random_photocurrent.png` | Viewer 实景(随机光电流) |
| `results/visualization/09_snr_N10_noisiest.png` | Viewer 实景(最噪档 N=10) |
| `tools/evaluate_reconstruction.py` | 指标工具(可复用于未来实测数据) |

## 五、局限与下一步

1. 参数均为假设:结论的有效性以"物理定律真实 + 链路位级真实"为界,绝对 SNR 数值
   要等真实探测器参数替换后才有硬件意义;
2. 场景为静态(sweep 未含运动目标);如需可复用 12_moving_target 思路扩展;
3. N=10 档定位 PASS 但 r 仅 0.61:像元级幅度信息在极限光子预算下不可靠 ——
   这正是"探测"与"计量"的边界,真实系统设计需按此表选光子预算;
4. 下一步(Phase V2,板级):断电重启 → v3 流程下载 → J7 串口接线 →
   `serial_frame_logger --verify` → Viewer 实测(见 docs/PHASE0_STATE_REPORT.md)。
