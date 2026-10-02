// ============================================================================
// thz_row_scanner.sv — 2×(8×32) 数字样机行扫描 wrapper(Phase V4-Demo)
//
// 工程假设(非真实样机结构结论,见 docs/THZ_2X8X32_DIGITAL_TWIN_DEMO.md):
//   两排 8×32 探测阵列;读出 = 32 列方向并行模拟通道 × 16 行顺序扫描。
//   global_row 0..7 = Bank A,global_row 8..15 = Bank B。
//
// 职责:把"整帧 512 采样流"重整为"逐行喂给现有 32 通道成像核"的行流,
// 并在核输出侧按 (bank,row) 收集 16 行重建帧:
//   输入 s_*: 512 采样/帧(行主序,即 row0 ch0..31, row1 ch0..31, ...)
//             s_last = 每帧第 512 个采样。
//   内部: 每凑齐 32 个采样形成一个行流(带行内 s_last_row)驱动
//         thz_imaging_top(32ch/1×32 参数);行流间隔由 FILL/DUMP 自然节拍。
//   输出 m_*: 与核输出相同语义,但重新拼接为 512 像素帧:
//             m_pixel_valid + pixel[15:0] + bank_id + row_id + frame_last。
//
// 现有成像核(thz_imaging_top 七模块)零修改 —— 仅参数化实例化(NUM_CHANNELS=32,
// ROWS=1, COLS=32, AVG_FRAMES=4),与已验证 19/19 配置同核。
// ============================================================================
module thz_row_scanner #(
    parameter ADC_WIDTH   = 12,
    parameter PIXEL_WIDTH = 16,
    parameter GAIN_FRAC   = 8,
    parameter AVG_FRAMES  = 4,
    parameter BANKS       = 2,               // 两排 8×32
    parameter ROWS_PER_BANK = 8,
    parameter COLS        = 32,
    localparam NUM_CH       = COLS,          // 32 并行通道
    localparam TOTAL_ROWS   = BANKS * ROWS_PER_BANK,   // 16
    localparam FRAME_SAMPLES = TOTAL_ROWS * COLS,      // 512
    localparam ROW_W = $clog2(ROWS_PER_BANK),
    localparam BANK_W = $clog2(BANKS),
    localparam CH_W  = $clog2(NUM_CH),
    parameter DARK_FILE = "data/calib/dark.hex",
    parameter GAIN_FILE = "data/calib/gain.hex",
    parameter MAP_FILE  = "data/calib/channel_map.hex"
)(
    input                       clk,
    input                       rstn,
    // 整帧采样流输入(行主序 512/帧)
    input                       s_valid,
    input  [ADC_WIDTH-1:0]      s_data,
    input                       s_last,        // 帧 = 第 512 个采样
    output                      s_ready,
    // 拼接后的重建帧输出
    output                      m_pixel_valid,
    output [PIXEL_WIDTH-1:0]    m_pixel,
    output [BANK_W-1:0]         m_bank,
    output [ROW_W-1:0]          m_row,
    output                      frame_last,    // 每帧第 512 个输出像素
    input                       m_pixel_ready,
    output [95:0]               audit_data,
    output [5:0]                audit_fire,
    output [ADC_WIDTH-1:0]      audit_core_data,
    output                      audit_core_fire
);
    // ---------------- 行缓冲:把 512 流切成 16 个 32-sample 行 ----------------
    reg [ADC_WIDTH-1:0] row_buf [0:NUM_CH-1];
    reg [CH_W:0]        row_cnt;              // 0..32
    reg [ROW_W+BANK_W:0] grow_cnt;            // 0..15(global row)

    // ---------------- 喂行流的握手(核的 s_ready 透传) ----------------
    wire core_s_ready;
    wire feed_fire = core_s_ready & core_s_valid;
    reg  core_s_valid;
    reg  core_s_last_row;                     // 行内第 32 个采样
    reg [ADC_WIDTH-1:0] core_s_data;

    assign audit_core_data = core_s_data;
    assign audit_core_fire = core_s_valid && core_s_ready;

    // 行发射状态:0=idle 等凑行;1=正在把 row_buf 按序送入核
    reg        emitting;
    reg [CH_W:0] emit_idx;

    assign s_ready = !emitting;               // 凑行期间接收整帧流

    always @(posedge clk) begin
        if (!rstn) begin
            row_cnt <= 0; grow_cnt <= 0;
            emitting <= 0; emit_idx <= 0;
            core_s_valid <= 0; core_s_last_row <= 0; core_s_data <= 0;
        end else begin
            // ---- 行流发射优先 ----
            if (emitting) begin
                if (core_s_ready || !core_s_valid) begin
                    if (emit_idx == NUM_CH) begin
                        emitting <= 0; core_s_valid <= 0;
                        emit_idx <= 0;
                    end else begin
                        core_s_valid  <= 1;
                        core_s_data   <= row_buf[emit_idx[CH_W-1:0]];
                        core_s_last_row <= (emit_idx == NUM_CH-1);
                        emit_idx <= emit_idx + 1'b1;
                    end
                end
            end
            // ---- 整帧流接收 ----
            if (s_valid && s_ready) begin
                row_buf[row_cnt[CH_W-1:0]] <= s_data;
                if (row_cnt == NUM_CH-1) begin
                    row_cnt  <= 0;
                    emitting <= 1;            // 凑齐一行,发射(行首)
                    emit_idx <= 0;
                    grow_cnt <= s_last ? 0 : grow_cnt + 1'b1;  // 帧回绕
                end else begin
                    row_cnt <= row_cnt + 1'b1;
                end
            end
        end
    end

    // ---------------- 现有 32 通道成像核(零修改,参数化实例) ----------------
    wire                     core_m_valid, core_m_last;
    wire [PIXEL_WIDTH-1:0]   core_m_data;
    wire                     core_m_ready;
    // 核按 1×32 几何输出:m_row 恒 0,直接用 m_last 作为行尾
    assign core_m_ready = m_pixel_ready;      // 行像素直通(反压透传)

    thz_imaging_top #(
        .NUM_CHANNELS(NUM_CH), .ROWS(1), .COLS(COLS),
        .ADC_WIDTH(ADC_WIDTH), .PIXEL_WIDTH(PIXEL_WIDTH),
        .GAIN_FRAC(GAIN_FRAC), .AVG_FRAMES(AVG_FRAMES),
        .DARK_FILE(DARK_FILE), .GAIN_FILE(GAIN_FILE), .MAP_FILE(MAP_FILE)
    ) U_CORE (
        .clk(clk), .rstn(rstn),
        .s_valid(core_s_valid), .s_data(core_s_data),
        .s_last(core_s_last_row), .s_ready(core_s_ready),
        .m_valid(core_m_valid), .m_data(core_m_data),
        .m_row(), .m_col(), .m_last(core_m_last), .m_ready(core_m_ready),
        .audit_data(audit_data), .audit_fire(audit_fire));

    // ---------------- 输出拼接:16 行 × 32 像素 → 重建帧 ----------------
    reg [ROW_W+BANK_W:0] out_grow;            // 当前输出 global row 0..15
    reg [CH_W:0]         out_col;             // 行内 0..31

    assign m_pixel_valid = core_m_valid;
    assign m_pixel       = core_m_data;
    assign m_bank        = out_grow[ROW_W+BANK_W-1:ROW_W];
    assign m_row         = out_grow[ROW_W-1:0];

    always @(posedge clk) begin
        if (!rstn) begin
            out_grow <= 0; out_col <= 0;
        end else if (core_m_valid && core_m_ready) begin
            if (core_m_last) begin            // 行尾 → 下一行
                out_grow <= (out_grow == TOTAL_ROWS-1) ? 0 : out_grow + 1'b1;
                out_col  <= 0;
            end else begin
                out_col <= out_col + 1'b1;
            end
        end
    end

    assign frame_last = core_m_valid && core_m_last
                        && (out_grow == TOTAL_ROWS-1);
endmodule
