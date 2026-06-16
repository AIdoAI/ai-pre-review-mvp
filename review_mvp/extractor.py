"""Extract review-oriented fields from classified materials."""

from __future__ import annotations

import re
from typing import Any


PLACEHOLDERS = {"XXXX", "xxxx", "X", "占位符"}


def first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ：:。")
    return None


def evidence(material: dict[str, Any]) -> list[str]:
    return material.get("pages", [])


def field(value: Any, material: dict[str, Any], confidence: float = 0.9) -> dict[str, Any]:
    return {
        "value": value,
        "evidence_pages": evidence(material),
        "confidence": confidence if value not in (None, "", []) else 0.0,
    }


def extract_common(text: str, material: dict[str, Any]) -> dict[str, Any]:
    company = first_match(
        text,
        [
            r"(?:企业名称|生产者名称|经营者名称|获奖单位|依托单位|名称)\s*[:：]?\s*([^\n，。；]{4,80}(?:公司|集团|研究院|中心))",
            r"兹证明\s*([^\n，。；]{4,80}(?:公司|集团|研究院|中心))",
        ],
    )
    credit_code = first_match(
        text,
        [r"(?:统一社会信用代码|统一信用代码)\s*[:：]?\s*([0-9A-Z]{18})"],
    )
    legal_representative = first_match(
        text,
        [r"法定代表人(?:（负责人）|\(负责人\))?\s*[:：]?\s*([\u4e00-\u9fa5]{2,8})"],
    )
    certificate_no = first_match(
        text,
        [r"(?:证书编号|许可证编号|登记号|备案号)\s*[:：]?\s*([0-9A-Z./-]{5,50})"],
    )
    valid_until = first_match(
        text,
        [r"(?:有效期至|有效日期至)\s*[:：]?\s*([0-9年月日./ -]{6,30})"],
    )
    return {
        "company_name": field(company, material),
        "credit_code": field(credit_code, material),
        "legal_representative": field(legal_representative, material),
        "certificate_no": field(certificate_no, material),
        "valid_until": field(valid_until, material),
    }


def extract_commitment(text: str, material: dict[str, Any]) -> dict[str, Any]:
    project_name = first_match(text, [r"本单位提交了\s*(.*?)\s*参评"])
    # 联系人：允许“：/空格/无分隔”，可带括注（如“联系人（项目）”），值取连续中文
    contact = first_match(text, [r"联系人(?:（[^）]*）)?[\s:：]*([\u4e00-\u9fa5·]{2,15})"])
    # 电话：优先匹配真实手机号/固话，最后退到宽松数字串（减少抓到噪声）
    phone = first_match(
        text,
        [
            r"(?:联系电话|联系方式|电话|手机)[\s:：]*((?:\+?86[-\s]?)?1[3-9]\d{9})",
            r"(?:联系电话|联系方式|电话|手机)[\s:：]*(0\d{2,3}[-\s]?\d{7,8})",
            r"(?:联系电话|联系方式|电话|手机)[\s:：]*([0-9()（）+\-\s]{7,20})",
        ],
    )
    # 日期/年份：容忍 OCR 在"年/月/日"前后插入的空格（如"2026 年 6 月 3 日"）
    date = first_match(text, [r"(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"])
    year = first_match(text, [r"(20\d{2})\s*年"])
    return {
        "project_name": field(project_name, material),
        "contact_person": field(contact, material),
        "contact_phone": field(phone, material),
        "date": field(date, material),
        "year": field(year, material),
        "signature_label_detected": field(bool(re.search(r"法定代表人|实际负责人|授权代表", text)), material, 0.7),
        "seal_label_detected": field("单位盖章" in text, material, 0.7),
    }


def extract_financial(text: str, material: dict[str, Any]) -> dict[str, Any]:
    revenue = first_match(text, [r"营业收入\s*[:：]?\s*([0-9,.]+\s*(?:亿元|万元|元))"])
    rd = first_match(text, [r"(?:研发投入|研发费用|研发经费)\s*[:：]?\s*([0-9,.]+\s*(?:亿元|万元|元))"])
    year = first_match(text, [r"(20\d{2})年度"])
    return {
        "report_year": field(year, material),
        "revenue": field(revenue, material),
        "rd_investment": field(rd, material),
    }


def extract_qualifications(text: str, material: dict[str, Any]) -> dict[str, Any]:
    company_names = sorted(
        set(
            re.findall(
                r"[\u4e00-\u9fa5A-Za-z0-9（）()·]{4,60}(?:有限责任公司|股份有限公司|有限公司|集团有限公司)",
                text,
            )
        )
    )
    claimed_counts = {
        label: int(value)
        for label, value in re.findall(
            r"(发明专利|实用新型专利|软件著作权|算法备案)\s*[:：]?\s*(\d+)\s*项",
            text,
        )
    }
    form = "certificate"
    if "名单" in text:
        form = "government_list"
    elif "汇总" in text or claimed_counts:
        form = "summary_claim"
    return {
        "listed_company_names": field(company_names, material, 0.8),
        "claimed_counts": field(claimed_counts, material, 0.8),
        "evidence_form": field(form, material, 0.9),
    }


def extract_material_fields(material: dict[str, Any]) -> dict[str, Any]:
    text = material.get("full_text", "")
    fields = extract_common(text, material)
    if material["document_type"] == "申报材料真实性承诺书":
        fields.update(extract_commitment(text, material))
    if material["document_type"] in {"主营业务收入或财务证明", "研发投入证明"}:
        fields.update(extract_financial(text, material))
    if material["category"] in {"研发能力证明", "荣誉资质证明", "行业资质证明"}:
        fields.update(extract_qualifications(text, material))
    return {
        "material_id": material["material_id"],
        "document_type": material["document_type"],
        "category": material["category"],
        "fields": fields,
    }


def extract_all(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [extract_material_fields(material) for material in materials]
