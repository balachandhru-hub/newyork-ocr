"""Phase 3 source onboarding.

Customer, property and source behaviour is configuration. This module does
not branch on a customer name and does not call an ASP or a live OPERA API.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from uae_einvoicing.engine import field, final_of, money
from uae_einvoicing.pipeline import (
    FIELD_REGISTRY,
    apply_mapping_outputs,
    execute_mapping,
    run_pipeline,
    context_for,
)


SOURCE_CODES = {
    "SAP S/4HANA": "SAP_S4",
    "SAP_S4": "SAP_S4",
    "SAP Ariba": "SAP_ARIBA",
    "SAP_ARIBA": "SAP_ARIBA",
    "OPERA": "OPERA",
    "OPERA_KIT": "OPERA_KIT",
    "KIT": "OPERA_KIT",
    "OTHER": "OTHER",
}

# Header and item columns already returned by the existing S/4 invoice mapper.
# Used only to decide whether a regulatory value needs a new SAP field.
SAP_INVOICE_FIELDS = {
    "SupplierInvoice", "FiscalYear", "CompanyCode", "DocumentDate", "PostingDate",
    "DocumentCurrency", "InvoiceGrossAmount", "PaymentTerms", "InvoiceReference",
    "SupplierInvoiceIsCreditMemo", "InvoicingParty", "DocumentHeaderText",
    "SupplierInvoiceItem", "PurchaseOrder", "TaxCode", "SupplierInvoiceItemAmount",
    "PurchaseOrderQuantityUnit", "QuantityInPurchaseOrderUnit",
}

KEY_FIELDS = {
    "SAP_COMPANY_CODE": ("CompanyCode", "company_code"),
    "SAP_SALES_ORGANIZATION": ("SalesOrganization",),
    "SAP_BUSINESS_PLACE": ("BusinessPlace",),
    "OPERA_PROPERTY_CODE": ("PROPERTY_CODE", "propertyCode"),
    "OPERA_HOTEL_CODE": ("HOTEL_CODE", "hotelCode"),
    "OPERA_BUSINESS_UNIT": ("BUSINESS_UNIT",),
    "ARIBA_ENTITY": ("AribaEntity",),
    "ARIBA_COMPANY_CODE": ("AribaCompanyCode",),
    "TRN": ("TRN", "HOTEL_TRN"),
}

SECRET_KEYS = {"password", "token", "secret", "authorization", "credential", "api_key", "apikey"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def source_code(value: str | None) -> str:
    return SOURCE_CODES.get(value or "", "OTHER")


def payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            masked[key] = "***" if key.lower() in SECRET_KEYS else mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def retry_allowed(category: str) -> bool:
    return category == "SOURCE_CONNECTION_ERROR"


def _keys_from_payload(payload: dict, envelope: dict) -> dict[str, str]:
    found = {}
    for key_type, names in KEY_FIELDS.items():
        for name in names:
            value = payload.get(name)
            if value not in (None, ""):
                found[key_type] = str(value)
                break
    if envelope.get("legalEntityKey") and envelope.get("legalEntityKeyType"):
        found[envelope["legalEntityKeyType"]] = str(envelope["legalEntityKey"])
    return found


def resolve_legal_entity(mappings: list[dict], source: str, keys: dict[str, str]) -> dict:
    matches = []
    for item in mappings:
        if not item.get("active", True):
            continue
        if item.get("sourceSystem") not in {source, "ALL", "*"}:
            continue
        if keys.get(item.get("keyType")) == item.get("keyValue"):
            matches.append(item)
    entities = []
    for item in matches:
        if item["legalEntityId"] not in entities:
            entities.append(item["legalEntityId"])
    if len(entities) == 0:
        return {"status": "ENTITY_RESOLUTION_FAILED", "legalEntityId": None, "matches": []}
    if len(entities) > 1:
        return {"status": "ENTITY_RESOLUTION_AMBIGUOUS", "legalEntityId": None, "matches": matches}
    return {"status": "RESOLVED", "legalEntityId": entities[0], "matches": matches, "keyType": matches[0]["keyType"], "keyValue": matches[0]["keyValue"]}


def classify_document(rules: list[dict], source: str, payload: dict, envelope: dict) -> dict:
    for rule in rules:
        if rule.get("sourceSystem") not in {source, "ALL", "*"}:
            continue
        field_name = rule.get("field")
        expected = rule.get("equals")
        actual = envelope.get(field_name) if field_name in envelope else payload.get(field_name)
        if field_name and str(actual) != str(expected):
            continue
        return {
            "documentClass": rule.get("documentClass") or "Unclassified",
            "canonicalDocumentType": rule.get("canonicalDocumentType") or "Tax Invoice",
            "inScope": bool(rule.get("inScope")),
            "route": rule.get("route") or "OUTBOUND_UAE_EINVOICE",
            "ruleId": rule.get("id"),
        }
    return {"documentClass": "Unclassified", "canonicalDocumentType": "Tax Invoice", "inScope": False, "route": "OUT_OF_SCOPE", "ruleId": None}


def select_mapping_lines(mappings: list[dict], *, tenant_id: str, source_name: str, document_type: str, legal_entity_id: str | None, property_code: str | None) -> tuple[dict | None, list[dict]]:
    """Merge an inherited mapping. A child line replaces the same target field."""
    chosen = None
    for item in mappings:
        if item.get("tenantId") not in {tenant_id, "global"} or item.get("status") != "Active":
            continue
        if item.get("sourceSystem") not in {source_name, source_code(source_name)}:
            continue
        if item.get("documentType") not in {document_type, "All", None, ""}:
            continue
        if item.get("legalEntityId") not in {None, "", legal_entity_id}:
            continue
        if item.get("propertyCode") not in {None, "", property_code}:
            continue
        specificity = int(bool(item.get("propertyCode"))) + int(bool(item.get("legalEntityId")))
        if chosen is None or specificity >= chosen[0]:
            chosen = (specificity, item)
    if chosen is None:
        return None, []
    lines: dict[str, dict] = {}
    parent_id = chosen[1].get("parentId")
    if parent_id:
        parent = next((item for item in mappings if item["id"] == parent_id), None)
        for line in [] if parent is None else parent.get("lines") or []:
            lines[line.get("targetField")] = line
    for line in chosen[1].get("lines") or []:
        lines[line.get("targetField")] = line
    return chosen[1], list(lines.values())


def _set_enriched(canonical: dict, path: str, value: Any, source: str, rule: str, original: Any, source_system: str) -> None:
    record = field(original, None, value, rule="Enrichment", derivation=rule)
    record["enrichedValue"] = value
    record["enrichmentSource"] = source
    record["lineage"] = {
        "final": value,
        "source": source,
        "sourceSystem": source_system,
        "original": original,
        "enrichment": source,
        "rule": rule,
    }
    section, name = path.split(".", 1)
    canonical.setdefault(section, {})[name] = record


def enrich_masters(canonical: dict, payload: dict, entity: dict | None, buyers: list[dict], code_lists: dict, source_name: str) -> None:
    entity = entity or {}
    if final_of(canonical, "seller.name") in (None, "") and entity.get("legalName"):
        _set_enriched(canonical, "seller.name", entity["legalName"], "Legal Entity Master", "SELLER_NAME_LOOKUP", None, source_name)
    if final_of(canonical, "seller.trn") in (None, "") and re.fullmatch(r"\d{15}", str(entity.get("trn") or "")):
        _set_enriched(canonical, "seller.trn", entity["trn"], "Legal Entity Master", "SELLER_TRN_LOOKUP", payload.get("HOTEL_TRN"), source_name)
    code = payload.get("CUSTOMER_CODE") or payload.get("InvoicingParty") or payload.get("Customer")
    buyer = next((item for item in buyers if code and code in {
        item.get("operaCustomerCode"), item.get("sapCustomerNumber"), item.get("sourceCustomerId"), item.get("aribaVendor"),
    }), None)
    if buyer:
        if final_of(canonical, "buyer.name") in (None, "") and buyer.get("legalName"):
            _set_enriched(canonical, "buyer.name", buyer["legalName"], "Buyer Master", "BUYER_NAME_LOOKUP", payload.get("CUSTOMER_NAME"), source_name)
        if final_of(canonical, "buyer.trn") in (None, "") and buyer.get("trn"):
            _set_enriched(canonical, "buyer.trn", buyer["trn"], "Buyer Master", "BUYER_TRN_LOOKUP", payload.get("CUSTOMER_TRN"), source_name)
    if final_of(canonical, "totals.netAmount") in (None, "") and payload.get("SupplierInvoiceItemAmount"):
        net = money(payload.get("SupplierInvoiceItemAmount"))
        gross = money(payload.get("InvoiceGrossAmount") or net)
        _set_enriched(canonical, "totals.netAmount", str(net), "SAP invoice item amount", "SAP_ITEM_NET", payload.get("SupplierInvoiceItemAmount"), source_name)
        _set_enriched(canonical, "totals.taxInclusiveAmount", str(gross), "SAP invoice gross amount", "SAP_GROSS", payload.get("InvoiceGrossAmount"), source_name)
    net_value = final_of(canonical, "totals.netAmount")
    gross_value = final_of(canonical, "totals.taxInclusiveAmount") or payload.get("InvoiceGrossAmount")
    if final_of(canonical, "totals.taxAmount") in (None, "") and net_value not in (None, "") and gross_value not in (None, ""):
        _set_enriched(canonical, "totals.taxAmount", str(money(money(gross_value) - money(net_value))), "Derived from gross and net", "SAP_TAX_SPLIT", None, source_name)
    tax_code = payload.get("TaxCode")
    if tax_code:
        row = next((item for item in code_lists.get("taxCode", []) if str(item.get("code")) == str(tax_code) or str(item.get("sourceValue")) == str(tax_code)), None)
        if row and final_of(canonical, "tax.category") in (None, ""):
            _set_enriched(canonical, "tax.category", row.get("canonicalValue") or row.get("code"), "Tax mapping", "TAX_CODE_LOOKUP", tax_code, source_name)
        if row and row.get("rate") and final_of(canonical, "tax.rate") in (None, ""):
            _set_enriched(canonical, "tax.rate", row["rate"], "Tax mapping", "TAX_RATE_LOOKUP", tax_code, source_name)


def _regulatory_match(txn: dict, reference: str | None) -> bool:
    if not reference or not txn.get("regulatory", True):
        return False
    return reference in {txn.get("invoiceNumber"), txn.get("sourceDocumentId")}


def find_regulatory_original(transactions: list[dict], tenant_id: str, reference: str | None) -> dict | None:
    for txn in transactions:
        if txn.get("tenantId") == tenant_id and _regulatory_match(txn, reference):
            return txn
    return None


def _next_id(data: dict) -> str:
    data["nextTransaction"] = int(data.get("nextTransaction") or 1000) + 1
    return f"UAE-2026-{data['nextTransaction']:05d}"


def _exception(data: dict, txn: dict, category: str, code: str, message: str, detail: str, resolution: str) -> dict:
    row = {
        "id": _nid("EX"),
        "tenantId": txn["tenantId"],
        "legalEntityId": txn.get("legalEntityId"),
        "transactionId": txn["id"],
        "sourceSystem": txn.get("sourceSystem"),
        "severity": "ERROR",
        "category": category,
        "errorCode": code,
        "message": message,
        "technicalDetail": detail,
        "businessResolution": resolution,
        "owner": "Unassigned",
        "status": "Open",
        "resolution": "",
        "createdAt": _now(),
        "ageHours": 0,
        "retryable": retry_allowed(category),
    }
    data.setdefault("exceptions", []).insert(0, row)
    return row


def _archive(data: dict, txn: dict, kind: str, payload: Any) -> None:
    data.setdefault("archive", []).append({
        "id": _nid("AR"),
        "tenantId": txn["tenantId"],
        "legalEntityId": txn.get("legalEntityId"),
        "transactionId": txn["id"],
        "kind": kind,
        "mappingVersionId": txn.get("mappingVersionId"),
        "ruleVersion": txn.get("ruleVersion"),
        "payloadHash": txn.get("payloadHash"),
        "createdAt": _now(),
        "body": payload if kind == "Source payload" else None,
    })


def _remember_payload(data: dict, txn: dict, payload: dict) -> dict:
    row = {
        "id": _nid("pay"),
        "tenantId": txn["tenantId"],
        "sourceSystem": txn.get("sourceSystem"),
        "propertyCode": txn.get("propertyCode"),
        "legalEntityKey": txn.get("resolutionKey"),
        "invoiceId": txn.get("sourceDocumentId"),
        "receivedAt": _now(),
        "payloadHash": txn.get("payloadHash"),
        "processingStatus": txn.get("lifecycleStatus"),
        "payload": deepcopy(payload),
    }
    data.setdefault("sourcePayloads", []).append(row)
    return row


def ingest(data: dict, tenant_id: str, envelope: dict) -> dict:
    """Create or version one transaction. A repeated source document is not duplicated."""
    source_name = envelope.get("sourceSystem") or "OTHER"
    code = source_code(source_name)
    payload = deepcopy(envelope.get("payload") or {})
    digest = payload_hash(payload)
    source_document_id = str(envelope.get("sourceDocumentId") or payload.get("FOLIO_NO") or payload.get("SupplierInvoice") or "")
    existing = next((item for item in data.get("transactions", []) if item.get("tenantId") == tenant_id and item.get("sourceCode") == code and item.get("sourceDocumentId") == source_document_id and source_document_id), None)
    if existing and existing.get("payloadHash") == digest:
        return {"idempotent": True, "created": False, "transaction": existing, "lifecycleStatus": existing.get("lifecycleStatus")}
    reference = envelope.get("regulatoryReference") or payload.get("InvoiceReference") or payload.get("OPERA_REFERENCE")
    classification = classify_document(
        [item for item in data.get("documentRules") or [] if item.get("tenantId") in {tenant_id, "global", None}],
        code, payload, envelope,
    )
    if classification["route"] == "ACCOUNTING_REFERENCE_ONLY" or envelope.get("documentClass") == "Accounting posting":
        original = find_regulatory_original(data.get("transactions", []), tenant_id, reference or payload.get("DocumentHeaderText"))
        if original:
            original.setdefault("accountingReferences", []).append({
                "sourceSystem": source_name,
                "sourceDocumentId": source_document_id,
                "payloadHash": digest,
                "at": _now(),
                "relationship": "ACCOUNTING_REFERENCE_ONLY",
            })
            return {
                "idempotent": False,
                "created": False,
                "duplicate": "CROSS_SYSTEM_DUPLICATE",
                "relationship": "ACCOUNTING_REFERENCE_ONLY",
                "transactionId": original["id"],
                "regulatoryInvoiceCreated": False,
                "lifecycleStatus": original.get("lifecycleStatus"),
            }
    if existing:
        txn = existing
        txn.setdefault("versions", []).append({
            "version": txn.get("version") or 1,
            "payloadHash": txn.get("payloadHash"),
            "sourcePayload": deepcopy(txn.get("sourcePayload")),
            "canonical": deepcopy(txn.get("canonical")),
            "validationResults": deepcopy(txn.get("validationResults")),
            "at": txn.get("updatedAt"),
        })
        txn["version"] = int(txn.get("version") or 1) + 1
        created = False
    else:
        txn = {
            "id": _next_id(data),
            "tenantId": tenant_id,
            "version": 1,
            "versions": [],
            "regulatory": classification["route"] == "OUTBOUND_UAE_EINVOICE",
            "timeline": [],
            "corrections": [],
            "createdAt": _now(),
        }
        data.setdefault("transactions", []).append(txn)
        created = True
    txn.update({
        "sourceSystem": source_name,
        "sourceCode": code,
        "sourceInstance": envelope.get("sourceInstance") or source_name,
        "sourceDocumentId": source_document_id,
        "sourceDocumentType": classification["documentClass"],
        "sourceTimestamp": envelope.get("sourceTimestamp") or _now(),
        "correlationId": envelope.get("correlationId") or _nid("cor"),
        "payloadHash": digest,
        "sourcePayload": payload,
        "propertyCode": payload.get("PROPERTY_CODE") or envelope.get("propertyCode"),
        "documentType": classification["canonicalDocumentType"],
        "documentClass": classification["documentClass"],
        "route": classification["route"],
        "classificationRule": classification["ruleId"],
        "invoiceNumber": str(payload.get("FOLIO_NO") or payload.get("SupplierInvoice") or source_document_id or txn["id"]),
        "invoiceDate": str(payload.get("DocumentDate") or payload.get("BILLINGDATE") or "2026-10-14")[:10],
        "currency": payload.get("CURRENCY") or payload.get("DocumentCurrency") or "AED",
        "direction": "Inbound AP" if classification["route"] == "AP_PROCESS" else "Outbound AR",
        "supplier": "",
        "buyer": payload.get("CUSTOMER_NAME") or "",
        "amount": float(money(payload.get("GROSSAMOUNT") or payload.get("InvoiceGrossAmount") or payload.get("NETAMOUNT") or 0)),
        "aspStatus": "NOT_SUBMITTED",
        "networkStatus": "NOT_SENT",
        "erpStatus": "NOT_POSTED",
        "sourceStatus": "RECEIVED",
        "updatedAt": _now(),
        "canonical": {},
    })
    if len(txn["invoiceDate"]) == 8 and txn["invoiceDate"].isdigit():
        txn["invoiceDate"] = f"{txn['invoiceDate'][0:4]}-{txn['invoiceDate'][4:6]}-{txn['invoiceDate'][6:8]}"
    txn["timeline"] = list(txn.get("timeline") or [])
    txn["timeline"].append({"at": _now(), "label": f"Received from {source_name}"})
    txn["lifecycleStatus"] = "SOURCE_RECEIVED"
    stored = _remember_payload(data, txn, payload)
    txn["sourcePayloadId"] = stored["id"]
    keys = _keys_from_payload(payload, envelope)
    resolution = resolve_legal_entity(
        [item for item in data.get("entityResolution") or [] if item.get("tenantId") in {tenant_id, "global", None}],
        code, keys,
    )
    txn["entityResolution"] = resolution
    txn["resolutionKey"] = None if not resolution.get("keyType") else f"{resolution['keyType']}={resolution['keyValue']}"
    if resolution["status"] != "RESOLVED":
        txn["legalEntityId"] = None
        txn["lifecycleStatus"] = resolution["status"]
        txn["complianceStatus"] = "NON-COMPLIANT"
        txn["timeline"].append({"at": _now(), "label": resolution["status"].replace("_", " ").title()})
        _exception(data, txn, "ENTITY_RESOLUTION_ERROR", resolution["status"], "Legal entity was not resolved to one entity.", json.dumps(mask_secrets(keys)), "Add one legal-entity mapping for this source key.")
        _archive(data, txn, "Entity resolution", resolution)
        return {"idempotent": False, "created": created, "transaction": txn, "lifecycleStatus": txn["lifecycleStatus"]}
    txn["legalEntityId"] = resolution["legalEntityId"]
    txn["timeline"].append({"at": _now(), "label": "Property resolved" if txn.get("propertyCode") else "Source key resolved"})
    txn["timeline"].append({"at": _now(), "label": "Legal entity resolved"})
    txn["lifecycleStatus"] = "ENTITY_RESOLVED"
    txn["timeline"].append({"at": _now(), "label": f"{classification['documentClass']} classified"})
    txn["lifecycleStatus"] = "DOCUMENT_CLASSIFIED"
    if classification["route"] != "OUTBOUND_UAE_EINVOICE":
        txn["regulatory"] = False
        txn["lifecycleStatus"] = "OUT_OF_SCOPE" if classification["route"] == "OUT_OF_SCOPE" else classification["route"]
        txn["complianceStatus"] = "NOT APPLICABLE"
        txn["timeline"].append({"at": _now(), "label": txn["lifecycleStatus"].replace("_", " ").title()})
        _archive(data, txn, "Source payload", payload)
        return {"idempotent": False, "created": created, "transaction": txn, "lifecycleStatus": txn["lifecycleStatus"], "regulatoryInvoiceCreated": False}
    entity = next((item for item in data.get("legalEntities", []) if item["id"] == txn["legalEntityId"]), None)
    mapping, lines = select_mapping_lines(
        data.get("mappings") or [],
        tenant_id=tenant_id,
        source_name=source_name,
        document_type=txn["documentType"],
        legal_entity_id=txn["legalEntityId"],
        property_code=txn.get("propertyCode"),
    )
    if mapping is None:
        txn["lifecycleStatus"] = "MAPPING_FAILED"
        txn["complianceStatus"] = "NON-COMPLIANT"
        _exception(data, txn, "MAPPING_ERROR", "MAPPING_MISSING", "No active mapping profile matched this document.", source_name, "Activate a mapping for this source and document type.")
        return {"idempotent": False, "created": created, "transaction": txn, "lifecycleStatus": txn["lifecycleStatus"]}
    txn["mappingVersionId"] = mapping["id"]
    outputs = execute_mapping(payload, lines, data.get("codeLists") or {})
    apply_mapping_outputs(txn["canonical"], outputs)
    txn["timeline"].append({"at": _now(), "label": "Canonical mapping completed"})
    txn["lifecycleStatus"] = "MAPPED"
    buyers = [item for item in data.get("buyers", []) if item.get("tenantId") == tenant_id]
    enrich_masters(txn["canonical"], payload, entity, buyers, data.get("codeLists") or {}, source_name)
    if final_of(txn["canonical"], "buyer.trn") not in (None, ""):
        txn["timeline"].append({"at": _now(), "label": "Buyer TRN enriched"})
    txn["lifecycleStatus"] = "ENRICHED"
    txn["timeline"].append({"at": _now(), "label": "Transaction type derived"})
    txn["lifecycleStatus"] = "COMPLIANCE_VALIDATING"
    run_pipeline(txn, context_for(data, txn), mode="revalidate")
    txn["supplier"] = final_of(txn["canonical"], "seller.name") or (entity or {}).get("legalName") or ""
    txn["buyer"] = final_of(txn["canonical"], "buyer.name") or txn.get("buyer") or ""
    truth = next((item for item in data.get("sourceOfTruth") or [] if item.get("documentType") in {txn["documentClass"], txn["documentType"], "All"}), None)
    txn["sourceOfTruth"] = truth
    if txn.get("complianceStatus") == "NON-COMPLIANT" or txn.get("lifecycleStatus") == "COMPLIANCE_FAILED":
        txn["timeline"].append({"at": _now(), "label": "UAE compliance failed"})
        failed = next((item for item in txn.get("validationResults") or [] if item.get("status") == "FAILED" and item.get("severity") == "ERROR"), {})
        _exception(
            data, txn, "COMPLIANCE_ERROR", failed.get("ruleId") or "COMPLIANCE_FAILED",
            failed.get("message") or failed.get("ruleName") or "Compliance failed",
            failed.get("field") or "",
            failed.get("resolution") or "Correct the enriched value and revalidate.",
        )
    else:
        txn["lifecycleStatus"] = "COMPLIANCE_PASSED"
        txn["timeline"].append({"at": _now(), "label": "UAE compliance passed"})
        txn["lifecycleStatus"] = "READY_FOR_ASP"
        txn["gateStage"] = "READY FOR ASP"
        txn["timeline"].append({"at": _now(), "label": "READY FOR ASP"})
    _archive(data, txn, "Source payload", payload)
    _archive(data, txn, "Canonical payload", None)
    _archive(data, txn, "Compliance results", None)
    return {"idempotent": False, "created": created, "transaction": txn, "lifecycleStatus": txn.get("lifecycleStatus"), "regulatoryInvoiceCreated": True}


class OperaMockConnector:
    """Returns configured sample documents. It does not call OPERA or KIT."""

    name = "OperaMockConnector"

    def test_connection(self, connector: dict) -> dict:
        return {"ok": True, "httpStatus": None, "message": "Mock connector is configured. No live OPERA or KIT call was made.", "implementation": self.name}

    def fetch(self, connector: dict, data: dict) -> dict:
        cursor = connector.get("cursor")
        documents = []
        for item in data.get("fixtures") or []:
            if item.get("connectorId") != connector["id"] or not item.get("queue"):
                continue
            if cursor and item.get("sourceDocumentId") <= cursor:
                continue
            documents.append(item)
        new_cursor = cursor
        if documents:
            new_cursor = documents[-1]["sourceDocumentId"]
        return {"documents": documents, "cursor": new_cursor, "httpStatus": None, "endpoint": "mock://fixtures"}


class OperaRemoteConnector:
    """KIT, REST and file connectors stay inactive until a base URL is configured.

    This build does not open a network connection.
    """

    def __init__(self, name: str):
        self.name = name

    def test_connection(self, connector: dict) -> dict:
        if not connector.get("baseUrl"):
            return {"ok": False, "httpStatus": None, "message": "No endpoint is configured. No live call was made.", "implementation": self.name}
        return {"ok": False, "httpStatus": None, "message": "Live OPERA retrieval is not enabled. Use the mock connector for pipeline tests.", "implementation": self.name}

    def fetch(self, connector: dict, data: dict) -> dict:
        raise RuntimeError("SOURCE_CONNECTION_ERROR")


def connector_for(connector: dict):
    kind = (connector.get("connectionType") or connector.get("implementation") or "MOCK").upper()
    if kind in {"MOCK", "OPERAMOCKCONNECTOR"}:
        return OperaMockConnector()
    if kind in {"KIT", "OPERAKITCONNECTOR"}:
        return OperaRemoteConnector("OperaKitConnector")
    if kind in {"FILE", "OPERAFILECONNECTOR"}:
        return OperaRemoteConnector("OperaFileConnector")
    return OperaRemoteConnector("OperaRestConnector")


def run_source_connector(data: dict, connector: dict) -> dict:
    started = _now()
    implementation = connector_for(connector)
    request_id = _nid("req")
    try:
        fetched = implementation.fetch(connector, data)
        error = None
    except RuntimeError as exc:
        fetched = {"documents": [], "cursor": connector.get("cursor"), "httpStatus": None, "endpoint": connector.get("baseUrl") or ""}
        error = str(exc)
    results = []
    if not error:
        connector["cursor"] = fetched.get("cursor")
        connector["lastPoll"] = started
        connector["lastSuccess"] = started
        connector["lastFailedPoll"] = None
        for document in fetched["documents"]:
            document["queue"] = False
            results.append(ingest(data, connector["tenantId"], document))
        if results:
            connector["lastTransactionReceived"] = started
    else:
        connector["lastFailedPoll"] = started
        connector["lastPoll"] = started
    ended = _now()
    log = {
        "id": _nid("log"),
        "tenantId": connector["tenantId"],
        "connectorId": connector["id"],
        "at": ended,
        "level": "ERROR" if error else "INFO",
        "message": error or f"Retrieved {len(results)} document(s). No live OPERA call was made.",
        "requestId": request_id,
        "correlationId": request_id,
        "endpoint": fetched.get("endpoint"),
        "start": started,
        "end": ended,
        "durationMs": 0,
        "httpStatus": fetched.get("httpStatus"),
        "result": "FAILED" if error else "OK",
        "retry": retry_allowed("SOURCE_CONNECTION_ERROR") if error else False,
        "error": error,
    }
    data.setdefault("connectorLogs", []).insert(0, mask_secrets(log))
    return {"log": log, "results": results, "implementation": implementation.name}


def entity_onboarding_status(entity: dict, data: dict) -> dict:
    identity = bool(entity.get("legalName")) and bool(re.fullmatch(r"\d{15}", str(entity.get("trn") or "")))
    if entity.get("status") == "CONFIGURATION REQUIRED" or not identity:
        status = "CONFIGURATION REQUIRED"
    else:
        sources = [item.strip() for item in str(entity.get("sourceSystems") or "").split(",") if item.strip()]
        mappings = [item for item in data.get("mappings", []) if item.get("tenantId") == entity.get("tenantId") and item.get("status") == "Active" and item.get("legalEntityId") in {entity["id"], None, ""} and item.get("sourceSystem") in sources]
        covered = {item["sourceSystem"] for item in mappings}
        profile = any(item.get("status") == "ACTIVE" for item in data.get("profiles") or [])
        if sources and not set(sources).issubset(covered | {"SAP Ariba"}):
            status = "MAPPING INCOMPLETE" if "SAP S/4HANA" in sources or "OPERA" in sources else "INTEGRATION INCOMPLETE"
            if not covered:
                status = "MAPPING INCOMPLETE"
        elif not profile:
            status = "COMPLIANCE INCOMPLETE"
        elif entity.get("status") == "Production":
            status = "READY"
        else:
            status = "READY FOR TEST"
        if status == "READY FOR TEST":
            connectors = [item for item in data.get("connectors", []) if item.get("tenantId") == entity.get("tenantId") and item.get("legalEntityId") == entity["id"]]
            if sources and not connectors:
                status = "INTEGRATION INCOMPLETE"
    missing = []
    for label, present in (
        ("Legal name", bool(entity.get("legalName"))),
        ("TRN", bool(re.fullmatch(r"\d{15}", str(entity.get("trn") or "")))),
        ("TIN", bool(entity.get("tin"))),
        ("Electronic address", bool(entity.get("electronicAddress"))),
        ("SAP company code", bool(entity.get("sapCompanyCode"))),
        ("OPERA property", bool(entity.get("operaPropertyCode"))),
    ):
        if not present:
            missing.append(label)
    return {"status": status, "missing": missing}


def opera_gap(data: dict, tenant_id: str) -> list[dict]:
    mapping = next((item for item in data.get("mappings", []) if item.get("tenantId") == tenant_id and item.get("sourceSystem") == "OPERA" and item.get("status") == "Active"), None)
    lines = {} if mapping is None else {line.get("targetField"): line for line in mapping.get("lines") or []}
    rows = []
    for field_def in FIELD_REGISTRY:
        line = lines.get(field_def["id"])
        if line and line.get("mappingType") == "Constant":
            status = "CONSTANT"
        elif line and line.get("status") == "MAPPED":
            status = "DIRECT" if (line.get("mappingType") or "Direct") == "Direct" else "MAPPED"
        elif field_def["id"] in {"seller.trn", "seller.name"}:
            status = "ENRICHED"
        elif field_def["id"] in {"buyer.trn", "buyer.electronicAddress", "buyer.tin"}:
            status = "ENRICHED"
        elif field_def["id"] == "invoiceDetails.transactionTypeCode":
            status = "DERIVED"
        elif field_def["id"] in {"invoiceDetails.businessProcessType", "invoiceDetails.specificationIdentifier"}:
            status = "CONSTANT"
        elif field_def.get("requirement") == "OPTIONAL":
            status = "NOT APPLICABLE"
        else:
            status = "MISSING"
        rows.append({
            "businessTerm": field_def.get("businessTermId") or "—",
            "canonicalField": field_def["id"],
            "displayName": field_def["displayName"],
            "requirement": field_def.get("requirement"),
            "sourceField": None if not line else line.get("sourceField"),
            "available": bool(line),
            "mapping": None if not line else line.get("mappingType"),
            "enrichmentSource": "Buyer Master" if field_def["id"].startswith("buyer.") and status == "ENRICHED" else ("Legal Entity Master" if status == "ENRICHED" else ("Regulatory Profile" if status == "CONSTANT" else "")),
            "status": status,
        })
    return rows


def sap_gap_register() -> list[dict]:
    rows = [
        ("IBT-001", "Invoice number", True, "SupplierInvoice", False, False, False, "Available", "Existing API"),
        ("IBT-002", "Invoice date", True, "DocumentDate", False, False, False, "Available", "Existing API"),
        ("IBT-005", "Currency", True, "DocumentCurrency", False, False, False, "Available", "Existing API"),
        ("IBT-112", "Gross amount", True, "InvoiceGrossAmount", False, False, False, "Available", "Existing API"),
        ("IBT-106", "Line net amount", True, "SupplierInvoiceItemAmount", False, False, False, "Available", "Existing item API"),
        ("IBT-110", "VAT amount", False, "", True, False, False, "Derived", "Gross minus item amount"),
        ("IBT-118", "VAT category", True, "TaxCode", True, True, False, "Derived", "Tax code mapping"),
        ("IBT-048", "Buyer TRN", False, "", False, True, False, "Enrich", "Buyer master"),
        ("IBT-031", "Seller TRN", False, "", False, True, False, "Enrich", "Legal entity master"),
        ("IBT-034", "Electronic address", False, "", True, True, False, "Derived", "Not posted to SAP or Ariba"),
        ("", "UOM", True, "PurchaseOrderQuantityUnit", False, True, False, "Available", "Existing item API"),
        ("IBT-013", "Purchase order", True, "PurchaseOrder", False, False, False, "Available", "Existing item API"),
    ]
    result = []
    for term, label, available, existing, derive, enrich, change, status, notes in rows:
        result.append({
            "businessTerm": term or "—",
            "requiredValue": label,
            "availableFromExistingApi": available,
            "existingField": existing or "—",
            "canDerive": derive,
            "canEnrich": enrich,
            "sapChangeRequired": change,
            "status": status,
            "owner": "Integration",
            "notes": notes,
        })
    return result


def ariba_posting_map() -> list[dict]:
    pairs = [
        ("invoiceDetails.invoiceNumber", "InvoiceNumber", False),
        ("invoiceDetails.issueDate", "InvoiceDate", False),
        ("invoiceDetails.invoiceTypeCode", "DocumentType", False),
        ("invoiceDetails.currency", "Currency", False),
        ("seller.name", "Supplier", False),
        ("buyer.name", "Buyer", False),
        ("invoiceDetails.purchaseOrderReference", "PurchaseOrder", False),
        ("invoiceDetails.contractReference", "Contract", False),
        ("invoiceLines.quantity", "Quantity", False),
        ("invoiceLines.uom", "UOM", False),
        ("tax.category", "Tax", False),
        ("totals.taxInclusiveAmount", "Total", False),
        ("payment.means", "PaymentTerms", False),
        ("creditNoteReferences.invoiceNumber", "CreditReference", False),
        ("sourceDocumentId", "ExternalEInvoiceReference", False),
        ("seller.electronicAddress", "", True),
        ("invoiceDetails.specificationIdentifier", "", True),
        ("invoiceDetails.transactionTypeCode", "", True),
    ]
    return [{"canonicalField": canonical, "aribaField": ariba or "—", "regulatoryOnly": regulatory} for canonical, ariba, regulatory in pairs]


def implementation_readiness(data: dict, tenant_id: str) -> dict:
    entities = [item for item in data.get("legalEntities", []) if item.get("tenantId") == tenant_id]
    configured = [item for item in entities if entity_onboarding_status(item, data)["status"] not in {"CONFIGURATION REQUIRED"}]
    checks = {
        "Legal Entities": (len(configured), len(entities) or 1),
        "Properties": (len([item for item in data.get("properties", []) if item.get("tenantId") == tenant_id and item.get("legalEntityId")]), max(1, len([item for item in data.get("properties", []) if item.get("tenantId") == tenant_id]))),
        "SAP": (1 if any(item.get("sourceSystem") == "SAP S/4HANA" and item.get("status") == "Active" and item.get("tenantId") == tenant_id for item in data.get("mappings", [])) else 0, 1),
        "OPERA": (1 if any(item.get("sourceSystem") == "OPERA" and item.get("status") == "Active" and item.get("tenantId") == tenant_id for item in data.get("mappings", [])) else 0, 1),
        "Ariba": (1 if any(item.get("tenantId") == tenant_id and item.get("systemType") == "SAP Ariba" for item in data.get("connectors", [])) else 0, 1),
        "Customer Master": (1 if any(item.get("tenantId") == tenant_id for item in data.get("buyers", [])) else 0, 1),
        "Tax Mapping": (1 if data.get("codeLists", {}).get("taxCode") else 0, 1),
        "UOM Mapping": (1 if data.get("codeLists", {}).get("uom") else 0, 1),
        "Document Types": (1 if data.get("documentRules") else 0, 1),
        "Compliance": (1 if any(item.get("status") == "ACTIVE" for item in data.get("profiles") or []) else 0, 1),
        "ASP": (0, 1),
    }
    sections = []
    for name, (done, total) in checks.items():
        percent = round(100 * done / total)
        sections.append({"section": name, "done": done, "total": total, "percent": percent, "note": "Future phase" if name == "ASP" else ""})
    scored = [item for item in sections if item["section"] != "ASP"]
    overall = round(sum(item["percent"] for item in scored) / len(scored))
    return {"overall": overall, "sections": sections, "asp": "FUTURE"}


def attach_phase3(data: dict) -> dict:
    """Add customer onboarding configuration. Existing transactions are left as validated."""
    data["schemaVersion"] = 3
    data["nextTransaction"] = 1000
    five = "ten_five"
    for row in data.get("codeLists", {}).get("taxCode", []):
        if row.get("code") == "A5":
            row["rate"] = "5"
        if row.get("code") == "A0":
            row["rate"] = "0"
    for entity in data.get("legalEntities", []):
        entity.setdefault("tradingName", entity.get("entityName") or "")
        entity.setdefault("standaloneVat", not bool(entity.get("vatGroupMember")))
        entity.setdefault("legalRegistrationNumber", entity.get("tradeLicenceNumber") or "")
        entity.setdefault("legalRegistrationType", "TL" if entity.get("tradeLicenceNumber") else "")
        entity.setdefault("sapCompanyCode", "")
        entity.setdefault("sapSalesOrganization", "")
        entity.setdefault("sapPurchasingOrganization", "")
        entity.setdefault("operaPropertyCode", "")
        entity.setdefault("operaHotelCode", "")
        entity.setdefault("aribaEntityMapping", "")
        entity.setdefault("effectiveTo", None)
    jv = next((item for item in data["legalEntities"] if item["id"] == "le_five_jv"), None)
    if jv:
        jv["sapCompanyCode"] = "CC-DEMO"
        jv["operaPropertyCode"] = "OP-DEMO"
        jv["status"] = "Active"
    for index in range(3, 24):
        data["legalEntities"].append({
            "id": f"le_cfg_{index:02d}",
            "tenantId": five,
            "entityName": "CONFIGURATION REQUIRED",
            "legalName": "",
            "tradingName": "",
            "entityCode": f"CFG-{index:02d}",
            "trn": "",
            "tin": "",
            "peppolIdentifier": "",
            "electronicAddress": "",
            "electronicAddressScheme": "",
            "tradeLicenceNumber": "",
            "tradeLicenceAuthority": "",
            "legalRegistrationNumber": "",
            "legalRegistrationType": "",
            "vatRegistrationStatus": "",
            "vatGroup": "",
            "vatGroupMember": False,
            "standaloneVat": True,
            "address": "",
            "building": "",
            "street": "",
            "city": "",
            "emirate": "",
            "country": "AE",
            "postalCode": "",
            "defaultCurrency": "AED",
            "sourceSystems": "",
            "aspId": "",
            "sapCompanyCode": "",
            "sapSalesOrganization": "",
            "sapPurchasingOrganization": "",
            "operaPropertyCode": "",
            "operaHotelCode": "",
            "aribaEntityMapping": "",
            "effectiveDate": "",
            "effectiveTo": None,
            "status": "CONFIGURATION REQUIRED",
        })
    data["customerProfiles"] = [{
        "tenantId": five,
        "customer": "FIVE",
        "country": "United Arab Emirates",
        "jurisdiction": "UAE",
        "complianceProfileId": "prof_uae_2026_1",
        "sources": ["SAP S/4HANA", "SAP Ariba", "OPERA"],
    }]
    data["properties"] = [{
        "id": "prop_demo",
        "tenantId": five,
        "propertyCode": "OP-DEMO",
        "propertyName": "CONFIGURATION REQUIRED",
        "legalEntityId": "le_five_jv",
        "operaHotelCode": "",
        "connectionStatus": "Configured",
        "status": "Active",
    }]
    data["entityResolution"] = [
        {"id": "res_opera", "tenantId": five, "sourceSystem": "OPERA", "keyType": "OPERA_PROPERTY_CODE", "keyValue": "OP-DEMO", "legalEntityId": "le_five_jv", "active": True},
        {"id": "res_sap", "tenantId": five, "sourceSystem": "SAP_S4", "keyType": "SAP_COMPANY_CODE", "keyValue": "CC-DEMO", "legalEntityId": "le_five_jv", "active": True},
    ]
    data["documentRules"] = [
        {"id": "doc_opera_b2c", "tenantId": five, "sourceSystem": "OPERA", "field": "DOCUMENT_SCOPE", "equals": "B2C", "documentClass": "B2C Guest", "canonicalDocumentType": "Tax Invoice", "inScope": False, "route": "OUT_OF_SCOPE"},
        {"id": "doc_opera_b2b", "tenantId": five, "sourceSystem": "OPERA", "field": "DOCUMENT_SCOPE", "equals": "B2B", "documentClass": "B2B Invoice", "canonicalDocumentType": "Tax Invoice", "inScope": True, "route": "OUTBOUND_UAE_EINVOICE"},
        {"id": "doc_sap_acct", "tenantId": five, "sourceSystem": "SAP_S4", "field": "documentClass", "equals": "Accounting posting", "documentClass": "Accounting posting", "canonicalDocumentType": "Tax Invoice", "inScope": False, "route": "ACCOUNTING_REFERENCE_ONLY"},
        {"id": "doc_sap_b2b", "tenantId": five, "sourceSystem": "SAP_S4", "field": "documentClass", "equals": "B2B AR", "documentClass": "B2B Invoice", "canonicalDocumentType": "Tax Invoice", "inScope": True, "route": "OUTBOUND_UAE_EINVOICE"},
        {"id": "doc_ariba", "tenantId": five, "sourceSystem": "SAP_ARIBA", "field": "documentClass", "equals": "AP", "documentClass": "Supplier invoice", "canonicalDocumentType": "Tax Invoice", "inScope": False, "route": "AP_PROCESS"},
    ]
    data["sourceOfTruth"] = [
        {"id": "sot_hotel", "tenantId": five, "businessProcess": "Hotel B2B AR", "documentType": "B2B Invoice", "regulatorySource": "OPERA", "accountingSystem": "SAP S/4HANA", "procurementSystem": "—", "masterDataSource": "Buyer Master", "asp": "FUTURE"},
        {"id": "sot_sap", "tenantId": five, "businessProcess": "SAP B2B AR", "documentType": "Tax Invoice", "regulatorySource": "SAP S/4HANA", "accountingSystem": "SAP S/4HANA", "procurementSystem": "—", "masterDataSource": "Buyer Master", "asp": "FUTURE"},
        {"id": "sot_ap", "tenantId": five, "businessProcess": "Supplier AP", "documentType": "Supplier invoice", "regulatorySource": "ASP", "accountingSystem": "SAP S/4HANA", "procurementSystem": "SAP Ariba", "masterDataSource": "Supplier Master", "asp": "FUTURE"},
    ]
    data["buyers"] = [{
        "id": "buyer_gulf",
        "tenantId": five,
        "customerId": "buyer_gulf",
        "sourceCustomerId": "TA-DEMO",
        "customerName": "Gulf Events LLC",
        "legalName": "Gulf Events LLC",
        "trn": "100555666777003",
        "tin": "100555666777003",
        "electronicAddress": "0235:100555666777003",
        "electronicAddressScheme": "0235",
        "legalRegistration": "",
        "address": "",
        "emirate": "Dubai",
        "country": "AE",
        "contact": "",
        "effectiveDate": "2026-01-01",
        "status": "ACTIVE",
        "operaCustomerCode": "TA-DEMO",
        "sapCustomerNumber": "10000455",
        "aribaVendor": "",
    }]
    opera_lines = [
        {"sourceField": "FOLIO_NO", "targetField": "invoiceDetails.invoiceNumber", "targetBusinessTerm": "IBT-001", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
        {"sourceField": "BILLINGDATE", "targetField": "invoiceDetails.issueDate", "targetBusinessTerm": "IBT-002", "requirement": "MANDATORY", "mappingType": "DATE_FORMAT", "transformation": "YYYYMMDD to ISO date", "status": "MAPPED"},
        {"sourceField": "CURRENCY", "targetField": "invoiceDetails.currency", "targetBusinessTerm": "IBT-005", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
        {"sourceField": "CUSTOMER_NAME", "targetField": "buyer.name", "targetBusinessTerm": "IBT-044", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
        {"sourceField": "NETAMOUNT", "targetField": "totals.netAmount", "targetBusinessTerm": "IBT-106", "requirement": "MANDATORY", "mappingType": "NUMBER_FORMAT", "status": "MAPPED"},
        {"sourceField": "TAXAMOUNT", "targetField": "totals.taxAmount", "targetBusinessTerm": "IBT-110", "requirement": "MANDATORY", "mappingType": "NUMBER_FORMAT", "status": "MAPPED"},
        {"sourceField": "GROSSAMOUNT", "targetField": "totals.taxInclusiveAmount", "targetBusinessTerm": "IBT-112", "requirement": "MANDATORY", "mappingType": "NUMBER_FORMAT", "status": "MAPPED"},
        {"sourceField": "TAX_CATEGORY", "targetField": "tax.category", "targetBusinessTerm": "IBT-118", "requirement": "MANDATORY", "mappingType": "Constant", "defaultValue": "S", "status": "MAPPED"},
        {"sourceField": "TAX_RATE", "targetField": "tax.rate", "targetBusinessTerm": "IBT-119", "requirement": "MANDATORY", "mappingType": "Constant", "defaultValue": "5", "status": "MAPPED"},
    ]
    data["mappings"].append({
        "id": "map_opera_b2b",
        "tenantId": five,
        "legalEntityId": "le_five_jv",
        "sourceSystem": "OPERA",
        "documentType": "Tax Invoice",
        "version": 1,
        "status": "Active",
        "scope": "SOURCE",
        "parentId": None,
        "propertyCode": "",
        "effectiveFrom": "2026-01-01",
        "effectiveTo": None,
        "lines": opera_lines,
    })
    data["mappings"].append({
        "id": "map_opera_property_override",
        "tenantId": five,
        "legalEntityId": "le_five_jv",
        "sourceSystem": "OPERA",
        "documentType": "Tax Invoice",
        "version": 1,
        "status": "Active",
        "scope": "PROPERTY",
        "parentId": "map_opera_b2b",
        "propertyCode": "OP-DEMO",
        "effectiveFrom": "2026-01-01",
        "effectiveTo": None,
        "lines": [{"sourceField": "SELLER_REG", "targetField": "seller.tradeLicenceNumber", "requirement": "OPTIONAL", "mappingType": "Direct", "status": "MAPPED"}],
    })
    data["mappings"].append({
        "id": "map_sap_ar",
        "tenantId": five,
        "legalEntityId": "le_five_jv",
        "sourceSystem": "SAP S/4HANA",
        "documentType": "Tax Invoice",
        "version": 2,
        "status": "Inactive",
        "scope": "SOURCE",
        "parentId": None,
        "effectiveFrom": "2026-01-01",
        "effectiveTo": None,
        "lines": [
            {"sourceField": "SupplierInvoice", "targetField": "invoiceDetails.invoiceNumber", "targetBusinessTerm": "IBT-001", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
            {"sourceField": "DocumentDate", "targetField": "invoiceDetails.issueDate", "targetBusinessTerm": "IBT-002", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
            {"sourceField": "DocumentCurrency", "targetField": "invoiceDetails.currency", "targetBusinessTerm": "IBT-005", "requirement": "MANDATORY", "mappingType": "Direct", "status": "MAPPED"},
            {"sourceField": "SupplierInvoiceItemAmount", "targetField": "totals.netAmount", "targetBusinessTerm": "IBT-106", "requirement": "MANDATORY", "mappingType": "NUMBER_FORMAT", "status": "MAPPED"},
            {"sourceField": "InvoiceGrossAmount", "targetField": "totals.taxInclusiveAmount", "targetBusinessTerm": "IBT-112", "requirement": "MANDATORY", "mappingType": "NUMBER_FORMAT", "status": "MAPPED"},
            {"sourceField": "TaxCode", "targetField": "tax.category", "targetBusinessTerm": "IBT-118", "requirement": "MANDATORY", "mappingType": "Code Translation", "codeList": "taxCode", "status": "MAPPED"},
        ],
    })
    # The active S/4 mapping remains the Phase 1 profile. This SAP profile is
    # selected by source field names during ingestion when the document type matches
    # and the older profile does not contain SupplierInvoice. Ingestion uses the
    # most specific active profile, so activate this one for the pipeline demo
    # without removing the original profile: mark the original inactive only for
    # the new document path by giving this profile equal specificity and relying
    # on select order. Keep the original Active so readiness tests stay valid.
    # Ingestion passes source SAP S/4HANA and both are active for the same entity.
    # select_mapping_lines keeps the later equal-specificity profile.
    data["mappings"][-1]["status"] = "Active"
    for connector in data.get("connectors", []):
        if connector.get("id") == "con_opera":
            connector.update({
                "connectionType": "MOCK",
                "implementation": "OperaMockConnector",
                "propertyCode": "OP-DEMO",
                "hotelCode": "",
                "pollingEnabled": True,
                "frequency": "15 minutes",
                "lastPoll": None,
                "lastFailedPoll": None,
                "lastTransactionReceived": None,
                "cursor": None,
                "active": True,
                "connectionStatus": "Configured",
            })
    data["fixtures"] = [{
        "id": "fx_opera_b2b",
        "tenantId": five,
        "connectorId": "con_opera",
        "queue": True,
        "fixture": "opera-b2b",
        "sourceSystem": "OPERA",
        "sourceInstance": "OPERA-MOCK",
        "sourceDocumentId": "OP-B2B-1001",
        "documentClass": "B2B Invoice",
        "payload": {
            "FOLIO_NO": "OP-B2B-1001",
            "PROPERTY_CODE": "OP-DEMO",
            "DOCUMENT_SCOPE": "B2B",
            "BILLINGDATE": "20261014",
            "CURRENCY": "AED",
            "CUSTOMER_CODE": "TA-DEMO",
            "CUSTOMER_NAME": "Gulf Events LLC",
            "HOTEL_TRN": "",
            "NETAMOUNT": "1000.00",
            "TAXAMOUNT": "50.00",
            "GROSSAMOUNT": "1050.00",
        },
    }]
    data["fixtureLibrary"] = {
        "opera-b2c": {
            "tenantId": five,
            "sourceSystem": "OPERA", "sourceDocumentId": "OP-B2C-1", "sourceInstance": "OPERA-MOCK",
            "payload": {"FOLIO_NO": "OP-B2C-1", "PROPERTY_CODE": "OP-DEMO", "DOCUMENT_SCOPE": "B2C", "BILLINGDATE": "20261014", "CURRENCY": "AED", "NETAMOUNT": "200.00"},
        },
        "sap-b2b": {
            "tenantId": five,
            "sourceSystem": "SAP S/4HANA", "sourceDocumentId": "510560099", "sourceInstance": "S4", "documentClass": "B2B AR",
            "payload": {"SupplierInvoice": "510560099", "FiscalYear": "2026", "CompanyCode": "CC-DEMO", "DocumentDate": "2026-10-14", "DocumentCurrency": "AED", "InvoiceGrossAmount": "1050.00", "SupplierInvoiceItemAmount": "1000.00", "TaxCode": "A5", "InvoicingParty": "10000455", "DocumentHeaderText": "B2B AR"},
        },
        "sap-accounting-ref": {
            "tenantId": five,
            "sourceSystem": "SAP S/4HANA", "sourceDocumentId": "940099", "sourceInstance": "S4", "documentClass": "Accounting posting", "regulatoryReference": "OP-B2B-1001",
            "payload": {"SupplierInvoice": "940099", "CompanyCode": "CC-DEMO", "DocumentDate": "2026-10-14", "DocumentCurrency": "AED", "InvoiceGrossAmount": "1050.00", "DocumentHeaderText": "OP-B2B-1001"},
        },
        "opera-missing-buyer": {
            "tenantId": five,
            "sourceSystem": "OPERA", "sourceDocumentId": "OP-B2B-1002", "sourceInstance": "OPERA-MOCK",
            "payload": {"FOLIO_NO": "OP-B2B-1002", "PROPERTY_CODE": "OP-DEMO", "DOCUMENT_SCOPE": "B2B", "BILLINGDATE": "20261014", "CURRENCY": "AED", "CUSTOMER_CODE": "UNKNOWN", "NETAMOUNT": "1000.00", "TAXAMOUNT": "50.00", "GROSSAMOUNT": "1050.00"},
        },
    }
    data["aribaPostingMap"] = ariba_posting_map()
    return data
