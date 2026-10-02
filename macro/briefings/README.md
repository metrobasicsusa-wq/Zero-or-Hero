# 盘前宏观简报

每个交易日 8:50（美东）由 Claude 的 Routine 生成：`YYYY-MM-DD.md`。

- 内容：美债收益率、美联储、油价、美元、地缘冲突/战争、当天重要数据与财报。
- **只记录，不影响交易。** 积累几周后对照当天实际走势评估判断是否有价值，再决定是否接入。
- 价格型宏观指标（TLT/USO/VIXY/UUP）由交易程序每天计算，见 `hero/macro.py`，是否影响仓位由每周进化（`macro_scale`）决定。
- 预测核对：盘后复盘写当天简报的「收盘核对」，下一份盘前简报写「昨日预测核对」。每条结论同时追加一行到 `scores.csv`（`date,kind,prediction,result,source`；kind 为 `prediction` 或 `direction`，result 为 `hit` / `miss` / `na`）。
- 每周六进化会把 `scores.csv` 的准确率写进 `journal/evolution.md`，**只做记录，不参与选参数**。
