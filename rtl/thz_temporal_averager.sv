// ============================================================================
// thz_temporal_averager.sv — 多帧时域平均(降噪)
//
// 每累积 AVG_FRAMES 帧输出 1 帧平均结果:
//   out[ch] = (sum_{f=0..AVG_FRAMES-1} in[ch]) >> AVG_SHIFT   (截断)
// AVG_FRAMES 必须为 2 的幂;AVG_FRAMES=1 时退化为直通(等效)。
//
// FILL 状态:逐拍累加,s_ready=1;
// DUMP 状态:顺序输出 NUM_CHANNELS 个像素,s_ready=0(上游数据保持,不丢失)。
// 与帧缓冲的读出反压通过 valid/ready 链自然传递。
// ============================================================================
module thz_temporal_averager #(
    parameter NUM_CHANNELS = 32,
    parameter PIXEL_WIDTH  = 16,
    parameter AVG_FRAMES   = 4,                 // 必须为 2 的幂
    localparam CH_W   = $clog2(NUM_CHANNELS),
    localparam SHIFT  = $clog2(AVG_FRAMES),
    localparam ACC_W  = PIXEL_WIDTH + SHIFT,
    localparam BEATS  = NUM_CHANNELS * AVG_FRAMES,
    localparam CNT_W  = $clog2(BEATS)
)(
    input                      clk,
    input                      rstn,
    input                      s_valid,
    input  [PIXEL_WIDTH-1:0]   s_data,
    input  [CH_W-1:0]          s_ch,            // 仅透传一致性检查用
    output                     s_ready,
    output                     m_valid,
    output [PIXEL_WIDTH-1:0]   m_data,
    output [CH_W-1:0]          m_ch,
    input                      m_ready
);
    localparam [1:0] FILL = 2'd0, DUMP = 2'd1;

    reg [1:0]           state;
    reg [ACC_W-1:0]     acc [0:NUM_CHANNELS-1];
    reg [CNT_W-1:0]     gcnt;                    // 组内节拍计数
    reg [CH_W-1:0]      chcnt;                   // 累加通道指针
    reg [CH_W-1:0]      dump_idx;                // 输出像素指针

    wire accepting = (state == FILL);
    assign s_ready = accepting;

    // ---- 累加(FILL) ----
    integer i;
    always @(posedge clk) begin
        if (!rstn) begin
            state <= FILL; gcnt <= '0; chcnt <= '0; dump_idx <= '0;
            for (i = 0; i < NUM_CHANNELS; i = i + 1) acc[i] <= '0;
        end else begin
            case (state)
                FILL: begin
                    if (s_valid) begin
                        acc[chcnt] <= acc[chcnt] + {{SHIFT{1'b0}}, s_data}; // 零扩展累加
                        if (gcnt == BEATS - 1) begin
                            state    <= DUMP;
                            dump_idx <= '0;
                            gcnt     <= '0;
                            chcnt    <= '0;
                        end else begin
                            gcnt  <= gcnt + 1'b1;
                            chcnt <= (chcnt == NUM_CHANNELS - 1) ? '0 : chcnt + 1'b1;
                        end
                    end
                end
                DUMP: begin
                    if (m_ready) begin
                        if (dump_idx == NUM_CHANNELS - 1) begin
                            state <= FILL;
                            for (i = 0; i < NUM_CHANNELS; i = i + 1) acc[i] <= '0;
                        end else begin
                            dump_idx <= dump_idx + 1'b1;
                        end
                    end
                end
                default: state <= FILL;
            endcase
        end
    end

    assign m_valid = (state == DUMP);
    assign m_data  = acc[dump_idx][ACC_W-1:SHIFT];   // >> SHIFT 截断
    assign m_ch    = dump_idx;
endmodule
