import React, { useEffect, useState } from 'react';
import { uaeApi } from './api';
import { Panel, StatusChip } from './ui';

export const CommandCenter: React.FC<{ tenantId: string }> = ({ tenantId }) => {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    uaeApi.commandCenter(tenantId).then(setData);
  }, [tenantId]);

  if (!data) return <div className="uae-panel">Loading control center…</div>;

  return (
    <>
      <div className="uae-heading">
        <div>
          <h1>UAE e-Invoicing Control Center</h1>
          <p>Real-time compliance, integration and transaction monitoring</p>
        </div>
      </div>
      <div className="uae-kpis">
        {data.kpis.map((kpi: { label: string; value: number }) => (
          <article key={kpi.label} className="uae-kpi">
            <span>{kpi.label}</span>
            <strong>{kpi.value}</strong>
          </article>
        ))}
      </div>
      <Panel title="Transaction split">
        <div className="uae-split">
          {data.split.map((item: { label: string; value: number }) => (
            <div key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </Panel>
      <div className="uae-source-grid">
        <Panel title="Source systems">
          <div className="uae-source-list">
            {data.sources.map((source: any) => (
              <article key={source.name} className="uae-source">
                <header>
                  <b>{source.name}</b>
                  <StatusChip value={source.connectionStatus} />
                </header>
                <div className="uae-meta">
                  <span>Last success <b>{source.lastSuccess ? new Date(source.lastSuccess).toLocaleString() : '—'}</b></span>
                  <span>Today <b>{source.transactionsToday}</b></span>
                  <span>Success <b>{source.successPercent}%</b></span>
                  <span>Failures <b>{source.failures}</b></span>
                  <span>Pending <b>{source.pending}</b></span>
                </div>
              </article>
            ))}
          </div>
        </Panel>
        <Panel title="ASP">
          {data.asp ? (
            <>
              <div className="uae-inline">
                <strong>{data.asp.configuredAsp}</strong>
                <StatusChip value={data.asp.connectionStatus} />
              </div>
              <div className="uae-meta">
                <span>Provider <b>{data.asp.providerType}</b></span>
                <span>Mode <b>{data.asp.operatingMode}</b></span>
                <span>Last communication <b>{data.asp.lastCommunication ? new Date(data.asp.lastCommunication).toLocaleString() : '—'}</b></span>
                <span>Configured ASPs <b>{data.asp.configuredCount}</b></span>
                <span>Submitted <b>{data.asp.submitted}</b></span>
                <span>Accepted <b>{data.asp.accepted}</b></span>
                <span>Rejected <b>{data.asp.rejected}</b></span>
                <span>Pending <b>{data.asp.pending}</b></span>
              </div>
            </>
          ) : <p>No ASP is configured for this tenant.</p>}
        </Panel>
      </div>
      {data.phase2 && (
        <>
          <Panel title="Compliance today">
            <div className="uae-split">
              <div><span>Invoices received</span><strong>{data.phase2.complianceToday.received}</strong></div>
              <div><span>Compliance passed</span><strong>{data.phase2.complianceToday.passed}</strong></div>
              <div><span>Compliance failed</span><strong>{data.phase2.complianceToday.failed}</strong></div>
              <div><span>Warnings</span><strong>{data.phase2.complianceToday.warnings}</strong></div>
              <div><span>Ready for ASP</span><strong>{data.phase2.complianceToday.readyForAsp}</strong></div>
            </div>
          </Panel>
          <div className="uae-two">
            <Panel title="Top failure reasons">
              {data.phase2.topFailures.length === 0 && <p>No error-level failures in this tenant.</p>}
              {data.phase2.topFailures.map((item: { reason: string; count: number }) => (
                <div key={item.reason} className="uae-health-row"><span>{item.reason}</span><b>{item.count}</b></div>
              ))}
            </Panel>
            <Panel title="Readiness">
              <div className="uae-meta">
                <span>Legal entities <b>{data.phase2.readiness.legalEntities}%</b></span>
                <span>Source systems <b>{data.phase2.readiness.sourceSystems}%</b></span>
                <span>Mappings <b>{data.phase2.readiness.mappings}%</b></span>
                <span>Rules <b>{data.phase2.readiness.rules}</b></span>
                <span>Master data <b>{data.phase2.readiness.masterData}%</b></span>
              </div>
            </Panel>
          </div>
          <Panel title="Compliance trend">
            <div className="uae-gate">
              {data.phase2.trend.map((point: { date: string; passPercent: number; failurePercent: number }) => (
                <span key={point.date}>{point.date.slice(5)} · {point.passPercent}% pass · {point.failurePercent}% fail</span>
              ))}
            </div>
          </Panel>
        </>
      )}
          <Panel title="Customer source pipeline">
            <div className="uae-source-list">
              {(data.phase3?.sources || []).map((source: any) => (
                <article key={source.code} className="uae-source">
                  <header><b>{source.source}</b><StatusChip value={source.connection} /></header>
                  <div className="uae-meta">
                    <span>Last sync <b>{source.lastSync ? new Date(source.lastSync).toLocaleString() : '—'}</b></span>
                    <span>Received today <b>{source.receivedToday}</b></span>
                    <span>Processed <b>{source.processed}</b></span>
                    <span>Compliance passed <b>{source.passed}</b></span>
                    <span>Compliance failed <b>{source.failed}</b></span>
                    <span>Exceptions <b>{source.exceptions}</b></span>
                  </div>
                </article>
              ))}
            </div>
          </Panel>
          <Panel title="Compliance health">
        <div className="uae-health">
          {data.compliance.map((item: { label: string; status: string; score: number }) => (
            <div key={item.label} className="uae-health-row">
              <span>{item.label}</span>
              <span>{item.status === 'NOT APPLICABLE' ? '—' : `${item.score}%`}</span>
              <StatusChip value={item.status} />
            </div>
          ))}
        </div>
        <div className="uae-gate">
          {data.gate.map((stage: string) => <span key={stage}>{stage}</span>)}
        </div>
      </Panel>
    </>
  );
};
