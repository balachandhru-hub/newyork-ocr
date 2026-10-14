import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  FileText,
  CheckCircle,
  RotateCcw,
  XCircle,
  Download,
  Eye,
  FileSpreadsheet,
  Info,
  X,
  Copy,
} from 'lucide-react';
import {
  fetchCustomerGraph,
  fetchDistinctSuppliers,
  fetchDistinctPoNumbers,
  fetchDistinctCompanyCodes,
  fetchPdfBase64,
  aribaDataCompare,
  syncAllS4Data,
  // uploadPurchaseOrders,
  // uploadPurchaseOrdersAndReceipts,
  // uploadAribaReceipts,
  // downloadExcelTemplate,
  exportInvoicesExcel,
} from '../api/DashboardApi';
import type { CustomerGraphRecord, AribaResponseDTO } from '../Dto/CustomerGraphDto';
import { formatPoNumbers, getProductTypeShortLabel } from '../Dto/CustomerGraphDto';
import { BuyerInvoiceCompareModal } from './BuyerInvoiceCompareModal';
import './InvoiceDashboard.css';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';

interface SearchableSelectProps {
  value: string;
  options: string[];
  allOptionLabel: string;
  labelPrefix: string;
  onChange: (value: string) => void;
}

const SearchableSelect: React.FC<SearchableSelectProps> = ({ value, options, allOptionLabel, labelPrefix, onChange }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [menuStyle, setMenuStyle] = useState<{ top: number; left: number; width: number }>({ top: 0, left: 0, width: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (rect) {
        const menuWidth = Math.max(rect.width, 220);
        const left = Math.min(rect.left, window.innerWidth - menuWidth - 8);
        setMenuStyle({ top: rect.bottom + 4, left: Math.max(8, left), width: menuWidth });
      }
    };

    updatePosition();

    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        triggerRef.current && !triggerRef.current.contains(target) &&
        menuRef.current && !menuRef.current.contains(target)
      ) {
        setIsOpen(false);
        setQuery('');
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    window.addEventListener('scroll', updatePosition, true);
    window.addEventListener('resize', updatePosition);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      window.removeEventListener('scroll', updatePosition, true);
      window.removeEventListener('resize', updatePosition);
    };
  }, [isOpen]);

  const filteredOptions = options.filter((opt) => opt.toLowerCase().includes(query.trim().toLowerCase()));

  const handleSelect = (val: string) => {
    onChange(val);
    setIsOpen(false);
    setQuery('');
  };

  return (
    <div className="custom-dropdown">
      <button
        type="button"
        ref={triggerRef}
        className="filter-btn custom-dropdown-trigger"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <span className="custom-dropdown-label" title={`${labelPrefix}${value}`}>{labelPrefix}{value}</span>
        <span className="custom-dropdown-arrow" />
      </button>
      {isOpen && createPortal(
        <div
          ref={menuRef}
          className="custom-dropdown-menu"
          style={{ position: 'fixed', top: menuStyle.top, left: menuStyle.left, width: menuStyle.width }}
        >
          <input
            type="text"
            className="custom-dropdown-search"
            placeholder={`Search ${labelPrefix.replace(':', '').trim()}...`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <button
            type="button"
            className="custom-dropdown-option"
            onClick={() => handleSelect(allOptionLabel)}
          >
            {labelPrefix}{allOptionLabel}
          </button>
          {filteredOptions.length > 0 ? (
            filteredOptions.map((opt) => (
              <button
                key={opt}
                type="button"
                className="custom-dropdown-option"
                onClick={() => handleSelect(opt)}
              >
                {opt}
              </button>
            ))
          ) : (
            <div className="custom-dropdown-empty">No matches found</div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
};

export const BuyerInvoiceDashboard: React.FC = () => {
  const [records, setRecords] = useState<CustomerGraphRecord[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  const [supplierOptions, setSupplierOptions] = useState<string[]>([]);
  const [poNumberOptions, setPoNumberOptions] = useState<string[]>([]);
  const [companyCodeOptions, setCompanyCodeOptions] = useState<string[]>([]);

  const [activeFilter, setActiveFilter] = useState<string>('Success');
  const [search, setSearch] = useState<string>('');
  const [sourceFilter, setSourceFilter] = useState<string>('Manual');
  const [selectedSupplier, setSelectedSupplier] = useState<string>('All Suppliers');
  const [selectedPoNumber, setSelectedPoNumber] = useState<string>('All PO Numbers');
  const [selectedCompanyCode, setSelectedCompanyCode] = useState<string>('All Company Codes');
  const [typeFilter, setTypeFilter] = useState<string>('All');
  const [statusFilter, setStatusFilter] = useState<string>('Success');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [filtersReloadKey, setFiltersReloadKey] = useState<number>(0);
  const navigate = useNavigate();
  const [activeTooltip, setActiveTooltip] = useState<{ text: string; x: number; y: number; isTop: boolean } | null>(null);
  const commentTooltipHideTimer = useRef<number | null>(null);

  const clearCommentTooltipHideTimer = () => {
    if (commentTooltipHideTimer.current !== null) {
      window.clearTimeout(commentTooltipHideTimer.current);
      commentTooltipHideTimer.current = null;
    }
  };

  const hideCommentTooltip = () => {
    clearCommentTooltipHideTimer();
    commentTooltipHideTimer.current = window.setTimeout(() => {
      setActiveTooltip(null);
      commentTooltipHideTimer.current = null;
    }, 180);
  };

  const copyComment = async (comment: string) => {
    try {
      await navigator.clipboard.writeText(comment);
      toast.success('Comment copied.');
    } catch {
      toast.error('Unable to copy the comment.');
    }
  };

  useEffect(() => () => clearCommentTooltipHideTimer(), []);

  // Compare Modal state
  const [isCompareModalOpen, setIsCompareModalOpen] = useState<boolean>(false);
  const [compareData, setCompareData] = useState<AribaResponseDTO>({});
  const [selectedComparePo, setSelectedComparePo] = useState<string>('');
  const [selectedComparePdfId, setSelectedComparePdfId] = useState<string | null>(null);
  const [selectedCompareInvoiceNo, setSelectedCompareInvoiceNo] = useState<string>('');
  const [isCompareLoading, setIsCompareLoading] = useState<boolean>(false);

  // File Upload states and refs
  // const [isUploadDataModalOpen, setIsUploadDataModalOpen] = useState<boolean>(false);
  const [isExcelInfoModalOpen, setIsExcelInfoModalOpen] = useState<boolean>(false);
  // const poFileInputRef = useRef<HTMLInputElement>(null);
  // const aribaReceiptsFileInputRef = useRef<HTMLInputElement>(null);
  // const poAndReceiptsFileInputRef = useRef<HTMLInputElement>(null);
  // const [isUploadingPo, setIsUploadingPo] = useState<boolean>(false);
  // const [isUploadingAribaReceipts, setIsUploadingAribaReceipts] = useState<boolean>(false);
  // const [isUploadingPoAndReceipts, setIsUploadingPoAndReceipts] = useState<boolean>(false);
  const [isDownloadingReport, setIsDownloadingReport] = useState<boolean>(false);
  const [isSyncingS4Data, setIsSyncingS4Data] = useState<boolean>(false);
  // const [uploadNotice, setUploadNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // const handlePoAndReceiptsFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
  //   const files = e.target.files;
  //   if (!files || files.length === 0) return;
  //   const file = files[0];

  //   setIsUploadingPoAndReceipts(true);
  //   setUploadNotice(null);

  //   try {
  //     const res = await uploadPurchaseOrdersAndReceipts(file);
  //     if ('statusCode' in res) {
  //       setUploadNotice({ type: 'error', text: res.message || 'Failed to upload Excel file' });
  //     } else {
  //       setUploadNotice({ type: 'success', text: res.message || res.detail || 'Data uploaded successfully!' });
  //       loadData();
  //     }
  //   } catch (err: any) {
  //     setUploadNotice({ type: 'error', text: err.message || 'An error occurred during upload' });
  //   } finally {
  //     setIsUploadingPoAndReceipts(false);
  //     if (poAndReceiptsFileInputRef.current) poAndReceiptsFileInputRef.current.value = '';
  //   }
  // };

  // const handlePoFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
  //   const files = e.target.files;
  //   if (!files || files.length === 0) return;
  //   const file = files[0];

  //   setIsUploadingPo(true);
  //   setUploadNotice(null);

  //   try {
  //     const res = await uploadPurchaseOrders(file);
  //     if ('statusCode' in res) {
  //       setUploadNotice({ type: 'error', text: res.message || 'Failed to upload PO data file' });
  //     } else {
  //       setUploadNotice({ type: 'success', text: res.message || res.detail || 'Purchase Orders data uploaded successfully!' });
  //       loadData();
  //     }
  //   } catch (err: any) {
  //     setUploadNotice({ type: 'error', text: err.message || 'An error occurred during PO data upload' });
  //   } finally {
  //     setIsUploadingPo(false);
  //     if (poFileInputRef.current) poFileInputRef.current.value = '';
  //   }
  // };

  // const handleAribaReceiptsFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
  //   const files = e.target.files;
  //   if (!files || files.length === 0) return;
  //   const file = files[0];

  //   setIsUploadingAribaReceipts(true);
  //   setUploadNotice(null);

  //   try {
  //     const res = await uploadAribaReceipts(file);
  //     if ('statusCode' in res) {
  //       setUploadNotice({ type: 'error', text: res.message || 'Failed to upload Ariba receipts file' });
  //     } else {
  //       setUploadNotice({ type: 'success', text: res.message || res.detail || 'Ariba Receipts uploaded successfully!' });
  //       loadData();
  //     }
  //   } catch (err: any) {
  //     setUploadNotice({ type: 'error', text: err.message || 'An error occurred during Ariba receipts upload' });
  //   } finally {
  //     setIsUploadingAribaReceipts(false);
  //     if (aribaReceiptsFileInputRef.current) aribaReceiptsFileInputRef.current.value = '';
  //   }
  // };

  useEffect(() => {
    const fetchFilterOptions = async () => {
      const [suppliers, pos, companyCodes] = await Promise.all([
        fetchDistinctSuppliers('manual'),
        fetchDistinctPoNumbers('manual'),
        fetchDistinctCompanyCodes('manual'),
      ]);
      setSupplierOptions(suppliers);
      setPoNumberOptions(pos);
      setCompanyCodeOptions(companyCodes);
    };
    fetchFilterOptions();
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');

    const payload = {
      page: currentPage,
      page_size: 50,
      Field: 'manual',
      po_number: (selectedPoNumber !== 'All PO Numbers' && selectedPoNumber.trim() !== '' ? selectedPoNumber : search) || undefined,
      supplier: (selectedSupplier !== 'All Suppliers' && selectedSupplier.trim() !== '' ? selectedSupplier : undefined),
      company_code: (selectedCompanyCode !== 'All Company Codes' && selectedCompanyCode.trim() !== '' ? selectedCompanyCode : undefined),
      product_type_code: typeFilter !== 'All' ? typeFilter : undefined,
      status: statusFilter !== 'All' ? statusFilter : undefined,
      invoice_start_date: dateFrom || undefined,
      invoice_end_date: dateTo || undefined,
    };

    try {
      const [res, summaryRes] = await Promise.all([
        fetchCustomerGraph(payload),
        fetchCustomerGraph({
          page: 1,
          page_size: 1,
          Field: 'manual',
        }),
      ]);

      if ('records' in res && res.records && 'records' in summaryRes) {
        setRecords(res.records);
        setTotalPages(res.total_pages || 1);
        setTotalCount(summaryRes.total_count ?? summaryRes.status_counts?.['total_invoices'] ?? 0);
        setStatusCounts(summaryRes.status_counts || {});
      } else {
        const errorMsg = 'message' in res ? res.message : 'Failed to fetch customer records.';
        setError(errorMsg);
      }
    } catch (err: any) {
      setError(err.message || 'Unable to connect to customer graph server.');
    } finally {
      setLoading(false);
    }
  }, [
    currentPage,
    dateFrom,
    dateTo,
    search,
    selectedCompanyCode,
    selectedPoNumber,
    selectedSupplier,
    statusFilter,
    typeFilter,
  ]);

  useEffect(() => {
    loadData();
  }, [filtersReloadKey, loadData]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCurrentPage(1);
    loadData();
  };

  const handleClearFilters = () => {
    setSearch('');
    setSourceFilter('Manual');
    setSelectedSupplier('All Suppliers');
    setSelectedPoNumber('All PO Numbers');
    setSelectedCompanyCode('All Company Codes');
    setTypeFilter('All');
    setActiveFilter('Success');
    setStatusFilter('Success');
    setDateFrom('');
    setDateTo('');
    setCurrentPage(1);
    setFiltersReloadKey((key) => key + 1);
  };

  // const handleDownloadExcel = async () => {
  //   try {
  //     toast.info('Downloading Excel template...');
  //     const blob = await downloadExcelTemplate();
  //     const url = window.URL.createObjectURL(new Blob([blob]));
  //     const link = document.createElement('a');
  //     link.href = url;
  //     link.setAttribute('download', 'Excel_Template.xlsx');
  //     document.body.appendChild(link);
  //     link.click();
  //     link.parentNode?.removeChild(link);
  //     window.URL.revokeObjectURL(url);
  //     toast.success('Download completed successfully!');
  //   } catch (error) {
  //     toast.error('Failed to download the template.');
  //   }
  // };

  const handleDownloadReport = async () => {
    setIsDownloadingReport(true);
    try {
      toast.info('Generating report...');
      const payload = {
        Field: sourceFilter !== 'All' ? sourceFilter : '',
        po_number: (selectedPoNumber !== 'All PO Numbers' && selectedPoNumber.trim() !== '' ? selectedPoNumber : search) || '',
        supplier: selectedSupplier !== 'All Suppliers' && selectedSupplier.trim() !== '' ? selectedSupplier : '',
        company_code: selectedCompanyCode !== 'All Company Codes' && selectedCompanyCode.trim() !== '' ? selectedCompanyCode : '',
        status: statusFilter !== 'All' ? statusFilter : '',
        invoice_start_date: dateFrom || '',
        invoice_end_date: dateTo || '',
        process_start_date: '',
        process_end_date: ''
      };

      const blob = await exportInvoicesExcel(payload);
      const url = window.URL.createObjectURL(new Blob([blob]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', 'Invoices_Report.xlsx');
      document.body.appendChild(link);
      link.click();
      link.parentNode?.removeChild(link);
      window.URL.revokeObjectURL(url);
      toast.success('Report downloaded successfully!');
    } catch (error) {
      toast.error('Failed to download the report.');
    } finally {
      setIsDownloadingReport(false);
    }
  };

  const handleSyncS4Data = async () => {
    const toastId = 'sync-s4-data';
    setIsSyncingS4Data(true);
    toast.loading('Syncing S4 data...', { toastId });

    try {
      const response = await syncAllS4Data();
      toast.update(toastId, {
        render: response.message || response.detail || 'S4 data synced successfully.',
        type: 'success',
        isLoading: false,
        autoClose: 5000,
      });
      await loadData();
    } catch (error: any) {
      toast.update(toastId, {
        render: error?.response?.data?.detail || error?.response?.data?.message || 'Failed to sync S4 data.',
        type: 'error',
        isLoading: false,
        autoClose: 5000,
      });
    } finally {
      setIsSyncingS4Data(false);
    }
  };

  const handleReprocess = async (rec: CustomerGraphRecord) => {
    const poNumber = rec.PONumber || (rec as any).po_number || (rec as any).poNumber || (rec as any).PR_PONumber || '';
    const pdfId = rec.pdf_id ? String(rec.pdf_id) : String(rec.id);
    const invoiceNumber = rec.InvoiceNumber || rec.CUSTOMER_INVOICE_ID || '';

    setSelectedComparePo(poNumber);
    setSelectedComparePdfId(pdfId);
    setSelectedCompareInvoiceNo(invoiceNumber);
    setIsCompareLoading(true);

    try {
      const res = await aribaDataCompare(poNumber, pdfId);
      if ('statusCode' in res) {
        setCompareData({});
      } else {
        setCompareData(res);
      }
      setIsCompareModalOpen(true);
    } catch (err) {
      console.error('Failed to compare invoice:', err);
      setCompareData({});
      setIsCompareModalOpen(true);
    } finally {
      setIsCompareLoading(false);
    }
  };
  const handleRefreshComparison = async (poNumberToCompare: string, pdfIdToCompare: string) => {
    try {
      const res = await aribaDataCompare(poNumberToCompare, pdfIdToCompare);
      if (res && !('statusCode' in res)) {
        setCompareData(res);
      }
    } catch (err) {
      console.error('Failed to refresh comparison:', err);
    }
  };



  const handleUploadAriba = (pdf_id: string) => {
    alert(`Uploading to Ariba requested for PDF ID: ${pdf_id}`);
  };

  const handleViewPdf = async (rec: CustomerGraphRecord) => {
    const pdfId = rec.pdf_id || rec.id;
    if (!pdfId) return;

    let newTab: Window | null = null;
    try {
      newTab = window.open('', '_blank');
    } catch {
      console.warn('Popup blocked');
    }

    try {
      const res = await fetchPdfBase64(pdfId);
      if ('statusCode' in res) {
        if (newTab) newTab.close();
        return;
      }

      let base64 = res.file_base64 || res.base64 || res.pdf_base64;
      if (!base64) {
        if (newTab) newTab.close();
        return;
      }

      base64 = base64.includes('base64,') ? base64.split('base64,')[1] : base64;
      const byteCharacters = atob(base64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);

      if (newTab) {
        newTab.location.href = url;
      }
    } catch (err) {
      if (newTab) newTab.close();
      console.error('Failed to load PDF via API:', err);
    }
  };

  const formatDateTime = (dateStr?: string) => {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      const datePart = d.toLocaleDateString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric'
      });
      const timePart = d.toLocaleTimeString('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
      });
      return `${datePart}, ${timePart}`;
    } catch {
      return dateStr;
    }
  };

  const processedCount = statusCounts['Processed'] ?? statusCounts['processed'] ?? 0;
  const successCount =
    statusCounts['OCR-Success'] ??
    statusCounts['ocr-success'] ??
    statusCounts['Success'] ??
    statusCounts['success'] ??
    0;
  const failedCount = statusCounts['Failed'] ?? statusCounts['failed'] ?? 0;

  const cards = [
    { title: 'Total Invoice', count: totalCount, icon: FileText, color: 'blue' },
    { title: 'Processed', count: processedCount, icon: CheckCircle, color: 'green' },
    { title: 'Success', count: successCount, icon: CheckCircle, color: 'green' },
    { title: 'Failed', count: failedCount, icon: XCircle, color: 'red' }
  ];

  const getIconColor = (color: string) => {
    const colors: Record<string, string> = {
      blue: 'text-blue-500 bg-blue-100',
      green: 'text-green-500 bg-green-100',
      orange: 'text-orange-500 bg-orange-100',
      purple: 'text-purple-500 bg-purple-100',
      red: 'text-red-500 bg-red-100'
    };
    return colors[color] || 'text-gray-500 bg-gray-100';
  };

  return (
    <div className="invoice-dashboard-content buyer-invoice-dashboard">
      {/* Top Sub-Header */}
      <div className="dashboard-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <h2 style={{ fontSize: '20px', fontWeight: 600, color: '#1f2937', letterSpacing: '0' }}>Invoice Dashboard</h2>
        </div>
        <div className="dashboard-header-buttons">
          <button
            type="button"
            className="header-btn sync-s4-data-btn"
            onClick={handleSyncS4Data}
            disabled={isSyncingS4Data}
          >
            <RotateCcw size={15} className={isSyncingS4Data ? 'bicm-spin' : ''} />
            {isSyncingS4Data ? 'Syncing S4 Data...' : 'Sync S4 Data'}
          </button>
        </div>
      </div>

      {/* Statistics Cards */}
      <div className="dashboard-cards-container">
        {cards.map((card, index) => (
          <div
            key={index}
            className={`dashboard-card ${activeFilter === card.title ? 'active-card' : ''}`}
            onClick={() => {
              setActiveFilter(card.title);
              setStatusFilter(card.title === 'Total Invoice' ? 'All' : card.title);
              setCurrentPage(1);
            }}
          >
            <div className="dashboard-card-header">
              <h3 className="dashboard-card-title">{card.title}</h3>
              <div className={`dashboard-card-icon ${getIconColor(card.color)}`}>
                <card.icon size={20} />
              </div>
            </div>
            <div className="dashboard-card-content">
              <span className="dashboard-card-count">{card.count}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Filter Section */}
      <div className="invoice-filter-section">
        <form onSubmit={handleSearchSubmit} className="filter-wrapper">
          <div className="filter-status-display">
            <span className="filter-label">Showing:</span>
            <span className="filter-status-value">{activeFilter}</span>
          </div>

          <div className="filter-search-wrapper">
            <input
              type="text"
              placeholder="Search by PO number, supplier, or company code..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="filter-search-input"
            />
          </div>

          <div className="filter-dropdowns-container">
            <div className="source-filter-wrapper">
              <select
                className="filter-btn"
                value={sourceFilter}
                onChange={(e) => {
                  setSourceFilter(e.target.value);
                  setCurrentPage(1);
                }}
                style={{ paddingRight: '12px' }}
              >
                <option value="Manual">Source: Manual</option>
              </select>
            </div>

            <div className="filter-item-wrapper">
              <SearchableSelect
                value={selectedSupplier}
                options={supplierOptions}
                allOptionLabel="All Suppliers"
                labelPrefix="Supplier: "
                onChange={(val) => {
                  setSelectedSupplier(val);
                  setCurrentPage(1);
                }}
              />
            </div>

            <div className="filter-item-wrapper">
              <SearchableSelect
                value={selectedPoNumber}
                options={poNumberOptions}
                allOptionLabel="All PO Numbers"
                labelPrefix="PO Number: "
                onChange={(val) => {
                  setSelectedPoNumber(val);
                  setCurrentPage(1);
                }}
              />
            </div>

            <div className="filter-item-wrapper">
              <SearchableSelect
                value={selectedCompanyCode}
                options={companyCodeOptions}
                allOptionLabel="All Company Codes"
                labelPrefix="Company Code: "
                onChange={(val) => {
                  setSelectedCompanyCode(val);
                  setCurrentPage(1);
                }}
              />
            </div>

            <div className="sort-filter-wrapper">
              <select 
                className="filter-btn" 
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value);
                  setCurrentPage(1);
                }}
                style={{ paddingRight: '12px' }}
              >
                <option value="All">Status: All</option>
                <option value="Processed">Status: Processed</option>
                <option value="Success">Status: Success</option>
                <option value="Failed">Status: Failed</option>
              </select>
            </div>

            <div className="filter-item-wrapper">
              <select
                className="filter-btn"
                value={typeFilter}
                onChange={(e) => {
                  setTypeFilter(e.target.value);
                  setCurrentPage(1);
                }}
                style={{ paddingRight: '12px' }}
              >
                <option value="All">Type: All</option>
                <option value="1">Type: Material</option>
                <option value="2">Type: Service</option>
              </select>
            </div>

            <div className="date-range-wrapper">
              <input
                type="date"
                className="filter-date-input"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  setCurrentPage(1);
                }}
                title="Invoice From date"
              />
              <span style={{ fontSize: '12px', color: '#64748b' }}>to</span>
              <input
                type="date"
                className="filter-date-input"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setCurrentPage(1);
                }}
                title="Invoice To date"
              />
            </div>
          </div>

          {/* Action Buttons Row Below Filter Controls */}
          <div className="excel-upload-buttons-row">
            {/* <button type="button" className="download-btn" onClick={handleDownloadExcel}>
              <Download size={15} /> Download Template
            </button> */}

            <button 
              type="button" 
              className="download-btn" 
              onClick={handleDownloadReport}
              disabled={isDownloadingReport}
              style={{ marginLeft: '10px', backgroundColor: '#7c3aed', borderColor: '#7c3aed', color: '#fff' }}
            >
              <Download size={15} /> {isDownloadingReport ? 'Downloading...' : 'Download Report'}
            </button>

            {/* <input
              type="file"
              ref={poAndReceiptsFileInputRef}
              onChange={handlePoAndReceiptsFileSelected}
              accept=".xlsx, .xls, .csv"
              style={{ display: 'none' }}
            /> */}

            {/* <button
              type="button"
              className="excel-upload-btn"
              onClick={() => poAndReceiptsFileInputRef.current?.click()}
              disabled={isUploadingPoAndReceipts}
            >
              <FileSpreadsheet size={15} />
              {isUploadingPoAndReceipts ? 'Uploading...' : 'Upload Excel'}
            </button> */}

            {/*
            <input
              type="file"
              ref={poFileInputRef}
              onChange={handlePoFileSelected}
              accept=".xlsx, .xls, .csv"
              style={{ display: 'none' }}
            />
            <div className="upload-excel-dropdown">
              <button
                type="button"
                className="excel-upload-btn"
              >
                <FileSpreadsheet size={15} />
                Upload Excel
              </button>
              
              <div className="upload-excel-dropdown-content">
                <button
                  type="button"
                  className="dropdown-item"
                  onClick={() => poFileInputRef.current?.click()}
                  disabled={isUploadingPo}
                >
                  <FileSpreadsheet size={13} style={{ marginRight: '6px' }} />
                  {isUploadingPo ? 'Uploading PO Data...' : 'Upload PO Data'}
                </button>
                <button
                  type="button"
                  className="dropdown-item"
                  onClick={() => aribaReceiptsFileInputRef.current?.click()}
                  disabled={isUploadingAribaReceipts}
                >
                  <FileSpreadsheet size={13} style={{ marginRight: '6px' }} />
                  {isUploadingAribaReceipts ? 'Uploading Receipts...' : 'Upload Receipts'}
                </button>
              </div>
            </div>

            <input
              type="file"
              ref={aribaReceiptsFileInputRef}
              onChange={handleAribaReceiptsFileSelected}
              accept=".xlsx, .xls, .csv"
              style={{ display: 'none' }}
            />
            */}

            {/* <button
              type="button"
              className="excel-info-btn"
              onClick={() => setIsUploadDataModalOpen(true)}
              title="Upload PO Data or Receipt Data"
            >
              <Upload size={15} /> Upload Data
            </button> */}

            <button
              type="button"
              className="excel-info-btn"
              onClick={() => {navigate("/invoice_extractor")}}
            >
              Invoice Management System
            </button>

            <button
              type="button"
              className="excel-info-btn"
              onClick={() => {navigate("/uae-einvoicing/command-center")}}
            >
              UAE e-Invoicing
            </button>

           <button
              type="button"
              className="excel-info-btn"
              onClick={() => loadData()}
              disabled={loading}
              title="Refresh invoice data using the currently applied filters"
            >
              <RotateCcw size={15} className={loading ? 'bicm-spin' : ''} /> Refresh
            </button>

            <button type="button" className="clear-filters-btn" onClick={handleClearFilters}>
              Clear Filters
            </button>
          </div>
        </form>
      </div>

      {/* {uploadNotice && (
        <div style={{
          background: uploadNotice.type === 'success' ? '#f0fdf4' : '#fef2f2',
          color: uploadNotice.type === 'success' ? '#15803d' : '#dc2626',
          border: `1px solid ${uploadNotice.type === 'success' ? '#bbf7d0' : '#fecaca'}`,
          padding: '12px 16px',
          borderRadius: '10px',
          fontSize: '14px',
          marginBottom: '16px',
          fontWeight: 600,
        }}>
          {uploadNotice.text}
        </div>
      )} */}

      {error && (
        <div style={{ background: '#fef2f2', color: '#dc2626', padding: '12px 16px', borderRadius: '10px', fontSize: '14px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {/* Invoice Table - 16 Full Columns matching Reference Table */}
      <div className="invoice-table-container">
        <table className="invoice-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Supplier Name</th>
              <th>Customer Invoice ID</th>
              <th>Comments</th>
              <th>PO Number</th>
              <th>Type</th>
              <th>Company Code</th>
              <th>Status</th>
              <th>Invoice Date</th>
              <th>Processed Date</th>
              <th>Total Amount</th>
              <th>View</th>
              <th>Invoice ID</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
          <td colSpan={14} style={{ textAlign: 'center', padding: '30px', color: '#0f68e6', fontWeight: 600 }}>
                  Loading customer records...
                </td>
              </tr>
            ) : records.length > 0 ? (
              records.map((rec) => {
                const statusNormalized = (rec.status || 'pending').toLowerCase();
                const displayStatus = rec.status ;
                const statusClass = statusNormalized === 'success' || statusNormalized === 'processed' ? 'status-processed' : `status-${statusNormalized}`;

                return (
                  <tr key={rec.id || rec.pdf_id}>
                    <td><strong>{rec.pdf_id || rec.id}</strong></td>
                    <td className="truncate-cell supplier-cell" title={rec.SupplierName || rec.PR_Supplier || ''}>
                      {rec.SupplierName || rec.PR_Supplier || '-'}
                    </td>
                    <td>{rec.CUSTOMER_INVOICE_ID || '-'}</td>
                    <td>
                      {rec.comments ? (
                        <div
                          className="comment-tooltip-wrapper"
                          onMouseEnter={(e) => {
                            clearCommentTooltipHideTimer();
                            const rect = e.currentTarget.getBoundingClientRect();
                            const isNearTop = rect.top < 220;
                            setActiveTooltip({
                              text: rec.comments,
                              x: Math.max(12, Math.min(rect.left, window.innerWidth - 410)),
                              y: isNearTop ? rect.bottom + 8 : rect.top - 8,
                              isTop: !isNearTop,
                            });
                          }}
                          onMouseLeave={hideCommentTooltip}
                        >
                          <span className="comment-text-truncated" title={rec.comments}>{rec.comments}</span>
                        </div>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td>{formatPoNumbers(rec.PONumber) || '-'}</td>
                    <td>{getProductTypeShortLabel(rec.product_type_code)}</td>
                    <td>{rec.Company_Code || 'N/A'}</td>
                    <td>
                      <span className={`status-badge ${statusClass}`}>
                        {displayStatus}
                      </span>
                    </td>
                    <td>{rec.invoice_date || '-'}</td>
                    <td>{formatDateTime(rec.processed_date)}</td>
                    <td>
                      {rec.Total !== undefined && rec.Total !== null ? Number(rec.Total).toFixed(2) : '0.00'}{' '}
                      {rec.DocumentCurrency || ''}
                    </td>
              
                    <td>
                      <button
                        className="icon-button--view-button"
                        onClick={() => handleViewPdf(rec)}
                        title="View Invoice PDF Document"
                      >
                        <Eye size={18} />
                      </button>
                    </td>
                    <td>{rec.InvoiceNumber || rec.CUSTOMER_INVOICE_ID || 'N/A'}</td>
                    <td>
                      <div className="action-buttons-wrapper" style={{ display: 'flex', gap: '6px' }}>
                        {statusNormalized === 'pending' && (
                          <button
                            className="upload-ariba-btn"
                            onClick={() => handleUploadAriba(rec.pdf_id)}
                          >
                            Upload
                          </button>
                        )}
                        <button
                          className="reprocess-btn"
                          onClick={() => handleReprocess(rec)}
                          disabled={
                            statusNormalized === 'processed' ||
                            (isCompareLoading && selectedComparePdfId === (rec.pdf_id || String(rec.id)))
                          }
                          title={statusNormalized === 'processed' ? 'Processed invoices cannot be reprocessed' : 'Reprocess invoice'}
                        >
                          <RotateCcw size={14} className={isCompareLoading && selectedComparePdfId === (rec.pdf_id || String(rec.id)) ? 'bicm-spin' : ''} /> Reprocess
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={14} style={{ textAlign: 'center', padding: '40px 20px', color: '#64748b' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
                    <FileText size={32} style={{ color: '#94a3b8' }} />
                    <span style={{ fontSize: '14px', fontWeight: 600, color: '#334155' }}>No Matching Customer Invoices Found</span>
                    <span style={{ fontSize: '12px', color: '#94a3b8' }}>Try adjusting your filters, search term, or date range to see results.</span>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="invoice-pagination-controls" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '16px' }}>
        <span style={{ fontSize: '13px', color: '#64748b' }}>
          Total Records: <strong>{totalCount}</strong>
        </span>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button disabled={currentPage <= 1 || loading} onClick={() => setCurrentPage(p => p - 1)}>
            Previous
          </button>
          <span style={{ fontSize: '13px', fontWeight: 600, color: '#475569' }}>
            Page {currentPage} of {totalPages}
          </span>
          <button disabled={currentPage >= totalPages || loading} onClick={() => setCurrentPage(p => p + 1)}>
            Next
          </button>
        </div>
      </div>

      {/* Reprocess Data Comparison Modal */}
      <BuyerInvoiceCompareModal
        isOpen={isCompareModalOpen}
        onClose={() => {
          setIsCompareModalOpen(false);
          loadData();
        }}
        data={compareData}
        poNumber={selectedComparePo}
        invoiceNumber={selectedCompareInvoiceNo}
        pdfId={selectedComparePdfId}
        onRefreshComparison={handleRefreshComparison}
      />

      {isExcelInfoModalOpen && (
        <div className="excel-info-modal-overlay" onClick={() => setIsExcelInfoModalOpen(false)}>
          <div className="excel-info-modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="excel-info-modal-header">
              <h3>
                <Info size={18} style={{ color: '#0284c7' }} /> Dashboard Instructions
              </h3>
              <button
                className="excel-info-modal-close"
                onClick={() => setIsExcelInfoModalOpen(false)}
                title="Close"
              >
                <X size={18} />
              </button>
            </div>

            <div className="excel-info-modal-body">
              <div className="excel-info-notice" style={{ marginBottom: '20px' }}>
                <Info size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
                <div>
                  <strong>Welcome to the Sila Fatoora Dashboard!</strong> Here is a quick guide on how to use the main actions available to you.
                </div>
              </div>

              {/* Download Template */}
              <div className="excel-info-section">
                <div className="excel-info-section-title">
                  <Download size={16} style={{ color: '#16a34a' }} /> 1. Download Template
                </div>
                <div style={{ fontSize: '14px', color: '#475569', lineHeight: '1.6', padding: '12px 14px', backgroundColor: '#f8fafc', borderRadius: '6px', marginTop: '8px', borderLeft: '4px solid #16a34a' }}>
                  Click this button to download the standard Excel template required for uploading your Purchase Orders and Receipts data. The template contains all the necessary columns formatted correctly to ensure seamless data processing.
                </div>
              </div>

              {/* Upload Excel */}
              <div className="excel-info-section" style={{ marginTop: '24px' }}>
                <div className="excel-info-section-title">
                  <FileSpreadsheet size={16} style={{ color: '#0284c7' }} /> 2. Upload Excel
                </div>
                <div style={{ fontSize: '14px', color: '#475569', lineHeight: '1.6', padding: '12px 14px', backgroundColor: '#f0f9ff', borderRadius: '6px', marginTop: '8px', borderLeft: '4px solid #0284c7' }}>
                  Once you have filled the downloaded template with your Purchase Orders and Receipts data, use this button to upload the completed file. The system accepts <strong>.xlsx</strong>, <strong>.xls</strong>, and <strong>.csv</strong> formats. Your data will be processed and reflected in the dashboard tables.
                </div>
              </div>

              {/* Invoice Management System */}
              <div className="excel-info-section" style={{ marginTop: '24px' }}>
                <div className="excel-info-section-title">
                  <FileText size={16} style={{ color: '#7c3aed' }} /> 3. Invoice Management System
                </div>
                <div style={{ fontSize: '14px', color: '#475569', lineHeight: '1.6', padding: '12px 14px', backgroundColor: '#f5f3ff', borderRadius: '6px', marginTop: '8px', borderLeft: '4px solid #7c3aed' }}>
                  Navigate to the Invoice Management System to view extracted invoice data, verify details, handle mismatches between POs and invoices, and submit or push approved invoices to the Ariba system.
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Global Fixed Floating Comment Tooltip (Escapes all table overflow clipping) */}
      {activeTooltip && (
        <div
          onMouseEnter={clearCommentTooltipHideTimer}
          onMouseLeave={hideCommentTooltip}
          style={{
            position: 'fixed',
            left: `${activeTooltip.x}px`,
            top: `${activeTooltip.y}px`,
            transform: activeTooltip.isTop ? 'translateY(-100%)' : 'none',
            backgroundColor: '#0f172a',
            color: '#f8fafc',
            padding: '10px 12px',
            borderRadius: '8px',
            fontSize: '12px',
            lineHeight: '1.5',
            maxWidth: '390px',
            maxHeight: '280px',
            overflowY: 'auto',
            boxShadow: '0 12px 32px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255, 255, 255, 0.12)',
            zIndex: 999999,
            pointerEvents: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
            <span style={{ flex: 1 }}>{activeTooltip.text}</span>
            <button
              type="button"
              onClick={() => copyComment(activeTooltip.text)}
              title="Copy comment"
              aria-label="Copy comment"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                width: '28px',
                height: '28px',
                padding: 0,
                border: '1px solid rgba(255, 255, 255, 0.25)',
                borderRadius: '5px',
                background: 'rgba(255, 255, 255, 0.12)',
                color: '#f8fafc',
                cursor: 'pointer',
              }}
            >
              <Copy size={14} />
            </button>
          </div>
        </div>
      )}

    </div>
  );
};

export default BuyerInvoiceDashboard;
