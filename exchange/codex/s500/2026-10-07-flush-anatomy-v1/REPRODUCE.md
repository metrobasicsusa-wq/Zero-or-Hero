# 复核与复算范围

本公开包可以运行合成测试、检查发布文件与源哈希绑定，并从公开的派生
账本复算集中度等派生统计。**它不是包含全部原始输入的、可完全独立复现
市场研究的公开包。** 12 日的原始行情及规范化分钟输入未随包公开；完整
宏观网页响应快照也未随包公开。没有这些原字节，不能独立重新生成全部
早盘特征或验证原始网页解析，更不能把重新下载的当前数据冒充旧快照。

## 固定版本与目录

仓库为 `metrobasicsusa-wq/Zero-or-Hero`，请按 commit 固定版本，避免使用
会继续移动的分支 HEAD：

| 依赖 | 固定 commit | 仓库目录 |
|---|---|---|
| 父研究：按市场状态分组 | `a73297d47a6076e4f4a4725eab494f0308d7fe33` | `exchange/codex/s500/2026-10-07-relative-flush-v1/` |
| 祖研究：全年急跌反弹事件研究 | `634a93c65e1adec6c5908f9ad249c5b8e6224ba8` | `exchange/codex/s500/2026-10-07-flush-rebound-v1/` |

父研究提供分类、主方案结果及部分方法代码；祖研究提供全部 36 组合的
原事件账本、选样登记、日历、行情清单和规范化方法。完整复算时，本阶段
脚本按照以下**相对目录名称**查找依赖：

```text
research/
  s500-flush-rebound-20261007/
  s500-relative-flush-20261007/
  s500-flush-anatomy-20261007/
```

将相应公开目录的逻辑文件还原到这些目录。若发布清单对某个大 JSON
额外使用 gzip 包装，应按清单声明的逻辑文件名解压，分别核对发布字节和
还原后的源字节哈希。原本就是 `*.jsonl.gz` 的账本应保持原格式，不能
仅凭扩展名把所有 gzip 文件都拆掉。具体依赖及哈希以本阶段
`reuse-input-manifest.json`、`study-design.json` 和
`pre-analysis-validation.json` 为准。

使用 Python 3.12 及标准库；时间处理需要 `America/New_York` 时区数据。
方法、纯统计和离线审计不需要券商凭据、账户连接或下单权限。

## 公开材料可以直接做的检查

在本阶段目录运行以下确切模块集合，共 **85 项合成测试**：

```bash
python -m unittest -v \
  test_prefix_features \
  test_concentration \
  test_run_anatomy \
  independent_prefix_tests \
  independent_concentration_tests
```

分别为 25、23、8、12、17 项。本轮保存的 `pre-analysis-tests.log` 记录
85 项通过、0 项失败。复测可以保存新日志，但不要覆盖已被哈希绑定的原
测试日志。合成测试通过不等于原始行情或策略优势已经得到独立验证。

公开的 `primary-case-ledger.jsonl.gz` 包含固定主方案全部 1,227 个
case。可以直接从其中的 SPY 下跌组信号复算 `concentration.json`：

```bash
python - <<'PY'
import gzip
import json
from concentration import summarize_concentration

fields = ("case_id", "date", "symbol", "net_return", "matched_excess")
signals = []
with gzip.open("primary-case-ledger.jsonl.gz", "rt") as stream:
    for line in stream:
        row = json.loads(line)
        if (row["target_role"] == "stock"
                and row["SPY_group"] == "market_down"
                and row["signal_status"] == "signal"):
            signals.append({field: row[field] for field in fields})
with open("concentration.json") as stream:
    published = json.load(stream)
assert len(signals) == 161
assert summarize_concentration(signals) == published
print("派生集中度复算一致；原始行情真实性未由此独立验证。")
PY
```

这保留 161 个信号中的缺失结果：153 个完整净收益、134 个完整配对超额。
所有逐日、逐股票贡献和 leave-one-out 结果均可从这些派生值检查；这并不
重新验证最初的选样、行情归一化、信号时间或价格来源。

## 拥有原始快照时的完整离线复算

先取得授权保存的、与 `reuse-input-manifest.json` 中
`normalized_anatomy_days` **12 个文件哈希逐一一致**的分钟输入，以及
`source_files` 中全部父、祖文件。规范化分钟文件须放在清单规定的祖
研究相对位置。原始分钟输入缺失时，公开包无法运行这项完整复算；重新
向行情商请求同一日期也不保证返回同一修订版本或字节。

使用单独的复算副本，保留发布产物作对照，不覆盖研究原目录。副本应包含
冻结的方法、登记、作用域、清单、原测试日志和其他绑定文件；初次运行时
不要放入已生成的 `feature-records.jsonl.gz` 或 `inherited-results/`。
脚本会检查这些产物不存在，并核对绑定文件及源哈希。

```bash
python run_anatomy.py
python independent_actual_audit.py
python independent_concentration_audit.py
```

**`run_anatomy.py` 没有命令行参数。** 它以自身所在目录为本阶段目录，
按上述固定兄弟目录查找父、祖输入；不存在 `--stage-dir`、`--input` 或
`--output-dir` 参数。两个行情/统计实际输出审计脚本也按自身位置定位文件。

预期主输出包括 1,227 个特征记录、44,172 条继承参数结果、108 个参数组
汇总、固定分箱、`concentration.json` 和 `analysis-output-manifest.json`。
其规模应为 12 日、1,203 个股票 case、24 个基准 case；其中 SPY 下跌组
主方案是 363 个已选 case、161 个信号、153 个完整净收益、134 个完整
配对结果。运行时间等元数据会随新运行变化；不能要求新运行清单的时间戳
与历史清单逐字节相同。收益、特征和确定性派生结果应按其定义核对。

`prepare_anatomy_inputs.py`、`expand_anatomy_scope.py` 和
`register_anatomy.py` 保存了本轮形成作用域与冻结方法的过程，分别包括
初始 11 日范围、6 月 18 日诊断扩展和登记。重放既有冻结研究应复用
已经发布并绑定的作用域文件，而不是在原目录重新运行这些会写文件、生成
新时间戳和哈希的准备脚本，再声称得到原来的登记记录。

## 宏观日历快照的复核

宏观部分是单独的公开来源快照研究，不读取券商价格或账户。完整离线重建
需要原 `macro-source-manifest.json` 和其中记录的全部响应 `.body`，以及
当时保存的对应文本等派生快照，按清单相对路径还原。使用同一份已保存的
快照后，确切命令为：

```bash
python build_macro_calendar_review.py --stage-dir .
python independent_macro_audit.py
```

转换器逐一核对响应体哈希；独立审计重新检查日历行、时区与覆盖范围。
这些命令同样应在复算副本执行。记录中的网页访问时间、版本与内容是证据
的一部分；今天的官方网页即使 URL 不变，也可能已更新，不能替代当时快照。

抓取程序的真实 CLI 是：

```bash
python fetch_macro_calendar.py \
  --output-dir new-calendar-capture \
  --id unique-source-id \
  --url 'https://www.bls.gov/schedule/2026/home.htm'
```

该示例会建立**新的**抓取记录，不是复现旧快照，也不是完整宏观重建命令。
它有累计最多 20 次 GET、固定官方主机和拒绝后不重试的限制。恢复旧研究
应使用原保存响应并核对哈希；不要为了复算而重新抓取当前页面，或把新
响应哈希写回旧清单。

源哈希证明文件是否与记录的字节一致，本身不证明网页在历史交易决策时
已可见，也不证明宏观事件造成了某个股票收益。公开包支持的派生复核、
需要私有原始输入的复算和实时可交易性验证，是三个不同范围。
