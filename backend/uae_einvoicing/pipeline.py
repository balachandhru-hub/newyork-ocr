"""Phase 2 UAE compliance pipeline.

Source payloads are normalized into the canonical model, mapped, enriched,
classified and validated. Nothing in this module calls an ASP or OPERA.
Regulatory behaviour comes from profiles, rules and code lists.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from uae_einvoicing.engine import (
    evaluate_rules,
    field,
    final_of,
    get_field,
    money,
    set_final,
)


FLAG_ORDER = [
    "FREE_TRADE_ZONE",
    "DEEMED_SUPPLY",
    "MARGIN_SCHEME",
    "SUMMARY_INVOICE",
    "CONTINUOUS_SUPPLY",
    "DISCLOSED_AGENT",
    "ECOMMERCE",
    "EXPORT",
]

STAGES = [
    "SCHEMA / DATA TYPE",
    "MANDATORY FIELDS",
    "CONDITIONAL MANDATORY FIELDS",
    "CODE LIST VALIDATION",
    "IDENTITY VALIDATION",
    "TRANSACTION CLASSIFICATION",
    "VAT / TAX VALIDATION",
    "LINE VALIDATION",
    "ARITHMETIC VALIDATION",
    "CROSS-FIELD VALIDATION",
    "DOCUMENT-SPECIFIC VALIDATION",
    "PINT-AE READINESS",
]

DOCUMENT_CLASSES = {
    "Tax Invoice": "tax-invoice",
    "Credit Note": "credit-note",
    "Tax Credit Note": "credit-note",
    "Commercial Invoice": "commercial-invoice",
    "Self-Billed Invoice": "self-billed-invoice",
    "Self-Billed Credit Note": "self-billed-credit-note",
}

# Official Peppol business-term ids are kept where the term is the standard
# invoice term. UAE-only values stay in the UAE extension and are not given
# invented PINT business-term ids.
FIELD_REGISTRY = [
    {"id": "invoiceDetails.invoiceNumber", "businessTermId": "IBT-001", "displayName": "Invoice Number", "section": "Document", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "maxLength": 70, "documents": ["All"]},
    {"id": "invoiceDetails.issueDate", "businessTermId": "IBT-002", "displayName": "Invoice Issue Date", "section": "Document", "dataType": "date", "cardinality": "1..1", "requirement": "MANDATORY", "format": "YYYY-MM-DD", "documents": ["All"]},
    {"id": "invoiceDetails.invoiceTypeCode", "businessTermId": "IBT-003", "displayName": "Invoice Type Code", "section": "Document", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "invoiceType", "documents": ["All"]},
    {"id": "invoiceDetails.currency", "businessTermId": "IBT-005", "displayName": "Invoice Currency", "section": "Document", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "currency", "documents": ["All"]},
    {"id": "invoiceDetails.taxAccountingCurrency", "businessTermId": "IBT-006", "displayName": "Tax Accounting Currency", "section": "Document", "dataType": "code", "cardinality": "0..1", "requirement": "OPTIONAL", "codeList": "currency", "documents": ["Tax Invoice"]},
    {"id": "invoiceDetails.transactionTypeCode", "businessTermId": "", "displayName": "Invoice Transaction Type Code", "section": "UAE Extension", "dataType": "string", "cardinality": "1..1", "requirement": "DERIVED", "format": "8 flags of 0 or 1", "documents": ["All"]},
    {"id": "invoiceDetails.vatPointDate", "businessTermId": "IBT-007", "displayName": "VAT Point Date", "section": "Document", "dataType": "date", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["Tax Invoice"]},
    {"id": "invoiceDetails.paymentDueDate", "businessTermId": "IBT-009", "displayName": "Payment Due Date", "section": "Document", "dataType": "date", "cardinality": "0..1", "requirement": "CONDITIONAL", "documents": ["Tax Invoice"]},
    {"id": "invoiceDetails.buyerReference", "businessTermId": "IBT-010", "displayName": "Buyer Reference", "section": "Document", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "invoiceDetails.projectReference", "businessTermId": "IBT-011", "displayName": "Project Reference", "section": "Document", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "invoiceDetails.contractReference", "businessTermId": "IBT-012", "displayName": "Contract Reference", "section": "Document", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "invoiceDetails.purchaseOrderReference", "businessTermId": "IBT-013", "displayName": "Purchase Order Reference", "section": "Document", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "invoiceDetails.salesOrderReference", "businessTermId": "IBT-014", "displayName": "Sales Order Reference", "section": "Document", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "invoiceDetails.businessProcessType", "businessTermId": "IBT-023", "displayName": "Business Process Type", "section": "Document", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "invoiceDetails.specificationIdentifier", "businessTermId": "IBT-024", "displayName": "Specification Identifier", "section": "Document", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "invoiceDetails.periodStart", "businessTermId": "IBT-073", "displayName": "Invoice Period Start", "section": "Document", "dataType": "date", "cardinality": "0..1", "requirement": "CONDITIONAL", "documents": ["Tax Invoice"]},
    {"id": "invoiceDetails.periodEnd", "businessTermId": "IBT-074", "displayName": "Invoice Period End", "section": "Document", "dataType": "date", "cardinality": "0..1", "requirement": "CONDITIONAL", "documents": ["Tax Invoice"]},
    {"id": "seller.name", "businessTermId": "IBT-027", "displayName": "Seller Legal Name", "section": "Seller", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "seller.tradingName", "businessTermId": "IBT-028", "displayName": "Seller Trading Name", "section": "Seller", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "seller.trn", "businessTermId": "IBT-031", "displayName": "Seller TRN", "section": "Seller", "dataType": "identifier", "cardinality": "1..1", "requirement": "MANDATORY", "format": "15 digits", "documents": ["Tax Invoice", "Self-Billed Invoice"]},
    {"id": "seller.tin", "businessTermId": "IBT-031", "displayName": "Seller TIN", "section": "Seller", "dataType": "identifier", "cardinality": "0..1", "requirement": "DERIVED", "documents": ["Tax Invoice"]},
    {"id": "seller.electronicAddress", "businessTermId": "IBT-034", "displayName": "Seller Electronic Address", "section": "Seller", "dataType": "identifier", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "seller.electronicAddressScheme", "businessTermId": "IBT-034-1", "displayName": "Seller Electronic Address Scheme", "section": "Seller", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "electronicAddressScheme", "documents": ["All"]},
    {"id": "seller.tradeLicenceNumber", "businessTermId": "", "displayName": "Trade Licence Number", "section": "Seller", "dataType": "string", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "seller.countryCode", "businessTermId": "IBT-040", "displayName": "Seller Country Code", "section": "Seller", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "country", "documents": ["All"]},
    {"id": "buyer.name", "businessTermId": "IBT-044", "displayName": "Buyer Legal Name", "section": "Buyer", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "buyer.trn", "businessTermId": "IBT-048", "displayName": "Buyer TRN", "section": "Buyer", "dataType": "identifier", "cardinality": "0..1", "requirement": "CONDITIONAL", "format": "15 digits", "documents": ["Tax Invoice"]},
    {"id": "buyer.tin", "businessTermId": "IBT-048", "displayName": "Buyer TIN", "section": "Buyer", "dataType": "identifier", "cardinality": "0..1", "requirement": "DERIVED", "documents": ["Tax Invoice"]},
    {"id": "buyer.electronicAddress", "businessTermId": "IBT-049", "displayName": "Buyer Electronic Address", "section": "Buyer", "dataType": "identifier", "cardinality": "0..1", "requirement": "CONDITIONAL", "documents": ["Tax Invoice"]},
    {"id": "buyer.countryCode", "businessTermId": "IBT-055", "displayName": "Buyer Country Code", "section": "Buyer", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "country", "documents": ["All"]},
    {"id": "tax.category", "businessTermId": "IBT-118", "displayName": "VAT Category", "section": "Tax", "dataType": "code", "cardinality": "1..1", "requirement": "MANDATORY", "codeList": "vatCategory", "documents": ["Tax Invoice"]},
    {"id": "tax.rate", "businessTermId": "IBT-119", "displayName": "VAT Rate", "section": "Tax", "dataType": "number", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["Tax Invoice"]},
    {"id": "tax.exemptionReason", "businessTermId": "IBT-120", "displayName": "Tax Exemption Reason", "section": "Tax", "dataType": "string", "cardinality": "0..1", "requirement": "CONDITIONAL", "documents": ["Tax Invoice"]},
    {"id": "tax.exemptionReasonCode", "businessTermId": "IBT-121", "displayName": "Tax Exemption Reason Code", "section": "Tax", "dataType": "code", "cardinality": "0..1", "requirement": "CONDITIONAL", "codeList": "exemptionReason", "documents": ["Tax Invoice"]},
    {"id": "totals.netAmount", "businessTermId": "IBT-106", "displayName": "Sum of Invoice Line Net Amount", "section": "Totals", "dataType": "amount", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "totals.allowances", "businessTermId": "IBT-107", "displayName": "Sum of Allowances", "section": "Totals", "dataType": "amount", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "totals.charges", "businessTermId": "IBT-108", "displayName": "Sum of Charges", "section": "Totals", "dataType": "amount", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "totals.taxExclusiveAmount", "businessTermId": "IBT-109", "displayName": "Invoice Total Excluding VAT", "section": "Totals", "dataType": "amount", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "totals.taxAmount", "businessTermId": "IBT-110", "displayName": "Invoice Total VAT", "section": "Totals", "dataType": "amount", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["Tax Invoice", "Credit Note", "Self-Billed Invoice"]},
    {"id": "totals.taxInclusiveAmount", "businessTermId": "IBT-112", "displayName": "Invoice Total Including VAT", "section": "Totals", "dataType": "amount", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "totals.paidAmount", "businessTermId": "IBT-113", "displayName": "Paid Amount", "section": "Totals", "dataType": "amount", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "totals.roundingAmount", "businessTermId": "IBT-114", "displayName": "Rounding Amount", "section": "Totals", "dataType": "amount", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
    {"id": "totals.amountDue", "businessTermId": "IBT-115", "displayName": "Amount Due", "section": "Totals", "dataType": "amount", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["All"]},
    {"id": "creditNoteReferences.invoiceNumber", "businessTermId": "IBT-025", "displayName": "Preceding Invoice Reference", "section": "References", "dataType": "string", "cardinality": "1..1", "requirement": "MANDATORY", "documents": ["Credit Note", "Tax Credit Note", "Self-Billed Credit Note"]},
    {"id": "payment.means", "businessTermId": "IBT-081", "displayName": "Payment Means Code", "section": "Payment", "dataType": "code", "cardinality": "0..1", "requirement": "CONDITIONAL", "codeList": "paymentMeans", "documents": ["Tax Invoice"]},
    {"id": "payment.endpoint", "businessTermId": "IBT-034", "displayName": "Payment Endpoint", "section": "Payment", "dataType": "identifier", "cardinality": "0..1", "requirement": "OPTIONAL", "documents": ["All"]},
]


def registry_by_id() -> dict[str, dict]:
    return {item["id"]: item for item in FIELD_REGISTRY}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _put_default(canonical: dict, path: str, value: Any, rule: str = "Direct") -> None:
    section, name = path.split(".", 1)
    canonical.setdefault(section, {})
    if name not in canonical[section]:
        canonical[section][name] = field(value, value, value, rule=rule)


def ensure_canonical(canonical: dict, document_type: str, profile: dict | None = None) -> dict:
    """Fill canonical sections that are absent. Existing values are kept."""
    profile = profile or {}
    invoice_number = final_of(canonical, "invoiceDetails.invoiceNumber") or "INV"
    issue_date = final_of(canonical, "invoiceDetails.issueDate") or "2026-10-14"
    currency = final_of(canonical, "invoiceDetails.currency") or "AED"
    seller_name = final_of(canonical, "seller.name") or ""
    seller_trn = final_of(canonical, "seller.trn")
    buyer_name = final_of(canonical, "buyer.name") or ""
    buyer_trn = final_of(canonical, "buyer.trn")
    net = money(final_of(canonical, "totals.netAmount") or 0)
    tax = money(final_of(canonical, "totals.taxAmount") or 0)
    gross = money(final_of(canonical, "totals.taxInclusiveAmount") or (net + tax))
    type_code = {"Tax Invoice": "380", "Credit Note": "381", "Tax Credit Note": "381", "Commercial Invoice": "380", "Self-Billed Invoice": "389", "Self-Billed Credit Note": "261"}.get(document_type, "380")
    defaults = {
        "invoiceDetails.invoiceTypeCode": type_code,
        "invoiceDetails.taxAccountingCurrency": currency,
        "invoiceDetails.buyerReference": final_of(canonical, "references.buyerReference") or "",
        "invoiceDetails.purchaseOrderReference": final_of(canonical, "purchaseOrder.purchaseOrderId") or "",
        "invoiceDetails.businessProcessType": profile.get("businessProcessId") or "urn:peppol:bis:billing",
        "invoiceDetails.specificationIdentifier": profile.get("specificationId") or "urn:peppol:pint:ae:1.0",
        "seller.tradingName": seller_name,
        "seller.electronicAddressScheme": "0235",
        "seller.countryCode": "AE",
        "buyer.electronicAddressScheme": "0235",
        "buyer.countryCode": "AE",
        "totals.allowances": Decimal("0.00"),
        "totals.charges": Decimal("0.00"),
        "totals.taxExclusiveAmount": net,
        "totals.paidAmount": Decimal("0.00"),
        "totals.roundingAmount": Decimal("0.00"),
        "totals.amountDue": gross,
        "payment.means": final_of(canonical, "payment.means") or "30",
    }
    for path, value in defaults.items():
        _put_default(canonical, path, value)
    if get_field(canonical, "seller.electronicAddress") is None and seller_trn:
        _put_default(canonical, "seller.electronicAddress", f"0235:{seller_trn}", "Derived")
    if get_field(canonical, "buyer.electronicAddress") is None and buyer_trn:
        _put_default(canonical, "buyer.electronicAddress", f"0235:{buyer_trn}", "Derived")
    _ensure_lines(canonical, net, tax)
    canonical.setdefault("uaeExtensions", {})
    return canonical


def _ensure_lines(canonical: dict, net: Decimal, tax: Decimal) -> None:
    section = canonical.setdefault("invoiceLines", {})
    if isinstance(section.get("lines"), list) and section["lines"]:
        return
    raw = final_of(canonical, "invoiceLines.items")
    if not isinstance(raw, list) or not raw:
        raw = [{"description": "Supply", "amount": net}]
    lines = []
    for index, item in enumerate(raw, start=1):
        amount = money(item.get("amount", net if index == 1 else 0))
        rate = money(item.get("vatRate", 5))
        lines.append({
            "lineId": field(str(index)),
            "itemName": field(item.get("name") or item.get("description") or "Item"),
            "itemDescription": field(item.get("description") or item.get("name") or "Item"),
            "quantity": field(item.get("qty") or item.get("quantity") or 1),
            "uom": field(item.get("uom") or "EA"),
            "netAmount": field(amount),
            "vatCategory": field(item.get("vatCategory") or "S"),
            "vatRate": field(rate),
            "vatAmount": field(item.get("vatAmount") if item.get("vatAmount") is not None else (amount * rate / Decimal("100")).quantize(Decimal("0.01"))),
        })
    section["lines"] = lines
    if "items" not in section:
        section["items"] = field(raw)


def select_profile(profiles: list[dict], invoice_date: str) -> dict | None:
    chosen = None
    for profile in profiles:
        if profile.get("status") != "ACTIVE":
            continue
        start = profile.get("effectiveFrom") or "0000-01-01"
        end = profile.get("effectiveTo") or "9999-12-31"
        if start <= (invoice_date or "9999-12-31") <= end:
            if chosen is not None:
                continue
            chosen = profile
    return chosen


def _payload_get(payload: dict, path: str) -> Any:
    current: Any = payload
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _approved(name: str, value: Any, argument: str = "") -> Any:
    if name == "digits_only":
        return re.sub(r"\D", "", "" if value is None else str(value))
    if name == "iso_date":
        text = "" if value is None else str(value)
        if re.fullmatch(r"\d{8}", text):
            return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
        return text
    if name == "copy_if_valid_trn":
        text = re.sub(r"\D", "", "" if value is None else str(value))
        return text if re.fullmatch(r"\d{15}", text) else None
    return value


def execute_mapping(payload: dict, lines: list[dict], code_lists: dict[str, list[dict]]) -> dict[str, dict]:
    """Run a mapping version. Customer mappings stay in configuration."""
    outputs: dict[str, dict] = {}
    for line in lines:
        operation = (line.get("mappingType") or "Direct").upper().replace(" ", "_")
        source_field = line.get("sourceField") or ""
        source_value = _payload_get(payload, source_field) if source_field else None
        output = source_value
        if operation == "CONSTANT":
            output = line.get("defaultValue")
        elif operation == "LOOKUP" or operation == "CODE_TRANSLATION":
            output = _translate(code_lists.get(line.get("codeList") or "taxCode", []), source_value, line.get("defaultValue"))
        elif operation == "CONCATENATE":
            parts = [_payload_get(payload, item) for item in line.get("sourceFields") or [source_field]]
            output = "".join("" if part is None else str(part) for part in parts)
        elif operation == "SUBSTRING":
            text = "" if source_value is None else str(source_value)
            start = int(line.get("start") or 0)
            end = line.get("end")
            output = text[start: int(end) if end is not None else None]
        elif operation in {"DATE_FORMAT", "TRANSFORMATION"} and "date" in (line.get("transformation") or "").lower():
            output = _approved("iso_date", source_value)
        elif operation in {"NUMBER_FORMAT", "CURRENCY_TRANSFORMATION"}:
            output = str(money(source_value))
        elif operation == "CONDITIONAL":
            output = source_value if source_value not in (None, "") else line.get("defaultValue")
        elif operation == "DERIVED":
            output = _approved(line.get("function") or "digits_only", source_value)
        elif operation == "MULTI_SOURCE":
            output = next((_payload_get(payload, item) for item in line.get("sourceFields") or [] if _payload_get(payload, item) not in (None, "")), line.get("defaultValue"))
        elif operation == "CUSTOM_APPROVED_FUNCTION":
            output = _approved(line.get("function") or "", source_value)
        elif operation == "TRANSFORMATION":
            output = _approved("digits_only", source_value) if "digit" in (line.get("transformation") or "").lower() else source_value
        target = line.get("targetField")
        if not target:
            continue
        record = field(source_value, output, output, rule=line.get("mappingType") or "Direct", derivation=line.get("derivationRule") or "")
        record["transformation"] = line.get("transformation") or ""
        record["mappingVersion"] = line.get("version")
        record["mappedAt"] = _now()
        outputs[target] = record
    return outputs


def _translate(rows: list[dict], source_value: Any, default: Any) -> Any:
    for row in rows:
        if str(row.get("sourceValue", row.get("code"))) == str(source_value):
            return row.get("canonicalValue", row.get("code"))
    return default if source_value in (None, "") else source_value


def apply_mapping_outputs(canonical: dict, outputs: dict[str, dict]) -> None:
    for path, record in outputs.items():
        if path.count(".") != 1:
            continue
        section, name = path.split(".", 1)
        canonical.setdefault(section, {})
        current = canonical[section].get(name)
        if isinstance(current, dict) and current.get("corrected"):
            current["mappedValue"] = record.get("mappedValue")
            current["sourceValue"] = record.get("sourceValue")
            continue
        canonical[section][name] = record


def enrich(canonical: dict, legal_entity: dict | None, profile: dict | None) -> None:
    """Derive identifiers only when the configured source value is valid."""
    profile = profile or {}
    entity = legal_entity or {}
    for side in ("seller", "buyer"):
        trn = get_field(canonical, f"{side}.trn")
        derived_tin = _approved("copy_if_valid_trn", None if not trn else trn.get("finalValue"))
        tin = get_field(canonical, f"{side}.tin")
        if derived_tin and (not tin or tin.get("finalValue") in (None, "")):
            record = field(None if not trn else trn.get("sourceValue"), None, derived_tin, rule="Derived", derivation=profile.get("tinDerivation") or "Copy a valid 15-digit TRN into TIN")
            record["enrichedValue"] = derived_tin
            record["enrichmentSource"] = "Derivation rule"
            record["sourceValue"] = None if not trn else trn.get("sourceValue")
            canonical.setdefault(side, {})[ "tin"] = record
        scheme = final_of(canonical, f"{side}.electronicAddressScheme") or "0235"
        address = get_field(canonical, f"{side}.electronicAddress")
        tin_value = final_of(canonical, f"{side}.tin") or derived_tin
        if tin_value and (not address or address.get("finalValue") in (None, "")):
            value = f"{scheme}:{tin_value}"
            record = field(None, None, value, rule="Derived", derivation="Scheme plus TIN")
            record["enrichedValue"] = value
            record["enrichmentSource"] = "Electronic address derivation"
            canonical.setdefault(side, {})["electronicAddress"] = record
    if entity and final_of(canonical, "seller.tradeLicenceNumber") in (None, ""):
        if entity.get("tradeLicenceNumber"):
            record = field(None, None, entity["tradeLicenceNumber"], rule="Enrichment", derivation="Legal entity master")
            record["enrichedValue"] = entity["tradeLicenceNumber"]
            record["enrichmentSource"] = "Legal Entity Master"
            canonical.setdefault("seller", {})["tradeLicenceNumber"] = record


def classify(canonical: dict, flag_rules: list[dict], profile: dict | None) -> dict:
    from uae_einvoicing.engine import derive_flags
    flags = derive_flags(canonical, flag_rules)
    order = (profile or {}).get("transactionTypeFlags") or FLAG_ORDER
    by_code = {item["code"]: item for item in flags}
    positions = []
    for code in order:
        item = by_code.get(code, {"code": code, "derived": False})
        bit = "1" if item.get("derived") else "0"
        positions.append({
            "code": code,
            "value": bit,
            "rule": item.get("derivation") or "",
            "evidence": item.get("code"),
            "ruleVersion": (profile or {}).get("version"),
            "timestamp": _now(),
        })
    code = "".join(item["value"] for item in positions)
    generated = field(None, code, code, rule="Derived", derivation="UAE transaction-type flags")
    generated["enrichedValue"] = code
    generated["enrichmentSource"] = "Transaction classification"
    canonical.setdefault("invoiceDetails", {})["transactionTypeCode"] = generated
    canonical.setdefault("uaeExtensions", {})["transactionTypeCode"] = generated
    document_type = final_of(canonical, "invoiceDetails.documentType") or ""
    tax_category = final_of(canonical, "tax.category")
    scenarios = []
    scenario_rules = (profile or {}).get("scenarioRules") or []
    for rule in scenario_rules:
        applies = rule.get("documentType") in (None, "", "All", document_type)
        if rule.get("when"):
            from uae_einvoicing.engine import _matches_when
            applies = applies and _matches_when(canonical, rule["when"])
        elif rule.get("taxCategory"):
            applies = applies and tax_category == rule["taxCategory"]
        if applies:
            scenarios.append({"code": rule["code"], "label": rule["label"], "rule": rule["code"]})
    return {"flags": positions, "code": code, "scenarios": scenarios, "ruleVersion": (profile or {}).get("version")}


def _stage_for(result: dict) -> str:
    logic_when = result.get("conditional")
    category = result.get("category") or ""
    if result.get("stage"):
        return result["stage"]
    if category in {"Totals"}:
        return "ARITHMETIC VALIDATION"
    if category in {"Invoice Lines"}:
        return "LINE VALIDATION"
    if category in {"Tax"}:
        return "VAT / TAX VALIDATION"
    if category in {"Credit Notes", "Self-Billing", "Reference Validation"}:
        return "DOCUMENT-SPECIFIC VALIDATION"
    if category in {"Transaction Type"}:
        return "TRANSACTION CLASSIFICATION"
    if category in {"Seller", "Buyer", "Payment"}:
        return "IDENTITY VALIDATION"
    if logic_when:
        return "CONDITIONAL MANDATORY FIELDS"
    return "MANDATORY FIELDS"


def _group_for(stage: str) -> str:
    return {
        "MANDATORY FIELDS": "Mandatory Fields",
        "CONDITIONAL MANDATORY FIELDS": "Conditional Fields",
        "CODE LIST VALIDATION": "PINT-AE",
        "IDENTITY VALIDATION": "Identity",
        "TRANSACTION CLASSIFICATION": "Transaction Type",
        "VAT / TAX VALIDATION": "Tax",
        "LINE VALIDATION": "Lines",
        "ARITHMETIC VALIDATION": "Totals",
        "CROSS-FIELD VALIDATION": "Totals",
        "DOCUMENT-SPECIFIC VALIDATION": "References",
        "PINT-AE READINESS": "PINT-AE",
        "SCHEMA / DATA TYPE": "Mandatory Fields",
    }.get(stage, "Mandatory Fields")


def validate_transaction(canonical: dict, rules: list[dict], document_type: str, code_lists: dict[str, list[dict]], profile: dict | None) -> dict:
    ensure_canonical(canonical, document_type, profile)
    raw_results = evaluate_rules(canonical, rules, document_type)
    results = []
    for item in raw_results:
        stage = _stage_for(item)
        item = {**item, "stage": stage, "group": _group_for(stage), "requirement": item.get("requirement") or ("CONDITIONAL" if item.get("conditional") else "MANDATORY")}
        field_record = get_field(canonical, item.get("field") or "")
        item["enrichedValue"] = None if not field_record else field_record.get("enrichedValue")
        item["finalValue"] = None if not field_record else field_record.get("finalValue")
        item["businessTermId"] = registry_by_id().get(item.get("field") or "", {}).get("businessTermId", "")
        results.append(item)
    results.extend(_code_list_results(canonical, code_lists, document_type))
    results.extend(_line_results(canonical))
    results.extend(_arithmetic_results(canonical))
    results.extend(_readiness_results(canonical, profile, document_type))
    stages = []
    for stage in STAGES:
        scoped = [item for item in results if item.get("stage") == stage]
        stages.append({"stage": stage, "status": _status(scoped), "passed": len([i for i in scoped if i["status"] == "PASSED"]), "failed": len([i for i in scoped if i["status"] == "FAILED"]), "total": len(scoped)})
    return {"results": results, "stages": stages, "score": compliance_score(results)}


def _status(results: list[dict]) -> str:
    if not results:
        return "NOT APPLICABLE"
    if any(item["status"] == "FAILED" and item.get("severity") == "ERROR" for item in results):
        return "FAIL"
    if any(item["status"] == "FAILED" for item in results):
        return "WARNING"
    return "PASS"


def compliance_score(results: list[dict]) -> dict:
    considered = [item for item in results if item.get("status") in {"PASSED", "FAILED"}]
    if not considered:
        return {"percent": 100, "status": "COMPLIANT", "label": "100% — COMPLIANT", "errors": 0}
    passed = len([item for item in considered if item["status"] == "PASSED"])
    percent = round(100 * passed / len(considered))
    errors = len([item for item in considered if item["status"] == "FAILED" and item.get("severity") == "ERROR"])
    warnings = len([item for item in considered if item["status"] == "FAILED" and item.get("severity") != "ERROR"])
    if errors:
        status = "FAILED"
    elif warnings:
        status = "WARNING"
    else:
        status = "COMPLIANT"
    return {"percent": percent, "status": status, "label": f"{percent}% — {status}", "errors": errors}


def _code_list_results(canonical: dict, code_lists: dict[str, list[dict]], document_type: str) -> list[dict]:
    results = []
    for meta in FIELD_REGISTRY:
        list_name = meta.get("codeList")
        if not list_name or (document_type not in meta["documents"] and "All" not in meta["documents"]):
            continue
        record = get_field(canonical, meta["id"])
        if not record or record.get("finalValue") in (None, ""):
            continue
        allowed = {str(row.get("code")) for row in code_lists.get(list_name, []) if row.get("active", True)}
        if not allowed:
            continue
        value = str(record.get("finalValue"))
        passed = value in allowed
        results.append(_result(
            f"CODE-{meta['businessTermId'] or meta['id']}",
            f"{meta['displayName']} code list",
            meta["id"],
            "CODE LIST VALIDATION",
            passed,
            "ERROR" if meta["requirement"] == "MANDATORY" else "WARNING",
            record,
            f"Value must be in {list_name}",
        ))
    return results


def _line_results(canonical: dict) -> list[dict]:
    lines = canonical.get("invoiceLines", {}).get("lines") or []
    results = []
    for index, line in enumerate(lines, start=1):
        for key, label in (("lineId", "Line ID"), ("itemDescription", "Description"), ("quantity", "Quantity"), ("uom", "UOM"), ("netAmount", "Net amount"), ("vatCategory", "VAT category"), ("vatRate", "VAT rate")):
            record = line.get(key) or {}
            present = record.get("finalValue") not in (None, "")
            results.append(_result(
                f"LINE-{index}-{key}",
                label,
                f"invoiceLines.lines[{index}].{key}",
                "LINE VALIDATION",
                present,
                "ERROR",
                record,
                "Required on every invoice line",
                line_number=index,
            ))
    return results


def _arithmetic_results(canonical: dict) -> list[dict]:
    lines = canonical.get("invoiceLines", {}).get("lines") or []
    line_net = sum((money((line.get("netAmount") or {}).get("finalValue")) for line in lines), Decimal("0.00"))
    header_net = money(final_of(canonical, "totals.netAmount"))
    allowances = money(final_of(canonical, "totals.allowances"))
    charges = money(final_of(canonical, "totals.charges"))
    exclusive = money(final_of(canonical, "totals.taxExclusiveAmount"))
    vat = money(final_of(canonical, "totals.taxAmount"))
    inclusive = money(final_of(canonical, "totals.taxInclusiveAmount"))
    paid = money(final_of(canonical, "totals.paidAmount"))
    rounding = money(final_of(canonical, "totals.roundingAmount"))
    due = money(final_of(canonical, "totals.amountDue"))
    checks = [
        ("ARITH-LINE-NET", "Line net equals header net", line_net == header_net, "totals.netAmount"),
        ("ARITH-EXCL", "Exclusive total equals net - allowances + charges", exclusive == (header_net - allowances + charges), "totals.taxExclusiveAmount"),
        ("ARITH-INCL", "Inclusive total equals exclusive plus VAT", inclusive == (exclusive + vat), "totals.taxInclusiveAmount"),
        ("ARITH-DUE", "Amount due equals inclusive - paid + rounding", due == (inclusive - paid + rounding), "totals.amountDue"),
    ]
    results = []
    for rule_id, name, passed, path in checks:
        record = get_field(canonical, path) or {}
        results.append(_result(rule_id, name, path, "ARITHMETIC VALIDATION", passed, "ERROR", record, "Decimal totals must balance"))
    return results


def _readiness_results(canonical: dict, profile: dict | None, document_type: str) -> list[dict]:
    process_id = final_of(canonical, "invoiceDetails.businessProcessType")
    specification = final_of(canonical, "invoiceDetails.specificationIdentifier")
    expected_process = (profile or {}).get("businessProcessId")
    expected_spec = (profile or {}).get("specificationId")
    type_code = final_of(canonical, "invoiceDetails.transactionTypeCode")
    checks = [
        ("PINT-PROC", "Business process identifier", process_id == expected_process and bool(process_id), "invoiceDetails.businessProcessType"),
        ("PINT-SPEC", "Specification identifier", specification == expected_spec and bool(specification), "invoiceDetails.specificationIdentifier"),
        ("PINT-TYPE", "Transaction type code generated", bool(type_code) and re.fullmatch(r"[01]{8}", str(type_code)) is not None, "invoiceDetails.transactionTypeCode"),
    ]
    if DOCUMENT_CLASSES.get(document_type) == "commercial-invoice":
        checks.append(("COM-PROFILE", "Commercial invoice profile selected", (profile or {}).get("commercialProfile") == document_type or document_type == "Commercial Invoice", "invoiceDetails.invoiceTypeCode"))
    results = []
    for rule_id, name, passed, path in checks:
        results.append(_result(rule_id, name, path, "PINT-AE READINESS", bool(passed), "ERROR", get_field(canonical, path) or {}, "Value must match the active UAE regulatory profile"))
    return results


def _result(rule_id: str, name: str, path: str, stage: str, passed: bool, severity: str, record: dict, expected: str, line_number: int | None = None) -> dict:
    return {
        "ruleId": rule_id,
        "ruleName": name,
        "category": stage,
        "stage": stage,
        "group": _group_for(stage),
        "severity": severity,
        "field": path,
        "businessTermId": registry_by_id().get(path, {}).get("businessTermId", ""),
        "requirement": "MANDATORY" if severity == "ERROR" else "OPTIONAL",
        "sourceValue": record.get("sourceValue"),
        "mappedValue": record.get("mappedValue"),
        "enrichedValue": record.get("enrichedValue"),
        "finalValue": record.get("finalValue"),
        "expected": expected,
        "message": None if passed else expected,
        "resolution": None if passed else "Correct the canonical value and revalidate. The original source payload is kept.",
        "status": "PASSED" if passed else "FAILED",
        "lineNumber": line_number,
        "regulatorySource": "UAE e-invoicing profile",
    }


def workbench(results: list[dict], score: dict) -> dict:
    groups = ["Mandatory Fields", "Conditional Fields", "Tax", "Identity", "Transaction Type", "Totals", "Lines", "References", "PINT-AE"]
    cards = []
    for group in groups:
        scoped = [item for item in results if item.get("group") == group]
        passed = len([item for item in scoped if item["status"] == "PASSED"])
        cards.append({"label": group, "passed": passed, "total": len(scoped), "status": _status(scoped)})
    return {"status": score["status"], "score": score, "cards": cards}


def apply_corrections(canonical: dict, corrections: list[dict]) -> None:
    for item in corrections:
        set_final(canonical, item["path"], _coerce(item.get("value")))
        record = get_field(canonical, item["path"])
        if record:
            record["corrected"] = True
            record["enrichmentSource"] = "Authorised correction"


def _coerce(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return str(money(value)) if "." in value else value
    return value


def sync_document_type(canonical: dict, document_type: str) -> None:
    if not document_type:
        return
    canonical.setdefault("invoiceDetails", {})
    current = canonical["invoiceDetails"].get("documentType")
    if not isinstance(current, dict) or "finalValue" not in current:
        canonical["invoiceDetails"]["documentType"] = field(document_type)
    elif not current.get("corrected"):
        current["mappedValue"] = document_type
        current["finalValue"] = document_type
    type_codes = {"Tax Invoice": "380", "Credit Note": "381", "Tax Credit Note": "381", "Commercial Invoice": "380", "Self-Billed Invoice": "389", "Self-Billed Credit Note": "261"}
    code = type_codes.get(document_type)
    if code and not (get_field(canonical, "invoiceDetails.invoiceTypeCode") or {}).get("corrected"):
        canonical["invoiceDetails"]["invoiceTypeCode"] = field(code, code, code, rule="Derived", derivation="Document type code list")


def context_for(data: dict, txn: dict) -> dict:
    entity = next((item for item in data.get("legalEntities", []) if item["id"] == txn.get("legalEntityId")), None)
    return {
        "profiles": data.get("profiles") or [],
        "rules": data.get("rules") or [],
        "mappings": data.get("mappings") or [],
        "flagRules": data.get("flagRules") or [],
        "codeLists": data.get("codeLists") or {},
        "legalEntity": entity,
    }


def attach_phase2(data: dict) -> dict:
    """Add regulatory configuration and validate seeded transactions."""
    data["schemaVersion"] = 2
    data["profiles"] = [default_profile(), draft_profile()]
    data["codeLists"] = default_code_lists()
    data["codeLists"]["taxCode"] = [
        {"code": "A5", "sourceValue": "A5", "canonicalValue": "S", "description": "Standard rated 5%", "source": "Tax mapping", "version": "2026.1", "effectiveDate": "2026-01-01", "active": True},
        {"code": "A0", "sourceValue": "A0", "canonicalValue": "Z", "description": "Zero rated", "source": "Tax mapping", "version": "2026.1", "effectiveDate": "2026-01-01", "active": True},
    ]
    if not any(item["id"] == "UAE-2026-00049" for item in data["transactions"]):
        sample = deepcopy(next(item for item in data["transactions"] if item["id"] == "UAE-2026-00041"))
        sample.update({
            "id": "UAE-2026-00049",
            "documentType": "Commercial Invoice",
            "invoiceNumber": "CI-100",
            "direction": "Outbound AR",
            "lifecycleStatus": "READY_FOR_ASP",
            "aspStatus": "NOT_SUBMITTED",
            "networkStatus": "NOT_SENT",
            "erpStatus": "NOT_POSTED",
            "sourceStatus": "RECEIVED",
            "timeline": [{"at": "2026-10-14T08:00:00+00:00", "label": "Received from SAP S/4HANA"}],
        })
        sample["canonical"] = deepcopy(sample["canonical"])
        data["transactions"].append(sample)
    protected = {"RECONCILED", "ASP_REJECTED", "SUBMITTED", "MAPPING_FAILED"}
    for txn in data["transactions"]:
        previous = {
            "lifecycleStatus": txn.get("lifecycleStatus"),
            "aspStatus": txn.get("aspStatus"),
            "networkStatus": txn.get("networkStatus"),
            "erpStatus": txn.get("erpStatus"),
        }
        run_pipeline(txn, context_for(data, txn), mode="revalidate")
        if previous["lifecycleStatus"] in protected:
            txn["lifecycleStatus"] = previous["lifecycleStatus"]
            txn["aspStatus"] = previous["aspStatus"]
            txn["networkStatus"] = previous["networkStatus"]
            txn["erpStatus"] = previous["erpStatus"]
            if previous["lifecycleStatus"] == "MAPPING_FAILED":
                txn["complianceStatus"] = "NON-COMPLIANT"
                txn["aspStatus"] = "NOT_SUBMITTED"
    data["masterCompliance"] = {
        "buyers": [{"tenantId": "ten_five", "sourceValue": "Gulf Events", "canonicalValue": "Gulf Events LLC", "regulatoryCode": "100555666777003", "status": "ACTIVE", "effectiveFrom": "2026-01-01", "effectiveTo": None}],
        "suppliers": [{"tenantId": "ten_five", "sourceValue": "Desert Linen", "canonicalValue": "Desert Linen LLC", "regulatoryCode": "100777888999003", "status": "ACTIVE", "effectiveFrom": "2026-01-01", "effectiveTo": None}],
        "taxCodes": data["codeLists"]["taxCode"],
        "uom": data["codeLists"]["uom"],
        "currencies": data["codeLists"]["currency"],
        "paymentMeans": data["codeLists"]["paymentMeans"],
        "vatCategories": data["codeLists"]["vatCategory"],
        "countryCodes": data["codeLists"]["country"],
        "emirates": data["codeLists"]["emirate"],
        "itemClassifications": [{"tenantId": "ten_five", "sourceValue": "ROOM", "canonicalValue": "Accommodation", "regulatoryCode": "5510", "status": "ACTIVE", "effectiveFrom": "2026-01-01", "effectiveTo": None}],
        "legalRegistrationTypes": data["codeLists"]["legalRegistrationType"],
        "electronicAddressSchemes": data["codeLists"]["electronicAddressScheme"],
    }
    return data


def run_pipeline(txn: dict, context: dict, mode: str = "full") -> dict:
    """Validate one transaction in place. The transaction id does not change."""
    profile = txn.get("complianceProfile") and next((item for item in context["profiles"] if item["id"] == txn.get("complianceProfileId")), None)
    if profile is None:
        profile = select_profile(context["profiles"], txn.get("invoiceDate") or "")
    if profile and not txn.get("complianceProfileId"):
        txn["complianceProfileId"] = profile["id"]
        txn["complianceVersion"] = profile["version"]
    elif profile and txn.get("complianceProfileId"):
        profile = next((item for item in context["profiles"] if item["id"] == txn["complianceProfileId"]), profile)
    canonical = txn.setdefault("canonical", {})
    sync_document_type(canonical, txn.get("documentType") or "")
    if mode in {"remap", "full"} and txn.get("sourcePayload") and txn.get("mappingVersionId"):
        mapping = next((item for item in context["mappings"] if item["id"] == txn["mappingVersionId"]), None)
        if mapping:
            outputs = execute_mapping(txn["sourcePayload"], mapping.get("lines") or [], context.get("codeLists") or {})
            apply_mapping_outputs(canonical, outputs)
            txn.setdefault("timeline", []).append({"at": _now(), "label": "Remapped"})
    if mode in {"reenrich", "full", "revalidate"}:
        enrich(canonical, context.get("legalEntity"), profile)
        if mode == "reenrich":
            txn.setdefault("timeline", []).append({"at": _now(), "label": "Re-enriched"})
    apply_corrections(canonical, txn.get("corrections") or [])
    ensure_canonical(canonical, txn.get("documentType") or "Tax Invoice", profile)
    classification = classify(canonical, context.get("flagRules") or [], profile)
    for item in classification["flags"]:
        override = (txn.get("flagOverrides") or {}).get(item["code"])
        if override is not None:
            item["value"] = "1" if override else "0"
            item["manualOverride"] = True
    classification["code"] = "".join(item["value"] for item in classification["flags"])
    generated = field(None, classification["code"], classification["code"], rule="Derived", derivation="UAE transaction-type flags")
    generated["enrichedValue"] = classification["code"]
    canonical.setdefault("invoiceDetails", {})["transactionTypeCode"] = generated
    canonical.setdefault("uaeExtensions", {})["transactionTypeCode"] = generated
    txn["transactionType"] = classification
    txn["classification"] = [
        {"code": item["code"], "label": item["code"].replace("_", " ").title(), "derived": item["value"] == "1", "manualOverride": bool(item.get("manualOverride")), "derivation": item["rule"]}
        for item in classification["flags"]
    ]
    rules = [rule for rule in context["rules"] if rule.get("tenantId") in {txn.get("tenantId"), "*", None}]
    validated = validate_transaction(canonical, rules, txn.get("documentType") or "Tax Invoice", context.get("codeLists") or {}, profile)
    txn["validationResults"] = validated["results"]
    txn["validationStages"] = validated["stages"]
    txn["complianceScore"] = validated["score"]
    txn["workbench"] = workbench(validated["results"], validated["score"])
    txn["ruleVersion"] = profile.get("version") if profile else txn.get("ruleVersion")
    errors = validated["score"]["errors"]
    if txn.get("lifecycleStatus") == "MAPPING_FAILED" and mode == "revalidate":
        txn["complianceStatus"] = "NON-COMPLIANT"
        txn["aspStatus"] = "NOT_SUBMITTED"
    elif errors:
        txn["lifecycleStatus"] = "COMPLIANCE_FAILED"
        txn["complianceStatus"] = "NON-COMPLIANT"
        txn["aspStatus"] = "NOT_SUBMITTED"
        txn["gateStage"] = "COMPLIANCE FAILED"
    elif validated["score"]["status"] == "WARNING":
        txn["lifecycleStatus"] = "READY_FOR_ASP"
        txn["complianceStatus"] = "WARNING"
        txn["aspStatus"] = txn.get("aspStatus") if txn.get("aspStatus") not in (None, "") else "NOT_SUBMITTED"
        txn["gateStage"] = "READY FOR ASP"
    else:
        advancing = {
            None, "RECEIVED", "NORMALIZED", "COMPLIANCE_FAILED", "READY_FOR_ASP", "MAPPING_FAILED",
            "SOURCE_RECEIVED", "ENTITY_RESOLVED", "DOCUMENT_CLASSIFIED", "MAPPED", "ENRICHED",
            "COMPLIANCE_VALIDATING", "COMPLIANCE_PASSED",
        }
        if txn.get("lifecycleStatus") in advancing:
            txn["lifecycleStatus"] = "READY_FOR_ASP"
        txn["complianceStatus"] = "COMPLIANT"
        txn["gateStage"] = "READY FOR ASP"
    if mode == "revalidate":
        txn.setdefault("timeline", []).append({"at": _now(), "label": "Revalidated"})
    return txn


def entity_readiness(entity: dict, profile: dict | None) -> dict:
    checks = [
        ("Legal name", bool(entity.get("legalName")), "ERROR"),
        ("TRN", bool(re.fullmatch(r"\d{15}", str(entity.get("trn") or ""))), "ERROR"),
        ("TIN", bool(entity.get("tin")), "ERROR"),
        ("Electronic address", bool(entity.get("electronicAddress")), "ERROR"),
        ("Scheme", bool(entity.get("electronicAddressScheme")), "ERROR"),
        ("Legal registration", bool(entity.get("tradeLicenceNumber")), "WARNING"),
        ("Trade licence authority", bool(entity.get("tradeLicenceAuthority")), "ERROR"),
        ("VAT status", bool(entity.get("vatRegistrationStatus")), "ERROR"),
        ("Address", bool(entity.get("address") and entity.get("city") and entity.get("country")), "ERROR"),
        ("ASP assignment", bool(entity.get("aspId")), "ERROR"),
        ("Compliance profile", bool(profile), "ERROR"),
    ]
    missing = [label for label, ok, _severity in checks if not ok]
    errors = [label for label, ok, severity in checks if not ok and severity == "ERROR"]
    passed = len([item for item in checks if item[1]])
    percent = round(100 * passed / len(checks))
    ready = not errors
    return {
        "score": percent,
        "label": f"{percent}% — {'READY' if ready else 'NOT READY'}",
        "status": "READY" if ready else "NOT READY",
        "missing": missing,
        "productionAllowed": ready,
    }


def default_code_lists() -> dict[str, list[dict]]:
    def rows(pairs: list[tuple[str, str]]) -> list[dict]:
        return [{"code": code, "description": description, "source": "UAE PINT-AE profile", "version": "2026.1", "effectiveDate": "2026-01-01", "active": True} for code, description in pairs]
    return {
        "invoiceType": rows([("380", "Commercial invoice"), ("381", "Credit note"), ("389", "Self-billed invoice"), ("261", "Self-billed credit note")]),
        "currency": rows([("AED", "UAE Dirham"), ("USD", "US Dollar"), ("EUR", "Euro")]),
        "country": rows([("AE", "United Arab Emirates"), ("SA", "Saudi Arabia"), ("SE", "Sweden")]),
        "vatCategory": rows([("S", "Standard rated"), ("Z", "Zero rated"), ("E", "Exempt"), ("O", "Not subject to VAT"), ("AE", "Reverse charge")]),
        "paymentMeans": rows([("30", "Credit transfer"), ("42", "Payment to bank account"), ("48", "Bank card")]),
        "uom": rows([("EA", "Each"), ("HUR", "Hour"), ("DAY", "Day"), ("KGM", "Kilogram")]),
        "electronicAddressScheme": rows([("0235", "UAE TIN scheme")]),
        "identifierScheme": rows([("0235", "UAE TIN"), ("0088", "GLN")]),
        "legalRegistrationType": rows([("TL", "Trade licence"), ("CR", "Commercial registration")]),
        "exemptionReason": rows([("VATEX-AE-EXPORT", "Export of goods or services"), ("VATEX-AE-DZ", "Designated zone supply")]),
        "emirate": rows([("AUH", "Abu Dhabi"), ("DXB", "Dubai"), ("SHJ", "Sharjah")]),
    }


def default_profile() -> dict:
    return {
        "id": "prof_uae_2026_1",
        "tenantId": "global",
        "country": "UAE",
        "profile": "UAE E-INVOICING",
        "standard": "PINT-AE",
        "version": "2026.1",
        "status": "ACTIVE",
        "effectiveFrom": "2026-01-01",
        "effectiveTo": None,
        "regulatorySource": "UAE Ministry of Finance eInvoicing programme",
        "sourceDocument": "PINT-AE billing specification",
        "publicationDate": "2026-01-01",
        "sectionReference": "Profile and transaction type",
        "notes": "Active foundation profile. Draft versions are not selected for new validations until activated.",
        "businessProcessId": "urn:peppol:bis:billing",
        "specificationId": "urn:peppol:pint:ae:1.0",
        "commercialProfile": "Commercial Invoice",
        "tinDerivation": "Copy a valid 15-digit TRN into TIN",
        "transactionTypeFlags": FLAG_ORDER,
        "scenarioRules": [
            {"code": "STANDARD_TAX_INVOICE", "label": "Standard Tax Invoice", "documentType": "Tax Invoice"},
            {"code": "REVERSE_CHARGE", "label": "Reverse Charge", "documentType": "Tax Invoice", "taxCategory": "AE"},
            {"code": "ZERO_RATED", "label": "Zero Rated Supply", "documentType": "Tax Invoice", "taxCategory": "Z"},
            {"code": "EXPORT", "label": "Export", "documentType": "All", "when": {"path": "transactionClassification.export", "equals": True}},
            {"code": "FREE_TRADE_ZONE", "label": "Free Trade Zone Supply", "documentType": "All", "when": {"path": "transactionClassification.designatedZone", "equals": True}},
            {"code": "DEEMED_SUPPLY", "label": "Deemed Supply", "documentType": "All", "when": {"path": "transactionClassification.deemedSupply", "equals": True}},
            {"code": "MARGIN_SCHEME", "label": "Margin Scheme", "documentType": "All", "when": {"path": "transactionClassification.marginScheme", "equals": True}},
            {"code": "SUMMARY_INVOICE", "label": "Summary Invoice", "documentType": "All", "when": {"path": "transactionClassification.summaryInvoice", "equals": True}},
            {"code": "CONTINUOUS_SUPPLY", "label": "Continuous Supply", "documentType": "All", "when": {"path": "transactionClassification.continuousSupply", "equals": True}},
            {"code": "DISCLOSED_AGENT", "label": "Disclosed Agent Billing", "documentType": "All", "when": {"path": "transactionClassification.disclosedAgent", "equals": True}},
            {"code": "ECOMMERCE", "label": "Supply Through E-Commerce", "documentType": "All", "when": {"path": "transactionClassification.ecommerce", "equals": True}},
            {"code": "CREDIT_NOTE", "label": "Standard Tax Credit Note", "documentType": "Credit Note"},
            {"code": "CREDIT_NOTE", "label": "Standard Tax Credit Note", "documentType": "Tax Credit Note"},
            {"code": "COMMERCIAL_INVOICE", "label": "Commercial Invoice", "documentType": "Commercial Invoice"},
            {"code": "SELF_BILLING", "label": "Self-Billing", "documentType": "Self-Billed Invoice"},
            {"code": "SELF_BILLING_CREDIT", "label": "Self-Billing Credit Note", "documentType": "Self-Billed Credit Note"},
        ],
    }


def draft_profile() -> dict:
    profile = deepcopy(default_profile())
    profile.update({
        "id": "prof_uae_2026_2",
        "version": "2026.2",
        "status": "DRAFT",
        "effectiveFrom": "2026-12-01",
        "notes": "Prepared regulatory update. It is not used until an administrator activates it.",
        "specificationId": "urn:peppol:pint:ae:1.1",
    })
    return profile
