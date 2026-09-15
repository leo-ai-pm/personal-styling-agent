# 单件加购与对话续接样例

`cases_v1.json` 至 `cases_v3.json` 保留测试样例的演进，checker_changes 文件记录检查器修订。包含单件购买、咨询与执行区分、历史/当前商品指代、否定、改口和局部换搭。

执行器：`python3 -m m2.evaluate_item_cart --help`。使用真实模型时会消耗运行者自己的 API 配额，结果写入 Git 忽略的 `reports/`。
