// ============================================================================
// thz_gain_calibrator.sv — 逐通道增益校正(定点 Q8,无浮点)
//
//   prod = d * gain_q          (16x16 -> 32 bit)
//   out  = prod >> GAIN_FRAC   (截断,与 Python 黄金模型 >> 一致)
//   out  钳位到 [0, PIXEL_MAX]
//
// 定点格式:gain_q = round(gain * 2^8),gain=1.0 对应 16'h0100。
// 截位/饱和规则:乘后右移 GAIN_FRAC 位(截断),超出 PIXEL_MAX 钳位。
// gain_table 由顶层 $readmemh 载入(data/calib/gain.hex)。
// ============================================================================
module thz_gain_calibrator #(
    parameter NUM_CHANNELS = 32,
    parameter PIXEL_WIDTH  = 16,
    parameter GAIN_FRAC    = 8,
    localparam CH_W = $clog2(NUM_CHANNELS),
    localparam [PIXEL_WIDTH-1:0] PIXEL_MAX = {PIXEL_WIDTH{1'b1}}
)(
    input                      clk,
    input                      rstn,
    input                      s_valid,
    input  [PIXEL_WIDTH-1:0]   s_data,
    input  [CH_W-1:0]          s_ch,
    output                     s_ready,
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [CH_W-1:0]          m_ch,
    input                      m_ready,
    input [15:0]               gain_table [0:NUM_CHANNELS-1]
);
    reg                 v_q;
    reg [PIXEL_WIDTH-1:0] d_q;
    reg [CH_W-1:0]      ch_q;

    wire [15:0]               gain_v = gain_table[s_ch];
    wire [PIXEL_WIDTH+15:0]   prod   = s_data * gain_v;
    wire [PIXEL_WIDTH+15:0]   shifted = prod >> GAIN_FRAC;
    wire [PIXEL_WIDTH-1:0]    g_out = (shifted > PIXEL_MAX) ? PIXEL_MAX : shifted[PIXEL_WIDTH-1:0];

    wire run = m_ready || !v_q;
    assign s_ready = run;

    always @(posedge clk) begin
        if (!rstn) begin
            v_q <= 1'b0; d_q <= '0; ch_q <= '0;
        end else if (run) begin
            v_q  <= s_valid;
            d_q  <= g_out;
            ch_q <= s_ch;
        end
    end

    assign m_valid = v_q;
    assign m_data  = d_q;
    assign m_ch    = ch_q;
endmodule
