import instance from '../api/axiosInstance';

export interface Tenant {
  id: string;
  name: string;
  code: string;
  status: string;
}

export interface UaeContext {
  tenants: Tenant[];
  actor: string;
  role: string;
  roles: string[];
  permissions: string[];
  sections: string[];
}

const base = '/api/v1/uae-einvoicing';

function withTenant(path: string, tenantId: string) {
  const joiner = path.includes('?') ? '&' : '?';
  return `${base}${path}${joiner}tenantId=${encodeURIComponent(tenantId)}`;
}

export const uaeApi = {
  context: () => instance.get<UaeContext>(`${base}/context`).then((res) => res.data),
  commandCenter: (tenantId: string) => instance.get(withTenant('/command-center', tenantId)).then((res) => res.data),
  profiles: (tenantId: string) => instance.get(withTenant('/profiles', tenantId)).then((res) => res.data),
  activateProfile: (tenantId: string, id: string) => instance.post(withTenant(`/profiles/${id}/activate`, tenantId)).then((res) => res.data),
  codeLists: () => instance.get(`${base}/code-lists`).then((res) => res.data),
  mappingReadiness: (tenantId: string) => instance.get(withTenant('/mapping-readiness', tenantId)).then((res) => res.data),
  entityReadiness: (tenantId: string) => instance.get(withTenant('/legal-entity-readiness', tenantId)).then((res) => res.data),
  regulatoryChange: (tenantId: string) => instance.get(withTenant('/regulatory-change', tenantId)).then((res) => res.data),
  masterCompliance: () => instance.get(`${base}/master-compliance`).then((res) => res.data),
  fieldRegistry: () => instance.get(`${base}/field-registry`).then((res) => res.data),
  simulate: (tenantId: string, body: Record<string, unknown>) => instance.post(withTenant('/simulator', tenantId), body).then((res) => res.data),
  ingest: (tenantId: string, body: Record<string, unknown>) => instance.post(withTenant('/ingest', tenantId), body).then((res) => res.data),
  runConnector: (tenantId: string, id: string) => instance.post(withTenant(`/connectors/${id}/run`, tenantId)).then((res) => res.data),
  onboarding: (tenantId: string) => instance.get(withTenant('/onboarding', tenantId)).then((res) => res.data),
  compliance: (tenantId: string) => instance.get(withTenant('/compliance', tenantId)).then((res) => res.data),
  transactions: (tenantId: string, filters: Record<string, string>) => {
    const params = new URLSearchParams({ tenantId, ...filters });
    return instance.get(`${base}/transactions?${params.toString()}`).then((res) => res.data);
  },
  transaction: (tenantId: string, id: string) => instance.get(withTenant(`/transactions/${id}`, tenantId)).then((res) => res.data),
  correct: (tenantId: string, id: string, body: { path: string; value: string; reason: string }) =>
    instance.post(withTenant(`/transactions/${id}/correct`, tenantId), body).then((res) => res.data),
  reprocess: (tenantId: string, id: string, mode = 'full') =>
    instance.post(`${base}/transactions/${id}/reprocess?tenantId=${encodeURIComponent(tenantId)}&mode=${encodeURIComponent(mode)}`).then((res) => res.data),
  override: (tenantId: string, id: string, body: { code: string; value: boolean; reason: string }) =>
    instance.post(withTenant(`/transactions/${id}/override`, tenantId), body).then((res) => res.data),
  legalEntities: (tenantId: string) => instance.get(withTenant('/legal-entities', tenantId)).then((res) => res.data),
  saveLegalEntity: (tenantId: string, body: Record<string, unknown>, id?: string) =>
    (id
      ? instance.put(withTenant(`/legal-entities/${id}`, tenantId), body)
      : instance.post(withTenant('/legal-entities', tenantId), body)
    ).then((res) => res.data),
  connectors: (tenantId: string) => instance.get(withTenant('/connectors', tenantId)).then((res) => res.data),
  saveConnector: (tenantId: string, body: Record<string, unknown>, id?: string) =>
    (id
      ? instance.put(withTenant(`/connectors/${id}`, tenantId), body)
      : instance.post(withTenant('/connectors', tenantId), body)
    ).then((res) => res.data),
  connectorAction: (tenantId: string, id: string, action: string) =>
    instance.post(withTenant(`/connectors/${id}/${action}`, tenantId)).then((res) => res.data),
  connectorLogs: (tenantId: string, id: string) => instance.get(withTenant(`/connectors/${id}/logs`, tenantId)).then((res) => res.data),
  asps: (tenantId: string) => instance.get(withTenant('/asps', tenantId)).then((res) => res.data),
  saveAsp: (tenantId: string, body: Record<string, unknown>, id?: string) =>
    (id ? instance.put(withTenant(`/asps/${id}`, tenantId), body) : instance.post(withTenant('/asps', tenantId), body)).then((res) => res.data),
  aspAction: (tenantId: string, id: string, action: string) =>
    instance.post(withTenant(`/asps/${id}/${action}`, tenantId)).then((res) => res.data),
  mappings: (tenantId: string, filters: Record<string, string>) => {
    const params = new URLSearchParams({ tenantId, ...filters });
    return instance.get(`${base}/mappings?${params.toString()}`).then((res) => res.data);
  },
  mappingAction: (tenantId: string, id: string, action: string) =>
    instance.post(withTenant(`/mappings/${id}/${action}`, tenantId)).then((res) => res.data),
  rules: (tenantId: string) => instance.get(withTenant('/rules', tenantId)).then((res) => res.data),
  exceptions: (tenantId: string) => instance.get(withTenant('/exceptions', tenantId)).then((res) => res.data),
  exceptionAction: (tenantId: string, id: string, body: { action: string; owner?: string; resolution?: string }) =>
    instance.post(withTenant(`/exceptions/${id}/action`, tenantId), body).then((res) => res.data),
  reconciliation: (tenantId: string) => instance.get(withTenant('/reconciliation', tenantId)).then((res) => res.data),
  archive: (tenantId: string) => instance.get(withTenant('/archive', tenantId)).then((res) => res.data),
  saveRetention: (tenantId: string, legalEntityId: string, retentionYears: number) =>
    instance.put(withTenant('/retention', tenantId), { legalEntityId, retentionYears }).then((res) => res.data),
  audit: (tenantId: string) => instance.get(withTenant('/audit', tenantId)).then((res) => res.data),
  reports: (tenantId: string) => instance.get(withTenant('/reports', tenantId)).then((res) => res.data),
  masterData: (tenantId: string) => instance.get(withTenant('/master-data', tenantId)).then((res) => res.data),
  administration: (tenantId: string) => instance.get(withTenant('/administration', tenantId)).then((res) => res.data),
  assignRole: (tenantId: string, body: { personName: string; silaRole: string; uaeRole: string }) =>
    instance.post(withTenant('/administration/assignments', tenantId), body).then((res) => res.data),
};
