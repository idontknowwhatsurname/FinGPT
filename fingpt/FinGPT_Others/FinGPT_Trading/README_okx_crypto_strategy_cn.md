# OKX 模拟盘虚拟货币量化策略（BTC/ETH + 迷因币）

> 仅用于学习与研究，不构成任何投资建议。

## 策略目标
- 主流币（BTC/ETH）：使用较低杠杆（2-3 倍，最高不超过 5 倍）。
- 迷因币（如 DOGE/PEPE）：使用 5-10 倍杠杆；行情强势时提高至 15-20 倍。
- 仓位管理：80% 资金用于 BTC/ETH，20% 资金用于迷因币。
- 对接 OKX 模拟盘，执行量化交易。
- 每笔交易后自动进行“自我反查 + 优化建议”并记录日志。

## 文件
- `okx_crypto_quant_strategy.py`：核心策略代码。
- `trade_review_log.jsonl`：每笔交易后的复盘日志（自动追加）。

## 快速开始
1. 安装依赖：
   ```bash
   pip install ccxt pandas numpy
   ```
2. 配置 OKX（可选，`dry_run=True` 不下真实单）：
   ```bash
   export OKX_API_KEY="..."
   export OKX_SECRET="..."
   export OKX_PASSWORD="..."
   ```
3. 运行：
   ```bash
   python fingpt/FinGPT_Others/FinGPT_Trading/okx_crypto_quant_strategy.py
   ```

## 关键实现说明
- **信号逻辑**：EMA(20/60) 趋势 + RSI +（迷因币）突破信号。
- **杠杆逻辑**：
  - BTC/ETH 默认 3 倍，且强制上限 5 倍。
  - 迷因币默认 8 倍；动量高时 15 倍；极端动量时 20 倍。
- **风险控制**：
  - 单笔风险上限 1% 账户权益。
  - 止损 2%，止盈 4%。
- **自动复盘**：
  - 若信号过少、杠杆偏高、成交异常等，会写入问题与下一步优化建议。

## 建议迭代
- 接入资金费率、持仓量（OI）与多周期共振过滤误信号。
- 增加回测与参数寻优（walk-forward + regime switching）。
- 将复盘日志接入 LLM 自动参数调优循环。
