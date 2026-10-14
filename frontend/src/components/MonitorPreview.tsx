import React from 'react';
import { 
  FileText, CheckCircle2, XCircle, Download, Upload, Info, 
  Search, Lock, Sparkles, Zap, ShieldCheck, Image as ImageIcon
} from 'lucide-react';

export const MonitorPreview: React.FC = () => {
  return (
    <div className="login-preview-container">
      {/* Soft ambient backlight glow */}
      <div className="preview-glow-backdrop"></div>

      {/* Main Browser Window Mockup Frame */}
      <div className="login-preview-card vector-showcase-frame">
        {/* Browser Top Window Bar */}
        <div className="preview-window-bar">
          <div className="mac-controls">
            <span className="dot red"></span>
            <span className="dot yellow"></span>
            <span className="dot green"></span>
          </div>
          <div className="window-address-bar">
            <Lock size={11} className="lock-icon" />
            <span>https://sila-fatoora.chervicaon.com/dashboard</span>
          </div>
        </div>

        {/* Scaled Crisp Vector Preview Container */}
        <div className="vector-preview-content">

          {/* Card 1: Invoice Dashboard */}
          <div className="preview-app-card">
            <div className="app-card-top-bar">
              <h3 className="app-title">Invoice Dashboard</h3>
              <span className="app-badge-primary">Invoice Management System</span>
            </div>

            {/* Metrics Row */}
            <div className="metrics-row">
              <div className="metric-box accent-blue">
                <div className="metric-header">
                  <span>TOTAL INVOICE</span>
                  <span className="metric-icon-chip blue"><FileText size={12} /></span>
                </div>
                <div className="metric-value">16</div>
              </div>

              <div className="metric-box accent-green">
                <div className="metric-header">
                  <span>PROCESSED</span>
                  <span className="metric-icon-chip green"><CheckCircle2 size={12} /></span>
                </div>
                <div className="metric-value">0</div>
              </div>

              <div className="metric-box accent-green">
                <div className="metric-header">
                  <span>SUCCESS</span>
                  <span className="metric-icon-chip green"><CheckCircle2 size={12} /></span>
                </div>
                <div className="metric-value">16</div>
              </div>

              <div className="metric-box accent-red">
                <div className="metric-header">
                  <span>FAILED</span>
                  <span className="metric-icon-chip red"><XCircle size={12} /></span>
                </div>
                <div className="metric-value">0</div>
              </div>
            </div>

            {/* Search & Filter Bar */}
            <div className="filter-card">
              <div className="showing-tag">Showing: <span className="tag-pill">All</span></div>
              <div className="search-bar-mock">
                <Search size={13} color="#94a3b8" />
                <span>Search by PO number, supplier, or company code...</span>
              </div>
              <div className="filter-selects-row">
                <div className="mock-select">Source: Manual ▾</div>
                <div className="mock-select">Supplier: All Suppliers ▾</div>
                <div className="mock-select">PO Number: All PO Numbers ▾</div>
                <div className="mock-select">Status: All ▾</div>
              </div>
              <div className="action-buttons-row">
                <div className="mock-btn btn-green"><Download size={13} /> Download</div>
                <div className="mock-btn btn-red-outline">Clear Filters</div>
                <div className="mock-btn btn-blue"><Upload size={13} /> Upload PO Data</div>
                <div className="mock-btn btn-purple"><Upload size={13} /> Upload Receipts</div>
                <div className="mock-btn btn-gray"><Info size={13} /> Excel Format Info</div>
              </div>
            </div>

            {/* Table Mockup */}
            <div className="table-mock-wrapper">
              <table className="preview-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>SUPPLIER NAME</th>
                    <th>COMMENTS</th>
                    <th>PO NUMBER</th>
                    <th>STATUS</th>
                    <th>INVOICE DATE</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="fw-bold">M1069</td>
                    <td>End 2 End Merchandising</td>
                    <td className="text-truncate">All checks passed via Method 3...</td>
                    <td>4500210978</td>
                    <td><span className="status-badge success">Success</span></td>
                    <td>16-06-2026</td>
                  </tr>
                  <tr>
                    <td className="fw-bold">M1068</td>
                    <td>US Foods</td>
                    <td className="text-truncate">Invoice PO ['4500924531'] vs Receipt...</td>
                    <td>4500924531</td>
                    <td><span className="status-badge success">Success</span></td>
                    <td>08-06-2026</td>
                  </tr>
                  <tr>
                    <td className="fw-bold">M1067</td>
                    <td>US. FOODS</td>
                    <td className="text-truncate">All checks passed via Method 3...</td>
                    <td>4500923171</td>
                    <td><span className="status-badge success">Success</span></td>
                    <td>06-08-2026</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Card 2: Supplier Invoice Management Upload Box */}
          <div className="preview-app-card upload-app-card">
            <h3 className="app-title center">Supplier Invoice Management</h3>
            <div className="dropzone-box-mock">
              <div className="dropzone-icon-circle">
                <ImageIcon size={24} color="#10b981" />
              </div>
              <div className="dropzone-link">Browse your files</div>
              <div className="dropzone-sub">Supports: .pdf, .zip, .png, .jpeg, .jpg (multiple files)</div>
            </div>
            <div className="start-btn-mock">Start</div>
            <div className="dropzone-footer-text">Select PDF/ZIP/PNG/JPEG or JPG files to show results.</div>
          </div>

        </div>
      </div>

      {/* Floating Feature Badges around the preview */}
      <div className="floating-badge badge-top-right">
        <span className="badge-icon-chip blue"><Sparkles size={13} /></span>
        <span>AI-Powered Extraction</span>
      </div>

      <div className="floating-badge badge-bottom-left">
        <span className="badge-icon-chip green"><Zap size={13} /></span>
        <span>Instant PO Data Sync</span>
      </div>

      <div className="floating-badge badge-bottom-right">
        <span className="badge-icon-chip purple"><ShieldCheck size={13} /></span>
        <span>Enterprise Compliant</span>
      </div>
    </div>
  );
};
