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

**两种用法：**
- 文件夹里**已有 MinerU JSON** → 直接审查（最快）。
- 文件夹里是**原始 PDF/图片**（无 JSON）→ 入口会**自动启动抽取编排层**（见第 4.1 节），无需手工跑 MinerU。

手工转 MinerU JSON（可选）：
```bash
mineru-open-api extract "申报书.pdf" -o ./sample_folder/ -f json
mineru-open-api extract "营业执照.png" -o ./sample_folder/ -f json --ocr
```

## 4. 运行

交互式（人工逐项选表单）：
```bash
python3.13 run_folder_review.py --input "/绝对路径/某样本文件夹"
```

非交互 / 批量（推荐给 Claude / CI / 多样本）：
```bash
# 单样本：表单走 JSON（字段见 config/form_answers_example.json）
python3.13 run_sample.py --input "/路径/样本A" --form form.json
# 多样本：并列多个文件夹
python3.13 run_sample.py --input "/路径/样本A" "/路径/样本B"
# 多样本：一个父目录，每个子文件夹算一个样本
python3.13 run_sample.py --input "/路径/所有样本" --parent
```
- 每样本表单优先级：样本文件夹内的 `*form*.json` > 命令行 `--form` > 内置 `DEFAULT_FORM`（兜底告警）。
- 批量会额外产出 `output_folder_review/batch_summary.md`（含「牵头单位/申报主体」列，便于汇总统计）。

### 4.1 抽取编排层（原始文件 → JSON，自动）

当文件夹没有现成 MinerU JSON 时，`run_folder_review.py` 自动调用 `review_mvp/extract_orchestrator.py` 逐文件分层抽取，**让一个慢文件不拖垮整单**：

| 层 | 手段 | 适用 |
|---|---|---|
| Tier 0 | 本地 `pdftotext`（免网络、毫秒级） | 有文字层的 PDF（覆盖率 ≥ 60% 直接用） |
| Tier 2 | MinerU 精度 OCR（**含重试退避 + 大文件分块**） | 图片、扫描件、低文字层的必传件 |
| Tier 3 | 转人工 | 重试仍失败 → `parse_status=failed`，规则层自动转人工 |

要点：
- **按角色给预算**：辅助材料（文件名含“荣誉/研发能力/专利/软著/奖”等）只做本地廉价抽取，**绝不为其烧 OCR**；必传件才走完整链。
- **OCR 重试**：超时/失败默认重试 2 次、退避递增（多为偶发网络）。
- **大文件分块**：页数 > 8 的文件按 `--pages` 逐块 OCR，**部分块失败仍保留已成功块**（`partial`→转人工）。
- **铁律**：抽取失败/不全 → 转人工，**绝不判材料缺失**。
- 缓存写在 `output_folder_review/_mineru_cache/<样本名>/`，不改动用户原文件夹。
- 可调预算（`extract_file` 参数）：`mineru_timeout` / `retries` / `backoff` / `chunk_pages` / `min_coverage`。

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
