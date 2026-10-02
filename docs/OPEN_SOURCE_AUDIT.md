# Open Source Audit Report

Date: 2026-10-02 | Version: 1.0.0

## Secret Scan

Manual regex scan across all files (`.py`, `.sv`, `.md`, `.ps1`, `.json`,
`.cff`, `.tcl`) for the following patterns:

- Session IDs (`sess_*`): **0 hits**
- API tokens (`ghp_`, `sk-`, `Bearer`, `api_key`): **0 hits**
- Private keys (`BEGIN PRIVATE KEY`, `ssh-rsa`): **0 hits**
- Passwords (`password`, `secret`): **0 hits**
- JTAG serial numbers (unique hardware IDs): **0 hits**
- USB VID/PID pairs: **0 hits**

Result: **PASS**

## Privacy Scan

- Personal names/emails: **0 hits** (CITATION.cff uses minimal repository fields)
- Windows absolute paths (`C:\Users`, `E:\Research`, `D:\`): **0 hits**
  (all references converted to repo-relative)
- COM port numbers (COM14, COM5, etc.): **0 hits** in code defaults;
  serial port is auto-detected or passed as parameter
- Internal session/feedback documents: **excluded from export**

Result: **PASS**

## License Scan

| Category | Files | License | Included |
|---|---|---|---|
| Original RTL (8 .sv) | rtl/ | Apache-2.0 (this repo) | Yes |
| Original testbench (3 .sv) | tb/ | Apache-2.0 | Yes |
| Original Python tools (16 .py) | tools/ | Apache-2.0 | Yes |
| Original scripts (2 .ps1) | scripts/ | Apache-2.0 | Yes |
| Original docs (~12 .md) | docs/ | Apache-2.0 | Yes |
| Synthetic datasets (.hex, .csv, .json) | data/, results/ | Apache-2.0 | Yes |
| Demo assets (.png, .gif) | docs/assets/ | Apache-2.0 | Yes |
| Xilinx BSP/generated code | NOT INCLUDED | Proprietary | No |
| Vivado/Vitis project files | NOT INCLUDED | Proprietary | No |
| Third-party repos (third_party/) | NOT INCLUDED | Various | No |
| Bitstream/ELF binaries | NOT INCLUDED | License unclear | No |

No code was copied from third-party repositories. Design patterns inspired
by open-source projects are acknowledged in THIRD_PARTY_NOTICES.md.

Result: **PASS** (Apache-2.0 applicable)

## Large-File Scan

| Size threshold | Count | Files |
|---|---|---|
| > 1 MB | 0 | (none) |
| > 10 MB | 0 | (none) |
| > 50 MB | 0 | (none) |

Total repository size: ~1.4 MB

Result: **PASS**

## Vendor-File Scan

- Xilinx IP (.xci, .xcix): **0 files**
- Vivado generated (.gen, .cache, .runs): **0 files** (in .gitignore)
- Vitis BSP/workspace: **0 files** (excluded)
- FSBL: **0 files** (excluded)
- Bitstream (.bit): **0 files** (in .gitignore; build locally)

Result: **PASS**

## Excluded Content

| Content | Reason |
|---|---|
| fpga_ax7020/ (board project) | Contains Vivado/Vitis proprietary files, BSP code |
| Bitstream + ELF binaries | Redistribution license unclear |
| UART raw captures (.bin dumps) | Large, contains no reusable information |
| Internal debugging history | Session-specific, not useful publicly |
| Advisor feedback reports | Internal/private documents |
| Release frozen artifacts | Intended for internal archival |

## Overall Result

**PASS** — All scans clean. Safe for public release.
