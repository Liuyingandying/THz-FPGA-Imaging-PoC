// ============================================================================
// thz_frame_mapper.sv — 通道 → 像素映射(独立、表驱动)
//
// pidx = channel_map[ch];row = pidx / COLS;col = pidx % COLS。
// 映射表由顶层 $readmemh 载入(data/calib/channel_map.hex,默认恒等映射),
// 未来真实探测器走线顺序变化时,只需改 hex 表,不改任何逻辑。
// ============================================================================
module thz_frame_mapper #(
    parameter NUM_CHANNELS = 32,
    parameter ROWS         = 4,
    parameter COLS         = 8,
    parameter PIXEL_WIDTH  = 16,
    localparam CH_W  = $clog2(NUM_CHANNELS),
    localparam ROW_W = (ROWS  <= 1) ? 1 : $clog2(ROWS),
    localparam COL_W = (COLS  <= 1) ? 1 : $clog2(COLS),
    localparam IDX_W = $clog2(NUM_CHANNELS)
)(
    input                      clk,
    input                      rstn,
    input                      s_valid,
    input  [PIXEL_WIDTH-1:0]   s_data,
    input  [CH_W-1:0]          s_ch,
    output                     s_ready,
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [ROW_W-1:0]         m_row,
    output [COL_W-1:0]         m_col,
    output                     m_last,     // 本帧最后一个像素
    input                      m_ready,
    input [IDX_W-1:0]          map_table [0:NUM_CHANNELS-1]
);
    reg v_q;
    reg [PIXEL_WIDTH-1:0] d_q;
    reg [ROW_W-1:0] r_q;
    reg [COL_W-1:0] c_q;
    reg last_q;

    wire [IDX_W-1:0] pidx = map_table[s_ch];
    wire [ROW_W-1:0] row  = pidx / COLS;
    wire [COL_W-1:0] col  = pidx % COLS;

    wire run = m_ready || !v_q;
    assign s_ready = run;

    always @(posedge clk) begin
        if (!rstn) begin
            v_q <= 1'b0; d_q <= '0; r_q <= '0; c_q <= '0; last_q <= 1'b0;
        end else if (run) begin
            v_q    <= s_valid;
            d_q    <= s_data;
            r_q    <= row;
            c_q    <= col;
            last_q <= (s_ch == NUM_CHANNELS - 1);
        end
    end

    assign m_valid = v_q;
    assign m_data  = d_q;
    assign m_row   = r_q;
    assign m_col   = c_q;
    assign m_last  = last_q;
endmodule
