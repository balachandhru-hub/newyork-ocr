import React, { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { IoMdEye } from "react-icons/io";
import "./InvoiceExtractionTable.css";
import { aribaData, aribaDataCompare, viewPdf } from "../api/invoiceExtract";
import type { AribaResponseDTO } from "../Dto/InvoiceExtractionDto";
import { extractBankDetailsFromInvoice } from "../Dto/InvoiceExtractionDto";
import type { BankDetailsDto } from "../Dto/InvoiceExtractionDto";
import InvoiceComparisonModal from "../components/InvoiceComparisonModal";
import { toast } from "react-toastify";
import AribaPopup from "../components/Aribapopup";
import BankDetailsModal from "../components/BankDetailsModal";

interface InvoiceDetail {
  sno: number;
  [key: string]: string | number | null | undefined;
}

export interface DynamicInvoice {
  id: number;
  action: string;
  details: InvoiceDetail[];
  is_Submit?: boolean;
  [key: string]: string | number | null | undefined | InvoiceDetail[] | boolean;
}

interface Props {
  invoices: DynamicInvoice[];
  mainHeaders: string[];
  detailHeaders: string[];
  handleDetailChange: (
    invoiceId: number,
    detailIndex: number,
    field: string,
    value: string
  ) => void;
  handleInvoiceChange: (invoiceId: number, field: string, value: string) => void;
  displayValue: (value: string | number | null | undefined) => string;
  readOnly?: boolean;
  originalInvoices?: DynamicInvoice[];
  isShowing: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  loading: boolean;
  showCheckbox?: boolean;
  selectedIds?: number[];
  onSelect?: (id: number, selected: boolean) => void;
  onSelectAll?: (selected: boolean) => void;
  editableInvoices?: DynamicInvoice[];
  getafterSaveData?: any[];
  onAfterSave?: (updatedData: any) => void;
  autoComparePoNumber?: string;
}

const InvoiceExtractionTable: React.FC<Props> = ({
  invoices = [],
  mainHeaders = [],
  detailHeaders = [],
  handleDetailChange,
  handleInvoiceChange,
  displayValue,
  readOnly = false,
  originalInvoices = [],
  isShowing,
  setLoading,
  loading,
  showCheckbox = false,
  selectedIds = [],
  onSelect = () => { },
  onSelectAll = () => { },
  // editableInvoices
  getafterSaveData,
  onAfterSave,
  autoComparePoNumber,
}) => {
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [isModalOpen, setModalOpen] = useState(false);
  const [aribaResponse, setAribaResponse] = useState<any | null>(null);
  const [bankDetailsModalOpen, setBankDetailsModalOpen] = useState(false);
  const [selectedBankDetails, setSelectedBankDetails] = useState<BankDetailsDto | null>(null);
  const [selectedSupplierName, setSelectedSupplierName] = useState<string>("");
  // Compare modal state
  const [compareModal, setCompareModal] = useState<boolean>(false);
  const [compareData, setCompareData] = useState<AribaResponseDTO | null>(null);
  const [selectedPo, setSelectedPo] = useState<string>("");
  const [selectedPdfId, setSelectedPdfId] = useState<string | null>(null);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState<string>("");
  const hasCompareTriggeredRef = useRef(false);

  const callCompare = useCallback(async (poNumber: string, pdfId?: number | string, invoiceNumber?: string) => {
    if (!poNumber || !pdfId) {
      toast.error("PO Number and PDF ID are required.");
      return;
    }

    setSelectedPo(poNumber);
    setSelectedPdfId(pdfId !== undefined ? String(pdfId) : null);
    setSelectedInvoiceNumber(invoiceNumber || "");

    try {
      const result = await aribaDataCompare(poNumber, pdfId);

      if (!result || !("po_number_comparison" in result)) {
        setCompareData({
          po_number_comparison: undefined,
          total_comparison: undefined,
          Comparision_of_lineItems: [],
          ariba_reference_data: undefined,
          poNumber: poNumber,
          message: "No data found",
        } as AribaResponseDTO);
      } else {
        setCompareData(result as AribaResponseDTO);
      }

      setCompareModal(true);
    } catch (error) {
      setCompareData({
        po_number_comparison: undefined,
        total_comparison: undefined,
        Comparision_of_lineItems: [],
        poNumber: poNumber,
        message: "No data found due to API error",
      } as AribaResponseDTO);
      setCompareModal(true);
    }
  }, []);

  useEffect(() => {
    if (autoComparePoNumber && invoices.length > 0 && !hasCompareTriggeredRef.current) {
      const firstInvoice = invoices[0];
      if (firstInvoice && firstInvoice.pdf_id) {
        hasCompareTriggeredRef.current = true;
        callCompare(
          autoComparePoNumber,
          firstInvoice.pdf_id as string,
          (firstInvoice.InvoiceNumber as string) || ""
        );
      }
    }
  }, [autoComparePoNumber, invoices, callCompare]);

  const allSelected = useMemo(() =>
    invoices.length > 0 && selectedIds.length === invoices.length,
    [invoices, selectedIds]
  );

  const handleOpen = async (poNumber: string, value: boolean) => {
    if (value) {
      try {
        const data = await aribaData(poNumber);
        setAribaResponse(data);
      } catch {
        setAribaResponse([]);
        toast.error("Failed to fetch Ariba data.");
      }
      setModalOpen(true);
    }
  };

  const handleClose = () => {
    setModalOpen(false);
    setAribaResponse(null);
  };

  const handleBankDetailsClick = useCallback(
    (invoice: DynamicInvoice) => {
      const bankDetails = extractBankDetailsFromInvoice(invoice);
      setSelectedBankDetails(bankDetails);
      setSelectedSupplierName(String(invoice.SupplierName || "Bank Details"));
      setBankDetailsModalOpen(true);
    },
    []
  );
  const formatHeaderName = useCallback((header?: string | null): string => {
    if (!header || typeof header !== "string") return "";
    // Special display name overrides
    if (header === "Invoice_Total") return "Invoice Total";
    if (header === "Po_Total") return "PO Total";
    return header
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
      .replace(/([a-z\d])([A-Z])/g, "$1 $2")
      .replace(/^./, (str) => str.toUpperCase())
      .trim();
  }, []);

  const truncateHeader = useCallback(
    (text: string, maxLength: number = 15): string => {
      if (!text || typeof text !== "string") return "";
      return text.length <= maxLength ? text : text.substring(0, maxLength) + "...";
    },
    []
  );

  const toggleExpand = useCallback((rowIndex: number) => {
    setExpandedRow((prev) => (prev === rowIndex ? null : rowIndex));
  }, []);

  const handleMainInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>, invoiceId: number, header: string) => {
      if (!readOnly) handleInvoiceChange(invoiceId, header, e.target.value);
    },
    [handleInvoiceChange, readOnly]
  );

  const handleDetailInputChange = useCallback(
    (
      e: React.ChangeEvent<HTMLInputElement>,
      invoiceId: number,
      detailIndex: number,
      header: string
    ) => {
      if (!readOnly) handleDetailChange(invoiceId, detailIndex, header, e.target.value);
    },
    [handleDetailChange, readOnly]
  );

  const formattedMainHeaders = useMemo(
    () =>
      mainHeaders?.map((header) => ({
        original: header,
        formatted: formatHeaderName(header),
        truncated: truncateHeader(formatHeaderName(header)),
      })),
    [mainHeaders, formatHeaderName, truncateHeader]
  );

  const formattedDetailHeaders = useMemo(
    () =>
      detailHeaders?.map((header) => ({
        original: header,
        formatted: formatHeaderName(header),
        truncated: truncateHeader(formatHeaderName(header)),
      })),
    [detailHeaders, formatHeaderName, truncateHeader]
  );

  const checkboxCount = showCheckbox ? 1 : 0;
  const showingCount = isShowing ? 4 : 0;
  const totalColSpan = formattedMainHeaders.length + checkboxCount + showingCount + 2; // +2 for view and expand

  if (!Array.isArray(invoices) || invoices?.length === 0) {
    return (
      <div className="iet-container">
        <div className="iet-no-data">
          <p>No invoice data available.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="iet-container">
      <div className="iet-table-wrapper">
        <table className="iet-table">
          <thead>
            <tr>
              {showCheckbox && (
                <th>
                  <input
                    type="checkbox"
                    className="iet-checkbox"
                    title="Select all"
                    checked={allSelected}
                    onChange={(e) => onSelectAll(e.target.checked)}
                  />
                </th>
              )}
              {formattedMainHeaders?.filter((header) => header.original !== "is_Submit")?.map((header, index) =>
                header.formatted !== "is_Submit" ? (
                  <th key={`main-${index}`} title={header.formatted}>
                    {header.formatted}
                  </th>
                ) : null
              )}

              {isShowing && (
                <>
                  <th className="iet-action-column">Action</th>
                  <th className="iet-button-column">Receipt</th>
                  <th className="iet-button-column">Bank Details</th>
                  <th className="iet-button-column">Compare</th>
                </>
              )}
              <th className="iet-action-column">View</th>
              <th className="iet-expand-column"></th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((invoice) => {
              const originalInvoice = originalInvoices?.find((orig) => orig.id === invoice.id);
              // PONumber may now be at item level (new API); fall back to invoice level for older data
              const invoicePONumber =
                (invoice?.details?.[0]?.PONumber as string) ||
                (invoice?.PONumber as string) ||
                "";
              const isDisabled = (getafterSaveData as any[])?.some(
                (d) => String(d.PO_Number ?? '') === invoicePONumber
              );
              return (
                <React.Fragment key={`invoice-${invoice.id}`}>
                  <tr>
                    {showCheckbox && (
                      <td>
                        <input
                          type="checkbox"
                          className={`iet-checkbox ${invoicePONumber &&
                              (getafterSaveData as any[])?.some(
                                d => String(d.PO_Number ?? '') === invoicePONumber
                              )
                              ? 'green-check'
                              : 'blue-check'
                            }`}
                          checked={selectedIds?.includes(invoice.id)}
                          onChange={(e) => onSelect(invoice.id, e.target.checked)}


                        // disabled={  editableInvoices?.find(inv => inv.id === invoice.id)?.is_Submit === true}
                        />
                      </td>
                    )}
                    {[...formattedMainHeaders]?.filter((header) => header.original !== "is_Submit")?.map((header, idx) => {
                      const value = invoice[header.original] === "is_Submit" ? undefined : invoice[header.original];
                      const originalValue = originalInvoice?.[header.original];
                      const displayedValue = Array.isArray(value) ? "—" : displayValue(value as string | number | null | undefined);
                      const isChanged = originalInvoice && value !== originalValue;

                      return (
                        <td key={`main-cell-${idx}`}>
                          {expandedRow === invoice.id && !readOnly ? (
                            <input
                              type="text"
                              value={
                                typeof value === "string" || typeof value === "number" ? value : ""
                              }
                              onChange={(e) => handleMainInputChange(e, invoice.id, header.original)}
                              className="iet-cell-input"
                            />
                          ) : (
                            <span
                              className={`iet-cell-content ${isChanged ? "iet-changed-cell" : ""}`}
                              title={displayedValue}
                            >
                              {displayedValue}
                            </span>
                          )}
                        </td>
                      );
                    })}
                    {isShowing && (
                      <>
                        <td>
                          <span
                            className={`iet-action-button ${readOnly ? "iet-readonly" : ""}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              if (isDisabled) {
                                toast.error("You are not allowed to edit/expand this row.");
                                return;
                              } else {
                                toggleExpand(invoice.id);
                              }
                            }}
                            title={readOnly ? "View details" : "Edit details"}
                          >
                            {readOnly ? "👁️ View" : invoice.action}
                          </span>
                        </td>
                        <td>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleOpen(invoicePONumber, true);
                            }}
                            className="iet-primary-button"
                          >
                            Receipt
                          </button>
                        </td>
                        <td>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleBankDetailsClick(invoice);
                            }}
                            className="iet-primary-button"
                            title="View Bank Details"
                          >
                            Bank Details
                          </button>
                        </td>
                        <td>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              callCompare(invoicePONumber, invoice.pdf_id as string, invoice.InvoiceNumber as string);
                            }}
                            className="iet-primary-button"
                          >
                            Compare
                          </button>
                        </td>
                      </>
                    )}
                    <td>
                      <span
                        className={`iet-view-icon`}
                        onClick={async (e) => {
                          e.stopPropagation();

                          if (!invoice?.pdf_id) {
                            toast.error("Data not available");
                            return;
                          }

                          // Show loading toast and popup
                          const toastId = toast.info("Loading PDF...", {
                            autoClose: false, // prevent auto close
                            closeButton: false,
                            draggable: false,
                          });
                          setLoading(true);

                          try {
                            await viewPdf(invoice.pdf_id as number | string, setLoading);
                            toast.update(toastId, {
                              render: "PDF Loaded successfully",
                              type: "success",
                              autoClose: 3000,
                              closeButton: true,
                            });
                          } catch {
                            toast.update(toastId, {
                              render: "Failed to load PDF",
                              type: "error",
                              autoClose: 3000,
                              closeButton: true,
                            });
                          } finally {
                            setLoading(false);
                          }
                        }}
                        title="View PDF"
                        style={{ cursor: "pointer", fontSize: "18px", color: "#1d4ed8" }}
                      >
                        <IoMdEye />
                      </span>
                    </td>
                    <td className="iet-expand-toggle">
                      <span
                        className={`iet-expand-icon ${expandedRow === invoice.id ? "iet-expanded" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (isDisabled) {
                            toast.error("You are not allowed to edit/expand this row.");
                            return;
                          }
                          else {
                            toggleExpand(invoice.id);
                          }
                        }}
                      >
                        ▶
                      </span>
                    </td>
                  </tr>
                  {expandedRow === invoice.id && (
                    <tr className="iet-expand-row">
                      <td colSpan={totalColSpan}>
                        <div className="iet-detail-container">
                
                          {Array.isArray(invoice.details) && invoice.details.length > 0 ? (
                            <div className="iet-detail-table-wrapper">
                              <table className="iet-detail-table">
                                <thead>
                                  <tr>
                                    <th>S.No</th>
                                    {formattedDetailHeaders.map((header, index) => (
                                      <th key={`detail-header-${index}`} title={header.formatted}>
                                        {header.formatted}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {invoice.details.map((detail, idx) => {
                                    const originalDetail = originalInvoice?.details?.[idx];
                                    return (
                                      <tr key={`detail-${idx}`}>
                                        <td>{detail.sno || idx + 1}</td>
                                        {formattedDetailHeaders.map((header, index) => {
                                          const value = detail[header.original];
                                          const originalValue = originalDetail?.[header.original];
                                          const displayedValue = displayValue(value);
                                          const isChanged = originalDetail && value !== originalValue;

                                          return (
                                            <td key={`detail-cell-${index}`}>
                                              {readOnly ? (
                                                <span
                                                  className={`iet-cell-content ${isChanged ? "iet-changed-cell" : ""}`}
                                                  title={displayedValue}
                                                >
                                                  {displayedValue}
                                                </span>
                                              ) : (
                                                <input
                                                  type="text"
                                                  disabled={readOnly}
                                                  value={typeof value === "string" || typeof value === "number" ? value : ""}
                                                  onChange={(e) =>
                                                    handleDetailInputChange(e, invoice.id, idx, header.original)
                                                  }
                                                  className="iet-detail-input"
                                                />
                                              )}
                                            </td>
                                          );
                                        })}
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          ) : (
                            <div className="iet-no-data">
                              <p>No detail items found</p>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Ariba Popup */}
      <AribaPopup data={aribaResponse?.ariba_data || []} isOpen={isModalOpen} onClose={handleClose} />

      {/* Compare Modal */}
      {compareModal && compareData && (
        <InvoiceComparisonModal
          isOpen={compareModal}
          onClose={() => setCompareModal(false)}
          data={compareData}
          poNumber={selectedPo}
          invoiceNumber={selectedInvoiceNumber}
          pdfId={selectedPdfId as string}
          onAfterSave={onAfterSave}
        />
      )}
      {loading && (
        <div className="iet-loading-overlay">
          <div className="iet-loading-popup">
            <div className="iet-spinner"></div>

          </div>
        </div>
      )}
      <BankDetailsModal
        isOpen={bankDetailsModalOpen}
        onClose={() => setBankDetailsModalOpen(false)}
        bankDetails={selectedBankDetails || {
          BankAccountName: null,
          BankName: null,
          BankBranch: null,
          BranchCode: null,
          AccountNumber: null,
          IBAN: null,
          SwiftCode: null,
        }}
        supplierName={selectedSupplierName}
      />
    </div>
  );
};

export default InvoiceExtractionTable;
