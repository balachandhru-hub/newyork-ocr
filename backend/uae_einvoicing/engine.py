"""Configurable UAE compliance gate.

Rules and transaction-type flags are data. Nothing in this module branches
on a customer name, a source system, or an ASP provider.
"""

from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


CANONICAL_SECTIONS = [
    "invoiceDetails",
    "seller",
    "buyer",
    "payee",
    "delivery",
    "payment",
    "references",
    "contract",
    "purchaseOrder",
    "tax",
    "allowances",
    "charges",
    "totals",
    "invoiceLines",
    "creditNoteReferences",
    "transactionClassification",
    "uaeExtensions",
    "aspMetadata",
    "sourceMetadata",
]

LIFECYCLE = [
    "RECEIVED",
    "NORMALIZED",
    "MAPPING_FAILED",
    "COMPLIANCE_FAILED",
    "READY_FOR_ASP",
    "SUBMITTED",
    "ASP_ACCEPTED",
    "ASP_REJECTED",
    "NETWORK_ACCEPTED",
    "NETWORK_REJECTED",
    "DELIVERED",
    "ERP_POSTED",
    "RECONCILED",
    "CLOSED",
]

GATE = [
    "SOURCE RECEIVED",
    "NORMALIZED",
    "MAPPED",
    "ENRICHED",
    "VALIDATED",
    "COMPLIANCE PASSED",
    "READY FOR ASP",
]

SUCCESS_STATES = {
    "ASP_ACCEPTED",
    "NETWORK_ACCEPTED",
    "DELIVERED",
    "ERP_POSTED",
    "RECONCILED",
    "CLOSED",
}
PENDING_STATES = {"RECEIVED", "NORMALIZED", "READY_FOR_ASP"}
REJECTED_STATES = {"ASP_REJECTED", "NETWORK_REJECTED"}

UAE_ROLES = [
    "UAE eInvoice Administrator",
    "Compliance Administrator",
    "Compliance Manager",
    "Integration Administrator",
    "Finance User",
    "Exception Processor",
    "Auditor",
    "Read Only",
]

# Existing SILA Fatoora roles stay the login roles. These bindings only
# describe UAE module permissions for those people.
SILA_ROLE_DEFAULTS = {
    "Lead": "UAE eInvoice Administrator",
    "Consultant": "Integration Administrator",
    "User": "Finance User",
}

ROLE_PERMISSIONS = {
    "UAE eInvoice Administrator": {"*"},
    "Compliance Administrator": {
        "compliance.read",
        "rules.read",
        "rules.write",
        "profiles.read",
        "profiles.write",
        "code_lists.read",
        "code_lists.write",
        "transactions.read",
        "audit.read",
        "reports.read",
        "simulator.run",
    },
    "Compliance Manager": {
        "compliance.read",
        "rules.read",
        "rules.write",
        "transactions.read",
        "transactions.correct",
        "transactions.reprocess",
        "transactions.revalidate",
        "transactions.remap",
        "transactions.reenrich",
        "transactions.override",
        "simulator.run",
        "ingest.run",
        "exceptions.read",
        "exceptions.manage",
        "audit.read",
        "reports.read",
    },
    "Integration Administrator": {
        "integrations.read",
        "connectors.write",
        "asp.write",
        "mappings.read",
        "mappings.write",
        "simulator.run",
        "ingest.run",
        "legal_entities.read",
        "legal_entities.write",
        "master_data.write",
        "transactions.read",
        "audit.read",
    },
    "Finance User": {
        "transactions.read",
        "reconciliation.read",
        "reports.read",
        "compliance.read",
        "archive.read",
    },
    "Exception Processor": {
        "exceptions.read",
        "exceptions.manage",
        "transactions.read",
        "transactions.correct",
        "transactions.reprocess",
        "transactions.retry",
    },
    "Auditor": {
        "audit.read",
        "archive.read",
        "compliance.read",
        "transactions.read",
        "reports.read",
        "mappings.read",
        "rules.read",
    },
    "Read Only": {
        "transactions.read",
        "compliance.read",
        "reports.read",
        "archive.read",
        "audit.read",
        "mappings.read",
        "rules.read",
        "integrations.read",
        "exceptions.read",
        "reconciliation.read",
    },
}


def allows(role: str, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["Read Only"])
    if "*" in granted:
        return True
    if permission in granted:
        return True
    if permission.endswith(".read") and "*.read" in granted:
        return True
    return False


def field(source: Any, mapped: Any = None, final: Any = None, rule: str = "Direct", derivation: str = "", status: str = "COMPLIANT") -> dict:
    if mapped is None:
        mapped = source
    if final is None:
        final = mapped
    return {
        "sourceValue": source,
        "mappedValue": mapped,
        "enrichedValue": final,
        "finalValue": final,
        "mappingRule": rule,
        "derivationRule": derivation,
        "enrichmentSource": "",
        "validationStatus": status,
    }


def get_field(canonical: dict, path: str) -> dict | None:
    if not path or "." not in path:
        return None
    section, name = path.split(".", 1)
    section_data = canonical.get(section) or {}
    value = section_data.get(name)
    if isinstance(value, dict) and "finalValue" in value:
        return value
    return None


def set_final(canonical: dict, path: str, value: Any) -> None:
    section, name = path.split(".", 1)
    canonical.setdefault(section, {})
    current = canonical[section].get(name)
    if not isinstance(current, dict) or "finalValue" not in current:
        current = field(None, None, value, rule="Corrected", status="WARNING")
        canonical[section][name] = current
    current["finalValue"] = value
    current["enrichedValue"] = value
    current["validationStatus"] = "WARNING"


def final_of(canonical: dict, path: str) -> Any:
    found = get_field(canonical, path)
    if not found:
        return None
    return found.get("finalValue")


def _matches_when(canonical: dict, when: dict | None) -> bool:
    if not when:
        return True
    if "all" in when:
        return all(_matches_when(canonical, item) for item in when["all"])
    if "any" in when:
        return any(_matches_when(canonical, item) for item in when["any"])
    actual = final_of(canonical, when.get("path", ""))
    if "equals" in when:
        return actual == when["equals"]
    if "in" in when:
        return actual in when["in"]
    if when.get("present"):
        return actual not in (None, "", [])
    return True


def money(value: Any) -> Decimal:
    """Decimal-safe money. Financial checks never use binary floats."""
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def evaluate_logic(canonical: dict, logic: dict) -> tuple[bool, str]:
    """Return (passed, expected requirement text)."""
    op = logic.get("op", "present")
    path = logic.get("path", "")
    if not _matches_when(canonical, logic.get("when")):
        return True, "Condition not applicable"
    actual = final_of(canonical, path) if path else None
    if op == "present":
        return (not _is_blank(actual), "Value required")
    if op == "matches":
        pattern = logic.get("pattern", "")
        text = "" if actual is None else str(actual)
        return (re.fullmatch(pattern, text) is not None, f"Must match {pattern}")
    if op == "one_of":
        return (actual in logic.get("values", []), f"One of {logic.get('values', [])}")
    if op == "numeric_sum":
        try:
            numbers = [money(final_of(canonical, item)) for item in logic.get("parts", [])]
            total = money(final_of(canonical, logic.get("total", "")))
        except Exception:
            return False, "Numeric total required"
        tolerance = Decimal(str(logic.get("tolerance", "0.01")))
        return (abs(sum(numbers, Decimal("0.00")) - total) <= tolerance, "Parts must equal total")
    if op == "line_count":
        lines = canonical.get("invoiceLines", {}).get("lines")
        if not isinstance(lines, list) or not lines:
            lines = final_of(canonical, "invoiceLines.items")
        count = len(lines) if isinstance(lines, list) else 0
        minimum = int(logic.get("min", 1))
        return (count >= minimum, f"At least {minimum} invoice line")
    return False, f"Unsupported rule operation {op}"


def rule_applies(rule: dict, document_type: str) -> bool:
    if not rule.get("active", True):
        return False
    target = rule.get("documentType") or "All"
    return target in ("All", document_type)


def evaluate_rules(canonical: dict, rules: list[dict], document_type: str) -> list[dict]:
    results = []
    for rule in rules:
        if not rule_applies(rule, document_type):
            continue
        passed, expected = evaluate_logic(canonical, rule.get("validationLogic") or {})
        path = (rule.get("validationLogic") or {}).get("path", rule.get("businessTerm"))
        source = get_field(canonical, path) if isinstance(path, str) and "." in path else None
        results.append({
            "ruleId": rule.get("ruleId"),
            "ruleName": rule.get("ruleName"),
            "category": rule.get("category"),
            "severity": rule.get("severity"),
            "field": path,
            "sourceValue": None if not source else source.get("sourceValue"),
            "mappedValue": None if not source else source.get("mappedValue"),
            "expected": expected,
            "message": None if passed else rule.get("errorMessage"),
            "resolution": None if passed else rule.get("suggestedResolution"),
            "status": "PASSED" if passed else "FAILED",
            "version": rule.get("version"),
        })
        if source and not passed and rule.get("severity") == "ERROR":
            source["validationStatus"] = "NON-COMPLIANT"
        elif source and not passed:
            source["validationStatus"] = "WARNING"
    return results


def derive_flags(canonical: dict, flag_rules: list[dict]) -> list[dict]:
    derived = []
    for flag in flag_rules:
        matched = _matches_when(canonical, flag.get("when"))
        derived.append({
            "code": flag.get("code"),
            "label": flag.get("label"),
            "derived": bool(matched),
            "manualOverride": False,
            "derivation": flag.get("derivation"),
        })
    return derived


def apply_gate(transaction: dict, rules: list[dict]) -> dict:
    """Run the pre-submission gate. ERROR failures never become READY_FOR_ASP."""
    txn = deepcopy(transaction)
    canonical = txn.get("canonical") or {}
    results = evaluate_rules(canonical, rules, txn.get("documentType", "All"))
    txn["validationResults"] = results
    errors = [item for item in results if item["status"] == "FAILED" and item["severity"] == "ERROR"]
    warnings = [item for item in results if item["status"] == "FAILED" and item["severity"] == "WARNING"]
    if errors:
        txn["lifecycleStatus"] = "COMPLIANCE_FAILED"
        txn["complianceStatus"] = "NON-COMPLIANT"
        txn["aspStatus"] = "NOT_SUBMITTED"
        txn["gateStage"] = "VALIDATED"
    elif warnings:
        txn["lifecycleStatus"] = "READY_FOR_ASP"
        txn["complianceStatus"] = "WARNING"
        txn["aspStatus"] = txn.get("aspStatus") if txn.get("aspStatus") not in (None, "NOT_SUBMITTED") else "NOT_SUBMITTED"
        txn["gateStage"] = "READY FOR ASP"
    else:
        if txn.get("lifecycleStatus") in (None, "RECEIVED", "NORMALIZED", "COMPLIANCE_FAILED", "READY_FOR_ASP"):
            txn["lifecycleStatus"] = "READY_FOR_ASP"
        txn["complianceStatus"] = "COMPLIANT"
        txn["gateStage"] = "READY FOR ASP"
    return txn


def compliance_health(transactions: list[dict]) -> list[dict]:
    categories = {
        "Mandatory Field Compliance": "Invoice Details",
        "Conditional Field Compliance": "Buyer",
        "Tax Compliance": "Tax",
        "Buyer/Seller Identity Compliance": "Seller",
        "Transaction Type Compliance": "Transaction Type",
        "Line-Level Compliance": "Invoice Lines",
        "Amount Reconciliation Compliance": "Totals",
        "Endpoint/Peppol Compliance": "Payment",
    }
    panels = []
    all_results = []
    for txn in transactions:
        all_results.extend(txn.get("validationResults") or [])

    def status_for(results: list[dict]) -> tuple[str, int]:
        if not results:
            return "NOT APPLICABLE", 0
        if any(item["status"] == "FAILED" and item["severity"] == "ERROR" for item in results):
            failed = len([item for item in results if item["status"] == "FAILED"])
            score = max(0, round(100 * (1 - failed / len(results))))
            return "NON-COMPLIANT", score
        if any(item["status"] == "FAILED" for item in results):
            failed = len([item for item in results if item["status"] == "FAILED"])
            score = max(0, round(100 * (1 - failed / len(results))))
            return "WARNING", score
        return "COMPLIANT", 100

    overall, overall_score = status_for(all_results)
    panels.append({"label": "Overall Compliance Score", "status": overall, "score": overall_score})
    for label, category in categories.items():
        subset = [item for item in all_results if item.get("category") == category]
        status, score = status_for(subset)
        panels.append({"label": label, "status": status, "score": score})
    return panels
