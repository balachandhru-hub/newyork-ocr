"""Tenant-scoped persistence for the UAE e-Invoicing module.

The SQL Server shape lives in backend/sql/uae_einvoicing.sql. Phase 1 uses a
file repository so the module runs before a customer database is attached.
Secrets are stored only as references.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from uae_einvoicing.engine import (
    SILA_ROLE_DEFAULTS,
    UAE_ROLES,
    allows,
    apply_gate,
    compliance_health,
    derive_flags,
    field,
    set_final,
)
from uae_einvoicing.pipeline import (
    FIELD_REGISTRY,
    attach_phase2,
    context_for,
    entity_readiness,
    execute_mapping,
    run_pipeline,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _blank_canonical() -> dict:
    return {
        "invoiceDetails": {
            "invoiceNumber": field("INV-1001"),
            "issueDate": field("2026-10-01"),
            "documentType": field("Tax Invoice"),
            "currency": field("AED"),
        },
        "seller": {
            "name": field("Seller LLC"),
            "trn": field("100000000000003"),
            "emirate": field("Dubai"),
            "electronicAddress": field("0235:0000"),
        },
        "buyer": {
            "name": field("Buyer LLC"),
            "trn": field("100000000000012"),
            "emirate": field("Abu Dhabi"),
        },
        "payee": {"name": field(None, None, None, status="NOT APPLICABLE")},
        "delivery": {"city": field("Dubai")},
        "payment": {"means": field("30"), "endpoint": field("0235:0000")},
        "references": {"buyerReference": field("PO-100")},
        "contract": {"contractId": field(None, None, None, status="NOT APPLICABLE")},
        "purchaseOrder": {"purchaseOrderId": field("PO-100")},
        "tax": {"category": field("S"), "rate": field(5)},
        "allowances": {"amount": field(0)},
        "charges": {"amount": field(0)},
        "totals": {
            "netAmount": field(1000),
            "taxAmount": field(50),
            "taxInclusiveAmount": field(1050),
        },
        "invoiceLines": {"items": field([{"description": "Room revenue", "amount": 1000}])},
        "creditNoteReferences": {"invoiceNumber": field(None, None, None, status="NOT APPLICABLE")},
        "transactionClassification": {
            "freeTradeZone": field(False),
            "deemedSupply": field(False),
            "marginScheme": field(False),
            "summaryInvoice": field(False),
            "continuousSupply": field(False),
            "disclosedAgent": field(False),
            "ecommerce": field(False),
            "export": field(False),
            "designatedZone": field(False),
        },
        "uaeExtensions": {"designatedZone": field(False)},
        "aspMetadata": {"providerReference": field(None, None, None, status="NOT APPLICABLE")},
        "sourceMetadata": {"system": field("SAP S/4HANA"), "payloadId": field("SRC-1")},
    }


def _with(base: dict, path: str, source: Any, mapped: Any = None, final: Any = None, rule: str = "Direct", derivation: str = "", status: str = "COMPLIANT") -> dict:
    section, name = path.split(".", 1)
    base[section][name] = field(source, mapped, final, rule, derivation, status)
    return base


FLAG_RULES = [
    {
        "code": "FREE_TRADE_ZONE",
        "label": "Free Trade Zone",
        "derivation": "Seller is in a configured designated zone and the supply is not an export.",
        "when": {"all": [
            {"path": "transactionClassification.designatedZone", "equals": True},
            {"path": "transactionClassification.export", "equals": False},
        ]},
    },
    {
        "code": "DEEMED_SUPPLY",
        "label": "Deemed Supply",
        "derivation": "Source classification marks the supply as deemed.",
        "when": {"path": "transactionClassification.deemedSupply", "equals": True},
    },
    {
        "code": "MARGIN_SCHEME",
        "label": "Margin Scheme",
        "derivation": "Tax category indicates the margin scheme.",
        "when": {"path": "transactionClassification.marginScheme", "equals": True},
    },
    {
        "code": "SUMMARY_INVOICE",
        "label": "Summary Invoice",
        "derivation": "Document is flagged as a summary invoice by source data.",
        "when": {"path": "transactionClassification.summaryInvoice", "equals": True},
    },
    {
        "code": "CONTINUOUS_SUPPLY",
        "label": "Continuous Supply",
        "derivation": "Supply period is present on a continuous-supply document.",
        "when": {"path": "transactionClassification.continuousSupply", "equals": True},
    },
    {
        "code": "DISCLOSED_AGENT",
        "label": "Disclosed Agent Billing",
        "derivation": "Payee differs from the seller and the disclosed-agent flag is set.",
        "when": {"path": "transactionClassification.disclosedAgent", "equals": True},
    },
    {
        "code": "ECOMMERCE",
        "label": "Supply through E-Commerce",
        "derivation": "Source channel is an e-commerce platform.",
        "when": {"path": "transactionClassification.ecommerce", "equals": True},
    },
    {
        "code": "EXPORT",
        "label": "Exports",
        "derivation": "Buyer country is outside the UAE.",
        "when": {"path": "transactionClassification.export", "equals": True},
    },
]


def _rules() -> list[dict]:
    common = {"tenantId": "*", "version": 1, "active": True, "effectiveFrom": "2026-01-01", "effectiveTo": None}
    specs = [
        ("UAE-INV-001", "Invoice number required", "Invoice Details", "invoiceDetails.invoiceNumber", "ERROR", "All", {"op": "present", "path": "invoiceDetails.invoiceNumber"}, "Invoice number is mandatory.", "Map the source invoice number."),
        ("UAE-INV-002", "Issue date required", "Invoice Details", "invoiceDetails.issueDate", "ERROR", "All", {"op": "present", "path": "invoiceDetails.issueDate"}, "Issue date is mandatory.", "Map the source document date."),
        ("UAE-SEL-001", "Seller TRN format", "Seller", "seller.trn", "ERROR", "Tax Invoice", {"op": "matches", "path": "seller.trn", "pattern": r"\d{15}"}, "Seller TRN must be 15 digits.", "Correct the seller tax registration number."),
        ("UAE-BUY-001", "Buyer TRN when taxable", "Buyer", "buyer.trn", "ERROR", "Tax Invoice", {"op": "matches", "path": "buyer.trn", "pattern": r"\d{15}", "when": {"path": "tax.category", "equals": "S"}}, "Buyer TRN is required for standard-rated supplies.", "Provide the buyer TRN or change the tax category."),
        ("UAE-TAX-001", "Tax rate present", "Tax", "tax.rate", "ERROR", "Tax Invoice", {"op": "present", "path": "tax.rate"}, "Tax rate is mandatory.", "Map the VAT rate from the source tax code."),
        ("UAE-TAX-002", "Export tax category", "Transaction Type", "tax.category", "ERROR", "Tax Invoice", {"op": "one_of", "path": "tax.category", "values": ["Z"], "when": {"path": "transactionClassification.export", "equals": True}}, "Exports must use a zero-rated category.", "Set the tax category from the export rule."),
        ("UAE-PAY-001", "Electronic address", "Payment", "payment.endpoint", "WARNING", "All", {"op": "present", "path": "payment.endpoint"}, "Peppol endpoint is missing.", "Configure the participant identifier."),
        ("UAE-TOT-001", "Totals balance", "Totals", "totals.taxInclusiveAmount", "ERROR", "All", {"op": "numeric_sum", "parts": ["totals.netAmount", "totals.taxAmount"], "total": "totals.taxInclusiveAmount"}, "Tax inclusive amount must equal net plus tax.", "Recalculate header totals."),
        ("UAE-LIN-001", "At least one line", "Invoice Lines", "invoiceLines.items", "ERROR", "All", {"op": "line_count", "min": 1}, "An invoice needs at least one line.", "Map source line items."),
        ("UAE-CRN-001", "Credit note reference", "Credit Notes", "creditNoteReferences.invoiceNumber", "ERROR", "Credit Note", {"op": "present", "path": "creditNoteReferences.invoiceNumber"}, "A credit note must reference an invoice.", "Map the original invoice number."),
        ("UAE-SB-001", "Self-bill seller identity", "Self-Billing", "seller.trn", "ERROR", "Self-Billed Invoice", {"op": "present", "path": "seller.trn"}, "Self-billed invoices require the supplier TRN.", "Map the supplier tax identifier."),
        ("UAE-REF-001", "Purchase order reference", "Reference Validation", "purchaseOrder.purchaseOrderId", "WARNING", "Tax Invoice", {"op": "present", "path": "purchaseOrder.purchaseOrderId"}, "Purchase order reference is empty.", "Map the source PO number when the process uses one."),
    ]
    rows = []
    for rule_id, name, category, term, severity, document, logic, message, resolution in specs:
        rows.append({
            **common,
            "id": f"rule_{rule_id.lower()}",
            "ruleId": rule_id,
            "ruleName": name,
            "description": name,
            "category": category,
            "businessTerm": term,
            "severity": severity,
            "documentType": document,
            "condition": "Applies when the document type matches and the rule version is active.",
            "validationLogic": logic,
            "errorMessage": message,
            "suggestedResolution": resolution,
        })
    return rows


def _txn(tenant: str, legal: str, **kwargs) -> dict:
    canonical = kwargs.pop("canonical")
    now = kwargs.pop("now", "2026-10-14T06:00:00+00:00")
    txn = {
        "id": kwargs["id"],
        "tenantId": tenant,
        "legalEntityId": legal,
        "direction": kwargs["direction"],
        "documentType": kwargs["documentType"],
        "invoiceNumber": kwargs["invoiceNumber"],
        "sourceSystem": kwargs["sourceSystem"],
        "supplier": kwargs["supplier"],
        "buyer": kwargs["buyer"],
        "invoiceDate": kwargs["invoiceDate"],
        "amount": kwargs["amount"],
        "currency": kwargs.get("currency", "AED"),
        "sourceStatus": kwargs.get("sourceStatus", "RECEIVED"),
        "complianceStatus": kwargs.get("complianceStatus", "COMPLIANT"),
        "aspStatus": kwargs.get("aspStatus", "NOT_SUBMITTED"),
        "networkStatus": kwargs.get("networkStatus", "NOT_SENT"),
        "erpStatus": kwargs.get("erpStatus", "NOT_POSTED"),
        "lifecycleStatus": kwargs["lifecycleStatus"],
        "createdAt": kwargs.get("createdAt", now),
        "updatedAt": kwargs.get("updatedAt", now),
        "canonical": canonical,
        "timeline": kwargs.get("timeline", []),
        "mappingVersionId": kwargs.get("mappingVersionId"),
        "ruleVersion": 1,
        "validationResults": [],
        "classification": [],
    }
    return txn


def _assemble_seed() -> dict:
    five = "ten_five"
    harbour = "ten_harbour"
    five_jv = "le_five_jv"
    five_palm = "le_five_palm"
    harbour_le = "le_harbour"
    asp_primary = "asp_caspian"
    asp_secondary = "asp_harbour_exchange"
    map_active = "map_s4_tax_v1"

    rules = _rules()

    def canon_ok(number: str, seller: str, buyer: str, seller_trn: str, buyer_trn: str, net: float, system: str) -> dict:
        tax = round(net * 0.05, 2)
        data = _blank_canonical()
        _with(data, "invoiceDetails.invoiceNumber", number)
        _with(data, "invoiceDetails.issueDate", "2026-10-12")
        _with(data, "seller.name", seller)
        _with(data, "seller.trn", seller_trn)
        _with(data, "buyer.name", buyer)
        _with(data, "buyer.trn", buyer_trn)
        _with(data, "totals.netAmount", net)
        _with(data, "totals.taxAmount", tax)
        _with(data, "totals.taxInclusiveAmount", round(net + tax, 2))
        _with(data, "invoiceLines.items", [{"description": "Supply", "amount": net}])
        _with(data, "sourceMetadata.system", system)
        _with(data, "sourceMetadata.payloadId", f"SRC-{number}")
        return data

    broken = canon_ok("FV-24018", "FIVE Jumeirah Village LLC", "Gulf Events LLC", "", "100555666777003", 8400, "OPERA")
    _with(broken, "seller.trn", "", "", "", rule="Direct", status="NON-COMPLIANT")

    export_doc = canon_ok("FV-24022", "FIVE Palm Jumeirah LLC", "Nordic Travel AB", "100234567890012", "", 15600, "SAP S/4HANA")
    _with(export_doc, "transactionClassification.export", True)
    _with(export_doc, "tax.category", "S", "S", "S", status="NON-COMPLIANT")
    _with(export_doc, "buyer.trn", "", "", "", status="NOT APPLICABLE")

    credit = canon_ok("CN-1044", "FIVE Jumeirah Village LLC", "Gulf Events LLC", "100234567890003", "100555666777003", -500, "SAP Ariba")
    _with(credit, "invoiceDetails.documentType", "Credit Note")
    _with(credit, "creditNoteReferences.invoiceNumber", None, None, None, status="NON-COMPLIANT")

    zone = canon_ok("FV-24030", "FIVE Jumeirah Village LLC", "Zone Retail LLC", "100234567890003", "100888999000003", 2200, "OPERA")
    _with(zone, "transactionClassification.designatedZone", True, True, True, rule="Derived", derivation="Emirate and licence authority are on the designated-zone list.")

    transactions = [
        _txn(five, five_jv, id="UAE-2026-00041", direction="Outbound AR", documentType="Tax Invoice", invoiceNumber="FV-24011", sourceSystem="SAP S/4HANA", supplier="FIVE Jumeirah Village LLC", buyer="Emirates Catering LLC", invoiceDate="2026-10-12", amount=12810, lifecycleStatus="RECONCILED", aspStatus="ASP_ACCEPTED", networkStatus="DELIVERED", erpStatus="ERP_POSTED", sourceStatus="POSTED", canonical=canon_ok("FV-24011", "FIVE Jumeirah Village LLC", "Emirates Catering LLC", "100234567890003", "100111222333003", 12200, "SAP S/4HANA"), mappingVersionId=map_active, timeline=[
            {"at": "2026-10-12T08:01:00+00:00", "label": "Received from SAP S/4HANA"},
            {"at": "2026-10-12T08:01:20+00:00", "label": "Mapped"},
            {"at": "2026-10-12T08:01:30+00:00", "label": "Enriched"},
            {"at": "2026-10-12T08:01:40+00:00", "label": "Compliance Passed"},
            {"at": "2026-10-12T08:05:00+00:00", "label": "Submitted to ASP"},
            {"at": "2026-10-12T08:06:00+00:00", "label": "ASP Accepted"},
            {"at": "2026-10-12T08:20:00+00:00", "label": "Buyer ASP Delivered"},
            {"at": "2026-10-12T08:21:00+00:00", "label": "Network Status Received"},
            {"at": "2026-10-12T09:00:00+00:00", "label": "ERP Updated"},
            {"at": "2026-10-12T09:10:00+00:00", "label": "Reconciled"},
        ]),
        _txn(five, five_jv, id="UAE-2026-00042", direction="Outbound AR", documentType="Tax Invoice", invoiceNumber="FV-24018", sourceSystem="OPERA", supplier="FIVE Jumeirah Village LLC", buyer="Gulf Events LLC", invoiceDate="2026-10-13", amount=8820, lifecycleStatus="COMPLIANCE_FAILED", complianceStatus="NON-COMPLIANT", aspStatus="NOT_SUBMITTED", networkStatus="NOT_SENT", erpStatus="NOT_POSTED", canonical=broken, timeline=[
            {"at": "2026-10-13T14:12:00+00:00", "label": "Received from OPERA"},
            {"at": "2026-10-13T14:12:10+00:00", "label": "Mapped"},
            {"at": "2026-10-13T14:12:20+00:00", "label": "Compliance Failed"},
        ]),
        _txn(five, five_palm, id="UAE-2026-00043", direction="Inbound AP", documentType="Tax Invoice", invoiceNumber="AR-8831", sourceSystem="SAP Ariba", supplier="Desert Linen LLC", buyer="FIVE Palm Jumeirah LLC", invoiceDate="2026-10-11", amount=4600, lifecycleStatus="ASP_REJECTED", aspStatus="ASP_REJECTED", networkStatus="NOT_SENT", erpStatus="NOT_POSTED", canonical=canon_ok("AR-8831", "Desert Linen LLC", "FIVE Palm Jumeirah LLC", "100777888999003", "100234567890012", 4380.95, "SAP Ariba"), timeline=[
            {"at": "2026-10-11T11:00:00+00:00", "label": "Received from SAP Ariba"},
            {"at": "2026-10-11T11:05:00+00:00", "label": "Submitted to ASP"},
            {"at": "2026-10-11T11:08:00+00:00", "label": "ASP Rejected"},
        ]),
        _txn(five, five_jv, id="UAE-2026-00044", direction="Credit Notes", documentType="Credit Note", invoiceNumber="CN-1044", sourceSystem="SAP Ariba", supplier="FIVE Jumeirah Village LLC", buyer="Gulf Events LLC", invoiceDate="2026-10-14", amount=-525, lifecycleStatus="COMPLIANCE_FAILED", complianceStatus="NON-COMPLIANT", canonical=credit, timeline=[
            {"at": "2026-10-14T05:40:00+00:00", "label": "Received from SAP Ariba"},
            {"at": "2026-10-14T05:40:30+00:00", "label": "Compliance Failed"},
        ]),
        _txn(five, five_palm, id="UAE-2026-00045", direction="Self-Billing", documentType="Self-Billed Invoice", invoiceNumber="SB-220", sourceSystem="SAP S/4HANA", supplier="Marina Services LLC", buyer="FIVE Palm Jumeirah LLC", invoiceDate="2026-10-14", amount=3100, lifecycleStatus="SUBMITTED", aspStatus="SUBMITTED", networkStatus="PENDING", erpStatus="PENDING", canonical=canon_ok("SB-220", "Marina Services LLC", "FIVE Palm Jumeirah LLC", "100333444555003", "100234567890012", 2952.38, "SAP S/4HANA"), timeline=[
            {"at": "2026-10-14T04:10:00+00:00", "label": "Received from SAP S/4HANA"},
            {"at": "2026-10-14T04:12:00+00:00", "label": "Compliance Passed"},
            {"at": "2026-10-14T04:15:00+00:00", "label": "Submitted to ASP"},
        ]),
        _txn(five, five_jv, id="UAE-2026-00046", direction="Outbound AR", documentType="Tax Invoice", invoiceNumber="FV-24022", sourceSystem="SAP S/4HANA", supplier="FIVE Palm Jumeirah LLC", buyer="Nordic Travel AB", invoiceDate="2026-10-10", amount=15600, lifecycleStatus="COMPLIANCE_FAILED", complianceStatus="NON-COMPLIANT", canonical=export_doc, timeline=[
            {"at": "2026-10-10T09:00:00+00:00", "label": "Received from SAP S/4HANA"},
            {"at": "2026-10-10T09:01:00+00:00", "label": "Transaction classification derived"},
            {"at": "2026-10-10T09:01:20+00:00", "label": "Compliance Failed"},
        ]),
        _txn(five, five_jv, id="UAE-2026-00047", direction="Outbound AR", documentType="Tax Invoice", invoiceNumber="FV-24030", sourceSystem="OPERA", supplier="FIVE Jumeirah Village LLC", buyer="Zone Retail LLC", invoiceDate="2026-10-14", amount=2310, lifecycleStatus="READY_FOR_ASP", aspStatus="NOT_SUBMITTED", canonical=zone, timeline=[
            {"at": "2026-10-14T06:20:00+00:00", "label": "Received from OPERA"},
            {"at": "2026-10-14T06:20:20+00:00", "label": "Free trade zone flag derived"},
            {"at": "2026-10-14T06:20:40+00:00", "label": "Compliance Passed"},
        ]),
        _txn(five, five_palm, id="UAE-2026-00048", direction="Inbound AP", documentType="Tax Invoice", invoiceNumber="OP-19", sourceSystem="OPERA", supplier="Unknown Supplier", buyer="FIVE Palm Jumeirah LLC", invoiceDate="2026-10-09", amount=980, lifecycleStatus="MAPPING_FAILED", complianceStatus="NON-COMPLIANT", aspStatus="NOT_SUBMITTED", canonical=canon_ok("OP-19", "", "FIVE Palm Jumeirah LLC", "", "100234567890012", 933.33, "OPERA"), timeline=[
            {"at": "2026-10-09T16:00:00+00:00", "label": "Received from OPERA"},
            {"at": "2026-10-09T16:00:10+00:00", "label": "Mapping failed"},
        ]),
        _txn(harbour, harbour_le, id="UAE-2026-01001", direction="Outbound AR", documentType="Tax Invoice", invoiceNumber="HR-501", sourceSystem="Custom REST API", supplier="Harbour Retail LLC", buyer="City Mall LLC", invoiceDate="2026-10-14", amount=6400, lifecycleStatus="READY_FOR_ASP", canonical=canon_ok("HR-501", "Harbour Retail LLC", "City Mall LLC", "100999000111003", "100222333444003", 6095.24, "Custom REST API"), timeline=[
            {"at": "2026-10-14T07:00:00+00:00", "label": "Received from Custom REST API"},
            {"at": "2026-10-14T07:00:30+00:00", "label": "Compliance Passed"},
        ]),
    ]

    for txn in transactions:
        gated = apply_gate(txn, [dict(rule, tenantId=txn["tenantId"]) for rule in rules])
        if txn["lifecycleStatus"] in {"RECONCILED", "ASP_REJECTED", "SUBMITTED", "MAPPING_FAILED"}:
            gated["lifecycleStatus"] = txn["lifecycleStatus"]
            gated["aspStatus"] = txn["aspStatus"]
            gated["networkStatus"] = txn["networkStatus"]
            gated["erpStatus"] = txn["erpStatus"]
            if txn["lifecycleStatus"] == "MAPPING_FAILED":
                gated["complianceStatus"] = "NON-COMPLIANT"
        txn["validationResults"] = gated["validationResults"]
        txn["complianceStatus"] = gated["complianceStatus"]
        txn["lifecycleStatus"] = gated["lifecycleStatus"]
        txn["aspStatus"] = gated["aspStatus"]
        txn["classification"] = derive_flags(txn["canonical"], FLAG_RULES)
        if txn["lifecycleStatus"] == "MAPPING_FAILED":
            txn["complianceStatus"] = "NON-COMPLIANT"
            txn["aspStatus"] = "NOT_SUBMITTED"

    mapping_lines = [
        {"sourceField": "BILLINGDOCUMENT", "sourceDescription": "S/4 billing document", "sampleValue": "FV-24011", "targetBusinessTerm": "BT-1", "targetField": "invoiceDetails.invoiceNumber", "requirement": "MANDATORY", "mappingType": "Direct", "transformation": "", "defaultValue": "", "derivationRule": "", "validation": "Present", "status": "MAPPED"},
        {"sourceField": "BILLINGDATE", "sourceDescription": "Billing date", "sampleValue": "2026-10-12", "targetBusinessTerm": "BT-2", "targetField": "invoiceDetails.issueDate", "requirement": "MANDATORY", "mappingType": "Transformation", "transformation": "YYYYMMDD to ISO date", "defaultValue": "", "derivationRule": "", "validation": "Date", "status": "MAPPED"},
        {"sourceField": "SUPPLIERVAT", "sourceDescription": "Supplier VAT registration", "sampleValue": "100234567890003", "targetBusinessTerm": "BT-31", "targetField": "seller.trn", "requirement": "MANDATORY", "mappingType": "Direct", "transformation": "Digits only", "defaultValue": "", "derivationRule": "", "validation": "15 digits", "status": "MAPPED"},
        {"sourceField": "CUSTOMERVAT", "sourceDescription": "Customer VAT registration", "sampleValue": "100111222333003", "targetBusinessTerm": "BT-48", "targetField": "buyer.trn", "requirement": "CONDITIONAL", "mappingType": "Conditional", "transformation": "", "defaultValue": "", "derivationRule": "Required when tax category is standard", "validation": "15 digits when standard rated", "status": "MAPPED"},
        {"sourceField": "CURRENCY", "sourceDescription": "Document currency", "sampleValue": "AED", "targetBusinessTerm": "BT-5", "targetField": "invoiceDetails.currency", "requirement": "MANDATORY", "mappingType": "Lookup", "transformation": "Currency lookup", "defaultValue": "AED", "derivationRule": "Legal entity default currency", "validation": "ISO 4217", "status": "MAPPED"},
        {"sourceField": "NETAMOUNT", "sourceDescription": "Net amount", "sampleValue": "12200.00", "targetBusinessTerm": "BT-106", "targetField": "totals.netAmount", "requirement": "MANDATORY", "mappingType": "Direct", "transformation": "", "defaultValue": "", "derivationRule": "", "validation": "Numeric", "status": "MAPPED"},
        {"sourceField": "TAXAMOUNT", "sourceDescription": "VAT amount", "sampleValue": "610.00", "targetBusinessTerm": "BT-110", "targetField": "totals.taxAmount", "requirement": "MANDATORY", "mappingType": "Derived", "transformation": "", "defaultValue": "", "derivationRule": "Sum of line tax", "validation": "Balances to lines", "status": "MAPPED"},
        {"sourceField": "GROSSAMOUNT", "sourceDescription": "Gross amount", "sampleValue": "12810.00", "targetBusinessTerm": "BT-112", "targetField": "totals.taxInclusiveAmount", "requirement": "MANDATORY", "mappingType": "Derived", "transformation": "", "defaultValue": "", "derivationRule": "Net plus tax", "validation": "Arithmetic", "status": "MAPPED"},
        {"sourceField": "ITEMS", "sourceDescription": "Billing items", "sampleValue": "1 line", "targetBusinessTerm": "BG-25", "targetField": "invoiceLines.items", "requirement": "MANDATORY", "mappingType": "Transformation", "transformation": "Line table to canonical lines", "defaultValue": "", "derivationRule": "", "validation": "At least one line", "status": "MAPPED"},
        {"sourceField": "ENDPOINT", "sourceDescription": "Participant identifier", "sampleValue": "", "targetBusinessTerm": "BT-34", "targetField": "payment.endpoint", "requirement": "OPTIONAL", "mappingType": "Constant", "transformation": "", "defaultValue": "", "derivationRule": "Legal entity Peppol identifier", "validation": "Scheme and value", "status": "UNMAPPED"},
    ]

    return {
        "tenants": [
            {"id": five, "name": "FIVE Hotels", "code": "FIVE", "status": "Active"},
            {"id": harbour, "name": "Harbour Retail", "code": "HARBOUR", "status": "Active"},
        ],
        "legalEntities": [
            _entity(five, five_jv, "FIVE Jumeirah Village", "FIVE Jumeirah Village LLC", "FIVE-JV", "100234567890003", "Dubai", asp_primary, "SAP S/4HANA, OPERA"),
            _entity(five, five_palm, "FIVE Palm Jumeirah", "FIVE Palm Jumeirah LLC", "FIVE-PALM", "100234567890012", "Dubai", asp_primary, "SAP S/4HANA, SAP Ariba"),
            _entity(harbour, harbour_le, "Harbour Retail", "Harbour Retail LLC", "HAR-AUH", "100999000111003", "Abu Dhabi", asp_secondary, "Custom REST API"),
        ],
        "connectors": [
            _connector(five, "con_s4", "SAP S/4HANA", "SAP S/4HANA", "Both", "Connected", True, five_jv, "2026-10-14T05:50:00+00:00"),
            _connector(five, "con_ariba", "SAP Ariba", "SAP Ariba", "Inbound", "Connected", True, five_palm, "2026-10-14T05:40:00+00:00"),
            _connector(five, "con_opera", "OPERA", "OPERA", "Outbound", "Configured", False, five_jv, None),
            _connector(harbour, "con_rest", "Harbour REST", "Custom REST API", "Outbound", "Configured", True, harbour_le, "2026-10-14T07:00:00+00:00"),
        ],
        "connectorLogs": [
            {"id": "log_s4", "tenantId": five, "connectorId": "con_s4", "at": "2026-10-14T05:50:00+00:00", "level": "INFO", "message": "Existing S/4 integration heartbeat recorded. No live call was made by this module."},
            {"id": "log_ariba", "tenantId": five, "connectorId": "con_ariba", "at": "2026-10-14T05:40:00+00:00", "level": "INFO", "message": "Existing Ariba integration heartbeat recorded. No live call was made by this module."},
        ],
        "asps": [
            {
                "id": asp_primary, "tenantId": five, "aspName": "Caspian Peppol ASP", "providerType": "Accredited ASP",
                "environment": "Sandbox", "apiBaseUrl": "https://asp.example.ae/v1", "authenticationType": "OAuth2",
                "credentialReference": "secret://five/caspian-sandbox", "participantId": "0235:100234567890003",
                "peppolScheme": "0235", "outboundEndpoint": "/invoices", "inboundEndpoint": "/inbound",
                "statusEndpoint": "/status", "webhookEndpoint": "/webhooks/uae", "retryConfiguration": "3 attempts, 5 minute backoff",
                "timeoutSeconds": 30, "active": True, "operatingMode": "Test", "connectionStatus": "Configured",
                "lastCommunication": "2026-10-12T08:06:00+00:00",
            },
            {
                "id": asp_secondary, "tenantId": harbour, "aspName": "Harbour Exchange ASP", "providerType": "Accredited ASP",
                "environment": "Sandbox", "apiBaseUrl": "https://exchange.example.ae", "authenticationType": "API Key",
                "credentialReference": "secret://harbour/exchange", "participantId": "0235:100999000111003",
                "peppolScheme": "0235", "outboundEndpoint": "/submit", "inboundEndpoint": "/receive",
                "statusEndpoint": "/status", "webhookEndpoint": "/hooks", "retryConfiguration": "2 attempts",
                "timeoutSeconds": 20, "active": False, "operatingMode": "Test", "connectionStatus": "Inactive",
                "lastCommunication": None,
            },
        ],
        "mappings": [
            {
                "id": map_active, "tenantId": five, "legalEntityId": five_jv, "sourceSystem": "SAP S/4HANA",
                "documentType": "Tax Invoice", "version": 1, "status": "Active",
                "effectiveFrom": "2026-01-01", "effectiveTo": None, "lines": mapping_lines,
            },
            {
                "id": "map_opera_v1", "tenantId": five, "legalEntityId": five_jv, "sourceSystem": "OPERA",
                "documentType": "Tax Invoice", "version": 1, "status": "Inactive",
                "effectiveFrom": "2026-10-01", "effectiveTo": None,
                "lines": [
                    {**mapping_lines[0], "sourceField": "FOLIO_NO", "sourceDescription": "OPERA folio", "sampleValue": "FV-24018", "status": "MAPPED"},
                    {**mapping_lines[2], "sourceField": "HOTEL_TRN", "sampleValue": "", "status": "MISSING"},
                ],
            },
        ],
        "rules": [{**rule, "tenantId": five} for rule in rules] + [{**rule, "id": rule["id"] + "_h", "tenantId": harbour} for rule in rules],
        "flagRules": FLAG_RULES,
        "transactions": transactions,
        "exceptions": [
            {"id": "EX-1001", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00042", "severity": "ERROR", "category": "Compliance Error", "owner": "Unassigned", "ageHours": 18, "status": "Open", "resolution": "", "createdAt": "2026-10-13T14:12:20+00:00"},
            {"id": "EX-1002", "tenantId": five, "legalEntityId": five_palm, "transactionId": "UAE-2026-00043", "severity": "ERROR", "category": "ASP Error", "owner": "AP Operations", "ageHours": 40, "status": "Open", "resolution": "", "createdAt": "2026-10-11T11:08:00+00:00"},
            {"id": "EX-1003", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00044", "severity": "ERROR", "category": "Missing Data", "owner": "Unassigned", "ageHours": 4, "status": "Open", "resolution": "", "createdAt": "2026-10-14T05:40:30+00:00"},
            {"id": "EX-1004", "tenantId": five, "legalEntityId": five_palm, "transactionId": "UAE-2026-00048", "severity": "ERROR", "category": "Mapping Error", "owner": "Integration", "ageHours": 110, "status": "Open", "resolution": "", "createdAt": "2026-10-09T16:00:10+00:00"},
            {"id": "EX-1005", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00046", "severity": "WARNING", "category": "Compliance Error", "owner": "Tax", "ageHours": 90, "status": "Open", "resolution": "", "createdAt": "2026-10-10T09:01:20+00:00"},
        ],
        "reconciliation": [
            {"id": "RC-1", "tenantId": five, "legalEntityId": five_palm, "transactionId": "UAE-2026-00043", "sourceSystem": "SAP Ariba", "differenceType": "Rejected but Posted", "sourceAmount": 4600, "silaAmount": 4600, "aspAmount": 4600, "networkState": "NOT_SENT", "aribaState": "Posted", "s4State": "Not posted", "detail": "Ariba shows the invoice posted while the ASP rejected it."},
            {"id": "RC-2", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00042", "sourceSystem": "OPERA", "differenceType": "Pending Beyond SLA", "sourceAmount": 8820, "silaAmount": 8820, "aspAmount": None, "networkState": "NOT_SENT", "aribaState": "Not applicable", "s4State": "Not posted", "detail": "Compliance failure has been open beyond the configured SLA."},
            {"id": "RC-3", "tenantId": five, "legalEntityId": five_palm, "transactionId": None, "sourceSystem": "SAP S/4HANA", "differenceType": "Missing Transaction", "sourceAmount": 1500, "silaAmount": None, "aspAmount": None, "networkState": "Missing", "aribaState": "Not applicable", "s4State": "Posted", "detail": "S/4 document 940011 has no SILA transaction."},
        ],
        "archive": [
            {"id": "AR-41-SRC", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00041", "kind": "Original Source Payload", "mappingVersionId": map_active, "ruleVersion": 1, "createdAt": "2026-10-12T08:01:00+00:00"},
            {"id": "AR-41-NRM", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00041", "kind": "Normalized Payload", "mappingVersionId": map_active, "ruleVersion": 1, "createdAt": "2026-10-12T08:01:15+00:00"},
            {"id": "AR-41-FIN", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00041", "kind": "Final eInvoice Payload", "mappingVersionId": map_active, "ruleVersion": 1, "createdAt": "2026-10-12T08:01:40+00:00"},
            {"id": "AR-41-SUB", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00041", "kind": "ASP Submission", "mappingVersionId": map_active, "ruleVersion": 1, "createdAt": "2026-10-12T08:05:00+00:00"},
            {"id": "AR-41-RES", "tenantId": five, "legalEntityId": five_jv, "transactionId": "UAE-2026-00041", "kind": "ASP Response", "mappingVersionId": map_active, "ruleVersion": 1, "createdAt": "2026-10-12T08:06:00+00:00"},
        ],
        "retention": [
            {"id": "ret_five_jv", "tenantId": five, "legalEntityId": five_jv, "retentionYears": 7},
            {"id": "ret_five_palm", "tenantId": five, "legalEntityId": five_palm, "retentionYears": 7},
            {"id": "ret_harbour", "tenantId": harbour, "legalEntityId": harbour_le, "retentionYears": 5},
        ],
        "audit": [
            {"id": "aud_seed", "tenantId": five, "actor": "system", "at": "2026-10-01T00:00:00+00:00", "action": "Configuration seeded", "oldValue": None, "newValue": "Phase 1 foundation", "transactionId": None, "configurationArea": "Administration", "reason": "Initial customer configuration", "ipAddress": None, "sessionId": None},
        ],
        "assignments": [
            {"id": "asg_1", "tenantId": five, "personName": "Finance Lead", "silaRole": "Lead", "uaeRole": "UAE eInvoice Administrator"},
            {"id": "asg_2", "tenantId": five, "personName": "Integration Consultant", "silaRole": "Consultant", "uaeRole": "Integration Administrator"},
            {"id": "asg_3", "tenantId": five, "personName": "AP User", "silaRole": "User", "uaeRole": "Finance User"},
            {"id": "asg_4", "tenantId": harbour, "personName": "Harbour Lead", "silaRole": "Lead", "uaeRole": "Compliance Manager"},
        ],
        "masterData": {
            five: {
                "currencies": ["AED", "USD", "EUR"],
                "emirates": ["Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Umm Al Quwain", "Ras Al Khaimah", "Fujairah"],
                "documentTypes": ["Tax Invoice", "Credit Note", "Self-Billed Invoice", "Commercial Invoice"],
                "vatCategories": ["S", "Z", "E", "O"],
                "transactionFlags": FLAG_RULES,
            },
            harbour: {
                "currencies": ["AED"],
                "emirates": ["Abu Dhabi", "Dubai"],
                "documentTypes": ["Tax Invoice", "Credit Note"],
                "vatCategories": ["S", "Z"],
                "transactionFlags": FLAG_RULES,
            },
        },
    }


def build_seed() -> dict:
    from uae_einvoicing.onboarding import attach_phase3
    return attach_phase3(attach_phase2(_assemble_seed()))


def _entity(tenant, entity_id, name, legal_name, code, trn, emirate, asp_id, sources) -> dict:
    return {
        "id": entity_id, "tenantId": tenant, "entityName": name, "legalName": legal_name, "entityCode": code,
        "trn": trn, "tin": trn, "peppolIdentifier": f"0235:{trn}", "electronicAddress": f"0235:{trn}",
        "electronicAddressScheme": "0235", "tradeLicenceNumber": f"TL-{code}", "tradeLicenceAuthority": f"{emirate} DET",
        "vatRegistrationStatus": "Registered", "vatGroup": "", "vatGroupMember": False,
        "address": f"{name} Address", "building": "Building 1", "street": "Sheikh Zayed Road",
        "city": emirate, "emirate": emirate, "country": "AE", "postalCode": "00000",
        "defaultCurrency": "AED", "sourceSystems": sources, "aspId": asp_id,
        "effectiveDate": "2026-01-01", "status": "Active",
    }


def _connector(tenant, cid, name, system_type, direction, status, active, legal_entity, last_success) -> dict:
    return {
        "id": cid, "tenantId": tenant, "connectorName": name, "systemType": system_type, "direction": direction,
        "baseUrl": "", "authenticationType": "Existing SILA connection" if system_type in {"SAP S/4HANA", "SAP Ariba"} else "OAuth2",
        "credentialReference": f"secret://{tenant}/{cid}", "headers": {}, "timeoutSeconds": 30,
        "retryPolicy": "3 attempts", "deliveryMode": "Polling", "frequency": "15 minutes",
        "legalEntityId": legal_entity, "active": active, "lastConnection": last_success,
        "lastSuccess": last_success, "connectionStatus": status,
    }


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(__file__).resolve().parent.parent / "data" / "uae_einvoicing.json"
        self._lock = threading.Lock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get("schemaVersion") != 3:
                self.data = build_seed()
                self._write()
        else:
            self.data = build_seed()
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str))

    def _audit(self, tenant_id: str, actor: str, action: str, area: str, old: Any, new: Any, reason: str, request_meta: dict, transaction_id: str | None = None) -> None:
        self.data["audit"].insert(0, {
            "id": _id("aud"),
            "tenantId": tenant_id,
            "actor": actor,
            "at": _now(),
            "action": action,
            "oldValue": old,
            "newValue": new,
            "transactionId": transaction_id,
            "configurationArea": area,
            "reason": reason,
            "ipAddress": request_meta.get("ip"),
            "sessionId": request_meta.get("session"),
        })

    def tenants(self) -> list[dict]:
        return self.data["tenants"]

    def require(self, role: str, permission: str) -> None:
        if not allows(role, permission):
            raise PermissionError(f"{role} cannot {permission}")

    def command_center(self, tenant_id: str) -> dict:
        txns = [t for t in self.data["transactions"] if t["tenantId"] == tenant_id]
        def count(pred) -> int:
            return len([t for t in txns if pred(t)])
        kpis = [
            ("Total Transactions", len(txns)),
            ("Successful", count(lambda t: t["lifecycleStatus"] in {"ASP_ACCEPTED", "NETWORK_ACCEPTED", "DELIVERED", "ERP_POSTED", "RECONCILED", "CLOSED"})),
            ("Pending", count(lambda t: t["lifecycleStatus"] in {"RECEIVED", "NORMALIZED", "READY_FOR_ASP"})),
            ("Processing", count(lambda t: t["lifecycleStatus"] == "SUBMITTED")),
            ("Rejected", count(lambda t: t["lifecycleStatus"] in {"ASP_REJECTED", "NETWORK_REJECTED"})),
            ("Compliance Failed", count(lambda t: t["lifecycleStatus"] == "COMPLIANCE_FAILED")),
            ("ASP Failed", count(lambda t: t["lifecycleStatus"] == "ASP_REJECTED" or t["aspStatus"] == "ASP_REJECTED")),
            ("Integration Failed", count(lambda t: t["lifecycleStatus"] == "MAPPING_FAILED")),
            ("Needs Attention", count(lambda t: t["lifecycleStatus"] in {"COMPLIANCE_FAILED", "MAPPING_FAILED", "ASP_REJECTED", "NETWORK_REJECTED"})),
        ]
        directions = ["Outbound AR", "Inbound AP", "Credit Notes", "Self-Billing"]
        connectors = [c for c in self.data["connectors"] if c["tenantId"] == tenant_id]
        sources = []
        for connector in connectors:
            related = [t for t in txns if t["sourceSystem"] == connector["systemType"]]
            failures = len([t for t in related if t["lifecycleStatus"] in {"MAPPING_FAILED", "COMPLIANCE_FAILED", "ASP_REJECTED", "NETWORK_REJECTED"}])
            pending = len([t for t in related if t["lifecycleStatus"] in {"RECEIVED", "NORMALIZED", "READY_FOR_ASP", "SUBMITTED"}])
            success = len(related) - failures
            sources.append({
                "name": connector["connectorName"],
                "systemType": connector["systemType"],
                "connectionStatus": connector["connectionStatus"],
                "lastSuccess": connector.get("lastSuccess"),
                "transactionsToday": len([t for t in related if str(t["invoiceDate"]).startswith("2026-10-14")]),
                "successPercent": 0 if not related else round(100 * success / len(related)),
                "failures": failures,
                "pending": pending,
            })
        asps = [a for a in self.data["asps"] if a["tenantId"] == tenant_id]
        active = next((a for a in asps if a["active"]), asps[0] if asps else None)
        submitted = count(lambda t: t["aspStatus"] in {"SUBMITTED", "ASP_ACCEPTED", "ASP_REJECTED"})
        asp_card = None
        if active:
            asp_card = {
                "configuredAsp": active["aspName"],
                "providerType": active["providerType"],
                "connectionStatus": active["connectionStatus"],
                "lastCommunication": active.get("lastCommunication"),
                "operatingMode": active["operatingMode"],
                "submitted": submitted,
                "accepted": count(lambda t: t["aspStatus"] == "ASP_ACCEPTED" or t["lifecycleStatus"] in {"ASP_ACCEPTED", "NETWORK_ACCEPTED", "DELIVERED", "ERP_POSTED", "RECONCILED", "CLOSED"}),
                "rejected": count(lambda t: t["aspStatus"] == "ASP_REJECTED"),
                "pending": count(lambda t: t["lifecycleStatus"] in {"READY_FOR_ASP", "SUBMITTED"}),
                "configuredCount": len(asps),
            }
        return {
            "kpis": [{"label": label, "value": value} for label, value in kpis],
            "split": [{"label": label, "value": count(lambda t, label=label: t["direction"] == label)} for label in directions],
            "sources": sources,
            "asp": asp_card,
            "compliance": compliance_health(txns),
            "gate": ["SOURCE RECEIVED", "NORMALIZED", "MAPPED", "ENRICHED", "CLASSIFIED", "VALIDATED", "COMPLIANT", "READY FOR ASP"],
            "phase2": self._phase2_summary(tenant_id, txns),
            "phase3": self._phase3_summary(tenant_id, txns),
        }

    def list_transactions(self, tenant_id: str, filters: dict) -> list[dict]:
        rows = []
        for txn in self.data["transactions"]:
            if txn["tenantId"] != tenant_id:
                continue
            if filters.get("direction") and txn["direction"] != filters["direction"]:
                continue
            if filters.get("documentType") and txn["documentType"] != filters["documentType"]:
                continue
            if filters.get("sourceSystem") and txn["sourceSystem"] != filters["sourceSystem"]:
                continue
            if filters.get("legalEntityId") and txn["legalEntityId"] != filters["legalEntityId"]:
                continue
            if filters.get("lifecycleStatus") and txn["lifecycleStatus"] != filters["lifecycleStatus"]:
                continue
            query = (filters.get("q") or "").lower()
            if query and query not in " ".join([
                txn["id"], txn["invoiceNumber"], txn["supplier"], txn["buyer"], txn["sourceSystem"],
            ]).lower():
                continue
            rows.append(self._public_txn(txn))
        return rows

    def _public_txn(self, txn: dict) -> dict:
        hidden = {"canonical", "validationResults", "validationStages", "timeline", "classification", "workbench", "sourcePayload"}
        return {key: value for key, value in txn.items() if key not in hidden}

    def get_transaction(self, tenant_id: str, txn_id: str) -> dict:
        txn = self._find("transactions", txn_id, tenant_id)
        entity = next((item for item in self.data["legalEntities"] if item["id"] == txn.get("legalEntityId")), None)
        history = [event for event in self.data["audit"] if event.get("transactionId") == txn_id and event["tenantId"] == tenant_id]
        return {**txn, "legalEntity": entity, "history": history}

    def correct_transaction(self, tenant_id: str, txn_id: str, path: str, value: Any, reason: str, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "transactions.correct")
        txn = self._find("transactions", txn_id, tenant_id)
        source_payload = deepcopy(txn.get("sourcePayload"))
        txn.setdefault("corrections", []).append({
            "path": path,
            "value": value,
            "reason": reason,
            "actor": actor,
            "at": _now(),
        })
        self._rerun(txn, "full")
        if source_payload is not None:
            txn["sourcePayload"] = source_payload
        self._audit(tenant_id, actor, "Transaction correction", "Transactions", {path: "previous canonical"}, {path: value}, reason, meta, txn_id)
        txn["timeline"].append({"at": _now(), "label": f"Corrected {path}"})
        txn["updatedAt"] = _now()
        self._write()
        return self.get_transaction(tenant_id, txn_id)

    def reprocess(self, tenant_id: str, txn_id: str, actor: str, meta: dict, mode: str = "full") -> dict:
        permission = {
            "revalidate": "transactions.revalidate",
            "remap": "transactions.remap",
            "reenrich": "transactions.reenrich",
        }.get(mode, "transactions.reprocess")
        self.require(meta["role"], permission)
        txn = self._find("transactions", txn_id, tenant_id)
        previous = txn["lifecycleStatus"]
        same_id = txn["id"]
        self._rerun(txn, mode if mode in {"full", "revalidate", "remap", "reenrich"} else "full")
        if txn["id"] != same_id:
            txn["id"] = same_id
        txn["timeline"].append({"at": _now(), "label": f"Reprocessed ({mode})"})
        txn["updatedAt"] = _now()
        self._audit(tenant_id, actor, "Reprocess", "Transactions", previous, txn["lifecycleStatus"], "Authorised reprocess", meta, txn_id)
        self._write()
        return self.get_transaction(tenant_id, txn_id)

    def override_flag(self, tenant_id: str, txn_id: str, code: str, value: bool, reason: str, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "transactions.override")
        txn = self._find("transactions", txn_id, tenant_id)
        found = next((item for item in txn["classification"] if item["code"] == code), None)
        if not found:
            raise KeyError(code)
        old = found["derived"]
        txn.setdefault("flagOverrides", {})[code] = value
        found["derived"] = value
        found["manualOverride"] = True
        self._audit(tenant_id, actor, "Classification override", "Transactions", {code: old}, {code: value}, reason, meta, txn_id)
        txn["timeline"].append({"at": _now(), "label": f"Override {code}"})
        self._write()
        return self.get_transaction(tenant_id, txn_id)

    def _rerun(self, txn: dict, mode: str = "full") -> None:
        run_pipeline(txn, context_for(self.data, txn), mode=mode)

    def _find(self, collection: str, item_id: str, tenant_id: str) -> dict:
        for item in self.data[collection]:
            if item["id"] == item_id and item.get("tenantId") == tenant_id:
                return item
        raise KeyError(item_id)

    def scoped(self, collection: str, tenant_id: str) -> list[dict]:
        return [item for item in self.data[collection] if item.get("tenantId") == tenant_id]

    def save_legal_entity(self, tenant_id: str, payload: dict, actor: str, meta: dict, entity_id: str | None = None) -> dict:
        self.require(meta["role"], "legal_entities.write")
        payload = {**payload, "tenantId": tenant_id}
        payload.pop("secret", None)
        if entity_id:
            current = self._find("legalEntities", entity_id, tenant_id)
            old = deepcopy(current)
            candidate = deepcopy(current)
            candidate.update(payload)
            candidate["id"] = entity_id
        else:
            old = None
            current = None
            candidate = {**payload, "id": _id("le")}
        if candidate.get("status") == "Production":
            profile = next((item for item in self.data.get("profiles", []) if item.get("status") == "ACTIVE"), None)
            readiness = entity_readiness(candidate, profile)
            candidate["readiness"] = readiness
            if not readiness["productionAllowed"]:
                raise PermissionError("Production activation is blocked until " + ", ".join(readiness["missing"]) + " is resolved")
        if current is not None:
            current.clear()
            current.update(candidate)
            saved = current
        else:
            saved = candidate
            self.data["legalEntities"].append(saved)
            self.data["retention"].append({"id": _id("ret"), "tenantId": tenant_id, "legalEntityId": saved["id"], "retentionYears": 5})
        self._audit(tenant_id, actor, "Legal entity saved", "Legal Entities", old, saved["entityName"], "Configuration change", meta)
        self._write()
        return saved

    def save_connector(self, tenant_id: str, payload: dict, actor: str, meta: dict, connector_id: str | None = None) -> dict:
        self.require(meta["role"], "connectors.write")
        secret = payload.pop("secret", None)
        payload.pop("credentialValue", None)
        if connector_id:
            current = self._find("connectors", connector_id, tenant_id)
        else:
            current = {"id": _id("con"), "tenantId": tenant_id, "connectionStatus": "Configured", "lastConnection": None, "lastSuccess": None}
            self.data["connectors"].append(current)
        for key, value in payload.items():
            if key in {"id", "tenantId", "secret", "credentialValue"}:
                continue
            current[key] = value
        if secret:
            current["credentialReference"] = f"secret://{tenant_id}/{current['id']}"
        current["tenantId"] = tenant_id
        self._audit(tenant_id, actor, "Connector saved", "Integration Hub", None, current["connectorName"], "Credential stored as a reference only" if secret else "Configuration change", meta)
        self._write()
        return self._public_connector(current)

    def _public_connector(self, connector: dict) -> dict:
        safe = deepcopy(connector)
        safe.pop("secret", None)
        safe.pop("credentialValue", None)
        if safe.get("credentialReference"):
            safe["credentialReference"] = safe["credentialReference"]
        return safe

    def connector_action(self, tenant_id: str, connector_id: str, action: str, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "connectors.write")
        connector = self._find("connectors", connector_id, tenant_id)
        if action == "test":
            missing = [name for name in ("connectorName", "systemType", "credentialReference") if not connector.get(name)]
            status = "Configured" if not missing else "Incomplete"
            message = "Phase 1 test recorded configuration only. No live source call was made."
            if missing:
                message = "Missing " + ", ".join(missing)
            connector["lastConnection"] = _now()
            connector["connectionStatus"] = status if connector["active"] else connector["connectionStatus"]
            if not missing:
                connector["lastSuccess"] = _now()
            self.data["connectorLogs"].insert(0, {"id": _id("log"), "tenantId": tenant_id, "connectorId": connector_id, "at": _now(), "level": "INFO" if not missing else "WARNING", "message": message})
        elif action == "enable":
            connector["active"] = True
            connector["connectionStatus"] = "Configured"
        elif action == "disable":
            connector["active"] = False
            connector["connectionStatus"] = "Inactive"
        self._audit(tenant_id, actor, f"Connector {action}", "Integration Hub", None, connector["connectorName"], "No live call", meta)
        self._write()
        return self._public_connector(connector)

    def save_asp(self, tenant_id: str, payload: dict, actor: str, meta: dict, asp_id: str | None = None) -> dict:
        self.require(meta["role"], "asp.write")
        secret = payload.pop("secret", None)
        if asp_id:
            current = self._find("asps", asp_id, tenant_id)
        else:
            current = {"id": _id("asp"), "tenantId": tenant_id, "connectionStatus": "Configured", "lastCommunication": None, "active": False}
            self.data["asps"].append(current)
        current.update({key: value for key, value in payload.items() if key not in {"id", "tenantId"}})
        current["tenantId"] = tenant_id
        if secret:
            current["credentialReference"] = f"secret://{tenant_id}/{current['id']}"
        self._audit(tenant_id, actor, "ASP saved", "ASP Configuration", None, current["aspName"], "Provider remains configuration-driven", meta)
        self._write()
        return current

    def asp_action(self, tenant_id: str, asp_id: str, action: str, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "asp.write")
        asp = self._find("asps", asp_id, tenant_id)
        if action == "test":
            asp["lastCommunication"] = _now()
            asp["connectionStatus"] = "Configured" if asp.get("apiBaseUrl") and asp.get("credentialReference") else "Incomplete"
        elif action == "activate":
            for other in self.data["asps"]:
                if other["tenantId"] == tenant_id:
                    other["active"] = other["id"] == asp_id
            asp["connectionStatus"] = "Configured"
        elif action == "deactivate":
            asp["active"] = False
            asp["connectionStatus"] = "Inactive"
        self._audit(tenant_id, actor, f"ASP {action}", "ASP Configuration", None, asp["aspName"], "Live submission is not enabled in phase 1", meta)
        self._write()
        return asp

    def save_mapping(self, tenant_id: str, payload: dict, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "mappings.write")
        mapping = {
            "id": _id("map"),
            "tenantId": tenant_id,
            "legalEntityId": payload["legalEntityId"],
            "sourceSystem": payload["sourceSystem"],
            "documentType": payload["documentType"],
            "version": 1,
            "status": "Inactive",
            "effectiveFrom": payload.get("effectiveFrom"),
            "effectiveTo": payload.get("effectiveTo"),
            "lines": payload.get("lines") or [],
        }
        self.data["mappings"].append(mapping)
        self._audit(tenant_id, actor, "Mapping created", "Field Mapping", None, mapping["id"], "Configuration", meta)
        self._write()
        return mapping

    def mapping_action(self, tenant_id: str, mapping_id: str, action: str, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "mappings.write")
        mapping = self._find("mappings", mapping_id, tenant_id)
        if action == "clone":
            cloned = deepcopy(mapping)
            cloned["id"] = _id("map")
            cloned["status"] = "Inactive"
            cloned["version"] = mapping["version"]
            self.data["mappings"].append(cloned)
            saved = cloned
        elif action == "version":
            versioned = deepcopy(mapping)
            versioned["id"] = _id("map")
            versioned["version"] = mapping["version"] + 1
            versioned["status"] = "Inactive"
            self.data["mappings"].append(versioned)
            saved = versioned
        elif action == "activate":
            for other in self.data["mappings"]:
                if other["tenantId"] == tenant_id and other["legalEntityId"] == mapping["legalEntityId"] and other["sourceSystem"] == mapping["sourceSystem"] and other["documentType"] == mapping["documentType"]:
                    other["status"] = "Inactive"
            mapping["status"] = "Active"
            saved = mapping
        elif action == "deactivate":
            mapping["status"] = "Inactive"
            saved = mapping
        else:
            raise KeyError(action)
        self._audit(tenant_id, actor, f"Mapping {action}", "Field Mapping", mapping_id, saved["id"], "Versioned configuration", meta)
        self._write()
        return saved

    def save_rule(self, tenant_id: str, payload: dict, actor: str, meta: dict, rule_row_id: str | None = None) -> dict:
        self.require(meta["role"], "rules.write")
        if rule_row_id:
            current = self._find("rules", rule_row_id, tenant_id)
            old = current["version"]
            current.update(payload)
            current["id"] = rule_row_id
            current["tenantId"] = tenant_id
            saved = current
        else:
            old = None
            saved = {**payload, "id": _id("rule"), "tenantId": tenant_id}
            self.data["rules"].append(saved)
        self._audit(tenant_id, actor, "Rule saved", "Compliance Rules", old, saved.get("ruleId"), "Regulatory rule configuration", meta)
        self._write()
        return saved

    def exception_action(self, tenant_id: str, exception_id: str, action: str, actor: str, meta: dict, owner: str | None = None, resolution: str | None = None) -> dict:
        self.require(meta["role"], "exceptions.manage")
        item = self._find("exceptions", exception_id, tenant_id)
        if action == "ignore" and item["severity"] == "ERROR":
            raise PermissionError("ERROR-level compliance failures cannot be ignored")
        previous = item["status"]
        if action == "assign":
            item["owner"] = owner or item["owner"]
            item["status"] = "Assigned"
        elif action == "correct":
            item["status"] = "Correcting"
        elif action in {"retry", "reprocess"}:
            item["status"] = "Reprocessing"
            txn = next((row for row in self.data["transactions"] if row["id"] == item["transactionId"]), None)
            if txn:
                self._rerun(txn)
        elif action == "ignore":
            item["status"] = "Ignored"
        elif action == "escalate":
            item["status"] = "Escalated"
        elif action == "close":
            if item["severity"] == "ERROR":
                txn = next((row for row in self.data["transactions"] if row["id"] == item["transactionId"]), None)
                if txn and txn["lifecycleStatus"] == "COMPLIANCE_FAILED":
                    raise PermissionError("Resolve the compliance failure before closing the exception")
            item["status"] = "Closed"
        item["resolution"] = resolution or item.get("resolution") or ""
        self._audit(tenant_id, actor, f"Exception {action}", "Exceptions", previous, item["status"], resolution or action, meta, item["transactionId"])
        self._write()
        return item

    def save_retention(self, tenant_id: str, legal_entity_id: str, years: int, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "legal_entities.write")
        policy = next((item for item in self.data["retention"] if item["tenantId"] == tenant_id and item["legalEntityId"] == legal_entity_id), None)
        if not policy:
            policy = {"id": _id("ret"), "tenantId": tenant_id, "legalEntityId": legal_entity_id, "retentionYears": years}
            self.data["retention"].append(policy)
        old = policy["retentionYears"]
        policy["retentionYears"] = years
        self._audit(tenant_id, actor, "Retention updated", "Archive", old, years, "Customer retention configuration", meta)
        self._write()
        return policy

    def save_master(self, tenant_id: str, payload: dict, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "master_data.write")
        self.data["masterData"][tenant_id] = payload
        self._audit(tenant_id, actor, "Master data updated", "Master Data", None, "updated", "Configuration", meta)
        self._write()
        return payload

    def save_assignment(self, tenant_id: str, payload: dict, actor: str, meta: dict) -> dict:
        if meta["role"] != "UAE eInvoice Administrator":
            raise PermissionError("Only a UAE eInvoice Administrator can assign module roles")
        if payload.get("uaeRole") not in UAE_ROLES:
            raise ValueError("Unknown UAE role")
        if payload.get("silaRole") not in SILA_ROLE_DEFAULTS:
            raise ValueError("Role must be an existing SILA Fatoora role")
        assignment = {
            "id": _id("asg"),
            "tenantId": tenant_id,
            "personName": payload["personName"],
            "silaRole": payload["silaRole"],
            "uaeRole": payload["uaeRole"],
        }
        self.data["assignments"].append(assignment)
        self._audit(tenant_id, actor, "Role assigned", "Administration", None, payload["uaeRole"], "Mapped onto existing SILA role", meta)
        self._write()
        return assignment

    def _phase2_summary(self, tenant_id: str, txns: list[dict]) -> dict:
        today = [txn for txn in txns if str(txn.get("invoiceDate") or "").startswith("2026-10-14")]
        counts: dict[str, int] = {}
        for txn in txns:
            for result in txn.get("validationResults") or []:
                if result.get("status") == "FAILED" and result.get("severity") == "ERROR":
                    label = result.get("ruleName") or result.get("field") or "Rule"
                    counts[label] = counts.get(label, 0) + 1
        top = [{"reason": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: -item[1])[:7]]
        by_date: dict[str, dict[str, int]] = {}
        for txn in txns:
            day = str(txn.get("invoiceDate") or "")[:10]
            bucket = by_date.setdefault(day, {"pass": 0, "fail": 0})
            if txn.get("complianceStatus") == "NON-COMPLIANT" or txn.get("lifecycleStatus") in {"COMPLIANCE_FAILED", "MAPPING_FAILED"}:
                bucket["fail"] += 1
            else:
                bucket["pass"] += 1
        trend = []
        for day, bucket in sorted(by_date.items()):
            total = bucket["pass"] + bucket["fail"] or 1
            trend.append({"date": day, "passPercent": round(100 * bucket["pass"] / total), "failurePercent": round(100 * bucket["fail"] / total)})
        entities = self.legal_entity_readiness(tenant_id)
        mappings = self.mapping_readiness(tenant_id)
        ready_entities = len([item for item in entities if item["status"] == "READY"])
        ready_sources = len([item for item in mappings if item["productionReadiness"] == "READY"])
        return {
            "complianceToday": {
                "received": len(today),
                "passed": len([txn for txn in today if txn.get("complianceStatus") == "COMPLIANT"]),
                "failed": len([txn for txn in today if txn.get("complianceStatus") == "NON-COMPLIANT" or txn.get("lifecycleStatus") == "COMPLIANCE_FAILED"]),
                "warnings": len([txn for txn in today if txn.get("complianceStatus") == "WARNING"]),
                "readyForAsp": len([txn for txn in today if txn.get("lifecycleStatus") == "READY_FOR_ASP"]),
            },
            "topFailures": top,
            "trend": trend,
            "readiness": {
                "legalEntities": 0 if not entities else round(100 * ready_entities / len(entities)),
                "sourceSystems": 0 if not mappings else round(100 * ready_sources / len(mappings)),
                "mappings": 0 if not mappings else round(sum(item["mandatoryPercent"] for item in mappings) / len(mappings)),
                "rules": "ACTIVE",
                "masterData": 100,
            },
        }

    def mapping_readiness(self, tenant_id: str) -> list[dict]:
        rows = []
        for connector in self.scoped("connectors", tenant_id):
            versions = [
                item for item in self.scoped("mappings", tenant_id)
                if item["sourceSystem"] == connector["systemType"] and item.get("legalEntityId") == connector.get("legalEntityId")
            ]
            chosen = next((item for item in versions if item["status"] == "Active"), versions[-1] if versions else None)
            lines = [] if not chosen else chosen.get("lines") or []
            mandatory = [line for line in lines if line.get("requirement") == "MANDATORY"]
            mapped = [line for line in mandatory if line.get("status") == "MAPPED"]
            conditional = [line for line in lines if line.get("requirement") == "CONDITIONAL"]
            conditional_mapped = [line for line in conditional if line.get("status") == "MAPPED"]
            percent = 0 if not chosen else (100 if not mandatory else round(100 * len(mapped) / len(mandatory)))
            conditional_percent = 0 if not chosen else (100 if not conditional else round(100 * len(conditional_mapped) / len(conditional)))
            missing = [line for line in lines if line.get("status") != "MAPPED"]
            coverage = 0 if not lines else round(100 * len([line for line in lines if line.get("status") == "MAPPED"]) / len(lines))
            ready = bool(chosen) and chosen.get("status") == "Active" and connector.get("active") and percent == 100 and connector.get("connectionType") not in {"MOCK", "FILE"}
            rows.append({
                "sourceSystem": connector["systemType"],
                "connectorName": connector["connectorName"],
                "legalEntityId": connector.get("legalEntityId"),
                "mandatoryPercent": percent,
                "conditionalPercent": conditional_percent,
                "missing": len(missing),
                "missingFields": [{"sourceField": line.get("sourceField"), "targetField": line.get("targetField"), "requirement": line.get("requirement")} for line in missing],
                "rules": "ACTIVE" if chosen else "NOT CONFIGURED",
                "productionReadiness": "READY" if ready else "NOT READY",
                "productionPercent": coverage if ready else percent,
                "masterDataPercent": 100 if chosen else 0,
            })
        return rows

    def legal_entity_readiness(self, tenant_id: str) -> list[dict]:
        profile = next((item for item in self.data.get("profiles", []) if item.get("status") == "ACTIVE"), None)
        rows = []
        for entity in self.scoped("legalEntities", tenant_id):
            readiness = entity_readiness(entity, profile)
            mappings = [item for item in self.mapping_readiness(tenant_id) if item.get("legalEntityId") == entity["id"]]
            mapping_percent = 0 if not mappings else round(sum(item["mandatoryPercent"] for item in mappings) / len(mappings))
            rows.append({
                "id": entity["id"],
                "entity": entity["entityName"],
                "trn": entity.get("trn"),
                "tin": entity.get("tin"),
                "electronicAddress": entity.get("electronicAddress"),
                "asp": entity.get("aspId"),
                "sourceSystems": entity.get("sourceSystems"),
                "mappingPercent": mapping_percent,
                "compliancePercent": readiness["score"],
                "masterDataPercent": 100 if entity.get("vatRegistrationStatus") else 0,
                "readiness": readiness["label"],
                "status": readiness["status"],
                "missing": readiness["missing"],
                "productionAllowed": readiness["productionAllowed"],
            })
        return rows

    def regulatory_change(self, tenant_id: str) -> dict:
        profiles = [item for item in self.data.get("profiles", []) if item.get("tenantId") in {"global", tenant_id}]
        active = next((item for item in profiles if item["status"] == "ACTIVE"), None)
        draft = next((item for item in profiles if item["status"] == "DRAFT"), None)
        impacted = [txn["id"] for txn in self.scoped("transactions", tenant_id) if not active or txn.get("complianceProfileId") == active.get("id")]
        return {
            "active": active,
            "draft": draft,
            "rulesAdded": ["PINT-AE specification 1.1 is prepared in draft and is not active." ] if draft else [],
            "rulesChanged": [] if not draft or not active or draft.get("specificationId") == active.get("specificationId") else ["Specification identifier"],
            "rulesRetired": [],
            "mappingsImpacted": [item["id"] for item in self.scoped("mappings", tenant_id)],
            "entitiesImpacted": [item["id"] for item in self.scoped("legalEntities", tenant_id)],
            "transactionsImpacted": impacted,
        }

    def activate_profile(self, tenant_id: str, profile_id: str, actor: str, meta: dict) -> dict:
        if meta["role"] not in {"UAE eInvoice Administrator", "Compliance Administrator"}:
            raise PermissionError("Only a compliance administrator can activate a regulatory profile")
        draft = next((item for item in self.data["profiles"] if item["id"] == profile_id), None)
        if not draft:
            raise KeyError(profile_id)
        previous = next((item for item in self.data["profiles"] if item["status"] == "ACTIVE"), None)
        old_version = None if not previous else previous["version"]
        if previous and previous["id"] != draft["id"]:
            previous["status"] = "SUPERSEDED"
        draft["status"] = "ACTIVE"
        self._audit(tenant_id, actor, "Compliance profile activated", "Compliance Profile", old_version, draft["version"], "Draft activation does not rewrite historical transaction versions", meta)
        self._write()
        return draft

    def simulate(self, tenant_id: str, payload: dict, actor: str, meta: dict) -> dict:
        self.require(meta["role"], "simulator.run")
        before = len(self.data["transactions"])
        legal_entity_id = payload.get("legalEntityId")
        source_system = payload.get("sourceSystem")
        document_type = payload.get("documentType") or "Tax Invoice"
        mapping = next((item for item in self.scoped("mappings", tenant_id) if item["sourceSystem"] == source_system and item["legalEntityId"] == legal_entity_id and item["documentType"] == document_type), None)
        if mapping is None:
            mapping = next((item for item in self.scoped("mappings", tenant_id) if item["sourceSystem"] == source_system), None)
        outputs = execute_mapping(payload.get("payload") or {}, [] if not mapping else mapping.get("lines") or [], self.data.get("codeLists") or {})
        txn = {
            "id": "SIMULATION",
            "tenantId": tenant_id,
            "legalEntityId": legal_entity_id,
            "documentType": document_type,
            "invoiceNumber": "SIMULATION",
            "sourceSystem": source_system,
            "invoiceDate": payload.get("invoiceDate") or "2026-10-14",
            "canonical": {},
            "timeline": [{"at": _now(), "label": "Simulation received"}],
            "corrections": [],
            "mappingVersionId": None if not mapping else mapping["id"],
        }
        from uae_einvoicing.pipeline import apply_mapping_outputs
        apply_mapping_outputs(txn["canonical"], outputs)
        run_pipeline(txn, context_for(self.data, txn), mode="revalidate")
        if len(self.data["transactions"]) != before:
            raise RuntimeError("Simulation must not create a transaction")
        self._audit(tenant_id, actor, "Compliance simulation", "Simulator", None, document_type, "SIMULATION ONLY", meta)
        self._write()
        return {
            "label": "SIMULATION ONLY",
            "source": payload.get("payload") or {},
            "mappingResults": outputs,
            "canonical": txn["canonical"],
            "compliance": txn.get("workbench"),
            "score": txn.get("complianceScore"),
            "results": txn.get("validationResults"),
            "lifecycleStatus": txn.get("lifecycleStatus"),
            "transactionTypeCode": (txn.get("transactionType") or {}).get("code"),
            "scenarios": (txn.get("transactionType") or {}).get("scenarios"),
            "persisted": False,
        }

    def _phase3_summary(self, tenant_id: str, txns: list[dict]) -> dict:
        from uae_einvoicing.onboarding import source_code
        cards = []
        for connector in self.scoped("connectors", tenant_id):
            code = source_code(connector["systemType"])
            related = [txn for txn in txns if txn.get("sourceCode") == code or txn.get("sourceSystem") == connector["systemType"]]
            cards.append({
                "source": connector["connectorName"],
                "code": code,
                "connection": connector.get("connectionStatus"),
                "lastSync": connector.get("lastSuccess") or connector.get("lastPoll"),
                "receivedToday": len([txn for txn in related if str(txn.get("invoiceDate") or "").startswith("2026-10-14")]),
                "processed": len(related),
                "passed": len([txn for txn in related if txn.get("lifecycleStatus") in {"READY_FOR_ASP", "COMPLIANCE_PASSED", "RECONCILED", "SUBMITTED"} or txn.get("complianceStatus") == "COMPLIANT"]),
                "failed": len([txn for txn in related if txn.get("lifecycleStatus") in {"COMPLIANCE_FAILED", "MAPPING_FAILED", "ENTITY_RESOLUTION_FAILED"}]),
                "exceptions": len([item for item in self.scoped("exceptions", tenant_id) if item.get("sourceSystem") == connector["systemType"] and item.get("status") not in {"Closed", "Ignored"}]),
            })
        return {"sources": cards}

    def ingest_request(self, tenant_id: str, envelope: dict, actor: str, meta: dict) -> dict:
        from uae_einvoicing.onboarding import ingest
        self.require(meta["role"], "ingest.run")
        fixture = envelope.get("fixture")
        if fixture:
            library = self.data.get("fixtureLibrary") or {}
            chosen = library.get(fixture)
            queued = next((item for item in self.data.get("fixtures") or [] if item.get("fixture") == fixture), None)
            owner = (chosen or {}).get("tenantId") or (queued or {}).get("tenantId")
            if chosen is None and queued is None:
                raise KeyError(fixture)
            if owner and owner != tenant_id:
                raise PermissionError("Fixture is not available for this tenant")
            envelope = {**(chosen or {}), **(queued or {})}
            envelope.pop("queue", None)
            envelope.pop("tenantId", None)
            envelope.pop("connectorId", None)
            envelope.pop("id", None)
        before = len(self.data["transactions"])
        result = ingest(self.data, tenant_id, envelope)
        txn = result.get("transaction")
        self._audit(tenant_id, actor, "Source ingestion", "Transactions", None, None if not txn else txn["id"], result.get("lifecycleStatus") or result.get("duplicate") or "Ingested", meta, None if not txn else txn["id"])
        self._write()
        public = None if not txn else self.get_transaction(tenant_id, txn["id"])
        return {
            "created": result.get("created"),
            "idempotent": result.get("idempotent"),
            "duplicate": result.get("duplicate"),
            "regulatoryInvoiceCreated": result.get("regulatoryInvoiceCreated", bool(txn and txn.get("regulatory"))),
            "transactionId": result.get("transactionId") or (None if not txn else txn["id"]),
            "lifecycleStatus": result.get("lifecycleStatus"),
            "transactionCount": len(self.data["transactions"]),
            "countUnchanged": len(self.data["transactions"]) == before or result.get("idempotent") or result.get("created") is False,
            "transaction": public,
            "aspSubmitted": False,
        }

    def run_connector(self, tenant_id: str, connector_id: str, actor: str, meta: dict) -> dict:
        from uae_einvoicing.onboarding import run_source_connector
        self.require(meta["role"], "ingest.run")
        connector = self._find("connectors", connector_id, tenant_id)
        if connector.get("systemType") in {"OPERA", "OPERA_KIT"}:
            outcome = run_source_connector(self.data, connector)
        else:
            message = "Existing SILA connector. No live call was made by the UAE module."
            outcome = {"log": {"message": message, "result": "OK"}, "results": [], "implementation": "ExistingSilaConnector"}
            self.data["connectorLogs"].insert(0, {"id": _id("log"), "tenantId": tenant_id, "connectorId": connector_id, "at": _now(), "level": "INFO", "message": message, "endpoint": connector.get("baseUrl") or "", "httpStatus": None, "result": "OK"})
        self._audit(tenant_id, actor, "Connector run", "Integration Hub", None, connector["connectorName"], outcome["log"].get("message") or "", meta)
        self._write()
        return {
            "implementation": outcome["implementation"],
            "message": outcome["log"].get("message"),
            "count": len(outcome["results"]),
            "transactions": [
                {"id": item.get("transaction", {}).get("id") or item.get("transactionId"), "lifecycleStatus": item.get("lifecycleStatus"), "created": item.get("created")}
                for item in outcome["results"]
            ],
            "aspSubmitted": False,
        }

    def onboarding_views(self, tenant_id: str) -> dict:
        from uae_einvoicing.onboarding import entity_onboarding_status, implementation_readiness, opera_gap, sap_gap_register
        entities = []
        for entity in self.scoped("legalEntities", tenant_id):
            status = entity_onboarding_status(entity, self.data)
            entities.append({
                "id": entity["id"],
                "entity": entity.get("entityName"),
                "code": entity.get("entityCode"),
                "trn": entity.get("trn") or "CONFIGURATION REQUIRED",
                "tin": entity.get("tin") or "CONFIGURATION REQUIRED",
                "vatGroup": entity.get("vatGroup") or "—",
                "electronicAddress": entity.get("electronicAddress") or "CONFIGURATION REQUIRED",
                "sap": entity.get("sapCompanyCode") or "CONFIGURATION REQUIRED",
                "opera": entity.get("operaPropertyCode") or "CONFIGURATION REQUIRED",
                "ariba": entity.get("aribaEntityMapping") or "CONFIGURATION REQUIRED",
                "mapping": status["status"],
                "compliance": "ACTIVE" if any(item.get("status") == "ACTIVE" for item in self.data.get("profiles") or []) else "CONFIGURATION REQUIRED",
                "asp": "FUTURE",
                "readiness": status["status"],
                "missing": status["missing"],
            })
        properties = []
        for prop in [item for item in self.data.get("properties", []) if item.get("tenantId") == tenant_id]:
            entity = next((item for item in self.data["legalEntities"] if item["id"] == prop.get("legalEntityId")), None)
            related = [txn for txn in self.scoped("transactions", tenant_id) if txn.get("propertyCode") == prop.get("propertyCode")]
            connector = next((item for item in self.scoped("connectors", tenant_id) if item.get("propertyCode") == prop.get("propertyCode")), None)
            properties.append({
                **prop,
                "legalEntity": None if entity is None else entity.get("entityName"),
                "connection": None if connector is None else connector.get("connectionStatus"),
                "invoicesToday": len([txn for txn in related if str(txn.get("invoiceDate") or "").startswith("2026-10-14")]),
                "ready": len([txn for txn in related if txn.get("lifecycleStatus") == "READY_FOR_ASP"]),
                "failed": len([txn for txn in related if txn.get("lifecycleStatus") == "COMPLIANCE_FAILED"]),
                "lastSync": None if connector is None else connector.get("lastSuccess"),
            })
        return {
            "entities": entities,
            "properties": properties,
            "readiness": implementation_readiness(self.data, tenant_id),
            "operaGap": opera_gap(self.data, tenant_id),
            "sapGap": sap_gap_register(),
            "sourceOfTruth": [item for item in self.data.get("sourceOfTruth", []) if item.get("tenantId") == tenant_id],
            "routing": [item for item in self.data.get("documentRules", []) if item.get("tenantId") == tenant_id],
            "resolution": [item for item in self.data.get("entityResolution", []) if item.get("tenantId") == tenant_id],
            "buyers": [item for item in self.data.get("buyers", []) if item.get("tenantId") == tenant_id],
            "aribaMap": self.data.get("aribaPostingMap") or [],
            "customer": next((item for item in self.data.get("customerProfiles", []) if item.get("tenantId") == tenant_id), None),
        }

    def reports(self, tenant_id: str) -> dict:
        center = self.command_center(tenant_id)
        return {
            "kpis": center["kpis"],
            "compliance": center["compliance"],
            "bySource": center["sources"],
            "exceptionsOpen": len([item for item in self.scoped("exceptions", tenant_id) if item["status"] not in {"Closed", "Ignored"}]),
        }


_STORE: Store | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> Store:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None or os.environ.get("UAE_STORE_PATH"):
            path = os.environ.get("UAE_STORE_PATH")
            _STORE = Store(Path(path) if path else None)
        return _STORE
