// ============================================================================
// thz_imaging_top.sv — 32 通道太赫兹阵列成像 PoC 顶层
//
// 处理链:
//   input stream -> input adapter -> offset calibrator (暗场+极性)
//     -> gain calibrator (Q4.8 定点) -> temporal averager (N 帧平均)
//     -> frame mapper (通道→像素) -> frame buffer -> host interface
//
// 参数与 tools/thz_config.py 保持一致(NUM_CHANNELS=32, ROWS=4, COLS=8,
// ADC_WIDTH=12, PIXEL_WIDTH=16, GAIN_FRAC=8, AVG_FRAMES=4)。
//
// 标定表通过 $readmemh 载入,均为【仿真标定数据】,非实测:
//   DARK_FILE  data/calib/dark.hex         (12-bit/通道)
//   GAIN_FILE  data/calib/gain.hex         (Q4.8, 16-bit/通道)
//   MAP_FILE   data/calib/channel_map.hex  (像素线性索引)
//
// 未来接入真实 ADC / UART:将对应前端转成 s_valid/s_data/s_last 流接入
// 本模块输入即可,管线零改动。
// ============================================================================
module thz_imaging_top #(
    parameter NUM_CHANNELS = 32,
    parameter ROWS         = 4,
    parameter COLS         = 8,
    parameter ADC_WIDTH    = 12,
    parameter PIXEL_WIDTH  = 16,
    parameter GAIN_FRAC    = 8,
    parameter AVG_FRAMES   = 4,
`ifdef POL_NEG
    parameter POLARITY     = -1,     // 反极性系统变体: signal = dark - raw
`else
    parameter POLARITY     = 1,      // 默认: signal = raw - dark
`endif
    parameter DARK_FILE    = "data/calib/dark.hex",
    parameter GAIN_FILE    = "data/calib/gain.hex",
    parameter MAP_FILE     = "data/calib/channel_map.hex",
    localparam CH_W  = $clog2(NUM_CHANNELS),
    localparam ROW_W = (ROWS <= 1) ? 1 : $clog2(ROWS),
    localparam COL_W = (COLS <= 1) ? 1 : $clog2(COLS)
)(
    input                      clk,
    input                      rstn,
    // 原始采样流输入
    input                      s_valid,
    input  [ADC_WIDTH-1:0]     s_data,
    input                      s_last,
    output                     s_ready,
    // 处理后帧流输出(行主序,来自帧缓冲读出)
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [ROW_W-1:0]         m_row,
    output [COL_W-1:0]         m_col,
    output                     m_last,
    input                      m_ready,
    // Optional audit taps: accepted beats, removed when left unconnected.
    output [95:0]              audit_data,
    output [5:0]               audit_fire
);
    // ---- 标定表 ROM(分布式,仿真用 $readmemh 初始化) ----
    reg [ADC_WIDTH-1:0] dark_table [0:NUM_CHANNELS-1];
    reg [15:0]          gain_table [0:NUM_CHANNELS-1];
    reg [CH_W-1:0]      map_table  [0:NUM_CHANNELS-1];

    initial begin
        $readmemh(DARK_FILE, dark_table);
        $readmemh(GAIN_FILE, gain_table);
        $readmemh(MAP_FILE,  map_table);
    end

    // ---- 级间连线 ----
    wire                     a_valid, a_ready;
    wire [ADC_WIDTH-1:0]     a_data;
    wire [CH_W-1:0]          a_ch;

    wire                     o_valid, o_ready;
    wire [PIXEL_WIDTH-1:0]   o_data;
    wire [CH_W-1:0]          o_ch;

    wire                     g_valid, g_ready;
    wire [PIXEL_WIDTH-1:0]   g_data;
    wire [CH_W-1:0]          g_ch;

    wire                     t_valid, t_ready;
    wire [PIXEL_WIDTH-1:0]   t_data;
    wire [CH_W-1:0]          t_ch;

    wire                     p_valid, p_ready;
    wire [PIXEL_WIDTH-1:0]   p_data;
    wire [ROW_W-1:0]         p_row;
    wire [COL_W-1:0]         p_col;
    wire                     p_last;

    assign audit_data = {m_data, p_data, t_data, g_data, o_data,
                         {{(16-ADC_WIDTH){1'b0}}, a_data}};
    assign audit_fire = {m_valid && m_ready, p_valid && p_ready,
                         t_valid && t_ready, g_valid && g_ready,
                         o_valid && o_ready, a_valid && a_ready};

    thz_input_adapter #(.NUM_CHANNELS(NUM_CHANNELS), .ADC_WIDTH(ADC_WIDTH))
    U_ADAPTER (.clk(clk), .rstn(rstn),
        .s_valid(s_valid), .s_data(s_data), .s_last(s_last), .s_ready(s_ready),
        .m_valid(a_valid), .m_data(a_data), .m_ch(a_ch), .m_last(), .m_ready(a_ready));

    thz_offset_calibrator #(.NUM_CHANNELS(NUM_CHANNELS), .ADC_WIDTH(ADC_WIDTH),
        .PIXEL_WIDTH(PIXEL_WIDTH), .POLARITY(POLARITY))
    U_OFFSET (.clk(clk), .rstn(rstn),
        .s_valid(a_valid), .s_data(a_data), .s_ch(a_ch), .s_ready(a_ready),
        .m_valid(o_valid), .m_data(o_data), .m_ch(o_ch), .m_ready(o_ready),
        .dark_table(dark_table));

    thz_gain_calibrator #(.NUM_CHANNELS(NUM_CHANNELS), .PIXEL_WIDTH(PIXEL_WIDTH),
        .GAIN_FRAC(GAIN_FRAC))
    U_GAIN (.clk(clk), .rstn(rstn),
        .s_valid(o_valid), .s_data(o_data), .s_ch(o_ch), .s_ready(o_ready),
        .m_valid(g_valid), .m_data(g_data), .m_ch(g_ch), .m_ready(g_ready),
        .gain_table(gain_table));

    thz_temporal_averager #(.NUM_CHANNELS(NUM_CHANNELS), .PIXEL_WIDTH(PIXEL_WIDTH),
        .AVG_FRAMES(AVG_FRAMES))
    U_AVG (.clk(clk), .rstn(rstn),
        .s_valid(g_valid), .s_data(g_data), .s_ch(g_ch), .s_ready(g_ready),
        .m_valid(t_valid), .m_data(t_data), .m_ch(t_ch), .m_ready(t_ready));

    thz_frame_mapper #(.NUM_CHANNELS(NUM_CHANNELS), .ROWS(ROWS), .COLS(COLS),
        .PIXEL_WIDTH(PIXEL_WIDTH))
    U_MAPPER (.clk(clk), .rstn(rstn),
        .s_valid(t_valid), .s_data(t_data), .s_ch(t_ch), .s_ready(t_ready),
        .m_valid(p_valid), .m_data(p_data), .m_row(p_row), .m_col(p_col),
        .m_last(p_last), .m_ready(p_ready),
        .map_table(map_table));

    thz_frame_buffer #(.NUM_CHANNELS(NUM_CHANNELS), .ROWS(ROWS), .COLS(COLS),
        .PIXEL_WIDTH(PIXEL_WIDTH))
    U_FB (.clk(clk), .rstn(rstn),
        .s_valid(p_valid), .s_data(p_data), .s_row(p_row), .s_col(p_col),
        .s_last(p_last), .s_ready(p_ready),
        .m_valid(m_valid), .m_data(m_data), .m_row(m_row), .m_col(m_col),
        .m_last(m_last), .m_ready(m_ready));

endmodule
