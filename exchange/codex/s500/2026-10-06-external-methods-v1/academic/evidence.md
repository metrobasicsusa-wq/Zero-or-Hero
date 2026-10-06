# 原始学术与量化资料核验：开盘活跃度、财报延续

访问日期：2026-10-06。以下是资料研究，尚未运行这些来源的策略，也不是实盘成绩。仅保留短引文和自写摘要，不发布论文全文。

## 1. 开盘区间突破：关键是同一时段的相对成交量

[Zarattini、Barbon、Aziz，A Profitable Day Trading Strategy For The U.S. Equity Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)，初稿2024-02-16，SSRN标注修订2025-04-29；已阅读[作者官网完整PDF](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf)的方法。

来源短证据：“Trade the stocks with the top 20 Relative Volume.”（正文第15页）

可编码规则：09:35等首个5分钟结束，使用当天09:30–09:35成交量除以前14个交易日同样5分钟成交量均值；开盘价>$5、前14日均量至少100万股、ATR14>$0.50、相对量至少1倍，按相对量选前20。首5分钟上涨则突破其高点做多，下跌则破低点做空，平盘不做；止损为入场价距0.1ATR，未止损则16:00退出。

这和我们先前用“整天成交量相对过去日均量”的日线策略不同。公司新闻是作者解释，实际入选条件是开盘活跃度代理，并不等于已验证每个交易都有财报/订单新闻。

原论文回测7000余股、2016–2023，初始$25,000、允许4倍杠杆、多空前20股，佣金$0.0035/股；其1637%累计收益只能标为作者回测。没有从所读方法里验证完整点差、跳空止损滑点和借券费用。论文称CRSP股票池含退市股；我们当前目录不能继承这个保证。

对$500可借鉴其信号，但应另外登记“单只、整股、只做多、无杠杆”变体；不能照搬原资金曲线。缺全年的分钟成交量、开盘/停止交易细节、bid/ask、事件时间戳。仅有日线不能验证入场和止损先后。

## 2. 尾盘动量：不是从9:30开始算

[Gao、Han、Li、Zhou，Market Intraday Momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)，工作论文2017-06-19，已核验作者摘要；未读完整交易成本表。

来源短证据：“since the previous day’s market close”。

研究用SPY1993–2013高频数据，发现“前收盘至10:00”的收益可预测15:30–16:00收益，且在高量、高波动、重要宏观新闻日更强；并观察另外10只ETF。这个早段信号包含隔夜跳空，不能误写成只看9:30–10:00。

可先做条件收益检验，再独立验证只做多、15:30入场、16:00退出的现金变体。高量阈值和原交易成本细节尚未确认，不能自行填入后说原文如此。$500买不起整股ETF时不能擅自换成期权或杠杆ETF并继承论文结论。

## 3. 财报漂移必须与成本一起研究

[Ng、Rusticus、Verdi，Implications of Transaction Costs for the Post-Earnings-Announcement Drift](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=899902)，Journal of Accounting Research 46(3),661–696，2008；[伦敦商学院原始馆藏记录](https://lbsresearch.london.edu/id/eprint/286/)确认书目但无全文。

来源短证据：“significantly reduced by transaction costs”。

作者的组合与回归研究指出，交易成本高的股票可能显示更强的财报后漂移，但捕捉漂移的净利润受到成本明显侵蚀。我们读到的是摘要，未确认其精确样本年代、SUE公式、分组阈值或持有期，不伪造复制条件。

实际借鉴是：在相同事件名单上同时报告毛收益与含点差滑点收益，按入场前流动性分组；把成本直接影响和$500整股资金路径变化分开说明。缺当时可见的盈利预期、公告时刻和报价。

## 4. 财报电话会文本能提供EPS之外的信息

[Meursault、Liang、Routledge、Scanlon，PEAD.txt: Post-Earnings-Announcement Drift Using Text](https://www.philadelphiafed.org/-/media/frbp/assets/working-papers/2021/wp21-07.pdf)，费城联储工作论文21-07，封面2021年2月、修订2022年8月，内文日期2022-06-11；已读原始PDF。

来源短证据：“using only information from the past eight quarters as the training set”。

最终研究85160个2010–2019观测、4701家公司。其模型使用管理层陈述和问答的词/词组频率，逐季度仅用此前8季度训练正则化逻辑模型；下季度生成文本意外程度分数，以训练期阈值分五组。多最高组、空最低组，电话会后首个收盘入场并持有63交易日，也研究32日。

这不是“看见EPS超过预期就买”，更不是让LLM凭印象点评等于复现论文。原文有时间滚动预测，但没有因此证明我们2026策略已前向有效；所读组合段也未核验完整可交易成本。电话会后首个收盘是否真能用到已公开的完整文字稿，是必须补的时间戳检查。

对本项目，先建立公告/电话会/文字稿发布时间、EPS与收入超预期、明确上调指引的事件表。简单“上调指引且收入/EPS超预期”属于我们另立的可检验假设；不得把它标成本文经过验证的规则。原论文完整模型还需至少两年前置文本训练和点时预期数据。

## 研究顺序

优先补同钟点开盘相对量，再用带时间戳的财报/指引事件做分组。这两类资料提供了我们现有泛日线矩阵缺少的信息。保留原192条失败和结果，不因外部论文漂亮的收益数字就替换规则或启用交易。完整URL、样本、成本边界、可编码假设与缺失数据见 evidence.json。
