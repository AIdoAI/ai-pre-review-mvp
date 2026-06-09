# AI预审本地MVP

本目录提供一个不依赖云函数、仅使用 Python 标准库的本地验证原型。

> 安全说明：仓库不包含真实申报材料和生成的审查输出。原始 PDF、MinerU JSON
> 及输出报告可能含企业、财务和个人信息，应仅保存在受控环境中，不得提交到 Git。

处理流程：

```text
MinerU JSON
-> 读取页面、文本块和表格
-> 识别并合并材料
-> 抽取核心字段
-> 执行可自动判断的规则
-> 输出 JSON 与 Markdown 审查报告
```

## 快速运行

在工作区根目录执行：

```bash
python3 local_review/run_review.py \
  --manifest local_review/config/sample_manifest.json \
  --output local_review/output
```

运行自动测试：

```bash
python3 -m unittest discover -s local_review/tests -v
```

生成文件名资质画像：

```bash
python3 local_review/run_qualification_profile.py \
  --input "/path/to/企业资质文件夹" \
  --output local_review/output_qualification/企业名称 \
  --entity-name "企业名称"
```

该模块只读取文件名和目录结构，不读取正文。它用于向专家展示专利、软著、论文、奖项等候选画像，不参与必要材料缺失判断。详细说明见`QUALIFICATION_PROFILE_DESIGN.md`。

若总目录按企业分文件夹存放，增加`--group-by-first-level`即可分别统计；总目录中的散落文件会进入`未归属文件`分组。

## 申报主体结构

正式对接优先使用平台表单输出`form_answers`：手动选择是否联合申报，并为每家申报单位选择是否独立法人。系统会自动生成内部`subject_structure`和动态上传要求，不通过OCR猜测这些选项。

测试时也可以直接编辑`form_answers`。示例见`config/form_answers_example.json`。

如不经过表单，也可直接通过`subject_structure`描述独立/联合申报、每家单位的申报角色、单位性质和是否独立法人。非独立法人单位必须通过`parent_entity_id`绑定具有独立法人资格的上级单位。

材料可通过`material_assignments`关联所属主体；上级单位只提供授权时，使用`authorizing_parent`角色，不计入联合申报单位数量。完整示例见`config/subject_structure_example.json`。

如组委会已明确某类联合成员需要提交特定主体材料，可在该主体下配置`required_materials`。系统将按`owner_entity_id`逐家校验；未配置的成员材料要求不会被系统自行推断为自动拒绝项。

旧版清单中的`conditions`仍可继续使用，但只能触发粗粒度条件材料检查，不能判断每份材料属于哪家单位。

详细结构和判断顺序见`SUBJECT_STRUCTURE_DESIGN.md`。

## 两种审查模式

- `complete`：完整申报材料包。成功解析后未发现必传材料，可以判定缺失。
- `partial`：局部测试样本。未发现其他材料只能标记为“无法判断”，不能判缺失。

清单中的单个文件还可以设置解析状态：

```json
{
  "path": "../../input/长篇财务报告.json",
  "original_file": "长篇财务报告.pdf",
  "parse_status": "partial",
  "total_pages": 320,
  "parsed_pages": "1-50,120-150"
}
```

`partial`、`failed`或`pending`均表示解析不完整；即使申报包模式为`complete`，也不得据此判定材料缺失。

如果 MinerU JSON 中存在无文本、无内容块的空解析页，系统也会自动将该文件降为 `partial`，提示人工复核。

## 材料必要性分级

材料政策定义在 `config/material_policy.json`：

- `required`：所有申报必须提供，完整材料包中缺失可判不通过。
- `conditional_required`：仅在联合申报、分公司或对应项目阶段等条件满足时必需。
- `recommended`：专利、软著、奖项等鼓励提交材料，缺失不得打回。
- `auxiliary`：行业许可证、认证等辅助材料，只记录和抽取。
- `irrelevant/unknown`：疑似无关或无法分类材料，提示人工确认。

必要材料还会单独进行存在性确认：

- `confirmed`：命中材料强标题或起始特征，可以确认已提交。
- `suspected`：只命中统一社会信用代码等通用关键词，只能转人工，不能让必要材料规则通过。
- `observed`：推荐、辅助或待分类材料，仅记录，不参与必要材料确认。

## 输出文件

每个申报/样本生成：

- `material_catalog.json`：精简材料目录，直接给出材料类型、必要性、原始文件名、MinerU JSON和页码。
- `materials.json`：材料目录、页码及分类证据。
- `extracted_fields.json`：核心字段及证据页码。
- `rule_results.json`：规则检查结果。
- `subject_structure.json`：申报方式、多主体、独立法人和上级单位关系校验结果。
- `review_report.md`：便于人工查看的审查报告。

批量运行还会生成：

- `batch_summary.json`
- `batch_summary.md`

## 当前能力边界

能够：

- 读取 MinerU `middle.json` 风格结果。
- 同时读取普通文本和表格 HTML。
- 识别一个 PDF 内的多种材料，并合并连续同类页面。
- 抽取营业执照、承诺书、许可证、认证证书等常见字段。
- 检查必传材料存在性，并辅助提取承诺书联系人、电话、日期和基础一致性。
- 返回证据页码和人工复核事项。

不能自动终判：

- 公章和签字真伪。
- 证书真实有效性。
- 企业国资身份和股权穿透。
- 复杂语义、项目阶段冲突和重复参赛。
- MinerU 未成功解析的页面或文件。

解析失败、低置信度或需要视觉判断时，应进入人工复核，不能直接判定材料缺失。

规则与当前能力边界见：

- `RULE_FEASIBILITY_MATRIX.md`
- `config/rule_capabilities.json`
- `TEST_REPORT.md`
- `UPWARD_BRIEF.md`
- `QUALIFICATION_PROFILE_DESIGN.md`
- `SUBJECT_STRUCTURE_DESIGN.md`
