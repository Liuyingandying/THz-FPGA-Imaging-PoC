// ============================================================================
// tb_row_scanner.sv — 2×(8×32) 数字样机行扫描链路 TB(Phase V4-Demo)
//
// 输入: STIM_FILE = data/demo2x8x32/rtl_in/stim.hex
//       每帧 512 采样(AVG_FRAMES 帧为一组,与核平均一致),支持多组(场景帧序列)
// 输出: OUT_FILE  = data/demo2x8x32/rtl_out/rtl_out.txt
//       每行 "grow col value bank row"(十进制)
// 自检: 输出像素数 = 组数×512;bank/row 递进序列;帧尾标志;看门狗。
// 数值位级比对由 Python 黄金参考完成(test 工具)。
// ============================================================================
`timescale 1ns / 1ps

`ifndef STIM_FILE
`define STIM_FILE "data/demo2x8x32/rtl_in/stim.hex"
`endif
`ifndef OUT_FILE
`define OUT_FILE "data/demo2x8x32/rtl_out/rtl_out.txt"
`endif
`ifndef DARK_FILE
`define DARK_FILE "data/calib/dark.hex"
`endif
`ifndef GAIN_FILE
`define GAIN_FILE "data/calib/gain.hex"
`endif
`ifndef MAP_FILE
`define MAP_FILE "data/calib/channel_map.hex"
`endif
`ifndef AVG_FRAMES
`define AVG_FRAMES 4
`endif
`ifndef N_GROUPS
`define N_GROUPS 1
`endif

module tb_row_scanner;
    localparam ADC_WIDTH = 12, PIXEL_WIDTH = 16, GAIN_FRAC = 8;
    localparam AVG = `AVG_FRAMES;
    localparam TOTAL_ROWS = 16, COLS = 32;
    localparam FRAME_SAMPLES = TOTAL_ROWS * COLS;    // 512
    localparam NUM_IN   = AVG * FRAME_SAMPLES;       // 2048/组
    localparam EXP_PIX  = `N_GROUPS * FRAME_SAMPLES;
    localparam CLK_PERIOD = 10;

    reg clk = 0, rstn = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    reg                    s_valid = 0, s_last = 0;
    reg  [ADC_WIDTH-1:0]   s_data  = 0;
    wire                   s_ready;
    wire                   m_pixel_valid;
    wire [PIXEL_WIDTH-1:0] m_pixel;
    wire [0:0]             m_bank;
    wire [2:0]             m_row;
    wire                   frame_last;
    reg                    m_pixel_ready = 0;

    integer errors = 0, got = 0, sent = 0;

    thz_row_scanner #(
        .ADC_WIDTH(ADC_WIDTH), .PIXEL_WIDTH(PIXEL_WIDTH),
        .GAIN_FRAC(GAIN_FRAC), .AVG_FRAMES(AVG),
        .DARK_FILE(`DARK_FILE), .GAIN_FILE(`GAIN_FILE), .MAP_FILE(`MAP_FILE)
    ) dut (
        .clk(clk), .rstn(rstn),
        .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_pixel_valid(m_pixel_valid), .m_pixel(m_pixel),
        .m_bank(m_bank), .m_row(m_row), .frame_last(frame_last),
        .m_pixel_ready(m_pixel_ready));

    // ---- 激励 ----
    reg [ADC_WIDTH-1:0] stim [0:NUM_IN*`N_GROUPS-1];
    initial $readmemh(`STIM_FILE, stim);

    integer seed = 32'hD2E0_0001;
    integer idle;
    initial begin : drive
        repeat (5) @(negedge clk);
        rstn = 1;
        for (sent = 0; sent < NUM_IN*`N_GROUPS; sent = sent + 1) begin
            idle = $random(seed) % 3;
            if (idle < 0) idle = -idle;
            repeat (idle) begin
                @(negedge clk);
                s_valid <= 0;
            end
            @(negedge clk);
            s_valid <= 1;
            s_data  <= stim[sent];
            s_last  <= ((sent % FRAME_SAMPLES) == FRAME_SAMPLES-1);
            @(posedge clk);
            while (!s_ready) @(posedge clk);
        end
        @(negedge clk);
        s_valid <= 0; s_last <= 0;
    end

    // ---- 输出反压(75% ready) ----
    integer seed2 = 32'hD2E0_0002;
    always @(negedge clk) begin
        if (!rstn) m_pixel_ready <= 0;
        else       m_pixel_ready <= (($random(seed2) % 4) != 0);
    end

    // ---- 监视 ----
    integer outfile;
    integer exp_grow = 0, exp_col = 0;
    initial begin : monitor
        outfile = $fopen(`OUT_FILE, "w");
        if (outfile == 0) begin
            $display("TB_RESULT: FATAL cannot open OUT_FILE");
            $finish;
        end
        forever begin
            @(posedge clk);
            if (rstn && m_pixel_valid && m_pixel_ready) begin
                $fwrite(outfile, "%0d %0d %0d %0d %0d\n",
                        exp_grow, exp_col, m_pixel, m_bank, m_row);
                if (m_bank !== ((exp_grow >= 8) ? 1 : 0)
                    || m_row !== (exp_grow % 8)) begin
                    errors = errors + 1;
                    $display("[TB] ERROR bank/row at grow=%0d: got b%0d r%0d",
                             exp_grow, m_bank, m_row);
                end
                if (frame_last !== ((exp_grow == 15) && (exp_col == 31))) begin
                    errors = errors + 1;
                    $display("[TB] ERROR frame_last at grow=%0d col=%0d",
                             exp_grow, exp_col);
                end
                if (exp_col == 31) begin
                    exp_col = 0;
                    exp_grow = (exp_grow == 15) ? 0 : exp_grow + 1;
                end else begin
                    exp_col = exp_col + 1;
                end
                got = got + 1;
                if (got == EXP_PIX) begin
                    $fclose(outfile);
                    $display("TB_RESULT: PIXELS=%0d/%0d ERRORS=%0d",
                             got, EXP_PIX, errors);
                    if (errors == 0) $display("TB: PASS");
                    else             $display("TB: FAIL");
                    $finish;
                end
            end
        end
    end

    initial begin
        #(CLK_PERIOD * 3000000);
        $display("TB_RESULT: TIMEOUT got=%0d/%0d", got, EXP_PIX);
        $display("TB: FAIL");
        $finish;
    end
endmodule
