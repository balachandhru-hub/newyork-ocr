"""HTTP API for the UAE e-Invoicing module.

Mounted on the existing SILA Fatoora API. Authentication stays on the
existing cookie session. This router does not replace it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from uae_einvoicing.engine import ROLE_PERMISSIONS, UAE_ROLES
from uae_einvoicing.store import get_store


router = APIRouter(prefix="/uae-einvoicing", tags=["uae-einvoicing"])


class LegalEntityBody(BaseModel):
    entityName: str
    legalName: str
    entityCode: str
    trn: str | None = None
    tin: str | None = None
    peppolIdentifier: str | None = None
    electronicAddress: str | None = None
    electronicAddressScheme: str | None = None
    tradeLicenceNumber: str | None = None
    tradeLicenceAuthority: str | None = None
    vatRegistrationStatus: str | None = None
    vatGroup: str | None = None
    vatGroupMember: bool = False
    address: str | None = None
    building: str | None = None
    street: str | None = None
    city: str | None = None
    emirate: str | None = None
    country: str = "AE"
    postalCode: str | None = None
    defaultCurrency: str = "AED"
    legalRegistrationType: str | None = None
    sapCompanyCode: str | None = None
    sapSalesOrganization: str | None = None
    sapPurchasingOrganization: str | None = None
    operaPropertyCode: str | None = None
    operaHotelCode: str | None = None
    aribaEntityMapping: str | None = None
    tradingName: str | None = None
    standaloneVat: bool = True
    sourceSystems: str | None = None
    aspId: str | None = None
    effectiveDate: str | None = None
    status: str = "Active"


class ConnectorBody(BaseModel):
    connectorName: str
    systemType: str
    direction: str
    baseUrl: str | None = None
    authenticationType: str = "OAuth2"
    credentialReference: str | None = None
    secret: str | None = None
    headers: dict = {}
    timeoutSeconds: int = 30
    retryPolicy: str | None = None
    deliveryMode: str = "Polling"
    frequency: str | None = None
    legalEntityId: str | None = None
    active: bool = True


class AspBody(BaseModel):
    aspName: str
    providerType: str
    environment: str = "Sandbox"
    apiBaseUrl: str | None = None
    authenticationType: str = "OAuth2"
    credentialReference: str | None = None
    secret: str | None = None
    participantId: str | None = None
    peppolScheme: str | None = None
    outboundEndpoint: str | None = None
    inboundEndpoint: str | None = None
    statusEndpoint: str | None = None
    webhookEndpoint: str | None = None
    retryConfiguration: str | None = None
    timeoutSeconds: int = 30
    operatingMode: str = "Test"


class MappingBody(BaseModel):
    legalEntityId: str
    sourceSystem: str
    documentType: str
    effectiveFrom: str | None = None
    effectiveTo: str | None = None
    lines: list[dict] = []


class RuleBody(BaseModel):
    ruleId: str
    ruleName: str
    description: str
    category: str
    businessTerm: str
    severity: str
    documentType: str
    condition: str | None = None
    validationLogic: dict
    errorMessage: str
    suggestedResolution: str
    effectiveFrom: str | None = None
    effectiveTo: str | None = None
    version: int = 1
    active: bool = True


class CorrectBody(BaseModel):
    path: str
    value: Any
    reason: str


class OverrideBody(BaseModel):
    code: str
    value: bool
    reason: str


class ExceptionBody(BaseModel):
    action: str
    owner: str | None = None
    resolution: str | None = None


class RetentionBody(BaseModel):
    legalEntityId: str
    retentionYears: int


class AssignmentBody(BaseModel):
    personName: str
    silaRole: str
    uaeRole: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode_hs256(token: str, secret: str) -> dict | None:
    try:
        header, payload, signature = token.split(".")
        signing = f"{header}.{payload}".encode()
        expected = _b64(hmac.new(secret.encode(), signing, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None


def _meta(request: Request) -> dict:
    store = get_store()
    db_ready = all(os.getenv(name) for name in ("DB_NAME", "SERVER_NAME", "DB_USER"))
    actor = "foundation-session" if not db_ready else "signed-out"
    role = "UAE eInvoice Administrator" if not db_ready else "Read Only"
    token = request.cookies.get("access_token")
    secret = os.getenv("SECRET_KEY") or ""
    if token and secret:
        payload = _decode_hs256(token, secret)
        if payload and payload.get("userName"):
            actor = payload["userName"]
            assignment = next((item for item in store.data["assignments"] if item["personName"] == actor), None)
            role = assignment["uaeRole"] if assignment else ("Read Only" if db_ready else role)
    return {
        "actor": actor,
        "role": role,
        "ip": request.client.host if request.client else None,
        "session": request.cookies.get("session_id"),
    }


def _tenant(request: Request) -> str:
    tenant_id = request.query_params.get("tenantId")
    store = get_store()
    ids = {item["id"] for item in store.tenants()}
    if tenant_id and tenant_id in ids:
        return tenant_id
    return store.tenants()[0]["id"]


def _call(fn):
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/context")
def context(request: Request):
    store = get_store()
    meta = _meta(request)
    return {
        "tenants": store.tenants(),
        "actor": meta["actor"],
        "role": meta["role"],
        "roles": UAE_ROLES,
        "permissions": sorted(ROLE_PERMISSIONS.get(meta["role"], [])),
        "sections": [
            "command-center", "transactions", "exceptions", "compliance", "field-mapping",
            "integration-hub", "asp-configuration", "legal-entities", "entity-readiness", "properties",
            "implementation-readiness", "source-of-truth", "opera-gap", "sap-gap", "buyer-master",
            "source-test-lab", "master-data", "rules",
            "reconciliation", "archive", "audit-trail", "reports", "administration",
        ],
    }


@router.get("/command-center")
def command_center(request: Request):
    return get_store().command_center(_tenant(request))


class SimulationBody(BaseModel):
    legalEntityId: str
    sourceSystem: str
    documentType: str = "Tax Invoice"
    invoiceDate: str | None = None
    payload: dict = {}


@router.get("/field-registry")
def field_registry():
    from uae_einvoicing.pipeline import FIELD_REGISTRY
    return FIELD_REGISTRY


@router.get("/profiles")
def profiles(request: Request):
    tenant_id = _tenant(request)
    return [item for item in get_store().data["profiles"] if item.get("tenantId") in {"global", tenant_id}]


@router.post("/profiles/{profile_id}/activate")
def activate_profile(profile_id: str, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().activate_profile(_tenant(request), profile_id, meta["actor"], meta))


@router.get("/code-lists")
def code_lists():
    return get_store().data.get("codeLists", {})


@router.get("/mapping-readiness")
def mapping_readiness(request: Request):
    return get_store().mapping_readiness(_tenant(request))


@router.get("/legal-entity-readiness")
def legal_entity_readiness(request: Request):
    return get_store().legal_entity_readiness(_tenant(request))


@router.get("/regulatory-change")
def regulatory_change(request: Request):
    return get_store().regulatory_change(_tenant(request))


@router.get("/master-compliance")
def master_compliance(request: Request):
    tenant_id = _tenant(request)
    raw = get_store().data.get("masterCompliance", {})
    filtered = {}
    for domain, rows in raw.items():
        filtered[domain] = [
            row for row in rows
            if not row.get("tenantId") or row.get("tenantId") == tenant_id
        ]
    return filtered


@router.post("/simulator")
def simulator(body: SimulationBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().simulate(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.get("/compliance")
def compliance(request: Request):
    tenant_id = _tenant(request)
    store = get_store()
    txns = [txn for txn in store.data["transactions"] if txn["tenantId"] == tenant_id]
    from uae_einvoicing.engine import compliance_health
    from uae_einvoicing.pipeline import compliance_score, workbench
    results = []
    for txn in txns:
        for result in txn.get("validationResults") or []:
            results.append({**result, "transactionId": txn["id"], "invoiceNumber": txn.get("invoiceNumber")})
    score = compliance_score(results)
    return {
        "health": compliance_health(txns),
        "rules": store.scoped("rules", tenant_id),
        "score": score,
        "workbench": workbench(results, score),
        "results": results,
    }


@router.get("/transactions")
def transactions(request: Request):
    filters = {
        "direction": request.query_params.get("direction") or "",
        "documentType": request.query_params.get("documentType") or "",
        "sourceSystem": request.query_params.get("sourceSystem") or "",
        "legalEntityId": request.query_params.get("legalEntityId") or "",
        "lifecycleStatus": request.query_params.get("lifecycleStatus") or "",
        "q": request.query_params.get("q") or "",
    }
    return get_store().list_transactions(_tenant(request), filters)


@router.get("/transactions/{txn_id}")
def transaction_detail(txn_id: str, request: Request):
    return _call(lambda: get_store().get_transaction(_tenant(request), txn_id))


@router.post("/transactions/{txn_id}/correct")
def correct(txn_id: str, body: CorrectBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().correct_transaction(_tenant(request), txn_id, body.path, body.value, body.reason, meta["actor"], meta))


@router.post("/transactions/{txn_id}/reprocess")
def reprocess(txn_id: str, request: Request, mode: str = "full"):
    meta = _meta(request)
    return _call(lambda: get_store().reprocess(_tenant(request), txn_id, meta["actor"], meta, mode))


@router.post("/transactions/{txn_id}/override")
def override(txn_id: str, body: OverrideBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().override_flag(_tenant(request), txn_id, body.code, body.value, body.reason, meta["actor"], meta))


@router.get("/legal-entities")
def legal_entities(request: Request):
    return get_store().scoped("legalEntities", _tenant(request))


@router.post("/legal-entities")
def create_legal_entity(body: LegalEntityBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_legal_entity(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.put("/legal-entities/{entity_id}")
def update_legal_entity(entity_id: str, body: LegalEntityBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_legal_entity(_tenant(request), body.model_dump(), meta["actor"], meta, entity_id))


@router.get("/connectors")
def connectors(request: Request):
    return [get_store()._public_connector(item) for item in get_store().scoped("connectors", _tenant(request))]


@router.post("/connectors")
def create_connector(body: ConnectorBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_connector(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.put("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_connector(_tenant(request), body.model_dump(), meta["actor"], meta, connector_id))


@router.post("/connectors/{connector_id}/{action}")
def connector_action(connector_id: str, action: str, request: Request):
    if action not in {"test", "enable", "disable"}:
        raise HTTPException(status_code=404, detail="Unknown action")
    meta = _meta(request)
    return _call(lambda: get_store().connector_action(_tenant(request), connector_id, action, meta["actor"], meta))


@router.get("/connectors/{connector_id}/logs")
def connector_logs(connector_id: str, request: Request):
    tenant_id = _tenant(request)
    return [item for item in get_store().data["connectorLogs"] if item["tenantId"] == tenant_id and item["connectorId"] == connector_id]


@router.get("/asps")
def asps(request: Request):
    return get_store().scoped("asps", _tenant(request))


@router.post("/asps")
def create_asp(body: AspBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_asp(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.put("/asps/{asp_id}")
def update_asp(asp_id: str, body: AspBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_asp(_tenant(request), body.model_dump(), meta["actor"], meta, asp_id))


@router.post("/asps/{asp_id}/{action}")
def asp_action(asp_id: str, action: str, request: Request):
    if action not in {"test", "activate", "deactivate"}:
        raise HTTPException(status_code=404, detail="Unknown action")
    meta = _meta(request)
    return _call(lambda: get_store().asp_action(_tenant(request), asp_id, action, meta["actor"], meta))


@router.get("/mappings")
def mappings(request: Request):
    tenant_id = _tenant(request)
    rows = get_store().scoped("mappings", tenant_id)
    source = request.query_params.get("sourceSystem")
    legal = request.query_params.get("legalEntityId")
    document = request.query_params.get("documentType")
    if source:
        rows = [row for row in rows if row["sourceSystem"] == source]
    if legal:
        rows = [row for row in rows if row["legalEntityId"] == legal]
    if document:
        rows = [row for row in rows if row["documentType"] == document]
    return rows


@router.post("/mappings")
def create_mapping(body: MappingBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_mapping(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.post("/mappings/{mapping_id}/{action}")
def mapping_action(mapping_id: str, action: str, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().mapping_action(_tenant(request), mapping_id, action, meta["actor"], meta))


@router.get("/rules")
def rules(request: Request):
    return get_store().scoped("rules", _tenant(request))


@router.post("/rules")
def create_rule(body: RuleBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_rule(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_rule(_tenant(request), body.model_dump(), meta["actor"], meta, rule_id))


@router.get("/exceptions")
def exceptions(request: Request):
    return get_store().scoped("exceptions", _tenant(request))


@router.post("/exceptions/{exception_id}/action")
def exception_action(exception_id: str, body: ExceptionBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().exception_action(_tenant(request), exception_id, body.action, meta["actor"], meta, body.owner, body.resolution))


@router.get("/reconciliation")
def reconciliation(request: Request):
    return get_store().scoped("reconciliation", _tenant(request))


@router.get("/archive")
def archive(request: Request):
    tenant_id = _tenant(request)
    store = get_store()
    return {"artifacts": store.scoped("archive", tenant_id), "retention": store.scoped("retention", tenant_id)}


@router.put("/retention")
def retention(body: RetentionBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_retention(_tenant(request), body.legalEntityId, body.retentionYears, meta["actor"], meta))


@router.get("/audit")
def audit(request: Request):
    return get_store().scoped("audit", _tenant(request))


class IngestBody(BaseModel):
    fixture: str | None = None
    sourceSystem: str | None = None
    sourceDocumentId: str | None = None
    sourceInstance: str | None = None
    documentClass: str | None = None
    regulatoryReference: str | None = None
    propertyCode: str | None = None
    payload: dict = {}


@router.post("/ingest")
def ingest(body: IngestBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().ingest_request(_tenant(request), body.model_dump(), meta["actor"], meta))


@router.post("/connectors/{connector_id}/run")
def run_connector(connector_id: str, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().run_connector(_tenant(request), connector_id, meta["actor"], meta))


@router.get("/onboarding")
def onboarding(request: Request):
    return get_store().onboarding_views(_tenant(request))


@router.get("/reports")
def reports(request: Request):
    return get_store().reports(_tenant(request))


@router.get("/master-data")
def master_data(request: Request):
    tenant_id = _tenant(request)
    return get_store().data["masterData"].get(tenant_id, {})


@router.put("/master-data")
def update_master_data(payload: dict, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_master(_tenant(request), payload, meta["actor"], meta))


@router.get("/administration")
def administration(request: Request):
    tenant_id = _tenant(request)
    return {
        "roles": UAE_ROLES,
        "permissions": {role: sorted(grants) for role, grants in ROLE_PERMISSIONS.items()},
        "assignments": get_store().scoped("assignments", tenant_id),
        "silaRoles": ["Lead", "Consultant", "User"],
    }


@router.post("/administration/assignments")
def create_assignment(body: AssignmentBody, request: Request):
    meta = _meta(request)
    return _call(lambda: get_store().save_assignment(_tenant(request), body.model_dump(), meta["actor"], meta))
