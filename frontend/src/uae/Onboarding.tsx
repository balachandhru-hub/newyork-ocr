import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { uaeApi } from './api';
import { Empty, Panel, StatusChip } from './ui';

function message(error: any, fallback: string) {
  return error?.response?.data?.detail || fallback;
}

export const Onboarding: React.FC<{ section: string; tenantId: string }> = ({ section, tenantId }) => {
  const [data, setData] = useState<any>(null);
  const load = () => uaeApi.onboarding(tenantId).then(setData);
  useEffect(() => { load(); }, [tenantId]);
  if (!data) return <div className="uae-panel">Loading onboarding…</div>;
  if (section === 'entity-readiness') return <EntityReadiness data={data} />;
  if (section === 'properties') return <Properties data={data} />;
  if (section === 'implementation-readiness') return <Implementation data={data} />;
  if (section === 'source-of-truth') return <SourceOfTruth data={data} />;
  if (section === 'opera-gap') return <OperaGap data={data} />;
  if (section === 'sap-gap') return <SapGap data={data} />;
  if (section === 'buyer-master') return <Buyers data={data} />;
  if (section === 'source-test-lab') return <TestLab tenantId={tenantId} onDone={load} />;
  return null;
};

function EntityReadiness({ data }: { data: any }) {
  const [open, setOpen] = useState<any>(null);
  return (
    <Panel title="Entity readiness" extra={<span className="uae-role">{data.customer?.customer || 'Customer'} · {data.customer?.jurisdiction || 'UAE'}</span>}>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Entity', 'TRN', 'TIN', 'VAT group', 'Electronic address', 'SAP', 'OPERA', 'Ariba', 'ASP', 'Readiness', ''].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {data.entities.map((row: any) => (
              <tr key={row.id}>
                <td>{row.entity}<div style={{ color: '#64748b' }}>{row.code}</div></td>
                <td>{row.trn}</td>
                <td>{row.tin}</td>
                <td>{row.vatGroup}</td>
                <td>{row.electronicAddress}</td>
                <td>{row.sap}</td>
                <td>{row.opera}</td>
                <td>{row.ariba}</td>
                <td>{row.asp}</td>
                <td><StatusChip value={row.readiness} /></td>
                <td><button className="uae-btn" onClick={() => setOpen(row)}>Details</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && (
        <div className="uae-drawer-backdrop" onClick={() => setOpen(null)}>
          <div className="uae-drawer" onClick={(event) => event.stopPropagation()}>
            <h2>{open.entity}</h2>
            <p>Missing configuration</p>
            {(open.missing || []).length === 0 && <Empty>No configuration gaps for this entity.</Empty>}
            <ul>{(open.missing || []).map((item: string) => <li key={item}>{item}</li>)}</ul>
            <button className="uae-btn" onClick={() => setOpen(null)}>Close</button>
          </div>
        </div>
      )}
    </Panel>
  );
}

function Properties({ data }: { data: any }) {
  return (
    <Panel title="Properties">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Property', 'Name', 'Legal entity', 'OPERA connection', 'Invoices today', 'Ready for ASP', 'Failed', 'Last sync'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {data.properties.length === 0 && <tr><td colSpan={8}><Empty>No properties are configured.</Empty></td></tr>}
            {data.properties.map((row: any) => (
              <tr key={row.id}>
                <td>{row.propertyCode}</td>
                <td>{row.propertyName}</td>
                <td><Link to="/uae-einvoicing/transactions">{row.legalEntity}</Link></td>
                <td><StatusChip value={row.connection || 'CONFIGURATION REQUIRED'} /></td>
                <td>{row.invoicesToday}</td>
                <td>{row.ready}</td>
                <td>{row.failed}</td>
                <td>{row.lastSync ? new Date(row.lastSync).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Implementation({ data }: { data: any }) {
  const readiness = data.readiness || { overall: 0, sections: [] };
  return (
    <Panel title={`${data.customer?.customer || 'Customer'} UAE e-Invoicing readiness`} extra={<StatusChip value={`${readiness.overall}%`} />}>
      <div className="uae-kpis">
        {readiness.sections.map((item: any) => (
          <article key={item.section} className="uae-kpi">
            <span>{item.section}</span>
            <strong>{item.percent}%</strong>
            <em style={{ color: '#64748b', fontStyle: 'normal' }}>{item.note || `${item.done}/${item.total}`}</em>
          </article>
        ))}
      </div>
    </Panel>
  );
}

function SourceOfTruth({ data }: { data: any }) {
  return (
    <>
      <Panel title="Source of truth">
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr>{['Business process', 'Document type', 'Regulatory source', 'Accounting', 'Procurement', 'Master data', 'ASP'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
            <tbody>
              {data.sourceOfTruth.map((row: any) => (
                <tr key={row.id}>
                  <td>{row.businessProcess}</td>
                  <td>{row.documentType}</td>
                  <td>{row.regulatorySource}</td>
                  <td>{row.accountingSystem}</td>
                  <td>{row.procurementSystem}</td>
                  <td>{row.masterDataSource}</td>
                  <td>{row.asp}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Document routing">
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr>{['Source', 'When', 'Class', 'Route'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
            <tbody>
              {data.routing.map((row: any) => (
                <tr key={row.id}><td>{row.sourceSystem}</td><td>{row.field} = {row.equals}</td><td>{row.documentClass}</td><td>{row.route}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function OperaGap({ data }: { data: any }) {
  const [filter, setFilter] = useState('All');
  const rows = (data.operaGap || []).filter((row: any) => filter === 'All' || row.status === filter);
  return (
    <Panel title="OPERA UAE compliance gap analysis" extra={
      <select aria-label="Gap status" value={filter} onChange={(event) => setFilter(event.target.value)}>
        {['All', 'DIRECT', 'MAPPED', 'DERIVED', 'ENRICHED', 'CONSTANT', 'MISSING', 'NOT APPLICABLE'].map((item) => <option key={item}>{item}</option>)}
      </select>
    }>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Business term', 'Canonical field', 'Requirement', 'OPERA field', 'Mapping', 'Enrichment', 'Status'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {rows.map((row: any) => (
              <tr key={row.canonicalField}>
                <td>{row.businessTerm}<div>{row.displayName}</div></td>
                <td>{row.canonicalField}</td>
                <td>{row.requirement}</td>
                <td>{row.sourceField || '—'}</td>
                <td>{row.mapping || '—'}</td>
                <td>{row.enrichmentSource || '—'}</td>
                <td><StatusChip value={row.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SapGap({ data }: { data: any }) {
  return (
    <>
      <Panel title="SAP data gap register">
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr>{['Business term', 'Required value', 'Existing API', 'Existing field', 'Derive', 'Enrich', 'SAP change', 'Status', 'Notes'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
            <tbody>
              {data.sapGap.map((row: any) => (
                <tr key={row.requiredValue}>
                  <td>{row.businessTerm}</td>
                  <td>{row.requiredValue}</td>
                  <td>{row.availableFromExistingApi ? 'Yes' : 'No'}</td>
                  <td>{row.existingField}</td>
                  <td>{row.canDerive ? 'Yes' : 'No'}</td>
                  <td>{row.canEnrich ? 'Yes' : 'No'}</td>
                  <td>{row.sapChangeRequired ? 'Yes' : 'No'}</td>
                  <td><StatusChip value={row.status} /></td>
                  <td>{row.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Ariba posting map">
        <p style={{ color: '#475569' }}>Regulatory fields stay in SILA. They are not forced into the Ariba invoice.</p>
        <div className="uae-table-wrap">
          <table className="uae-table">
            <thead><tr><th>Canonical field</th><th>Ariba field</th><th>Regulatory only</th></tr></thead>
            <tbody>
              {data.aribaMap.map((row: any) => (
                <tr key={row.canonicalField}><td>{row.canonicalField}</td><td>{row.aribaField}</td><td>{row.regulatoryOnly ? 'Yes' : 'No'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function Buyers({ data }: { data: any }) {
  return (
    <Panel title="Buyer master">
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead><tr>{['Customer', 'Legal name', 'TRN', 'OPERA', 'SAP', 'Ariba', 'Electronic address', 'Status'].map((heading) => <th key={heading}>{heading}</th>)}</tr></thead>
          <tbody>
            {data.buyers.length === 0 && <tr><td colSpan={8}><Empty>No buyer profiles for this tenant.</Empty></td></tr>}
            {data.buyers.map((row: any) => (
              <tr key={row.id}>
                <td>{row.customerName}</td>
                <td>{row.legalName}</td>
                <td>{row.trn}</td>
                <td>{row.operaCustomerCode || '—'}</td>
                <td>{row.sapCustomerNumber || '—'}</td>
                <td>{row.aribaVendor || '—'}</td>
                <td>{row.electronicAddress}</td>
                <td><StatusChip value={row.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const LAB = [
  ['opera-b2b', 'OPERA B2B invoice'],
  ['sap-b2b', 'SAP S/4 invoice'],
  ['sap-accounting-ref', 'SAP accounting reference'],
  ['opera-missing-buyer', 'OPERA missing buyer TRN'],
  ['opera-b2c', 'OPERA guest bill'],
];

function TestLab({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const [result, setResult] = useState<any>(null);
  const run = async (fixture: string) => {
    try {
      const response = await uaeApi.ingest(tenantId, { fixture });
      setResult(response);
      toast.success('Pipeline finished. Nothing was sent to an ASP.');
      onDone();
    } catch (error) {
      toast.error(message(error, 'The sample could not be processed.'));
    }
  };
  return (
    <Panel title="Source test lab" extra={<StatusChip value="NOT SENT TO ASP" />}>
      <p>Run a configured sample through resolution, classification, mapping, enrichment and compliance.</p>
      <div className="uae-actions">
        {LAB.map(([fixture, label]) => <button key={fixture} className="uae-btn primary" onClick={() => run(fixture)}>{label}</button>)}
      </div>
      {result && (
        <div className="uae-meta" style={{ marginTop: 12 }}>
          <span>Transaction <b>{result.transactionId ? <Link to={`/uae-einvoicing/transactions/${result.transactionId}`}>{result.transactionId}</Link> : '—'}</b></span>
          <span>Status <b>{result.duplicate || result.lifecycleStatus}</b></span>
          <span>Created <b>{String(result.created)}</b></span>
          <span>Regulatory invoice <b>{String(result.regulatoryInvoiceCreated)}</b></span>
          <span>ASP submitted <b>{String(result.aspSubmitted)}</b></span>
        </div>
      )}
    </Panel>
  );
}
