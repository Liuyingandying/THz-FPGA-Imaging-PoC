// ============================================================================
// thz_frame_buffer.sv — 帧缓冲(单缓冲 + 读写反压,乒乓双缓冲为升级路径)
//
// 写侧:接收 (row,col,data),一帧写满后置 frame_full;
// 读侧:frame_full 后按行主序(row-major)流出整帧,带 m_last 帧尾;
//       读完成后释放缓冲,写侧才能写入下一帧。
// 任一时刻写/读不会同时访问同一缓冲 → 无端口冲突、无丢帧。
// (乒乓双缓冲思想来自 third_party/fpga_image_processing_reference,
//  PoC 阶段单缓冲 + 反压已足够,吞吐非目标。)
// ============================================================================
module thz_frame_buffer #(
    parameter NUM_CHANNELS = 32,
    parameter ROWS         = 4,
    parameter COLS         = 8,
    parameter PIXEL_WIDTH  = 16,
    localparam ROW_W = (ROWS <= 1) ? 1 : $clog2(ROWS),
    localparam COL_W = (COLS <= 1) ? 1 : $clog2(COLS),
    localparam IDX_W = $clog2(NUM_CHANNELS)
)(
    input                      clk,
    input                      rstn,
    // 写侧(来自 mapper)
    input                      s_valid,
    input  [PIXEL_WIDTH-1:0]   s_data,
    input  [ROW_W-1:0]         s_row,
    input  [COL_W-1:0]         s_col,
    input                      s_last,
    output                     s_ready,
    // 读侧(到主机接口)
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [ROW_W-1:0]         m_row,
    output [COL_W-1:0]         m_col,
    output                     m_last,
    input                      m_ready
);
    reg [PIXEL_WIDTH-1:0] ram [0:NUM_CHANNELS-1];

    reg        frame_full;
    reg [IDX_W-1:0] ridx;                    // 读指针(线性像素索引)

    // 写索引 = row*COLS + col
    wire [IDX_W+COL_W:0] widx = s_row * COLS + s_col;

    assign s_ready = !frame_full;

    always @(posedge clk) begin
        if (!rstn) begin
            frame_full <= 1'b0;
            ridx       <= '0;
        end else begin
            if (s_valid && s_ready) begin
                ram[widx] <= s_data;
                if (s_last)
                    frame_full <= 1'b1;
            end
            if (m_valid && m_ready) begin
                if (ridx == NUM_CHANNELS - 1) begin
                    frame_full <= 1'b0;
                    ridx       <= '0;
                end else begin
                    ridx <= ridx + 1'b1;
                end
            end
        end
    end

    assign m_valid = frame_full;
    assign m_data  = ram[ridx];
    assign m_row   = ridx / COLS;
    assign m_col   = ridx % COLS;
    assign m_last  = (ridx == NUM_CHANNELS - 1);
endmodule
