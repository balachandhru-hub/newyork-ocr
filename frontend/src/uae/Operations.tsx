import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { uaeApi } from './api';
import { Empty, Panel, StatusChip } from './ui';

function message(error: any, fallback: string) {
  return error?.response?.data?.detail || fallback;
}

const ENTITY_FIELDS = [
  ['entityName', 'Entity Name'], ['legalName', 'Legal Name'], ['entityCode', 'Entity Code'], ['trn', 'TRN'], ['tin', 'TIN'],
  ['peppolIdentifier', 'Peppol Identifier'], ['electronicAddress', 'Electronic Address'], ['electronicAddressScheme', 'Electronic Address Scheme'],
  ['tradeLicenceNumber', 'Trade Licence Number'], ['tradeLicenceAuthority', 'Trade Licence Authority'], ['vatRegistrationStatus', 'VAT Registration Status'],
  ['vatGroup', 'VAT Group'], ['address', 'Address'], ['building', 'Building'], ['street', 'Street'], ['city', 'City'],
  ['emirate', 'Emirate'], ['country', 'Country'], ['postalCode', 'Postal Code'], ['defaultCurrency', 'Default Currency'],
  ['sourceSystems', 'Source Systems'], ['sapCompanyCode', 'SAP Company Code'], ['sapSalesOrganization', 'SAP Sales Organization'],
  ['sapPurchasingOrganization', 'SAP Purchasing Organization'], ['operaPropertyCode', 'OPERA Property Code'],
  ['operaHotelCode', 'OPERA Hotel Code'], ['aribaEntityMapping', 'Ariba Entity Mapping'],
  ['aspId', 'ASP'], ['effectiveDate', 'Effective Date'], ['status', 'Status'],
] as const;

export const Operations: React.FC<{ section: string; tenantId: string }> = ({ section, tenantId }) => {
  if (section === 'exceptions') return <Exceptions tenantId={tenantId} />;
  if (section === 'compliance') return <Compliance tenantId={tenantId} />;
  if (section === 'compliance-simulator') return <Simulator tenantId={tenantId} />;
  if (section === 'mapping-readiness') return <MappingReadiness tenantId={tenantId} />;
  if (section === 'regulatory-change') return <RegulatoryChange tenantId={tenantId} />;
  if (section === 'field-mapping') return <FieldMapping tenantId={tenantId} />;
  if (section === 'integration-hub') return <IntegrationHub tenantId={tenantId} />;
  if (section === 'asp-configuration') return <AspConfiguration tenantId={tenantId} />;
  if (section === 'legal-entities') return <LegalEntities tenantId={tenantId} />;
  if (section === 'master-data') return <MasterData tenantId={tenantId} />;
  if (section === 'rules') return <Rules tenantId={tenantId} />;
  if (section === 'reconciliation') return <Reconciliation tenantId={tenantId} />;
  if (section === 'archive') return <Archive tenantId={tenantId} />;
  if (section === 'audit-trail') return <AuditTrail tenantId={tenantId} />;
  if (section === 'reports') return <Reports tenantId={tenantId} />;
  if (section === 'administration') return <Administration tenantId={tenantId} />;
  return null;
};

const Exceptions: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [owner, setOwner] = useState('AP Operations');
  const load = () => uaeApi.exceptions(tenantId).then(setRows);
  useEffect(() => { load(); }, [tenantId]);

  const act = async (id: string, action: string) => {
    try {
      await uaeApi.exceptionAction(tenantId, id, { action, owner, resolution: action });
      toast.success(`${action} recorded`);
      load();
    } catch (error) {
      toast.error(message(error, 'Action was not allowed.'));
    }
  };

  return (
    <Panel title="Exception workbench" extra={<input value={owner} onChange={(event) => setOwner(event.target.value)} aria-label="Owner" />}>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead>
            <tr>{['Exception ID', 'Transaction', 'Severity', 'Category', 'Owner', 'Age', 'Status', 'Resolution', 'Actions'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.id}</td>
                <td><Link to={`/uae-einvoicing/transactions/${row.transactionId}`}>{row.transactionId}</Link></td>
                <td><StatusChip value={row.severity} /></td>
                <td>{row.category}</td>
                <td>{row.owner}</td>
                <td>{row.ageHours}h</td>
                <td><StatusChip value={row.status} /></td>
                <td>{row.resolution || '—'}</td>
                <td>
                  <div className="uae-actions">
                    {['assign', 'correct', 'retry', 'reprocess', 'ignore', 'escalate', 'close'].map((action) => (
                      <button key={action} className="uae-btn" onClick={() => act(row.id, action)}>{action}</button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
};

const Compliance: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  const [filter, setFilter] = useState('Failed');
  const [selected, setSelected] = useState<any>(null);
  useEffect(() => { uaeApi.compliance(tenantId).then(setData); }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading compliance…</div>;
  const results = (data.results || []).filter((item: any) => {
    if (filter === 'Failed') return item.status === 'FAILED';
    if (filter === 'Warnings') return item.status === 'FAILED' && item.severity !== 'ERROR';
    if (filter === 'Passed') return item.status === 'PASSED';
    if (filter === 'Mandatory') return item.group === 'Mandatory Fields';
    if (filter === 'Conditional') return item.group === 'Conditional Fields';
    if (filter === 'Tax') return item.group === 'Tax';
    if (filter === 'Line') return item.group === 'Lines';
    if (filter === 'Header') return item.lineNumber == null;
    return true;
  }).slice(0, 80);
  return (
    <>
      <div className="uae-heading">
        <div>
          <h1>UAE Compliance Validation</h1>
          <p>Status takes precedence over the score. One mandatory error means the document is failed.</p>
        </div>
        <StatusChip value={data.score?.label || data.score?.status} />
      </div>
      <div className="uae-kpis">
        {(data.workbench?.cards || []).map((card: any) => (
          <article key={card.label} className="uae-kpi">
            <span>{card.label}</span>
            <strong>{card.total ? `${card.passed}/${card.total}` : '—'}</strong>
          </article>
        ))}
      </div>
      <div className="uae-filters">
        {['Failed', 'Warnings', 'Passed', 'Mandatory', 'Conditional', 'Tax', 'Line', 'Header'].map((item) => (
          <button key={item} className={filter === item ? 'uae-btn primary' : 'uae-btn'} onClick={() => setFilter(item)}>{item}</button>
        ))}
      </div>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead>
            <tr>{['Rule', 'Business term', 'Field', 'Invoice', 'Source', 'Final', 'Result', 'Severity', 'Message'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
          </thead>
          <tbody>
            {results.length === 0 && <tr><td colSpan={9}><Empty>No results for this filter.</Empty></td></tr>}
            {results.map((item: any, index: number) => (
              <tr key={`${item.transactionId}-${item.ruleId}-${index}`} onClick={() => setSelected(item)} style={{ cursor: 'pointer' }}>
                <td>{item.ruleId}</td>
                <td>{item.businessTermId || '—'}</td>
                <td>{item.field}</td>
                <td>{item.invoiceNumber}</td>
                <td>{formatAudit(item.sourceValue)}</td>
                <td>{formatAudit(item.finalValue)}</td>
                <td><StatusChip value={item.status} /></td>
                <td><StatusChip value={item.severity} /></td>
                <td>{item.message || 'Passed'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <div className="uae-drawer-backdrop" onClick={() => setSelected(null)}>
          <div className="uae-drawer" onClick={(event) => event.stopPropagation()}>
            <h2>{selected.ruleId}</h2>
            <p style={{ margin: '8px 0 12px' }}>{selected.ruleName}</p>
            <div className="uae-meta">
              <span>Regulatory source <b>{selected.regulatorySource || 'UAE e-invoicing profile'}</b></span>
              <span>Source value <b>{formatAudit(selected.sourceValue)}</b></span>
              <span>Mapped value <b>{formatAudit(selected.mappedValue)}</b></span>
              <span>Enriched value <b>{formatAudit(selected.enrichedValue)}</b></span>
              <span>Final value <b>{formatAudit(selected.finalValue)}</b></span>
              <span>Expected <b>{selected.expected}</b></span>
              <span>Resolution <b>{selected.resolution || '—'}</b></span>
            </div>
            <button className="uae-btn" style={{ marginTop: 12 }} onClick={() => setSelected(null)}>Close</button>
          </div>
        </div>
      )}
      <Panel title="Regulatory rules"><RuleTable rules={data.rules} /></Panel>
    </>
  );
};

const Simulator: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [entities, setEntities] = useState<any[]>([]);
  const [form, setForm] = useState({ legalEntityId: '', sourceSystem: 'OPERA', documentType: 'Tax Invoice', payload: '{\n  "FOLIO_NO": "SIM-100",\n  "HOTEL_TRN": "100234567890003",\n  "CURRENCY": "AED",\n  "NETAMOUNT": "1000.00",\n  "BILLINGDATE": "20261014"\n}' });
  const [result, setResult] = useState<any>(null);
  useEffect(() => { uaeApi.legalEntities(tenantId).then((rows) => { setEntities(rows); setForm((current) => ({ ...current, legalEntityId: current.legalEntityId || rows[0]?.id || '' })); }); }, [tenantId]);
  const run = async () => {
    try {
      const payload = JSON.parse(form.payload);
      const response = await uaeApi.simulate(tenantId, { ...form, payload });
      setResult(response);
      toast.success('Simulation finished. Nothing was sent to an ASP.');
    } catch (error) {
      toast.error(message(error, 'Simulation failed. Check the sample JSON.'));
    }
  };
  return (
    <Panel title="Compliance simulator" extra={<StatusChip value="SIMULATION ONLY" />}>
      <div className="uae-form">
        <label className="uae-field">Legal entity
          <select value={form.legalEntityId} onChange={(event) => setForm({ ...form, legalEntityId: event.target.value })}>
            {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.entityName}</option>)}
          </select>
        </label>
        <label className="uae-field">Source system
          <select value={form.sourceSystem} onChange={(event) => setForm({ ...form, sourceSystem: event.target.value })}>
            {['SAP S/4HANA', 'SAP Ariba', 'OPERA', 'Custom REST API'].map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label className="uae-field">Document type
          <select value={form.documentType} onChange={(event) => setForm({ ...form, documentType: event.target.value })}>
            {['Tax Invoice', 'Tax Credit Note', 'Commercial Invoice', 'Self-Billed Invoice', 'Self-Billed Credit Note', 'Credit Note'].map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label className="uae-field wide">Sample payload
          <textarea rows={8} value={form.payload} onChange={(event) => setForm({ ...form, payload: event.target.value })} />
        </label>
      </div>
      <button className="uae-btn primary" onClick={run}>Run mapping, enrichment and compliance</button>
      {result && (
        <div style={{ marginTop: 12 }}>
          <StatusChip value={result.label} />
          <StatusChip value={result.score?.label || result.lifecycleStatus} />
          <p style={{ marginTop: 8 }}>Transaction type code {result.transactionTypeCode || '—'} · {(result.scenarios || []).map((item: any) => item.label).join(', ') || 'No scenario'}</p>
          <p style={{ color: '#475569' }}>Nothing was stored and nothing was sent to an ASP. Persisted: {String(result.persisted)}</p>
          <div className="uae-two" style={{ marginTop: 12 }}>
            <Panel title="Mapping results">
              {Object.keys(result.mappingResults || {}).length === 0 && <Empty>No mapping lines matched this payload.</Empty>}
              {Object.entries(result.mappingResults || {}).map(([field, row]: [string, any]) => (
                <div key={field} className="uae-meta"><span>{row.sourceField || field} → <b>{field}</b> {String(row.finalValue ?? '—')}</span></div>
              ))}
            </Panel>
            <Panel title="Compliance results">
              {(result.results || []).filter((item: any) => item.status === 'FAILED').slice(0, 8).map((item: any) => (
                <div key={item.ruleId} className="uae-meta"><span>{item.ruleId} <b>{item.message || item.field}</b></span></div>
              ))}
              {(result.results || []).every((item: any) => item.status !== 'FAILED') && <Empty>No failed rules in this simulation.</Empty>}
            </Panel>
          </div>
        </div>
      )}
    </Panel>
  );
};

const MappingReadiness: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState<any>(null);
  useEffect(() => { uaeApi.mappingReadiness(tenantId).then(setRows); }, [tenantId]);
  return (
    <Panel title="Source system readiness">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Source', 'Mandatory mapped', 'Conditional', 'Master data', 'Rules', 'Missing', 'Production'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.sourceSystem}>
                <td>{row.connectorName}</td>
                <td>{row.mandatoryPercent}%</td>
                <td>{row.conditionalPercent}%</td>
                <td>{row.masterDataPercent}%</td>
                <td>{row.rules}</td>
                <td><button className="uae-btn" onClick={() => setOpen(row)}>{row.missing}</button></td>
                <td><StatusChip value={row.productionReadiness === 'READY' ? `${row.productionPercent}%` : row.productionReadiness} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && (
        <div style={{ marginTop: 12 }}>
          <h2 style={{ fontSize: 14, marginBottom: 8 }}>Missing mappings · {open.connectorName}</h2>
          {open.missingFields.length === 0 && <Empty>No missing mapping lines.</Empty>}
          {open.missingFields.map((line: any) => <div key={line.targetField} className="uae-meta"><span>{line.sourceField} → <b>{line.targetField}</b> ({line.requirement})</span></div>)}
        </div>
      )}
    </Panel>
  );
};

const RegulatoryChange: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  const load = () => uaeApi.regulatoryChange(tenantId).then(setData);
  useEffect(() => { load(); }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading regulatory change…</div>;
  const activate = async () => {
    if (!data.draft) return;
    try {
      await uaeApi.activateProfile(tenantId, data.draft.id);
      toast.success('Draft profile activated. Historical transactions keep their original version.');
      load();
    } catch (error) {
      toast.error(message(error, 'Activation was not allowed.'));
    }
  };
  return (
    <>
      <div className="uae-two">
        <Panel title="Current active version">
          <ProfileCard profile={data.active} />
        </Panel>
        <Panel title="Future version" extra={<button className="uae-btn primary" disabled={!data.draft} onClick={activate}>Activate draft</button>}>
          <ProfileCard profile={data.draft} />
        </Panel>
      </div>
      <Panel title="Impact">
        <div className="uae-meta">
          <span>Rules added <b>{(data.rulesAdded || []).join('; ') || '—'}</b></span>
          <span>Rules changed <b>{(data.rulesChanged || []).join(', ') || '—'}</b></span>
          <span>Mappings impacted <b>{(data.mappingsImpacted || []).length}</b></span>
          <span>Entities impacted <b>{(data.entitiesImpacted || []).length}</b></span>
          <span>Transactions impacted <b>{(data.transactionsImpacted || []).length}</b></span>
        </div>
      </Panel>
    </>
  );
};

function ProfileCard({ profile }: { profile: any }) {
  if (!profile) return <Empty>No profile in this state.</Empty>;
  return (
    <div className="uae-meta">
      <span>Country <b>{profile.country}</b></span>
      <span>Profile <b>{profile.profile}</b></span>
      <span>Standard <b>{profile.standard}</b></span>
      <span>Version <b>{profile.version}</b></span>
      <span>Status <b>{profile.status}</b></span>
      <span>Effective from <b>{profile.effectiveFrom}</b></span>
      <span>Business process <b>{profile.businessProcessId}</b></span>
      <span>Specification <b>{profile.specificationId}</b></span>
      <span>Source <b>{profile.regulatorySource}</b></span>
    </div>
  );
}

const Rules: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rules, setRules] = useState<any[]>([]);
  useEffect(() => { uaeApi.rules(tenantId).then(setRules); }, [tenantId]);
  return <Panel title="Compliance rules"><RuleTable rules={rules} /></Panel>;
};

function RuleTable({ rules }: { rules: any[] }) {
  return (
    <div className="uae-table-wrap">
      <table className="uae-table">
        <thead>
          <tr>{['Rule ID', 'Name', 'Category', 'Business term', 'Severity', 'Document', 'Version', 'Active', 'Error message'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.id}>
              <td>{rule.ruleId}</td>
              <td>{rule.ruleName}</td>
              <td>{rule.category}</td>
              <td>{rule.businessTerm}</td>
              <td><StatusChip value={rule.severity} /></td>
              <td>{rule.documentType}</td>
              <td>{rule.version}</td>
              <td>{rule.active ? 'Active' : 'Inactive'}</td>
              <td>{rule.errorMessage}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const FieldMapping: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [allRows, setAllRows] = useState<any[]>([]);
  const [entities, setEntities] = useState<any[]>([]);
  const [filters, setFilters] = useState({ sourceSystem: '', legalEntityId: '', documentType: '' });
  const [selected, setSelected] = useState('');

  const load = () => uaeApi.mappings(tenantId, {}).then((data) => {
    setAllRows(data);
    setSelected((current) => data.some((row: any) => row.id === current) ? current : data[0]?.id || '');
  });
  useEffect(() => { uaeApi.legalEntities(tenantId).then(setEntities); }, [tenantId]);
  useEffect(() => { load(); }, [tenantId]);
  const rows = allRows.filter((row) => (
    (!filters.sourceSystem || row.sourceSystem === filters.sourceSystem) &&
    (!filters.legalEntityId || row.legalEntityId === filters.legalEntityId) &&
    (!filters.documentType || row.documentType === filters.documentType)
  ));

  const visibleId = rows.some((row) => row.id === selected) ? selected : (rows[0]?.id || '');
  const mapping = rows.find((row) => row.id === visibleId);
  const act = async (action: string) => {
    if (!mapping) return;
    try {
      const saved = await uaeApi.mappingAction(tenantId, mapping.id, action);
      const data = await uaeApi.mappings(tenantId, {});
      setAllRows(data);
      setSelected(saved.id);
      toast.success(`Mapping ${action} complete`);
    } catch (error) {
      toast.error(message(error, 'Mapping action failed.'));
    }
  };

  return (
    <>
      <div className="uae-filters">
        <select value={filters.sourceSystem} onChange={(event) => setFilters({ ...filters, sourceSystem: event.target.value })}>
          <option value="">Source system</option>
          {Array.from(new Set(allRows.map((row) => row.sourceSystem))).map((value) => <option key={value}>{value}</option>)}
        </select>
        <select value={filters.legalEntityId} onChange={(event) => setFilters({ ...filters, legalEntityId: event.target.value })}>
          <option value="">Legal entity</option>
          {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.entityName}</option>)}
        </select>
        <select value={filters.documentType} onChange={(event) => setFilters({ ...filters, documentType: event.target.value })}>
          <option value="">Document type</option>
          {Array.from(new Set(allRows.map((row) => row.documentType))).map((value) => <option key={value}>{value}</option>)}
        </select>
        <select value={visibleId} onChange={(event) => setSelected(event.target.value)}>
          {rows.map((row) => <option key={row.id} value={row.id}>v{row.version} · {row.status}</option>)}
        </select>
      </div>
      {mapping && (
        <Panel title={`${mapping.sourceSystem} → ${mapping.documentType}`} extra={
          <div className="uae-actions">
            <button className="uae-btn" onClick={() => act('clone')}>Clone mapping</button>
            <button className="uae-btn" onClick={() => act('version')}>Version mapping</button>
            <button className="uae-btn primary" onClick={() => act('activate')}>Activate version</button>
            <button className="uae-btn" onClick={() => act('deactivate')}>Deactivate</button>
          </div>
        }>
          <div className="uae-meta" style={{ marginBottom: 10 }}>
            <span>Effective from <b>{mapping.effectiveFrom || '—'}</b></span>
            <span>Effective to <b>{mapping.effectiveTo || '—'}</b></span>
            <span>Status <b>{mapping.status}</b></span>
          </div>
          <div className="uae-table-wrap">
            <table className="uae-table">
              <thead>
                <tr>{['Source field', 'Description', 'Sample', 'PINT-AE term', 'Target field', 'Requirement', 'Transformation', 'Default', 'Derivation', 'Validation', 'Status'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
              </thead>
              <tbody>
                {mapping.lines.map((line: any) => (
                  <tr key={line.targetField}>
                    <td>{line.sourceField}</td>
                    <td>{line.sourceDescription}</td>
                    <td>{line.sampleValue || '—'}</td>
                    <td>{line.targetBusinessTerm}</td>
                    <td>{line.targetField}</td>
                    <td><StatusChip value={line.requirement} /></td>
                    <td>{line.transformation || line.mappingType}</td>
                    <td>{line.defaultValue || '—'}</td>
                    <td>{line.derivationRule || '—'}</td>
                    <td>{line.validation}</td>
                    <td><StatusChip value={line.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {!mapping && <Empty>No mapping version is stored for this selection.</Empty>}
    </>
  );
};

const IntegrationHub: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [logs, setLogs] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const load = () => uaeApi.connectors(tenantId).then(setRows);
  useEffect(() => { load(); }, [tenantId]);

  const run = async (id: string, action: string) => {
    try {
      if (action === 'run') {
        const result = await uaeApi.runConnector(tenantId, id);
        toast.success(result.message || 'Connector run finished. Nothing was sent to an ASP.');
      } else {
        await uaeApi.connectorAction(tenantId, id, action);
        toast.success(`Connector ${action} recorded`);
      }
      load();
    } catch (error) {
      toast.error(message(error, 'Connector action failed.'));
    }
  };

  return (
    <Panel title="Integration hub" extra={<button className="uae-btn primary" onClick={() => setActive({ connectorName: '', systemType: 'OPERA', direction: 'Outbound', authenticationType: 'OAuth2', timeoutSeconds: 30, deliveryMode: 'Polling', active: false })}>Add connector</button>}>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead>
            <tr>{['Name', 'System', 'Direction', 'Status', 'Legal entity', 'Last connection', 'Credential', 'Actions'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.connectorName}</td>
                <td>{row.systemType}</td>
                <td>{row.direction}</td>
                <td><StatusChip value={row.connectionStatus} /></td>
                <td>{row.legalEntityId}</td>
                <td>{row.lastConnection ? new Date(row.lastConnection).toLocaleString() : '—'}</td>
                <td>{row.credentialReference}</td>
                <td>
                  <div className="uae-actions">
                    <button className="uae-btn" onClick={() => run(row.id, 'test')}>Test connection</button>
                    {row.systemType === 'OPERA' && <button className="uae-btn primary" onClick={() => run(row.id, 'run')}>Run now</button>}
                    <button className="uae-btn" onClick={() => run(row.id, row.active ? 'disable' : 'enable')}>{row.active ? 'Disable' : 'Enable'}</button>
                    <button className="uae-btn" onClick={() => setActive(row)}>Edit</button>
                    <button className="uae-btn" onClick={() => uaeApi.connectorLogs(tenantId, row.id).then(setLogs)}>View logs</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {logs.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {logs.map((log) => <div key={log.id} className="uae-meta"><span>{new Date(log.at).toLocaleString()} <b>{log.message}</b></span></div>)}
        </div>
      )}
      {active && <ConnectorDrawer tenantId={tenantId} initial={active} onClose={() => { setActive(null); load(); }} />}
    </Panel>
  );
};

const ConnectorDrawer: React.FC<{ tenantId: string; initial: any; onClose: () => void }> = ({ tenantId, initial, onClose }) => {
  const [form, setForm] = useState({ ...initial, secret: '' });
  const save = async () => {
    try {
      await uaeApi.saveConnector(tenantId, { ...form, timeoutSeconds: Number(form.timeoutSeconds) || 30 }, initial.id);
      toast.success('Connector saved. Secrets are stored only as a reference.');
      onClose();
    } catch (error) {
      toast.error(message(error, 'Connector was not saved.'));
    }
  };
  return (
    <div className="uae-drawer-backdrop" onClick={onClose}>
      <div className="uae-drawer" onClick={(event) => event.stopPropagation()}>
        <h2 style={{ marginBottom: 12 }}>{initial.id ? 'Edit connector' : 'New connector'}</h2>
        <div className="uae-form">
          {['connectorName', 'systemType', 'direction', 'baseUrl', 'authenticationType', 'timeoutSeconds', 'retryPolicy', 'deliveryMode', 'frequency', 'legalEntityId'].map((key) => (
            <label key={key} className="uae-field">{key}
              <input value={form[key] ?? ''} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
            </label>
          ))}
          <label className="uae-field wide">Secret reference stays hidden. Enter a new secret only to rotate it.
            <input type="password" value={form.secret} autoComplete="new-password" onChange={(event) => setForm({ ...form, secret: event.target.value })} />
          </label>
        </div>
        <div className="uae-actions" style={{ marginTop: 12 }}>
          <button className="uae-btn primary" onClick={save}>Save</button>
          <button className="uae-btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
};

const AspConfiguration: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  const load = () => uaeApi.asps(tenantId).then(setRows);
  useEffect(() => { load(); }, [tenantId]);
  const act = async (id: string, action: string) => {
    try {
      await uaeApi.aspAction(tenantId, id, action);
      toast.success(`ASP ${action} recorded`);
      load();
    } catch (error) {
      toast.error(message(error, 'ASP action failed.'));
    }
  };
  return (
    <Panel title="ASP configuration">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead>
            <tr>{['ASP', 'Provider', 'Environment', 'Mode', 'Status', 'Active', 'Credential', 'Actions'].map((heading) => <th key={heading}>{heading}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.aspName}</td>
                <td>{row.providerType}</td>
                <td>{row.environment}</td>
                <td>{row.operatingMode}</td>
                <td><StatusChip value={row.connectionStatus} /></td>
                <td>{row.active ? 'Active' : 'Inactive'}</td>
                <td>{row.credentialReference}</td>
                <td>
                  <div className="uae-actions">
                    <button className="uae-btn" onClick={() => act(row.id, 'test')}>Test connection</button>
                    <button className="uae-btn" onClick={() => act(row.id, 'activate')}>Activate</button>
                    <button className="uae-btn" onClick={() => act(row.id, 'deactivate')}>Deactivate</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
};

const LegalEntities: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [readiness, setReadiness] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const load = () => {
    uaeApi.legalEntities(tenantId).then(setRows);
    uaeApi.entityReadiness(tenantId).then(setReadiness);
  };
  useEffect(() => { load(); }, [tenantId]);
  return (
    <Panel title="Legal entities" extra={<button className="uae-btn primary" onClick={() => setActive({ entityName: '', legalName: '', entityCode: '', country: 'AE', defaultCurrency: 'AED', status: 'Active', vatGroupMember: false })}>Add legal entity</button>}>
      <div className="uae-table-wrap" style={{ marginBottom: 12 }}>
        <table className="uae-table">
          <thead><tr>{['Entity', 'TRN', 'TIN', 'Electronic address', 'ASP', 'Sources', 'Mapping', 'Compliance', 'Master data', 'Readiness', 'Status'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {readiness.map((row) => (
              <tr key={row.id}>
                <td>{row.entity}</td>
                <td>{row.trn}</td>
                <td>{row.tin}</td>
                <td>{row.electronicAddress}</td>
                <td>{row.asp}</td>
                <td>{row.sourceSystems}</td>
                <td>{row.mappingPercent}%</td>
                <td>{row.compliancePercent}%</td>
                <td>{row.masterDataPercent}%</td>
                <td>{row.readiness}{(row.missing || []).length ? ` · ${(row.missing || []).join(', ')}` : ''}</td>
                <td><StatusChip value={row.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Entity', 'Code', 'TRN', 'Emirate', 'Peppol', 'ASP', 'Status', ''].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.entityName}</td>
                <td>{row.entityCode}</td>
                <td>{row.trn}</td>
                <td>{row.emirate}</td>
                <td>{row.peppolIdentifier}</td>
                <td>{row.aspId}</td>
                <td><StatusChip value={row.status} /></td>
                <td><button className="uae-btn" onClick={() => setActive(row)}>Edit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {active && (
        <EntityDrawer tenantId={tenantId} initial={active} onClose={() => { setActive(null); load(); }} />
      )}
    </Panel>
  );
};

const EntityDrawer: React.FC<{ tenantId: string; initial: any; onClose: () => void }> = ({ tenantId, initial, onClose }) => {
  const [form, setForm] = useState(initial);
  const save = async () => {
    try {
      await uaeApi.saveLegalEntity(tenantId, form, initial.id);
      toast.success('Legal entity saved');
      onClose();
    } catch (error) {
      toast.error(message(error, 'Legal entity was not saved.'));
    }
  };
  return (
    <div className="uae-drawer-backdrop" onClick={onClose}>
      <div className="uae-drawer" onClick={(event) => event.stopPropagation()}>
        <h2 style={{ marginBottom: 12 }}>Legal entity</h2>
        <div className="uae-form">
          {ENTITY_FIELDS.map(([key, label]) => (
            <label key={key} className="uae-field">{label}
              <input value={form[key] ?? ''} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
            </label>
          ))}
        </div>
        <div className="uae-actions" style={{ marginTop: 12 }}>
          <button className="uae-btn primary" onClick={save}>Save</button>
          <button className="uae-btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
};

const MasterData: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  const [compliance, setCompliance] = useState<any>(null);
  const [lists, setLists] = useState<any>(null);
  useEffect(() => {
    uaeApi.masterData(tenantId).then(setData);
    uaeApi.masterCompliance().then(setCompliance);
    uaeApi.codeLists().then(setLists);
  }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading master data…</div>;
  return (
    <>
      <Panel title="Currencies, emirates and documents">
        <div className="uae-meta">
          <span>Currencies <b>{(data.currencies || []).join(', ')}</b></span>
          <span>Emirates <b>{(data.emirates || []).join(', ')}</b></span>
          <span>Document types <b>{(data.documentTypes || []).join(', ')}</b></span>
          <span>VAT categories <b>{(data.vatCategories || []).join(', ')}</b></span>
        </div>
      </Panel>
      {compliance && (
        <Panel title="Master data compliance">
          {Object.entries(compliance).map(([domain, rows]) => (
            <div key={domain} style={{ marginBottom: 12 }}>
              <h2 style={{ fontSize: 13 }}>{domain}</h2>
              <div className="uae-table-wrap">
                <table className="uae-table">
                  <thead><tr><th>Source / code</th><th>Canonical</th><th>Regulatory code</th><th>Status</th><th>Effective</th></tr></thead>
                  <tbody>
                    {(rows as any[]).slice(0, 6).map((row, index) => (
                      <tr key={`${domain}-${index}`}>
                        <td>{row.sourceValue || row.code}</td>
                        <td>{row.canonicalValue || row.description}</td>
                        <td>{row.regulatoryCode || row.code}</td>
                        <td><StatusChip value={row.status || (row.active ? 'ACTIVE' : 'INACTIVE')} /></td>
                        <td>{row.effectiveFrom || row.effectiveDate}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </Panel>
      )}
      {lists && (
        <Panel title="Code lists">
          <div className="uae-meta">
            {Object.keys(lists).map((name) => <span key={name}>{name} <b>{lists[name].length}</b></span>)}
          </div>
        </Panel>
      )}
      <Panel title="Transaction type flags">
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr><th>Flag</th><th>Derivation</th></tr></thead>
            <tbody>
              {(data.transactionFlags || []).map((flag: any) => (
                <tr key={flag.code}><td>{flag.label}</td><td>{flag.derivation}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
};

const Reconciliation: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { uaeApi.reconciliation(tenantId).then(setRows); }, [tenantId]);
  return (
    <Panel title="Reconciliation">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Type', 'Transaction', 'Source', 'Source amount', 'SILA', 'ASP', 'Network', 'Ariba', 'S/4', 'Detail'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.differenceType}</td>
                <td>{row.transactionId || '—'}</td>
                <td>{row.sourceSystem}</td>
                <td>{row.sourceAmount ?? '—'}</td>
                <td>{row.silaAmount ?? '—'}</td>
                <td>{row.aspAmount ?? '—'}</td>
                <td>{row.networkState}</td>
                <td>{row.aribaState}</td>
                <td>{row.s4State}</td>
                <td>{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
};

const Archive: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  const [years, setYears] = useState<Record<string, number>>({});
  useEffect(() => {
    uaeApi.archive(tenantId).then((payload) => {
      setData(payload);
      const next: Record<string, number> = {};
      payload.retention.forEach((item: any) => { next[item.legalEntityId] = item.retentionYears; });
      setYears(next);
    });
  }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading archive…</div>;
  return (
    <>
      <Panel title="Retention by legal entity">
        {data.retention.map((policy: any) => (
          <div key={policy.id} className="uae-inline" style={{ marginBottom: 8 }}>
            <span>{policy.legalEntityId}</span>
            <input type="number" value={years[policy.legalEntityId] ?? policy.retentionYears} onChange={(event) => setYears({ ...years, [policy.legalEntityId]: Number(event.target.value) })} />
            <button className="uae-btn" onClick={async () => {
              await uaeApi.saveRetention(tenantId, policy.legalEntityId, years[policy.legalEntityId]);
              toast.success('Retention updated');
            }}>Save</button>
          </div>
        ))}
      </Panel>
      <Panel title="Archive metadata">
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr>{['Artifact', 'Transaction', 'Kind', 'Mapping version', 'Rule version', 'Created'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
            <tbody>
              {data.artifacts.map((item: any) => (
                <tr key={item.id}>
                  <td>{item.id}</td>
                  <td>{item.transactionId}</td>
                  <td>{item.kind}</td>
                  <td>{item.mappingVersionId}</td>
                  <td>{item.ruleVersion}</td>
                  <td>{new Date(item.createdAt).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
};

const AuditTrail: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { uaeApi.audit(tenantId).then(setRows); }, [tenantId]);
  return (
    <Panel title="Audit trail">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['When', 'User', 'Action', 'Area', 'Transaction', 'Old', 'New', 'Reason', 'IP'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{new Date(row.at).toLocaleString()}</td>
                <td>{row.actor}</td>
                <td>{row.action}</td>
                <td>{row.configurationArea}</td>
                <td>{row.transactionId || '—'}</td>
                <td>{formatAudit(row.oldValue)}</td>
                <td>{formatAudit(row.newValue)}</td>
                <td>{row.reason || '—'}</td>
                <td>{row.ipAddress || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
};

function formatAudit(value: unknown) {
  if (value == null || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const Reports: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  useEffect(() => { uaeApi.reports(tenantId).then(setData); }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading reports…</div>;
  return (
    <Panel title="Operational report">
      <div className="uae-kpis">
        {data.kpis.map((kpi: any) => <article key={kpi.label} className="uae-kpi"><span>{kpi.label}</span><strong>{kpi.value}</strong></article>)}
      </div>
      <p style={{ margin: '8px 0 12px', color: '#475569' }}>Open exceptions: {data.exceptionsOpen}</p>
      <div className="uae-health">
        {data.compliance.map((item: any) => (
          <div key={item.label} className="uae-health-row"><span>{item.label}</span><StatusChip value={item.status} /></div>
        ))}
      </div>
    </Panel>
  );
};

const Administration: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);
  const [form, setForm] = useState({ personName: '', silaRole: 'User', uaeRole: 'Read Only' });
  const load = () => uaeApi.administration(tenantId).then(setData);
  useEffect(() => { load(); }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading administration…</div>;
  return (
    <>
      <Panel title="Module roles">
        <p style={{ color: '#475569', marginBottom: 10, fontSize: 13 }}>These permissions sit on top of the existing SILA Fatoora roles. Login still uses the current authentication.</p>
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr><th>UAE role</th><th>Permissions</th></tr></thead>
            <tbody>
              {data.roles.map((role: string) => (
                <tr key={role}><td>{role}</td><td>{(data.permissions[role] || []).join(', ')}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Assignments">
        <div className="uae-form" style={{ marginBottom: 12 }}>
          <label className="uae-field">Person<input value={form.personName} onChange={(event) => setForm({ ...form, personName: event.target.value })} /></label>
          <label className="uae-field">SILA role
            <select value={form.silaRole} onChange={(event) => setForm({ ...form, silaRole: event.target.value })}>
              {data.silaRoles.map((role: string) => <option key={role}>{role}</option>)}
            </select>
          </label>
          <label className="uae-field">UAE role
            <select value={form.uaeRole} onChange={(event) => setForm({ ...form, uaeRole: event.target.value })}>
              {data.roles.map((role: string) => <option key={role}>{role}</option>)}
            </select>
          </label>
          <button className="uae-btn primary" onClick={async () => {
            try {
              await uaeApi.assignRole(tenantId, form);
              toast.success('Role assigned');
              load();
            } catch (error) {
              toast.error(message(error, 'Assignment was not saved.'));
            }
          }}>Assign</button>
        </div>
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr><th>Person</th><th>SILA role</th><th>UAE role</th></tr></thead>
            <tbody>
              {data.assignments.map((row: any) => (
                <tr key={row.id}><td>{row.personName}</td><td>{row.silaRole}</td><td>{row.uaeRole}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
};
