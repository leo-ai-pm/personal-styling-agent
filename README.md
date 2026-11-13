# 个性化穿搭Agent

用自然语言选择、调整一套穿搭，并把单件商品或整套搭配加入模拟购物车。

基于 **DeepSeek / 千问兼容接口 + Python 标准库后端 + 原生 JavaScript 前端**。模型负责理解表达，服务端负责校验预算、尺码、库存和商品映射，避免把模型生成的价格或商品当作真实数据。

## 可以体验什么

- 多轮收集预算、常穿尺码和穿着场景；拒绝提高预算后继续遵守原预算。
- “只换鞋，其他不变”“鞋子保留，衣服休闲一点”等局部修改。
- 查询当前、历史或刚浏览过的单品价格、库存和已登记属性。
- 查看、恢复搭配历史；恢复前重新校验商品。
- 单件与整套模拟加购、购物车查看；请求重试不会重复加购。
- 会话持久化、匿名访客隔离、调用限额和模型异常处理。

## 数据与使用范围

商品、价格、库存均为合成数据；购物车不产生订单、不扣库存、不接入支付。仓库附带 13 张 SVG 商品组合示意图，用于展示搭配组合，不提供真实试穿。

## 本地启动

需要 **Python 3.9+**。运行服务无需安装第三方 Python 包或 Node.js。

```sh
git clone https://github.com/leo-ai-pm/personal-styling-agent.git
cd personal-styling-agent
cp .env.example .env
```

用本地编辑器打开 `.env`，在 `DEEPSEEK_API_KEY=` 后填入你自己的 Key，再运行：

```sh
python3 run.py
```

打开 **http://127.0.0.1:8765/**。Windows 可把 `python3` 换成 `python`，用文件管理器复制 `.env.example` 为 `.env`。

默认模型是 `deepseek-v4-flash`。模型调用需要你自己的 API 账户，会按服务商规则计费。没有配置 Key 时，页面会提示模型未接入，不会用预设回答冒充模型。

macOS 也可在配置好 `.env` 后双击 `启动个性化穿搭Agent.command`。完整功能应通过 HTTP 入口使用，直接打开 HTML 文件只会显示启动指引。退出前台服务按 `Ctrl+C`。

可试着说：

1. “预算500，上衣S，下装S，鞋36，日常上班穿。”
2. “鞋子保留，衣服更休闲一点。”
3. “只换鞋子，其他别动。”
4. “这双鞋多少钱？”
5. “把这双鞋加入购物车。”

这是有限商品库：缺少满足条件的 SKU 或组合图时会说明缺口，无法对任意尺码、风格生成无限搭配。

## 模型与密钥配置

默认从本机 `.env` 读取 Key，该文件已被 Git 忽略。不要把 Key 填入前端源码、README、Issue 或截图。

可复制 `config/model.example.json` 为 `config/model.json` 来调整模型。JSON 只保存服务商、地址、模型名和凭据文件路径，不保存 Key 本身。配置修改后重启服务。

| 服务商 | provider | Key 名称 | 默认模型 / 地址 |
| --- | --- | --- | --- |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` / `https://api.deepseek.com` |
| 千问 | `qwen` | `DASHSCOPE_API_KEY` | `qwen3.7-plus` / `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 其他兼容接口 | `compatible` | `OPENAI_API_KEY` | 自行设置 `base_url` 和 `model` |

例如切换千问：在 `.env` 填写 `DASHSCOPE_API_KEY`，将 `config/model.json` 设置为：

```json
{
  "provider": "qwen",
  "api_key_env_file": ".env"
}
```

密钥、模型可用性与地域以服务商账户为准。也支持服务端环境变量：`YIXU_MODEL_PROVIDER`、`YIXU_API_KEY_FILE`、`YIXU_MODEL_CONFIG`、`DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL`、`QWEN_MODEL` / `QWEN_BASE_URL`、`OPENAI_MODEL` / `OPENAI_BASE_URL`。

选择优先级为显式 `YIXU_MODEL_PROVIDER` → 环境中的 `OPENAI_API_KEY` → `DASHSCOPE_API_KEY` → `DEEPSEEK_API_KEY` → `config/model.json` → 默认 DeepSeek。不会自动混用不同服务商的密钥；`.env` 仅解析 Key，不执行其中的 Shell 内容。

## 数据与素材

扩展示例包含 **12 款合成商品、56 个 SKU、13 组组合示意**，用于覆盖 S/S/36、M/M/38 以及 500/800 元的典型流程。价格按“分”存储。

`data/m1_v2` 保留基础示例，`data/m3_draft` 是扩展示例；这些路径沿用内部模块版本名以保持测试兼容。

```sh
# 重建确定性的 SVG 示意素材，不调用模型
python3 scripts/generate_sample_assets.py
```

模型只能选择已登记且满足校验的商品。接入真实商品时，需同时维护商品属性、SKU、尺码、库存、组合映射和素材许可；增加一段提示词无法替代这些数据。

## 测试

```sh
# 自动测试，不调用外部模型
python3 -m unittest discover -s m2/tests -p 'test_*.py'

# 253 项购物车虚拟组合测试，不调用外部模型；报告路径需不存在
python3 -m m2.item_cart_matrix --output reports/cart-matrix.json

# 修订版结构化业务评测，不等同于自然语言评测
python3 -m m2.semantic_eval --suite v2 --mode structured --output reports/structured-v2.json

# 检查待公开文件，不输出密钥内容
python3 scripts/audit_publication.py
```

有自己的 Key 后，可显式运行真实模型语义评测，会产生 API 调用费用：

```sh
python3 -m m2.semantic_eval --suite semantic --mode live --resources candidate --output reports/semantic-live.json
python3 -m m2.evaluate_item_cart --help
```

评测类型、历史用例冲突和本次开源检查结果见 [测试说明](docs/TESTING.md)。不会把结构化注入测试计为模型理解能力。

## 目录

```text
m2/                 对话意图、状态、商品校验、购物车、存储、HTTP 服务
m2/web/             页面、样式与浏览器交互
m2/tests/           自动测试与语义用例
data/               合成商品、库存、映射与 SVG 示意素材
evals/              多轮评测样例
scripts/            示意素材生成与发布检查
config/             无密钥配置模板
run.py              默认启动扩展示例
```

更多说明：[架构](docs/ARCHITECTURE.md) · [部署](docs/DEPLOYMENT.md) · [贡献指南](CONTRIBUTING.md)。
