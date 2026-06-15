# 部署与使用补充说明（协作者必读）

> 本文件是对 `README.md` / operational_prompt 的环境与用法补充，避免常见配置坑。

## 1. 环境要求

| 依赖 | 要求 | 安装 / 检查 |
|---|---|---|
| Python | **≥ 3.10**（注意 macOS 自带的 `python3` 常是 3.9，不满足） | `python3.13 --version`；缺则 `brew install python@3.13` |
| poppler（`pdftoppm`） | Claude 的 PDF 预览依赖 | macOS `brew install poppler`；Linux `apt install poppler-utils` |
| MinerU CLI | 文档抽取 | `npm install -g mineru-open-api`；`mineru-open-api version` |
| MinerU token | 精度抽取 / `-f json` 需要（`flash-extract` 免 token） | `mineru-open-api auth`（JWT 见 https://mineru.net/apiManage/token） |

> ⚠️ 所有命令请用 **`python3.13`** 运行，不要用默认 `python3`。

## 2. 获取代码

```bash
git clone https://github.com/AIdoAI/ai-pre-review-mvp.git ~/ai-pre-review-mvp
cd ~/ai-pre-review-mvp
```

## 3. 输入约定

一个样本 = 一个文件夹，里面放：

- **MinerU JSON**（必需，审查只读 JSON，不直接读 PDF）。两种格式都支持：
  - 本地 MinerU pipeline 的 `middle.json`（含 `pdf_info`）
  - MinerU Open API 的 `content_list`（`mineru-open-api extract <file> -o <dir> -f json`）
  - 文件名形如 `MinerU_<原文件名>__<时间戳>.json` 时，会自动回推原文件名
- **基础资料文件**（可选）：`*基础资料*` / `*表单*` / `*答题*`，用于自动读取表单选项（申报方式、是否联合、牵头单位、项目阶段等）；缺失则交互询问。

把 PDF/图片转成 MinerU JSON：

```bash
# 文字版 PDF / 图片（图片加 --ocr）
mineru-open-api extract "申报书.pdf" -o ./sample_folder/ -f json
mineru-open-api extract "营业执照.png" -o ./sample_folder/ -f json --ocr
```

## 4. 运行

```bash
python3.13 run_folder_review.py --input "/绝对路径/某样本文件夹"
```

跑完会在终端打印「统一结论 + 逐文件报告」，并在 `output_folder_review/<样本名>/` 写出：

| 文件 | 内容 |
|---|---|
| `conclusion.md` | 统一结论：是否合规 / 缺什么 / 补什么 / 需人工审查 |
| `per_file_report.md` | 逐文件详审表（审核项/标准/审核结果）+ 总览表 |
| `review_report.md` | 上两者 + 规则明细全表 |
| `rule_results.json` | 机器可读的规则结果 |

## 5. 规则配置（组委会可调，无需改代码）

| 文件 | 作用 |
|---|---|
| `config/material_policy.json` | 必传/条件必传/辅助材料；`material_groups` 定义“三选一”等组 |
| `config/material_types.json` | 各材料的识别规则（强标题/关键词） |
| `config/review_rules.json` | 规则 ID 映射、承诺书关键句、置信度阈值 |

**联合申报支持材料 = 三选一**（任一即满足，均须盖章）：项目合作协议 / 联合申报协议 / 牵头方申报声明。
选“牵头方申报声明”时，还须在“联合申报单位简介”中补充对知识产权无异议表述（系统提示人工核对）。

## 6. 铁律（结论可信度）

- 只有确定性硬规则触发才判“预审不通过”；模糊/语义/视觉判断一律转人工。
- 未识别 ≠ 缺失；解析失败 / 含未完整解析页 → 转人工，不判缺失。
- 盖章真伪、签字真伪、知识产权表述、国资身份等 → 人工复核。

## 7. 跑测试（开发者）

仓库测试用例期望代码位于 `~/local_review`，做一个软链即可：

```bash
ln -s ~/ai-pre-review-mvp ~/local_review
cd ~/ai-pre-review-mvp && python3.13 -m unittest discover -s tests
```

> 注：仓库未附带样例 `input/*.json` 时，少数依赖样例的测试会失败，属预期；核心规则/分类/结构测试应全绿。
