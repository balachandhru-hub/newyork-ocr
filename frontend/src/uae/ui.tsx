import React from 'react';

export function StatusChip({ value }: { value?: string | null }) {
  const text = value || '—';
  const upper = text.toUpperCase();
  let kind = 'info';
  if (upper.includes('NON-COMPLIANT') || upper.includes('FAIL') || upper.includes('REJECT') || upper === 'ERROR') kind = 'bad';
  else if (upper.includes('NOT ') || upper.startsWith('NOT_') || upper.includes('INACTIVE') || upper.includes('UNASSIGNED')) kind = 'mute';
  else if (upper.includes('WARN') || upper.includes('PENDING') || upper.includes('READY') || upper.includes('SUBMITTED') || upper.includes('OPEN')) kind = 'warn';
  else if (
    upper.includes('COMPLIANT') || upper.includes('CONNECTED') || upper.includes('ACTIVE') ||
    upper.includes('PASSED') || upper.includes('RECONCILED') || upper.includes('DELIVERED') ||
    upper.includes('POSTED') || upper.includes('CLOSED') || upper.includes('CONFIGURED')
  ) kind = 'ok';
  return <span className={`uae-chip ${kind}`}>{text}</span>;
}

export function Panel({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <section className="uae-panel">
      <div className="uae-inline">
        <h2>{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="uae-empty">{children}</div>;
}
