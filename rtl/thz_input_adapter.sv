// ============================================================================
// thz_input_adapter.sv
// 流式输入适配器:外部(仿真激励文件 / 未来 UART / 未来 ADC)送入
// "采样流 + 帧尾脉冲 s_last",本模块维护通道计数器,为每个输出节拍
// 附带通道号 ch_out,并保证整条流水线在 valid/ready 反压下不丢数据。
//
// 握手约定(仿 AXI-Stream,风格参考 cepdnaclk 工程的 skid_buffer):
//   s_valid && s_ready 同时为高时,一拍数据被接收;
//   每级流水寄存器 run = m_ready || !m_valid,级内数据保持直到下游可收。
//
// 未来 ADC 接入:只需把 ADC 接口转成同样的 s_valid/s_data/s_last 流,
// 即可复用整条管线(见 docs/system_architecture.md)。
// ============================================================================
module thz_input_adapter #(
    parameter NUM_CHANNELS = 32,
    parameter ADC_WIDTH    = 12,
    localparam CH_W = $clog2(NUM_CHANNELS)
)(
    input                    clk,
    input                    rstn,
    // slave side
    input                    s_valid,
    input  [ADC_WIDTH-1:0]   s_data,
    input                    s_last,     // 帧内最后一个采样(第 NUM_CHANNELS-1 通道)
    output                   s_ready,
    // master side
    output                   m_valid,
    output [ADC_WIDTH-1:0]   m_data,
    output [CH_W-1:0]        m_ch,       // 本拍数据对应的通道号
    output                   m_last,
    input                    m_ready
);
    reg                 v_q;
    reg [ADC_WIDTH-1:0] d_q;
    reg [CH_W-1:0]      ch_q;
    reg                 last_q;
    reg [CH_W-1:0]      ch_cnt;

    wire run = m_ready || !v_q;      // 本级寄存器可接受新数据
    assign s_ready = run;

    always @(posedge clk) begin
        if (!rstn) begin
            v_q <= 1'b0; d_q <= '0; last_q <= 1'b0;
            ch_q <= '0; ch_cnt <= '0;
        end else if (run) begin
            v_q    <= s_valid;
            d_q    <= s_data;
            last_q <= s_last;
            ch_q   <= ch_cnt;
            if (s_valid)
                ch_cnt <= s_last ? '0 : ch_cnt + 1'b1;
        end
    end

    assign m_valid = v_q;
    assign m_data  = d_q;
    assign m_ch    = ch_q;
    assign m_last  = last_q;
endmodule
