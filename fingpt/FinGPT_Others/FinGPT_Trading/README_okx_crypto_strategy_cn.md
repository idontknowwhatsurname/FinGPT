# OKX 模拟盘量化策略（修订版：BTC 4H Long-Only 主线）

> 仅用于学习与研究，不构成任何投资建议。

## 为什么修订
上一版“BTC/ETH + 迷因币 + 高杠杆”结构在实测中出现了交易频率过高、回撤偏大的问题。当前版本将交易核心收敛为：
- **仅 BTC**
- **仅做多（long-only）**
- **4H 周期**
- **强过滤入场（ADX + 均线斜率 + Donchian 突破）**

即：保留框架层（配置、日志、自我复盘），但将交易内核切换为更克制、更稳健的主线。

## 策略核心
- 标的：`BTC/USDT:USDT`
- 周期：`4h`
- 方向：`long-only`
- 入场过滤（必须全部满足）：
  1. 价格突破上一窗口 Donchian 上轨
  2. 快均线 > 慢均线
  3. 慢均线斜率为正
  4. ADX >= 阈值（默认 20）
- 风控：
  - 单笔风险上限（默认 1% 账户权益）
  - ATR 止损（默认 2x ATR）
  - 分批止盈（TP1=1.5x ATR，TP2=3x ATR）
- 杠杆：默认 3x，强制上限 5x

## OKX 模拟盘接入
代码中通过 ccxt 的 OKX `flag=1` 使用模拟盘：
- `options.defaultType = "swap"`
- `options.flag = "1"`

环境变量（dry_run=False 时需要）：
```bash
export OKX_API_KEY="..."
export OKX_SECRET="..."
export OKX_PASSWORD="..."
```

## 运行
```bash
python fingpt/FinGPT_Others/FinGPT_Trading/okx_crypto_quant_strategy.py
```

默认 `dry_run=True`，只做策略计算与日志，不发真实委托。

## 交易后自我反查
每次运行都会把“本次未交易/交易异常/优化建议”写入：
- `fingpt/FinGPT_Others/FinGPT_Trading/trade_review_log.jsonl`

便于后续做 walk-forward 参数调优与阈值迭代。
