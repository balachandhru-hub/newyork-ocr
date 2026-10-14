import React, { useEffect, useState } from 'react';
import { Link, Navigate, useMatch, useParams } from 'react-router-dom';
import { Header } from '../components/Header';
import { uaeApi, type UaeContext } from './api';
import { CommandCenter } from './CommandCenter';
import { Transactions } from './Transactions';
import { Operations } from './Operations';
import { Onboarding } from './Onboarding';
import './uae.css';

const NAV = [
  ['command-center', 'Command Center'],
  ['transactions', 'Transactions'],
  ['exceptions', 'Exceptions'],
  ['compliance', 'Compliance'],
  ['compliance-simulator', 'Compliance Simulator'],
  ['mapping-readiness', 'Mapping Readiness'],
  ['regulatory-change', 'Regulatory Change'],
  ['field-mapping', 'Field Mapping'],
  ['integration-hub', 'Integration Hub'],
  ['asp-configuration', 'ASP Configuration'],
  ['legal-entities', 'Legal Entities'],
  ['entity-readiness', 'Entity Readiness'],
  ['properties', 'Properties'],
  ['implementation-readiness', 'Implementation Readiness'],
  ['source-of-truth', 'Source of Truth'],
  ['opera-gap', 'OPERA Gap Analysis'],
  ['sap-gap', 'SAP Gap Register'],
  ['buyer-master', 'Buyer Master'],
  ['source-test-lab', 'Source Test Lab'],
  ['master-data', 'Master Data'],
  ['rules', 'Rules'],
  ['reconciliation', 'Reconciliation'],
  ['archive', 'Archive'],
  ['audit-trail', 'Audit Trail'],
  ['reports', 'Reports'],
  ['administration', 'Administration'],
];

export const UaeModule: React.FC = () => {
  const params = useParams();
  const detail = useMatch('/uae-einvoicing/transactions/:txnId');
  const section = detail ? 'transactions' : (params.section || 'command-center');
  const [context, setContext] = useState<UaeContext | null>(null);
  const [tenantId, setTenantId] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    uaeApi.context().then((data) => {
      setContext(data);
      setTenantId((current) => current || data.tenants[0]?.id || '');
    }).catch(() => setError('The UAE e-Invoicing service is not available.'));
  }, []);

  if (section && !NAV.some(([slug]) => slug === section) && !detail) {
    return <Navigate to="/uae-einvoicing/command-center" replace />;
  }

  return (
    <>
      <Header />
      <div className="uae-page">
        <nav className="uae-nav" aria-label="UAE e-Invoicing">
          <div className="uae-nav-label">UAE e-Invoicing</div>
          {NAV.map(([slug, label]) => (
            <Link key={slug} to={`/uae-einvoicing/${slug}`} className={section === slug ? 'active' : ''}>
              {label}
            </Link>
          ))}
        </nav>
        <main className="uae-main">
          <div className="uae-heading">
            <div>
              <h1>UAE e-Invoicing</h1>
              <p>Customer compliance, mapping and transaction control for every UAE legal entity.</p>
            </div>
            <div className="uae-toolbar">
              <select aria-label="Tenant" value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
                {(context?.tenants || []).map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
                ))}
              </select>
              {context && <span className="uae-role">{context.role}</span>}
            </div>
          </div>
          {error && <div className="uae-panel">{error}</div>}
          {tenantId && section === 'command-center' && <CommandCenter tenantId={tenantId} />}
          {tenantId && section === 'transactions' && <Transactions tenantId={tenantId} txnId={detail?.params.txnId} />}
          {tenantId && ['entity-readiness', 'properties', 'implementation-readiness', 'source-of-truth', 'opera-gap', 'sap-gap', 'buyer-master', 'source-test-lab'].includes(section) && (
            <Onboarding section={section} tenantId={tenantId} />
          )}
          {tenantId && !['command-center', 'transactions', 'entity-readiness', 'properties', 'implementation-readiness', 'source-of-truth', 'opera-gap', 'sap-gap', 'buyer-master', 'source-test-lab'].includes(section) && (
            <Operations section={section} tenantId={tenantId} />
          )}
        </main>
      </div>
    </>
  );
};
