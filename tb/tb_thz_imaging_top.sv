// ============================================================================
// tb_thz_imaging_top.sv — thz_imaging_top 的自动验证 testbench
//
// 输入:stimulus hex 文件(每行一个 12-bit ADC 码,共 EXPECT_FRAMES*AVG_FRAMES*32 个)
// 输出:OUT_FILE,每行 "row col value"(十进制),仅当 m_valid&&m_ready 时记录
//
// 自检项:
//   1) 输出像素数 == EXPECT_FRAMES * NUM_CHANNELS
//   2) row/col 序列 == channel_map 定义的行主序
//   3) m_last 帧尾位置正确(每帧最后一个像素)
//   4) 饱和事件统计(value==PIXEL_MAX,报告而非失败)
//   5) 超时看门狗
// 驱动特点:输入端随机空闲气泡 + 输出端 75% 随机反压,验证 valid/ready 无损。
// 种子固定 → 仿真完全可复现。
// ============================================================================
`timescale 1ns / 1ps

`ifndef STIM_FILE
`define STIM_FILE "data/rtl_in/stim.hex"
`endif
`ifndef OUT_FILE
`define OUT_FILE "results/rtl/rtl_out.txt"
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
`ifndef EXPECT_FRAMES
`define EXPECT_FRAMES 1
`endif
`ifndef AVG_FRAMES
`define AVG_FRAMES 4
`endif

module tb_thz_imaging_top;
    localparam NUM_CHANNELS = 32, ROWS = 4, COLS = 8;
    localparam ADC_WIDTH = 12, PIXEL_WIDTH = 16, GAIN_FRAC = 8;
    localparam EXPECT_FRAMES = `EXPECT_FRAMES;
    localparam AVG_FRAMES    = `AVG_FRAMES;
    localparam NUM_IN     = EXPECT_FRAMES * AVG_FRAMES * NUM_CHANNELS;
    localparam EXP_PIXELS = EXPECT_FRAMES * NUM_CHANNELS;
    localparam CLK_PERIOD = 10;

    reg clk = 0;
    reg rstn = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    reg                    s_valid = 0, s_last = 0;
    reg  [ADC_WIDTH-1:0]   s_data  = 0;
    wire                   s_ready;
    wire                   m_valid, m_last;
    wire [PIXEL_WIDTH-1:0] m_data;
    wire [$clog2(ROWS)-1:0] m_row;
    wire [$clog2(COLS)-1:0] m_col;
    reg                    m_ready = 0;

    integer errors = 0, got = 0, sent = 0, sat_events = 0;

    // ---- DUT ----
    thz_imaging_top #(
        .NUM_CHANNELS(NUM_CHANNELS), .ROWS(ROWS), .COLS(COLS),
        .ADC_WIDTH(ADC_WIDTH), .PIXEL_WIDTH(PIXEL_WIDTH),
        .GAIN_FRAC(GAIN_FRAC), .AVG_FRAMES(AVG_FRAMES),
        .DARK_FILE(`DARK_FILE), .GAIN_FILE(`GAIN_FILE), .MAP_FILE(`MAP_FILE)
    ) dut (
        .clk(clk), .rstn(rstn),
        .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(m_valid), .m_data(m_data), .m_row(m_row), .m_col(m_col),
        .m_last(m_last), .m_ready(m_ready));

    // ---- 激励 ----
    reg [ADC_WIDTH-1:0] stim [0:NUM_IN-1];
    initial $readmemh(`STIM_FILE, stim);

    // 期望的行主序像素坐标(由映射表定义,恒等映射下即 row-major)
    reg [$clog2(ROWS)-1:0] exp_row [0:NUM_CHANNELS-1];
    reg [$clog2(COLS)-1:0] exp_col [0:NUM_CHANNELS-1];
    integer p;
    initial begin
        for (p = 0; p < NUM_CHANNELS; p = p + 1) begin
            exp_row[p] = p / COLS;
            exp_col[p] = p % COLS;
        end
    end

    // ---- 驱动(negedge 施加激励,posedge 采样握手 → 无竞态) ----
    integer seed = 32'h5EED_0001;
    integer idle;
    initial begin : drive
        $dumpfile("sim/tb.vcd");
        $dumpvars(1, tb_thz_imaging_top);
        repeat (5) @(negedge clk);
        rstn = 1;
        for (sent = 0; sent < NUM_IN; sent = sent + 1) begin
            idle = $random(seed) % 3;
            if (idle < 0) idle = -idle;
            repeat (idle) begin
                @(negedge clk);
                s_valid <= 0;
            end
            @(negedge clk);
            s_valid <= 1;
            s_data  <= stim[sent];
            s_last  <= ((sent % NUM_CHANNELS) == NUM_CHANNELS-1);
            @(posedge clk);                    // DUT 在此沿采样
            while (!s_ready) @(posedge clk);   // valid/data 保持直到被接收
        end
        @(negedge clk);
        s_valid <= 0;
        s_last  <= 0;
    end

    // ---- 输出端随机反压(negedge 更新,posedge 采样稳定) ----
    integer seed2 = 32'h5EED_0002;
    always @(negedge clk) begin
        if (!rstn) m_ready <= 0;
        else       m_ready <= (($random(seed2) % 4) != 0);
    end

    // ---- 监视与记录 ----
    integer outfile;
    initial begin : monitor
        outfile = $fopen(`OUT_FILE, "w");
        if (outfile == 0) begin
            $display("TB_RESULT: FATAL cannot open output file `OUT_FILE");
            $finish;
        end
        forever begin
            @(posedge clk);
            if (rstn && m_valid && m_ready) begin
                $fwrite(outfile, "%0d %0d %0d\n", m_row, m_col, m_data);
                if (m_data == {PIXEL_WIDTH{1'b1}}) sat_events = sat_events + 1;
                if (m_row !== exp_row[got] || m_col !== exp_col[got]) begin
                    errors = errors + 1;
                    $display("[TB] ERROR pixel %0d: got (%0d,%0d) exp (%0d,%0d)",
                             got, m_row, m_col, exp_row[got], exp_col[got]);
                end
                if (m_last !== ((got % NUM_CHANNELS) == NUM_CHANNELS-1)) begin
                    errors = errors + 1;
                    $display("[TB] ERROR m_last wrong at pixel %0d", got);
                end
                got = got + 1;
                if (got == EXP_PIXELS) begin
                    $fclose(outfile);
                    $display("TB_RESULT: PIXELS=%0d/%0d ERRORS=%0d SATURATION=%0d",
                             got, EXP_PIXELS, errors, sat_events);
                    if (errors == 0) $display("TB: PASS");
                    else             $display("TB: FAIL");
                    $finish;
                end
            end
        end
    end

    // ---- 看门狗 ----
    initial begin
        #(CLK_PERIOD * 200000);
        $display("TB_RESULT: TIMEOUT got=%0d/%0d", got, EXP_PIXELS);
        $display("TB: FAIL");
        $finish;
    end
endmodule
