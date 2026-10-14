import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { uaeApi } from './api';
import { Empty, Panel, StatusChip } from './ui';

const FILTERS = ['direction', 'documentType', 'sourceSystem', 'lifecycleStatus', 'q'] as const;
const TABS = ['Overview', 'Source Data', 'OPERA Source', 'Mapped Data', 'PINT-AE Data', 'Compliance', 'ASP', 'Network Status', 'ERP/Ariba', 'History', 'Audit'];

function message(error: any, fallback: string) {
  return error?.response?.data?.detail || fallback;
}

export const Transactions: React.FC<{ tenantId: string; txnId?: string }> = ({ tenantId, txnId }) => {
  if (txnId) return <TransactionDetail tenantId={tenantId} txnId={txnId} />;
  return <TransactionList tenantId={tenantId} />;
};

const TransactionList: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [allRows, setAllRows] = useState<any[]>([]);
  const [entities, setEntities] = useState<Record<string, string>>({});
  const [filters, setFilters] = useState<Record<string, string>>({ direction: '', documentType: '', sourceSystem: '', lifecycleStatus: '', q: '' });

  useEffect(() => {
    uaeApi.transactions(tenantId, {}).then(setAllRows);
    uaeApi.legalEntities(tenantId).then((items) => {
      const names: Record<string, string> = {};
      items.forEach((item: any) => { names[item.id] = item.entityName; });
      setEntities(names);
    });
  }, [tenantId]);

  const rows = allRows.filter((row) => {
    if (filters.direction && row.direction !== filters.direction) return false;
    if (filters.documentType && row.documentType !== filters.documentType) return false;
    if (filters.sourceSystem && row.sourceSystem !== filters.sourceSystem) return false;
    if (filters.lifecycleStatus && row.lifecycleStatus !== filters.lifecycleStatus) return false;
    const query = filters.q.toLowerCase();
    if (!query) return true;
    return [row.id, row.invoiceNumber, row.supplier, row.buyer, row.sourceSystem].join(' ').toLowerCase().includes(query);
  });

  return (
    <>
      <div className="uae-filters">
        <input placeholder="Search invoice, party, or id" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} />
        {FILTERS.filter((name) => name !== 'q').map((name) => (
          <select key={name} value={filters[name]} onChange={(event) => setFilters({ ...filters, [name]: event.target.value })}>
            <option value="">{name}</option>
            {Array.from(new Set(allRows.map((row) => row[name]).filter(Boolean))).map((value) => (
              <option key={String(value)} value={String(value)}>{String(value)}</option>
            ))}
          </select>
        ))}
      </div>
      <div className="uae-table-wrap">
        <table className="uae-table">
          <thead>
            <tr>
              {['Transaction ID', 'Direction', 'Document Type', 'Invoice Number', 'Source System', 'Legal Entity', 'Supplier', 'Buyer', 'Invoice Date', 'Amount', 'Currency', 'Source Status', 'Compliance Status', 'ASP Status', 'Network Status', 'ERP Status', 'Created', 'Updated'].map((heading) => <th key={heading}>{heading}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={18}><Empty>No transactions match these filters.</Empty></td></tr>}
            {rows.map((row) => (
              <tr key={row.id}>
                <td><Link to={`/uae-einvoicing/transactions/${row.id}`}>{row.id}</Link></td>
                <td>{row.direction}</td>
                <td>{row.documentType}</td>
                <td>{row.invoiceNumber}</td>
                <td>{row.sourceSystem}</td>
                <td>{entities[row.legalEntityId] || row.legalEntityId}</td>
                <td>{row.supplier}</td>
                <td>{row.buyer}</td>
                <td>{row.invoiceDate}</td>
                <td>{Number(row.amount).toLocaleString()}</td>
                <td>{row.currency}</td>
                <td><StatusChip value={row.sourceStatus} /></td>
                <td><StatusChip value={row.complianceStatus} /></td>
                <td><StatusChip value={row.aspStatus} /></td>
                <td><StatusChip value={row.networkStatus} /></td>
                <td><StatusChip value={row.erpStatus} /></td>
                <td>{new Date(row.createdAt).toLocaleDateString()}</td>
                <td>{new Date(row.updatedAt).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
};

const TransactionDetail: React.FC<{ tenantId: string; txnId: string }> = ({ tenantId, txnId }) => {
  const [txn, setTxn] = useState<any>(null);
  const [tab, setTab] = useState('Overview');
  const [path, setPath] = useState('seller.trn');
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('');
  const [lineFocus, setLineFocus] = useState<number | null>(null);

  const load = () => uaeApi.transaction(tenantId, txnId).then(setTxn);
  useEffect(() => { load(); }, [tenantId, txnId]);

  if (!txn) return <div className="uae-panel">Loading transaction…</div>;

  const failed = (txn.validationResults || []).filter((item: any) => item.status === 'FAILED');
  const sections = Object.entries(txn.canonical || {});

  const correct = async () => {
    try {
      const updated = await uaeApi.correct(tenantId, txnId, { path, value, reason });
      setTxn(updated);
      toast.success('Correction audited and the compliance gate ran again.');
    } catch (error) {
      toast.error(message(error, 'Correction was not applied.'));
    }
  };

  const runMode = async (mode: string) => {
    try {
      setTxn(await uaeApi.reprocess(tenantId, txnId, mode));
      toast.success(`${mode} completed on ${txnId}.`);
    } catch (error) {
      toast.error(message(error, 'Reprocess was not allowed.'));
    }
  };
  const reprocess = () => runMode('full');

  return (
    <>
      <div className="uae-inline" style={{ marginBottom: 12 }}>
        <div>
          <Link to="/uae-einvoicing/transactions">Transactions</Link>
          <h1 style={{ fontSize: 20, marginTop: 4 }}>{txn.invoiceNumber} · {txn.id}</h1>
        </div>
        <div className="uae-actions">
          <StatusChip value={txn.complianceScore?.label || txn.lifecycleStatus} />
          <button className="uae-btn" onClick={() => runMode('revalidate')}>Revalidate</button>
          <button className="uae-btn" onClick={() => runMode('remap')}>Remap</button>
          <button className="uae-btn" onClick={() => runMode('reenrich')}>Re-enrich</button>
          <button className="uae-btn primary" onClick={reprocess}>Full reprocess</button>
        </div>
      </div>
      <div className="uae-tabs">
        {TABS.map((item) => (
          <button key={item} className="uae-btn" aria-selected={tab === item} onClick={() => setTab(item)}>{item}</button>
        ))}
      </div>
      {tab === 'Overview' && (
        <Panel title="Lifecycle">
          <div className="uae-meta">
            <span>Direction <b>{txn.direction}</b></span>
            <span>Source <b>{txn.sourceSystem}</b></span>
            <span>Legal entity <b>{txn.legalEntity?.entityName}</b></span>
            <span>Amount <b>{Number(txn.amount).toLocaleString()} {txn.currency}</b></span>
            <span>Compliance <b>{txn.complianceScore?.label || txn.complianceStatus}</b></span>
            <span>Transaction type <b>{txn.transactionType?.code || '—'}</b></span>
            <span>Profile <b>{txn.complianceVersion || '—'}</b></span>
            <span>Entity resolution <b>{txn.entityResolution?.status || '—'}</b></span>
            <span>Document class <b>{txn.documentClass || txn.documentType}</b></span>
            <span>Source of truth <b>{txn.sourceOfTruth?.regulatorySource || '—'}</b></span>
            <span>ASP <b>{txn.aspStatus}</b></span>
          </div>
          <ol className="uae-timeline" style={{ marginTop: 14 }}>
            {(txn.timeline || []).map((event: any, index: number) => (
              <li key={`${event.at}-${index}`}><time>{new Date(event.at).toLocaleString()}</time><span>{event.label}</span></li>
            ))}
          </ol>
          <div className="uae-meta" style={{ marginTop: 12 }}>
            {(txn.validationResults || []).filter((item: any) => item.lineNumber).reduce((groups: any[], item: any) => {
              const found = groups.find((group) => group.line === item.lineNumber);
              const failed = item.status === 'FAILED';
              if (found) found.failed = found.failed || failed;
              else groups.push({ line: item.lineNumber, failed });
              return groups;
            }, []).map((group: { line: number; failed: boolean }) => (
              <button key={group.line} className="uae-btn" onClick={() => { setLineFocus(group.line); setTab('Compliance'); }}>
                Line {group.line} <b>{group.failed ? 'FAIL' : 'PASS'}</b>
              </button>
            ))}
          </div>
          <div className="uae-actions" style={{ marginTop: 12 }}>
            {(txn.classification || []).map((flag: any) => (
              <StatusChip key={flag.code} value={`${flag.label}: ${flag.derived ? 'Yes' : 'No'}${flag.manualOverride ? ' (override)' : ''}`} />
            ))}
          </div>
          <Lineage canonical={txn.canonical} />
        </Panel>
      )}
      {tab === 'OPERA Source' && <OperaSource txn={txn} />}
      {['Source Data', 'Mapped Data', 'PINT-AE Data'].includes(tab) && (
        <FieldTable sections={sections} mode={tab === 'Source Data' ? 'sourceValue' : tab === 'Mapped Data' ? 'mappedValue' : 'finalValue'} />
      )}
      {tab === 'Compliance' && (
        <Panel title="Compliance gate">
          {(failed.length === 0) && <p>No failed rules. The transaction can proceed toward ASP readiness.</p>}
          {lineFocus != null && <p>Line {lineFocus} rules</p>}
          {failed.filter((item: any) => lineFocus == null || item.lineNumber === lineFocus).map((item: any) => (
            <article key={item.ruleId} className="uae-source" style={{ marginBottom: 8 }}>
              <header><b>{item.ruleId} · {item.ruleName}</b><StatusChip value={item.severity} /></header>
              <div className="uae-meta">
                <span>Field <b>{item.field}</b></span>
                <span>Source value <b>{String(item.sourceValue ?? '—')}</b></span>
                <span>Mapped value <b>{String(item.mappedValue ?? '—')}</b></span>
                <span>Expected <b>{item.expected}</b></span>
                <span className="uae-field wide">Resolution <b>{item.resolution}</b></span>
              </div>
            </article>
          ))}
          <div className="uae-form" style={{ marginTop: 12 }}>
            <label className="uae-field">Field path<input value={path} onChange={(event) => setPath(event.target.value)} /></label>
            <label className="uae-field">Corrected value<input value={value} onChange={(event) => setValue(event.target.value)} /></label>
            <label className="uae-field wide">Reason<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
            <button className="uae-btn primary" onClick={correct}>Correct and revalidate</button>
          </div>
        </Panel>
      )}
      {tab === 'ASP' && (
        <Panel title="ASP status">
          <div className="uae-meta">
            <span>Status <b>{txn.aspStatus}</b></span>
            <span>Gate <b>{txn.lifecycleStatus === 'COMPLIANCE_FAILED' ? 'Held — compliance failed' : txn.lifecycleStatus}</b></span>
          </div>
          <p style={{ marginTop: 10, color: '#475569', fontSize: 13 }}>Live ASP submission is not enabled in this phase. The gate still blocks ERROR failures before any later submission.</p>
        </Panel>
      )}
      {tab === 'Network Status' && <Panel title="Network status"><StatusChip value={txn.networkStatus} /></Panel>}
      {tab === 'ERP/Ariba' && (
        <Panel title="ERP and Ariba">
          <div className="uae-meta">
            <span>ERP status <b>{txn.erpStatus}</b></span>
            <span>Source status <b>{txn.sourceStatus}</b></span>
            <span>Source system <b>{txn.sourceSystem}</b></span>
          </div>
        </Panel>
      )}
      {tab === 'History' && (
        <Panel title="History">
          <ol className="uae-timeline">
            {(txn.timeline || []).map((event: any, index: number) => (
              <li key={`${event.at}-${index}`}><time>{new Date(event.at).toLocaleString()}</time><span>{event.label}</span></li>
            ))}
          </ol>
        </Panel>
      )}
      {tab === 'Audit' && (
        <Panel title="Audit">
          {(txn.history || []).length === 0 && <Empty>No audited changes for this transaction yet.</Empty>}
          {(txn.history || []).map((event: any) => (
            <article key={event.id} className="uae-source" style={{ marginBottom: 8 }}>
              <header><b>{event.action}</b><time>{new Date(event.at).toLocaleString()}</time></header>
              <div className="uae-meta">
                <span>User <b>{event.actor}</b></span>
                <span>Reason <b>{event.reason || '—'}</b></span>
              </div>
            </article>
          ))}
        </Panel>
      )}
    </>
  );
};

const FieldTable: React.FC<{ sections: [string, any][]; mode: 'sourceValue' | 'mappedValue' | 'finalValue' }> = ({ sections, mode }) => (
  <div className="uae-table-wrap">
    <table className="uae-table">
      <thead>
        <tr><th>Section</th><th>Field</th><th>{mode === 'sourceValue' ? 'Source' : mode === 'mappedValue' ? 'Mapped' : 'Final'}</th><th>Enriched</th><th>Mapping rule</th><th>Derivation</th><th>Validation</th></tr>
      </thead>
      <tbody>
        {sections.flatMap(([section, fields]) => Object.entries(fields || {}).map(([name, value]: [string, any]) => (
          <tr key={`${section}.${name}`}>
            <td>{section}</td>
            <td>{name}</td>
            <td>{formatValue(value?.[mode])}</td>
            <td>{formatValue(value?.enrichedValue)}</td>
            <td>{value?.mappingRule}</td>
            <td>{value?.derivationRule || '—'}</td>
            <td><StatusChip value={value?.validationStatus} /></td>
          </tr>
        )))}
      </tbody>
    </table>
  </div>
);

function formatValue(value: unknown) {
  if (value == null || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function Lineage({ canonical }: { canonical: any }) {
  const rows = ['seller.trn', 'seller.name', 'buyer.trn', 'buyer.name', 'tax.category', 'tax.rate'].map((path) => {
    const [section, name] = path.split('.');
    const value = canonical?.[section]?.[name];
    if (!value?.lineage && !value?.enrichmentSource) return null;
    return { path, value };
  }).filter(Boolean) as { path: string; value: any }[];
  if (!rows.length) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <h2 style={{ fontSize: 14 }}>Lineage</h2>
      {rows.map((row) => (
        <div key={row.path} className="uae-meta">
          <span>{row.path} <b>{formatValue(row.value.finalValue)}</b></span>
          <span>Source <b>{row.value.lineage?.source || row.value.enrichmentSource || 'Source payload'}</b></span>
          <span>Original <b>{formatValue(row.value.lineage?.original ?? row.value.sourceValue)}</b></span>
          <span>Rule <b>{row.value.lineage?.rule || row.value.derivationRule || '—'}</b></span>
        </div>
      ))}
    </div>
  );
}

function OperaSource({ txn }: { txn: any }) {
  const [raw, setRaw] = useState(false);
  const payload = txn.sourcePayload || {};
  if (txn.sourceCode !== 'OPERA' && txn.sourceSystem !== 'OPERA') {
    return <Panel title="OPERA source"><Empty>This transaction did not come from OPERA.</Empty></Panel>;
  }
  const groups: [string, string[]][] = [
    ['Document', ['FOLIO_NO', 'DOCUMENT_SCOPE', 'BILLINGDATE', 'CURRENCY']],
    ['Property', ['PROPERTY_CODE', 'HOTEL_CODE']],
    ['Customer', ['CUSTOMER_CODE', 'CUSTOMER_NAME', 'CUSTOMER_TRN']],
    ['Tax', ['TAXAMOUNT', 'TAX_CATEGORY']],
    ['Totals', ['NETAMOUNT', 'GROSSAMOUNT']],
  ];
  return (
    <Panel title="OPERA source" extra={<button className="uae-btn" onClick={() => setRaw((value) => !value)}>{raw ? 'Structured view' : 'Raw payload'}</button>}>
      <div className="uae-meta">
        <span>Payload id <b>{txn.sourcePayloadId || '—'}</b></span>
        <span>Hash <b>{txn.payloadHash || '—'}</b></span>
        <span>Version <b>{txn.version || 1}</b></span>
      </div>
      {raw ? <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(payload, null, 2)}</pre> : groups.map(([title, fields]) => (
        <div key={title} style={{ marginTop: 10 }}>
          <h2 style={{ fontSize: 13 }}>{title}</h2>
          <div className="uae-meta">
            {fields.map((name) => <span key={name}>{name} <b>{formatValue(payload[name])}</b></span>)}
          </div>
        </div>
      ))}
    </Panel>
  );
}
