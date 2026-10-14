import React, { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useMatch, useParams } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import { Header } from '../components/Header';
import { uaeApi, type UaeContext } from './api';
import { CommandCenter } from './CommandCenter';
import { Transactions } from './Transactions';
import { Operations } from './Operations';
import { Onboarding } from './Onboarding';
import './uae.css';

const PRIMARY: [string, string][] = [
  ['command-center', 'Command Center'],
  ['transactions', 'Transactions'],
  ['exceptions', 'Exceptions'],
  ['compliance', 'Compliance'],
];

const RIBBONS: { id: string; label: string; items: [string, string][] }[] = [
  {
    id: 'validate',
    label: 'Validate',
    items: [
      ['compliance-simulator', 'Compliance Simulator'],
      ['mapping-readiness', 'Mapping Readiness'],
      ['regulatory-change', 'Regulatory Change'],
      ['rules', 'Rules'],
    ],
  },
  {
    id: 'connect',
    label: 'Connect',
    items: [
      ['field-mapping', 'Field Mapping'],
      ['integration-hub', 'Integration Hub'],
      ['asp-configuration', 'ASP Configuration'],
      ['master-data', 'Master Data'],
    ],
  },
  {
    id: 'entities',
    label: 'Entities',
    items: [
      ['legal-entities', 'Legal Entities'],
      ['entity-readiness', 'Entity Readiness'],
      ['properties', 'Properties'],
      ['implementation-readiness', 'Implementation Readiness'],
      ['buyer-master', 'Buyer Master'],
    ],
  },
  {
    id: 'sources',
    label: 'Sources',
    items: [
      ['source-of-truth', 'Source of Truth'],
      ['opera-gap', 'OPERA Gap Analysis'],
      ['sap-gap', 'SAP Gap Register'],
      ['source-test-lab', 'Source Test Lab'],
    ],
  },
  {
    id: 'records',
    label: 'Records',
    items: [
      ['reconciliation', 'Reconciliation'],
      ['archive', 'Archive'],
      ['audit-trail', 'Audit Trail'],
      ['reports', 'Reports'],
      ['administration', 'Administration'],
    ],
  },
];

const NAV: [string, string][] = [...PRIMARY, ...RIBBONS.flatMap((ribbon) => ribbon.items)];

export const UaeModule: React.FC = () => {
  const params = useParams();
  const detail = useMatch('/uae-einvoicing/transactions/:txnId');
  const section = detail ? 'transactions' : (params.section || 'command-center');
  const [context, setContext] = useState<UaeContext | null>(null);
  const [tenantId, setTenantId] = useState('');
  const [error, setError] = useState('');
  const [openRibbon, setOpenRibbon] = useState<string | null>(null);
  const navRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    uaeApi.context().then((data) => {
      setContext(data);
      setTenantId((current) => current || data.tenants[0]?.id || '');
    }).catch(() => setError('The UAE e-Invoicing service is not available.'));
  }, []);

  useEffect(() => {
    setOpenRibbon(null);
  }, [section]);

  useEffect(() => {
    if (!openRibbon) return undefined;
    const close = (event: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(event.target as Node)) setOpenRibbon(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenRibbon(null);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [openRibbon]);

  if (section && !NAV.some(([slug]) => slug === section) && !detail) {
    return <Navigate to="/uae-einvoicing/command-center" replace />;
  }

  const pageTitle = section === 'command-center'
    ? 'UAE e-Invoicing Control Center'
    : (NAV.find(([slug]) => slug === section)?.[1] || 'UAE e-Invoicing');

  return (
    <>
      <Header />
      <div className="uae-page">
        <div className="uae-nav-shell" ref={navRef}>
          <nav className="uae-nav" aria-label="UAE e-Invoicing">
            <div className="uae-nav-primary">
              {PRIMARY.map(([slug, label]) => (
                <Link key={slug} to={`/uae-einvoicing/${slug}`} className={section === slug ? 'active' : ''}>
                  {label}
                </Link>
              ))}
            </div>
            <div className="uae-nav-ribbons">
              {RIBBONS.map((ribbon) => {
                const selected = ribbon.items.some(([slug]) => slug === section);
                const open = openRibbon === ribbon.id;
                return (
                  <div key={ribbon.id} className="uae-menu">
                    <button
                      type="button"
                      className={`uae-ribbon-btn${selected ? ' active' : ''}${open ? ' open' : ''}`}
                      aria-expanded={open}
                      aria-haspopup="menu"
                      aria-controls={`uae-menu-${ribbon.id}`}
                      onClick={() => setOpenRibbon(open ? null : ribbon.id)}
                    >
                      {ribbon.label}
                      <ChevronDown size={14} />
                    </button>
                    {open && (
                      <div className="uae-menu-list" id={`uae-menu-${ribbon.id}`} role="menu" aria-label={ribbon.label}>
                        {ribbon.items.map(([slug, label]) => (
                          <Link key={slug} role="menuitem" to={`/uae-einvoicing/${slug}`} className={section === slug ? 'active' : ''}>
                            {label}
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </nav>
        </div>
        <main className="uae-main">
          <div className="uae-heading">
            <h2>{pageTitle}</h2>
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
