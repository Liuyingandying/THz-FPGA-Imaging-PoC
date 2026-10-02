// ============================================================================
// tb_scn512.sv — 16×32(512 通道)场景级验证 testbench(新增文件)
//
// 阵列建模假设:两排 8×32 上下拼接为 16×32(row-major 恒等映射),
// 见 docs/PHYSICAL_SCENE_16x32_VALIDATION.md。既有 RTL 零改动 —— 仅通过
// thz_imaging_top 的既有参数(NUM_CHANNELS/ROWS/COLS/标定表路径)实例化。
//
// 输入:stim.hex   data/scn512/rtl_in/stim.hex   (AVG_FRAMES×512 个 12bit 采样)
// 输出:OUT_FILE   results/scn512/rtl_out/rtl_out.txt ("row col value"/行)
// 自检:像素数、行列序列、帧尾位置、看门狗;数值由 Python 黄金参考位级比对。
// ============================================================================
`timescale 1ns / 1ps

`ifndef STIM_FILE
`define STIM_FILE "data/scn512/rtl_in/stim.hex"
`endif
`ifndef OUT_FILE
`define OUT_FILE "results/scn512/rtl_out/rtl_out.txt"
`endif
`ifndef DARK_FILE
`define DARK_FILE "data/scn512/calib/dark.hex"
`endif
`ifndef GAIN_FILE
`define GAIN_FILE "data/scn512/calib/gain.hex"
`endif
`ifndef MAP_FILE
`define MAP_FILE "data/scn512/calib/channel_map.hex"
`endif

module tb_scn512;
    localparam NUM_CHANNELS = 512, ROWS = 16, COLS = 32;
    localparam ADC_WIDTH = 12, PIXEL_WIDTH = 16, GAIN_FRAC = 8;
    localparam AVG_FRAMES    = 4;
    localparam NUM_IN        = AVG_FRAMES * NUM_CHANNELS;   // 2048
    localparam EXP_PIXELS    = NUM_CHANNELS;
    localparam CLK_PERIOD    = 10;

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

    reg [ADC_WIDTH-1:0] stim [0:NUM_IN-1];
    initial $readmemh(`STIM_FILE, stim);

    reg [$clog2(ROWS)-1:0] exp_row [0:NUM_CHANNELS-1];
    reg [$clog2(COLS)-1:0] exp_col [0:NUM_CHANNELS-1];
    integer p;
    initial begin
        for (p = 0; p < NUM_CHANNELS; p = p + 1) begin
            exp_row[p] = p / COLS;
            exp_col[p] = p % COLS;
        end
    end

    // ---- 驱动(negedge 施加,posedge 采样;随机气泡) ----
    integer seed = 32'h51C2_0001;
    integer idle;
    initial begin : drive
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
            @(posedge clk);
            while (!s_ready) @(posedge clk);
        end
        @(negedge clk);
        s_valid <= 0;
        s_last  <= 0;
    end

    // ---- 输出端随机反压(75% ready) ----
    integer seed2 = 32'h51C2_0002;
    always @(negedge clk) begin
        if (!rstn) m_ready <= 0;
        else       m_ready <= (($random(seed2) % 4) != 0);
    end

    // ---- 监视与记录 ----
    integer outfile;
    initial begin : monitor
        outfile = $fopen(`OUT_FILE, "w");
        if (outfile == 0) begin
            $display("TB_RESULT: FATAL cannot open output file");
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
                    $display("TB_RESULT: PIXELS=%0d/%0d ERRORS=%0d SAT=%0d",
                             got, EXP_PIXELS, errors, sat_events);
                    if (errors == 0) $display("TB: PASS");
                    else             $display("TB: FAIL");
                    $finish;
                end
            end
        end
    end

    initial begin
        #(CLK_PERIOD * 1000000);
        $display("TB_RESULT: TIMEOUT got=%0d/%0d", got, EXP_PIXELS);
        $display("TB: FAIL");
        $finish;
    end
endmodule
