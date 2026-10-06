# 期权数据与500美元约束：官方资料核验

本文件只核验官方公开文档，不查询账户、订单或市场报价，不做收益回测。完整正文只留在私有raw-docs/；JSON内保存短引文、URL、获取时刻和正文SHA256。

## 能确认的数据边界

- 历史期权数据官方起点为2024年2月。当前OpenAPI提供历史bars/trades及latest quotes，未查到历史options quotes路由；latest不能替代历史bid/ask，成交价或分钟高低价不能充当历史可成交报价。
- 历史bars/trades未列feed参数；latest quotes才列opra/indicative。不要通过给历史URL加feed=opra就声称确认了行情源。行情symbols每次最多100，分页limit默认1000/最多10000，start/end含端点，必须翻完跨symbol分页。
- 过期合约需显式查询inactive及具体到期日/区间；默认只返回active、最近周末前到期的合约。合约limit默认100/最多10000，翻到next_page_token为空。今天取到的合约状态、OI、close及交割字段不能证明过去决策时已知。
- indicative的报价是修改后的衍生值，并非真实OPRA报价；成交也是衍生且延迟15分钟。不得在OPRA失败后无提示降级并宣称真实报价。

## 数量、交割与条件

期权qty必须整张，不支持按notional下单。权利金预算为价格×**multiplier**×整张数量，加费用；**size不是multiplier**。请求show_deliverables=true；调整合约可能交割多种股票/现金，数量可能延迟确定。当前元数据不能证明历史交割版本，不能把所有合约硬编码100。

OPRA原生规范明确报价size单位为合约数；Alpaca schema只写bid/ask size，未明确重述映射。因此要保留原始值与来源，不能套用股票的单位。OPRA的A为可自动执行，空格为普通交易，R为轮转，F/I/T分别为非确定报价（non-firm）/指示值/停牌，X/Y为单边不firm；完整派生表见JSON。不能把股票的R条件规则复制过来，也不能把显示尺寸当保证成交量。

## 费用与到期

当前Alpaca收费表修订于2026-10-01。普通零售股票/ETF期权佣金与指数期权、专业订单流不同。当前表列每边ORF0.015美元/张、OCC0.025美元/张、CAT0.000003美元×等价股数；卖出另有SEC按交易额0.0000206、该表TAF为0。每个fee种类按账户日汇总后向上取整到分。不能把这个版本无验证套到全年，也未证明PAPER实际扣费口径。

标准股票期权会交割股票，长call行权资金为strike×multiplier；官方写ITM至少0.01美元默认行权、购买力不足时到期前1小时内尝试由券商卖出。500美元能支付权利金不等于能支付行权款，不能把强平当保证成交。普通指南说DNE联系支持，最新OpenAPI另列DNE接口：保留文档差异，本次不调用或假设已有保护。

## OPRA协议

OPRA电子协议要求订阅者亲自同意条款，非专业附录涉及身份、用途与职业资格陈述。若实际接口返回未签协议，应保留该具体原因；这与是否具备OPRA套餐不是同一个状态。账户持有人需自行核对并接受适用协议，本研究不代签、不改订阅、不绕过限制。没有核实到Alpaca具体UI路径，不编造点击步骤。

## 主要官方来源

- [historical_options](https://docs.alpaca.markets/us/docs/historical-option-data.md)；SHA256 `a43aa53bf2575a3713bdbb9f21430411b24088a60c3c36b5d4159a533223b91b`。
- [option_contracts](https://docs.alpaca.markets/us/reference/get-options-contracts.md)；SHA256 `b0fadfb35f2ec2aa4d493e432ffa9430e29c355054826e8cb34ff1181becd0d0`。
- [option_bars](https://docs.alpaca.markets/us/reference/optionbars.md)；SHA256 `91ba9fbc7ca154339b1bf1c6ab15362430e331ed896cb6c34f6f53cd7ae097c5`。
- [option_trades](https://docs.alpaca.markets/us/reference/optiontrades.md)；SHA256 `48a5dcca1ae35fecc13262842d7da4c616e2e6d9482082b4459bd24e37e60e45`。
- [option_latest_quotes](https://docs.alpaca.markets/us/reference/optionlatestquotes.md)；SHA256 `140046c772f6665e5a066c365ea3fd02d72b81ad41fbfea32ed65282c313e96d`。
- [opra_output_spec](https://cdn.opraplan.com/documents/OPRA_Pillar_Output_Specification.pdf)；SHA256 `f991f4fff611d937039bbfd1ad3418d86d9c79f8b215a14f670c9f2872abb2e6`。
- [alpaca_fee_schedule](https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf)；SHA256 `0700d871c54af72e1355d97376fffbe3f55848dda00649676e3c60eb0949cb7f`。
- [options_trading](https://docs.alpaca.markets/us/docs/options-trading.md)；SHA256 `3dd9d19e5879aa55d9751b65e76908008e89b5c4a82fb5c51003435f159b9866`。
- [option_dne](https://docs.alpaca.markets/us/reference/optiondonotexercise.md)；SHA256 `66f84ec734d50fb7eb41a9e17fb7e0aafccbc65455d7b4ffcf9d9563a2e79793`。
- [opra_electronic_agreement](https://cdn.opraplan.com/documents/OPRA_Electronic_Subscriber_Agreement.pdf)；SHA256 `81c1bb0886c84798bc17890a3888b7dbb2b7084aefda376dc8823e1b2ee0f14c`。
