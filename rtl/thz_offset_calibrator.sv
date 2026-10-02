// ============================================================================
// thz_offset_calibrator.sv — 暗场扣除 + 极性处理
//
// POLARITY = +1 : d = raw - dark   (信号使 ADC 码值升高)
// POLARITY = -1 : d = dark - raw   (零偏前端以 VREF 为基准,信号可能使码值下降)
// d 钳位到 [0, PIXEL_MAX-1]。raw/dark 均为无符号 12-bit,13-bit 差不会回绕。
//
// dark_table 由顶层通过 $readmemh 载入(data/calib/dark.hex)。
// ============================================================================
module thz_offset_calibrator #(
    parameter NUM_CHANNELS = 32,
    parameter ADC_WIDTH    = 12,
    parameter PIXEL_WIDTH  = 16,
    parameter POLARITY     = 1,          // +1 / -1,不得写死真实系统极性
    localparam CH_W = $clog2(NUM_CHANNELS),
    localparam [PIXEL_WIDTH-1:0] PIXEL_MAX = {PIXEL_WIDTH{1'b1}}
)(
    input                      clk,
    input                      rstn,
    input                      s_valid,
    input  [ADC_WIDTH-1:0]     s_data,
    input  [CH_W-1:0]          s_ch,
    output                     s_ready,
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [CH_W-1:0]          m_ch,
    input                      m_ready,
    input [ADC_WIDTH-1:0]      dark_table [0:NUM_CHANNELS-1]
);
    reg                 v_q;
    reg [PIXEL_WIDTH-1:0] d_q;
    reg [CH_W-1:0]      ch_q;

    wire [ADC_WIDTH-1:0]     dark_v = dark_table[s_ch];
    // 有向差值:负数(信号低于暗场)必须钳位为 0,不能让无符号减法回绕
    wire                     neg    = (POLARITY > 0) ? (s_data < dark_v) : (dark_v < s_data);
    wire [ADC_WIDTH:0]       sub    = (POLARITY > 0) ? (s_data - dark_v) : (dark_v - s_data);
    wire [ADC_WIDTH:0]       mag    = neg ? '0 : sub;

    // clamp 到 [0, PIXEL_MAX](数学上 mag<=4095,钳位仅为参数安全)
    wire [PIXEL_WIDTH-1:0] d_clamp =
        (mag > PIXEL_MAX[PIXEL_WIDTH-1:0]) ? PIXEL_MAX : {{(PIXEL_WIDTH-ADC_WIDTH-1){1'b0}}, mag};

    wire run = m_ready || !v_q;
    assign s_ready = run;

    always @(posedge clk) begin
        if (!rstn) begin
            v_q <= 1'b0; d_q <= '0; ch_q <= '0;
        end else if (run) begin
            v_q  <= s_valid;
            d_q  <= d_clamp;
            ch_q <= s_ch;
        end
    end

    assign m_valid = v_q;
    assign m_data  = d_q;
    assign m_ch    = ch_q;
endmodule
