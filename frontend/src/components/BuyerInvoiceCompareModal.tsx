import React, { useState, useEffect } from 'react';
import './BuyerInvoiceCompareModal.css';
import { toast } from 'react-toastify';
import {
  Edit,
  X,
  Check,
  AlertTriangle,
  Eye,
  EyeOff,
  RefreshCw,
  Copy,
} from 'lucide-react';
import {
  fetchPdfBase64,
  getPoReceiptsSummary,
  aribaDataCompare,
} from '../api/DashboardApi';
import type {
  AribaResponseDTO,
  LineItemComparisonDTO,
  PoReceiptsSummaryResponseDto,
  PoReceiptDto,
} from '../Dto/CustomerGraphDto';
import { formatPoNumbers, isServicePoOrTypeCode2 } from '../Dto/CustomerGraphDto';
import {
  getInvoiceDataFromPdfId,
  getAllPoNumbersForSupplier,
  updateInvoiceData,
  createInvoiceInS4,
  formatDateToYYYYMMDD,
  type UpdateInvoicePayload,
  type S4InvoiceCreatePayload,
  type SupplierPurchaseOrder,
} from '../api/invoiceExtract';

interface ReviewData {
  poNumber: string;
  invoiceNumber: string;
  total: string;
  lineItems: Array<{
    lineItemIndex: number;
    itemCode: string;
    itemDescription: string;
    qty: string;
    unit: string;
    lineTotal?: string | number;
  }>;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  data: AribaResponseDTO;
  poNumber: string;
  invoiceNumber?: string;
  pdfId: string | null;
  onAfterSave?: () => void;
  onRefreshComparison?: (poNumber: string, pdfId: string) => Promise<any>;
}

interface EditableDataState {
  poNumber: string;
  invoiceNumber: string;
  total: string;
  items: Record<
    number,
    {
      itemCode: string;
      itemDescription: string;
      qty: string;
      unit: string;
      lineTotal?: string | number;
      line_item?: string;
    }
  >;
}

type FilterStatus = 'all' | 'matched' | 'mismatched';

export const BuyerInvoiceCompareModal: React.FC<Props> = ({
  isOpen,
  onClose,
  data,
  poNumber,
  invoiceNumber = '',
  pdfId,
  onAfterSave,
  onRefreshComparison,
}) => {
  const [editableData, setEditableData] = useState<EditableDataState>({
    poNumber: '',
    invoiceNumber: invoiceNumber,
    total: '',
    items: {},
  });

  const [stage, setStage] = useState<'comparison' | 'review' | 'submitted'>('comparison');
  const [reviewData, setReviewData] = useState<ReviewData | null>(null);
  const [finalSubmittedData, setFinalSubmittedData] = useState<any>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploadingS4, setIsUploadingS4] = useState(false);
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('all');
  const [showInvoice, setShowInvoice] = useState(true);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [isPdfLoading, setIsPdfLoading] = useState(false);
  const [pdfLoadError, setPdfLoadError] = useState<string | null>(null);

  const [showRcDetails, setShowRcDetails] = useState(false);
  const [rcSummaryData, setRcSummaryData] = useState<PoReceiptsSummaryResponseDto | null>(null);
  const [isRcLoading, setIsRcLoading] = useState(false);
  const [rcLoadError, setRcLoadError] = useState<string | null>(null);

  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingPoNumbers, setIsLoadingPoNumbers] = useState(false);
  const [hasFetchedSupplierPurchaseOrders, setHasFetchedSupplierPurchaseOrders] = useState(false);
  const [supplierPurchaseOrders, setSupplierPurchaseOrders] = useState<SupplierPurchaseOrder[]>([]);
  const [selectedSupplierPo, setSelectedSupplierPo] = useState('');
  const [pendingSupplierPo, setPendingSupplierPo] = useState('');
  const [isPoConfirmationOpen, setIsPoConfirmationOpen] = useState(false);

  const getPoPercentageColorClass = (percentage?: string) => {
    const percentageValue = Number(String(percentage || '').replace('%', '').trim());
    const absolutePercentage = Math.abs(percentageValue);

    if (!Number.isFinite(percentageValue) || absolutePercentage > 100) {
      return '';
    }

    if (percentageValue === 0) {
      return 'bicm-po-percentage-zero';
    }

    return absolutePercentage <= 50
      ? 'bicm-po-percentage-dark'
      : 'bicm-po-percentage-light';
  };

  const formatPoPercentage = (percentage?: string) => percentage?.replace(/^[+-]\s*/, '');

  const loadInvoicePdf = async (currentPdfId: string) => {
    setIsPdfLoading(true);
    setPdfLoadError(null);

    try {
      const response = await fetchPdfBase64(currentPdfId);
      if (response && 'statusCode' in response) {
        setPdfLoadError(response.message || 'Failed to load invoice PDF');
        return;
      }

      let base64 =
        (response as any)?.file_base64 ||
        (response as any)?.base64 ||
        (response as any)?.pdf_base64;
      if (!base64) {
        setPdfLoadError('No PDF data found for this invoice');
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

      setPdfUrl((prevUrl) => {
        if (prevUrl) {
          URL.revokeObjectURL(prevUrl);
        }
        return URL.createObjectURL(blob);
      });
    } catch {
      setPdfLoadError('Failed to load invoice PDF');
    } finally {
      setIsPdfLoading(false);
    }
  };

  const fetchRcSummary = async () => {
    const rawPo =
      editableData.poNumber ||
      data.po_number_comparison?.invoice_po_number ||
      data.po_number_comparison?.ariba_po_number ||
      data.po_number_comparison?.receipt_po_number ||
      poNumber ||
      '';

    const formattedPoStr = formatPoNumbers(rawPo);
    const poList = Array.from(new Set(formattedPoStr.split(',').map((p) => p.trim()).filter(Boolean)));
    if (poList.length === 0) return;

    setIsRcLoading(true);
    setRcLoadError(null);

    try {
      const results = await Promise.all(poList.map((po) => getPoReceiptsSummary(po)));
      const combinedReceipts: PoReceiptDto[] = [];
      let errorCount = 0;
      let lastErrorMessage = '';

      results.forEach((res) => {
        if (res && !('statusCode' in res) && Array.isArray((res as PoReceiptsSummaryResponseDto).receipts)) {
          combinedReceipts.push(...(res as PoReceiptsSummaryResponseDto).receipts);
        } else if (res && 'statusCode' in res) {
          errorCount++;
          lastErrorMessage = (res as any).message || 'Failed to fetch RC details';
        }
      });

      if (combinedReceipts.length > 0) {
        setRcSummaryData({
          po_number: poList.join(', '),
          total_rc_count: combinedReceipts.length,
          receipts: combinedReceipts,
        });
      } else if (errorCount === results.length) {
        setRcLoadError(lastErrorMessage || 'Failed to fetch RC details');
      } else {
        setRcSummaryData({
          po_number: poList.join(', '),
          total_rc_count: 0,
          receipts: [],
        });
      }
    } catch {
      setRcLoadError('Failed to fetch RC details');
    } finally {
      setIsRcLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      setStage('comparison');
      setReviewData(null);
      setFinalSubmittedData(null);
      setEditableData({
        poNumber: '',
        invoiceNumber: invoiceNumber,
        total: '',
        items: {},
      });
      setSupplierPurchaseOrders([]);
      setHasFetchedSupplierPurchaseOrders(false);
      setSelectedSupplierPo('');
      setPendingSupplierPo('');
      setIsPoConfirmationOpen(false);
      setIsLoadingPoNumbers(false);
    }
  }, [isOpen, pdfId]);

  useEffect(() => {
    if (!isOpen || !pdfId) return;
    loadInvoicePdf(pdfId);

    return () => {
      setPdfUrl((prevUrl) => {
        if (prevUrl) {
          URL.revokeObjectURL(prevUrl);
        }
        return null;
      });
    };
  }, [isOpen, pdfId]);

  if (!isOpen) return null;

  const poComparison = data.po_number_comparison;
  const totalComparison = data.total_comparison;
  const allLineItems: LineItemComparisonDTO[] = data.Comparision_of_lineItems || [];
  const showRctData = !isServicePoOrTypeCode2(data);
  const alreadyProcessedMessage = 'Invoice already exists in SAP - marked processed. Service PO comparison included for reference.';
  const isAlreadyProcessed = data.message?.trim() === alreadyProcessedMessage;

  const displayValue = (value: any) =>
    value === null || value === undefined || value === '' ? '—' : value;

  const getStatusBadge = (status: string) => {
    const isMatched = status === 'matched';
    const statusClass = isMatched ? 'bicm-status-matched' : 'bicm-status-mismatched';
    const StatusIcon = isMatched ? Check : AlertTriangle;
    const statusText = isMatched ? 'Matched' : 'Mismatched';

    return (
      <span className={`bicm-status-badge ${statusClass}`}>
        <span className="bicm-status-icon">
          <StatusIcon size={12} />
        </span>
        {statusText}
      </span>
    );
  };

  const handleFilterChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    setFilterStatus(event.target.value as FilterStatus);
  };

  const shouldShowHeaderRow = (status: string) => {
    if (filterStatus === 'all') return true;
    if (filterStatus === 'matched') return status === 'matched';
    if (filterStatus === 'mismatched') return status !== 'matched';
    return false;
  };

  const handleCopyAribaRCT = () => {
    const newEditableData: EditableDataState = {
      poNumber: String(poComparison?.receipt_po_number || ''),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(totalComparison?.receipt_total || ''),
      items: {},
    };

    allLineItems.forEach((item, idx) => {
      const rItem = item.receipt_item;
      if (rItem && (rItem.line_item || rItem.description || rItem.quantity || rItem.unit_of_measure)) {
        newEditableData.items[idx] = {
          itemCode: String(rItem.line_item || ''),
          itemDescription: String(rItem.description || ''),
          qty: String(rItem.quantity || ''),
          unit: String(rItem.unit_of_measure || ''),
        };
      }
    });

    setEditableData(newEditableData);
  };

  const handleCopyAribaRef = () => {
    const newEditableData: EditableDataState = {
      poNumber: String(data.ariba_reference_data?.ariba_po_number || ''),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(data.ariba_reference_data?.ariba_total || ''),
      items: {},
    };

    allLineItems.forEach((item, idx) => {
      const refItem =
        item.ariba_item?.description || item.ariba_item?.line_item
          ? item.ariba_item
          : data.ariba_reference_data?.ariba_line_items?.find(
              (ri) => String(ri.line_item) === String(item.line_item_comparison_number)
            );

      if (refItem && (refItem.line_item || refItem.description || refItem.quantity || refItem.unit_of_measure)) {
        newEditableData.items[idx] = {
          itemCode: String(refItem.line_item || ''),
          itemDescription: String(refItem.description || ''),
          qty: String(refItem.quantity || ''),
          unit: String(refItem.unit_of_measure || ''),
          lineTotal: refItem.line_total ?? undefined,
        };
      }
    });

    setEditableData(newEditableData);
  };

  const handleCopyInvoice = () => {
    const newEditableData: EditableDataState = {
      poNumber: String(poComparison?.invoice_po_number || ''),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(totalComparison?.invoice_total || ''),
      items: {},
    };

    allLineItems.forEach((item, idx) => {
      newEditableData.items[idx] = {
        itemCode: String(item.invoice_item?.ItemCode || item.invoice_item?.line_item || item.line_item_comparison_number || ''),
        itemDescription: String(item.invoice_item?.ItemDescription || ''),
        qty: String(item.invoice_item?.QTY || ''),
        unit: String(item.invoice_item?.Unit || item.ariba_item?.unit_of_measure || ''),
        lineTotal: item.invoice_item?.line_total ?? undefined,
      };
    });

    setEditableData(newEditableData);
  };

  const handleCopyReceiptData = (receipt: PoReceiptDto) => {
    const newEditableData: EditableDataState = {
      poNumber: String(
        poComparison?.ariba_po_number ||
          data.ariba_reference_data?.ariba_po_number ||
          poNumber ||
          ''
      ),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(receipt.total_gross_amount ?? ''),
      items: {},
    };

    allLineItems.forEach((item, idx) => {
      const matchedReceiptItem = receipt.items.find(
        (ri) =>
          (ri.description && item.invoice_item?.ItemDescription &&
            (ri.description.toLowerCase().includes(item.invoice_item.ItemDescription.toLowerCase().slice(0, 4)) ||
              item.invoice_item.ItemDescription.toLowerCase().includes(ri.description.toLowerCase().slice(0, 4)))) ||
          String(ri.line_item) === String(item.line_item_comparison_number) ||
          (item.receipt_item?.line_item && String(ri.line_item) === String(item.receipt_item.line_item))
      );

      if (matchedReceiptItem && (matchedReceiptItem.line_item || matchedReceiptItem.description || matchedReceiptItem.quantity || matchedReceiptItem.unit_of_measure)) {
        newEditableData.items[idx] = {
          itemCode: String(matchedReceiptItem.line_item || ''),
          itemDescription: String(matchedReceiptItem.description || ''),
          qty: String(matchedReceiptItem.quantity ?? ''),
          unit: String(matchedReceiptItem.unit_of_measure || ''),
        };
      }
    });

    setEditableData(newEditableData);
  };

  const handleItemFieldChange = (itemIndex: number, field: string, value: string) => {
    setEditableData((prev: EditableDataState) => ({
      ...prev,
      items: {
        ...prev.items,
        [itemIndex]: {
          ...(prev.items[itemIndex] || {
            itemCode: '',
            itemDescription: '',
            qty: '',
            unit: '',
          }),
          [field]: value,
        },
      },
    }));
  };

  const handleHeaderFieldChange = (
    field: keyof Omit<EditableDataState, 'items'>,
    value: string
  ) => {
    setEditableData((prev: EditableDataState) => ({
      ...prev,
      [field]: value,
    }));
  };

  const handleToggleInvoiceView = () => {
    if (!showInvoice && showRcDetails) {
      setShowRcDetails(false);
    }
    setShowInvoice(!showInvoice);
  };

  const handleToggleRcView = () => {
    if (!showRctData) return;
    if (!showRcDetails) {
      if (showInvoice) {
        setShowInvoice(false);
      }
      fetchRcSummary();
    }
    setShowRcDetails(!showRcDetails);
  };

  const handleRefreshData = async () => {
    if (!pdfId) return;

    const poNumberForCompare =
      editableData.poNumber || poComparison?.ariba_po_number || poNumber || '';

    setIsRefreshing(true);
    try {
      const refreshTasks: Promise<any>[] = [loadInvoicePdf(pdfId)];

      if (rcSummaryData) {
        refreshTasks.push(fetchRcSummary());
      }

      if (onRefreshComparison) {
        refreshTasks.push(onRefreshComparison(poNumberForCompare, pdfId));
      } else {
        refreshTasks.push(aribaDataCompare(poNumberForCompare, pdfId));
      }

      await Promise.all(refreshTasks);
      if (onAfterSave) onAfterSave();
    } catch (err) {
      console.error('Failed to refresh data:', err);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleGetAllPoNumbers = async () => {
    if (!pdfId) {
      toast.error('PDF ID is missing. Cannot get supplier PO numbers.');
      return;
    }

    setIsLoadingPoNumbers(true);
    try {
      const invoiceResponse = await getInvoiceDataFromPdfId(pdfId);
      const invoice =
        'results' in invoiceResponse && invoiceResponse.results[0]?.invoices[0]
          ? invoiceResponse.results[0].invoices[0]
          : null;
      const supplierId = invoice?.supplier_id;
      const invoiceTotal = invoice?.Invoice_Total;

      if (!supplierId) {
        toast.error('Supplier ID was not found for this invoice.');
        return;
      }

      if (invoiceTotal === undefined || invoiceTotal === null || invoiceTotal === '') {
        toast.error('Invoice total was not found for this invoice.');
        return;
      }

      const parsedInvoiceTotal = Number(String(invoiceTotal).replace(/,/g, ''));
      const result = await getAllPoNumbersForSupplier(
        String(supplierId),
        Number.isFinite(parsedInvoiceTotal) ? parsedInvoiceTotal : invoiceTotal
      );
      setSupplierPurchaseOrders(result.purchaseOrders);
      setHasFetchedSupplierPurchaseOrders(true);
      if (result.purchaseOrders.length === 0) {
        toast.info('No PO numbers found for this supplier.');
      }
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Failed to get supplier PO numbers.');
    } finally {
      setIsLoadingPoNumbers(false);
    }
  };

  const handleSupplierPoChange = async (selectedPo: string) => {
    if (!selectedPo || !pdfId) return;

    setPendingSupplierPo(selectedPo);
    setIsPoConfirmationOpen(true);
  };

  const handleConfirmSupplierPo = async () => {
    const selectedPo = pendingSupplierPo;
    if (!selectedPo || !pdfId) return;

    setSelectedSupplierPo(selectedPo);
    setIsPoConfirmationOpen(false);

    const toastId = 'update-invoice-po';
    toast.loading(`Updating invoice with PO ${selectedPo}...`, { toastId });

    try {
      const invoiceResponse = await getInvoiceDataFromPdfId(pdfId);
      const invoice =
        'results' in invoiceResponse && invoiceResponse.results[0]?.invoices[0]
          ? invoiceResponse.results[0].invoices[0]
          : null;

      if (!invoice) {
        toast.dismiss(toastId);
        toast.error('Invoice data was not found.');
        return;
      }

      const allowedInvoiceFields = [
        'user_id', 'source_file', 'invoice_id', 'InvoiceNumber', 'SupplierName',
        'CustomerName', 'InvoiceDate', 'SupplierTRN', 'CustomerTRN', 'Email',
        'Invoice_Total', 'is_trusted', 'BankAccountName', 'BankName', 'BankBranch',
        'AccountNumber', 'IBAN', 'SwiftCode', 'BranchCode', 'Tax_invoice', 'Stamp',
        'Customer_address', 'Supplier_address',
      ] as const;

      const payload: UpdateInvoicePayload = {};
      allowedInvoiceFields.forEach((field) => {
        const value = invoice[field as keyof typeof invoice];
        if (value !== undefined && value !== null) {
          (payload as Record<string, unknown>)[field] = value;
        }
      });

      payload.items = (invoice.Items || []).map((item) => ({
        LineItemNumber: item.LineItemNumber,
        ItemCode: item.ItemCode,
        ItemDescription: item.ItemDescription,
        QTY: item.QTY,
        Unit: item.Unit || undefined,
        UnitPrice: item.UnitPrice,
        Amount: item.Amount,
        Discount: item.Discount || undefined,
        VAT: item.VAT || undefined,
        VATAmount: item.VATAmount || undefined,
        AmountInclVAT: item.AmountInclVAT || undefined,
        PONumber: selectedPo,
        Po_Total: item.Po_Total || undefined,
      }));

      const response = await updateInvoiceData(pdfId, payload);
      if (response && 'statusCode' in response && response.statusCode >= 400) {
        toast.dismiss(toastId);
        toast.error(response.message || 'Failed to update invoice PO number.');
        return;
      }

      toast.dismiss(toastId);
      toast.success(`Invoice updated with PO ${selectedPo}.`);

      try {
        if (onRefreshComparison) {
          await onRefreshComparison(selectedPo, pdfId);
        } else {
          await aribaDataCompare(selectedPo, pdfId);
        }
      } catch (compareErr) {
        console.error('Failed to refresh comparison after PO update:', compareErr);
      }

      if (onAfterSave) onAfterSave();
    } catch (error: any) {
      toast.dismiss(toastId);
      toast.error(error?.response?.data?.detail || 'Failed to update invoice PO number.');
    }
  };

  const handleCancelSupplierPo = () => {
    setPendingSupplierPo('');
    setIsPoConfirmationOpen(false);
  };

  const hasDataToPush = () =>
    Boolean(
      editableData.poNumber ||
        editableData.invoiceNumber ||
        editableData.total ||
        Object.values(editableData.items).some(
          (item) => item.itemCode || item.itemDescription || item.qty || item.unit || item.lineTotal
        )
    );

  const handleReview = async () => {
    if (!hasDataToPush()) {
      toast.warning('Please fill in data to be pushed before reviewing.');
      return;
    }

    const lineItems = allLineItems
      .map((item, idx) => {
        const values = editableData.items[idx];
        if (!values || (!values.itemCode && !values.itemDescription && !values.qty && !values.unit)) {
          return null;
        }
        return {
          lineItemIndex: item.line_item_comparison_number,
          itemCode: values.itemCode || '',
          itemDescription: values.itemDescription || '',
          qty: values.qty || '',
          unit: values.unit || '',
          lineTotal: values.lineTotal,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null);

    const nextReviewData: ReviewData = {
      poNumber: editableData.poNumber || poNumber,
      invoiceNumber: editableData.invoiceNumber || invoiceNumber,
      total: editableData.total || String(totalComparison?.invoice_total || ''),
      lineItems,
    };

    setReviewData(nextReviewData);
    await handleSubmit(nextReviewData);
  };

  const handleBackToComparison = () => {
    setStage('comparison');
    setReviewData(null);
  };

  const handleSubmit = async (submittedReviewData: ReviewData | null = reviewData) => {
    if (!submittedReviewData || !pdfId) {
      toast.error(!pdfId ? 'PDF ID is missing. Cannot update.' : 'Nothing to submit.');
      return;
    }

    setIsSubmitting(true);
    try {
      const currentData = await getInvoiceDataFromPdfId(pdfId);
      const currentInvoice: any = 'results' in currentData && currentData.results.length > 0
        ? currentData.results[0].invoices[0] || {}
        : {};
      const updatedItems = submittedReviewData.lineItems.map((line) => {
        const existing = (currentInvoice.Items || []).find(
          (item: any, index: number) =>
            String(item.LineItemNumber) === String(line.lineItemIndex) || String(index + 1) === String(line.lineItemIndex)
        );
        return {
          ...(existing || {}),
          LineItemNumber: existing?.LineItemNumber || String(line.lineItemIndex),
          ItemCode: line.itemCode || existing?.ItemCode,
          ItemDescription: line.itemDescription || existing?.ItemDescription,
          QTY: line.qty || existing?.QTY,
          Unit: line.unit || existing?.Unit,
          Amount: line.lineTotal !== undefined && line.lineTotal !== '' ? String(line.lineTotal) : existing?.Amount,
          PONumber: submittedReviewData.poNumber || existing?.PONumber,
        };
      });
      const payload: UpdateInvoicePayload = {
        ...currentInvoice,
        InvoiceNumber: submittedReviewData.invoiceNumber || currentInvoice.InvoiceNumber,
        Invoice_Total: submittedReviewData.total || currentInvoice.Invoice_Total || currentInvoice.Total,
        items: updatedItems,
      };
      delete (payload as any).Items;
      delete (payload as any).Total;

      const response = await updateInvoiceData(pdfId, payload);
      if (response && 'statusCode' in response && response.statusCode >= 400) {
        toast.error(response.message || 'Invoice update failed.');
        return;
      }
      setFinalSubmittedData({ ...currentInvoice, ...payload, items: updatedItems });
      setStage('submitted');
      toast.success('Invoice updated successfully.');
    } catch (error) {
      toast.error('Failed to submit invoice data.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUploadS4 = async () => {
    if (!finalSubmittedData || !pdfId) return;
    setIsUploadingS4(true);
    try {
      const items = finalSubmittedData.items || [];
      const firstItem = items[0] || {};
      const documentDate = formatDateToYYYYMMDD(finalSubmittedData.InvoiceDate || '');
      const s4Payload: S4InvoiceCreatePayload = {
        pdf_id: String(pdfId),
        CompanyCode: String(finalSubmittedData.CompanyCode || ''),
        DocumentDate: documentDate,
        DueCalculationBaseDate: documentDate,
        DocumentHeaderText: String(
          firstItem.ItemDescription ||
            firstItem.itemDescription ||
            reviewData?.lineItems?.[0]?.itemDescription ||
            ''
        ),
        InvoiceGrossAmount: String(finalSubmittedData.Invoice_Total || finalSubmittedData.Total || ''),
        SupplierInvoiceIDByInvcgParty: String(finalSubmittedData.InvoiceNumber || invoiceNumber || ''),
        TaxIsCalculatedAutomatically: false,
        DocumentCurrency: String(finalSubmittedData.DocumentCurrency || ''),
        to_SuplrInvcItemPurOrdRef: {
          results: items.map((item: any, index: number) => ({
            SupplierInvoiceItem: String(index + 1).padStart(2, '0'),
            PurchaseOrder: String(item.PONumber || reviewData?.poNumber || ''),
            PurchaseOrderItem: String(item.purchase_order_item || item.LineItemNumber || ''),
            DocumentCurrency: String(item.DocumentCurrency || finalSubmittedData.DocumentCurrency || ''),
            TaxCode: String(item.tax_code || item.VAT || ''),
            QuantityInPurchaseOrderUnit: String(item.QTY || ''),
            SupplierInvoiceItemAmount: String(item.Amount || item.UnitPrice || ''),
            PurchaseOrderQuantityUnit: String(item.PurchaseOrderQuantityUnit || item.Unit || ''),
            TaxJurisdiction: String(item.tax_jurisdiction || ''),
          })),
        },
        to_SupplierInvoiceTax: {
          results: [
            {
              TaxCode: String(firstItem.tax_code || firstItem.VAT || ''),
              TaxJurisdiction: String(firstItem.tax_jurisdiction || ''),
              DocumentCurrency: String(finalSubmittedData.DocumentCurrency || ''),
              TaxAmount: '0.00',
            },
          ],
        },
      };
      const response = await createInvoiceInS4(s4Payload, pdfId);
      const responseStatus = Number(
        response?.statusCode ??
          response?.status_code ??
          response?.status ??
          response?.code ??
          200
      );
      const responseMessage = response?.message || response?.detail || response?.error;
      const responseDetails = response?.description || response?.errors;
      const uploadFailed =
        responseStatus >= 400 ||
        response?.success === false ||
        Boolean(response?.error);

      if (uploadFailed) {
        const details = responseDetails
          ? `: ${typeof responseDetails === 'string' ? responseDetails : JSON.stringify(responseDetails)}`
          : '';
        toast.error(`${responseMessage || 'S4 upload failed.'}${details}`, { autoClose: 5000 });
        return;
      }

      toast.success(responseMessage || 'Uploaded to S4 successfully.', { autoClose: 3000 });
      onClose();
    } catch (error: any) {
      const errorData = error?.response?.data;
      const errorMessage =
        errorData?.detail || errorData?.message || error?.message || 'Failed to upload to S4.';
      toast.error(errorMessage, { autoClose: 5000 });
    } finally {
      setIsUploadingS4(false);
    }
  };

  const renderWorkflowData = () => (
    <div className="bicm-modal-content">
      <h3>{stage === 'review' ? 'Reviewed Data to be Pushed' : 'Submitted Invoice Data'}</h3>
      <table className="bicm-comparison-table">
        <tbody>
          <tr><td>PO Number</td><td>{displayValue(reviewData?.poNumber)}</td></tr>
          <tr><td>Invoice Number</td><td>{displayValue(reviewData?.invoiceNumber)}</td></tr>
          <tr>
            <td>Total</td>
            <td>
              {displayValue(reviewData?.total)}{' '}
              {finalSubmittedData?.DocumentCurrency || ''}
            </td>
          </tr>
        </tbody>
      </table>
      <h3>Line Items</h3>
      <table className="bicm-comparison-table">
        <thead><tr><th>Line</th><th>Item Code</th><th>Description</th><th>QTY</th><th>Unit</th></tr></thead>
        <tbody>
          {(stage === 'review' ? reviewData?.lineItems : finalSubmittedData?.items || []).map((item: any, index: number) => (
            <tr key={index}>
              <td>{item.lineItemIndex || item.LineItemNumber}</td>
              <td>{displayValue(item.itemCode || item.ItemCode)}</td>
              <td>{displayValue(item.itemDescription || item.ItemDescription)}</td>
              <td>{displayValue(item.qty || item.QTY)}</td>
              <td>{displayValue(item.unit || item.Unit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const CopyButton = ({
    label,
    onClick,
  }: {
    label: string;
    onClick: () => void;
  }) => (
    <button className="bicm-copy-button" onClick={onClick}>
      <span className="bicm-copy-icon">
        <Copy size={13} />
      </span>
      {label}
    </button>
  );

  const renderInvoicePanel = () => (
    <div className="bicm-pdf-panel">
      <div className="bicm-pdf-panel-header">Invoice Document</div>
      <div className="bicm-pdf-panel-body">
        {isPdfLoading ? (
          <div className="bicm-pdf-status">
            <span className="bicm-pdf-spinner" /> Loading document...
          </div>
        ) : pdfLoadError ? (
          <div className="bicm-pdf-status bicm-pdf-error">{pdfLoadError}</div>
        ) : pdfUrl ? (
          <iframe
            src={pdfUrl}
            title="Invoice PDF"
            className="bicm-pdf-iframe"
          />
        ) : (
          <div className="bicm-pdf-status">No PDF available</div>
        )}
      </div>
    </div>
  );

  const renderRcDetailsPanel = () => (
    <div className="bicm-pdf-panel">
      <div className="bicm-pdf-panel-header">RC Details (PO Receipts)</div>
      <div className="bicm-pdf-panel-body bicm-rc-panel-body">
        {isRcLoading ? (
          <div className="bicm-pdf-status">
            <span className="bicm-pdf-spinner" /> Loading RC details...
          </div>
        ) : rcLoadError ? (
          <div className="bicm-pdf-status bicm-pdf-error">{rcLoadError}</div>
        ) : rcSummaryData && rcSummaryData.receipts && rcSummaryData.receipts.length > 0 ? (
          <div className="bicm-rc-list">
            {rcSummaryData.receipts.map((receipt, idx) => (
              <div key={idx} className="bicm-rc-card">
                <div className="bicm-rc-card-header">
                  <div className="bicm-rc-card-heading">
                    <span className="bicm-rc-id">Receipt #{receipt.receipt_id}</span>
                    <span className="bicm-rc-meta">
                      {receipt.total_line_items} item(s) • Gross: {receipt.total_gross_amount}
                    </span>
                  </div>
                  <CopyButton
                    label="Copy"
                    onClick={() => handleCopyReceiptData(receipt)}
                  />
                </div>
                <div className="bicm-table-scroll">
                  <table className="bicm-comparison-table bicm-rc-table">
                    <thead>
                      <tr>
                        <th>Line</th>
                        <th>Description</th>
                        <th>QTY</th>
                        <th>Unit</th>
                        <th>Amount</th>
                        <th>Gross Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {receipt.items.map((item, i) => (
                        <tr key={i}>
                          <td>{displayValue(item.line_item)}</td>
                          <td>{displayValue(item.description)}</td>
                          <td>{displayValue(item.quantity)}</td>
                          <td>{displayValue(item.unit_of_measure)}</td>
                          <td>{displayValue(item.amount_accepted)}</td>
                          <td>{displayValue(item.gross_amount_accepted)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="bicm-pdf-status">No RC (receipt) data found for this PO.</div>
        )}
      </div>
    </div>
  );

  const isSplitView = (showInvoice || (showRctData && showRcDetails)) && stage === 'comparison';

  return (
    <div className="bicm-modal-overlay">
      <div className="bicm-modal-container">
        <div className="bicm-modal-header">
          <div className="bicm-title-wrapper">
            <h2 className="bicm-modal-title">Data Comparison</h2>
            {isAlreadyProcessed && (
              <span className="bicm-comparison-message">{data.message}</span>
            )}
          </div>
          <button onClick={onClose} className="bicm-close-button" aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        {stage === 'comparison' && (
          <div className="bicm-action-buttons">
            <div className="bicm-dropdown-wrapper">
              <select
                className="bicm-dropdown"
                value={filterStatus}
                onChange={handleFilterChange}
                aria-label="Filter status"
              >
                <option value="all">Show All</option>
                <option value="matched">Show Matched</option>
                <option value="mismatched">Show Mismatched</option>
              </select>
            </div>

            <div className="bicm-buttons-wrapper">
              <button
                className="bicm-icon-button bicm-refresh-button"
                onClick={handleRefreshData}
                disabled={isRefreshing}
                title="Refresh table data"
              >
                <RefreshCw size={14} className={isRefreshing ? 'bicm-spin' : ''} />
              </button>
              {!hasFetchedSupplierPurchaseOrders && (
                <button
                  className="bicm-copy-button"
                  onClick={handleGetAllPoNumbers}
                  disabled={isLoadingPoNumbers}
                  title="Get all PO numbers for this supplier"
                >
                  {isLoadingPoNumbers ? 'Loading POs...' : 'Get All PO'}
                </button>
              )}
              {supplierPurchaseOrders.length > 0 && (
                <select
                  className={`bicm-po-select ${getPoPercentageColorClass(
                    supplierPurchaseOrders.find((po) => po.purchase_order === selectedSupplierPo)?.percentage
                  )}`}
                  value={selectedSupplierPo}
                  onChange={(event) => handleSupplierPoChange(event.target.value)}
                  aria-label="Select supplier PO number"
                >
                  <option value="">Select PO</option>
                  {supplierPurchaseOrders.map((po) => {
                    const labelParts = [po.purchase_order];
                    if (po.po_total !== undefined && po.po_total !== null) {
                      labelParts.push(`PO_total:${po.po_total}`);
                    }
                    if (po.purchase_order_date) {
                      labelParts.push(`PO_date:${po.purchase_order_date}`);
                    }
                    if (po.percentage) {
                      labelParts.push(formatPoPercentage(po.percentage) || po.percentage);
                    }
                    return (
                      <option
                        key={po.purchase_order}
                        value={po.purchase_order}
                        className={getPoPercentageColorClass(po.percentage)}
                      >
                        {labelParts.join(', ')}
                      </option>
                    );
                  })}
                </select>
              )}
              <button
                className="bicm-toggle-invoice-button"
                onClick={handleToggleInvoiceView}
                title={showInvoice ? 'Hide invoice' : 'Show invoice'}
              >
                {showInvoice ? <EyeOff size={14} /> : <Eye size={14} />}
                {showInvoice ? 'Hide Invoice' : 'Show Invoice'}
              </button>
              {showRctData && (
                <button
                  className="bicm-toggle-rc-button"
                  onClick={handleToggleRcView}
                  title={showRcDetails ? 'Hide RC details' : 'Show all RC details'}
                >
                  {showRcDetails ? <EyeOff size={14} /> : <Eye size={14} />}
                  {showRcDetails ? 'Hide RC Details' : 'Show All RC Details'}
                </button>
              )}
              <CopyButton
                label="Copy PO Data"
                onClick={handleCopyAribaRef}
              />
              {showRctData && (
                <CopyButton
                  label="Copy RCT Data"
                  onClick={handleCopyAribaRCT}
                />
              )}
              <CopyButton
                label="Copy Invoice Data"
                onClick={handleCopyInvoice}
              />
              <button className="bicm-edit-button" aria-label="Edit mode">
                <Edit size={14} className="bicm-edit-icon" />
                Edit
              </button>
            </div>
          </div>
        )}

        <div className={`bicm-modal-body ${isSplitView ? 'bicm-modal-body-split' : ''}`}>
          {isSplitView && (showInvoice ? renderInvoicePanel() : (showRctData ? renderRcDetailsPanel() : null))}

          <div className="bicm-modal-content">
            {stage === 'comparison' ? (
              <>
                <div className="bicm-comparison-section">
                  <div className="bicm-table-scroll">
                    <table className="bicm-comparison-table">
                      <thead>
                        <tr>
                          <th className="bicm-field-column">FIELD</th>
                          <th className="bicm-data-column">PO DATA</th>
                          {showRctData && <th className="bicm-data-column">RCT DATA</th>}
                          <th className="bicm-data-column">INVOICE EXTRACTED DATA</th>
                          <th className="bicm-status-column">STATUS</th>
                          <th className="bicm-comments-column">COMMENTS</th>
                          <th className="bicm-data-column">DATA TO BE PUSHED INTO</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shouldShowHeaderRow(poComparison?.status || 'mismatched') && (
                          <tr>
                            <td className="bicm-field-name">
                              PO Number<span className="bicm-required">*</span>
                            </td>
                            <td>{displayValue(formatPoNumbers(poComparison?.ariba_po_number))}</td>
                            {showRctData && <td>{displayValue(formatPoNumbers(poComparison?.receipt_po_number))}</td>}
                            <td>{displayValue(formatPoNumbers(poComparison?.invoice_po_number))}</td>
                            <td>{getStatusBadge(poComparison?.status || 'mismatched')}</td>
                            <td>{displayValue(poComparison?.error_comment)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.poNumber}
                                onChange={(e) => handleHeaderFieldChange('poNumber', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={poComparison?.ariba_po_number || poNumber}
                              />
                            </td>
                          </tr>
                        )}

                        <tr>
                          <td className="bicm-field-name">Invoice Number</td>
                          <td>—</td>
                          {showRctData && <td>—</td>}
                          <td>{displayValue(invoiceNumber)}</td>
                          <td>—</td>
                          <td>—</td>
                          <td>
                            <input
                              type="text"
                              value={editableData.invoiceNumber}
                              onChange={(e) => handleHeaderFieldChange('invoiceNumber', e.target.value)}
                              className="bicm-editable-input"
                              placeholder={invoiceNumber || 'Enter Invoice Number'}
                            />
                          </td>
                        </tr>

                        {shouldShowHeaderRow(totalComparison?.status || 'mismatched') && (
                          <tr>
                            <td className="bicm-field-name">Total</td>
                            <td>{displayValue(totalComparison?.ariba_total)}</td>
                            {showRctData && <td>{displayValue(totalComparison?.receipt_total)}</td>}
                            <td>{displayValue(totalComparison?.invoice_total)}</td>
                            <td>{getStatusBadge(totalComparison?.status || 'mismatched')}</td>
                            <td>{displayValue(totalComparison?.error_comment)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.total}
                                onChange={(e) => handleHeaderFieldChange('total', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={String(totalComparison?.ariba_total || '')}
                              />
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {allLineItems.map((item, index) => (
                  <div key={index} className="bicm-item-section">
                    <div className="bicm-item-header">
                      <h3>Item {item.line_item_comparison_number}</h3>
                    </div>
                    <div className="bicm-table-scroll">
                      <table className="bicm-comparison-table">
                        <thead>
                          <tr>
                            <th className="bicm-field-column">FIELD</th>
                            <th className="bicm-data-column">PO DATA</th>
                            {showRctData && <th className="bicm-data-column">RCT DATA</th>}
                            <th className="bicm-data-column">INVOICE EXTRACTED DATA</th>
                            <th className="bicm-status-column">STATUS</th>
                            <th className="bicm-comments-column">COMMENTS</th>
                            <th className="bicm-data-column">DATA TO BE PUSHED INTO</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td className="bicm-field-name">Line item</td>
                            <td>{displayValue(item.ariba_item?.line_item)}</td>
                            {showRctData && <td>{displayValue(item.receipt_item?.line_item)}</td>}
                            <td>{displayValue(item.invoice_item?.line_item)}</td>
                            <td>{getStatusBadge(item.comparison?.ItemCode_vs_line_item || 'mismatched')}</td>
                            <td>{displayValue(item.comparison?.error_comments?.ItemCode)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.items[index]?.itemCode || ''}
                                onChange={(e) => handleItemFieldChange(index, 'itemCode', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={item.ariba_item?.line_item || ''}
                              />
                            </td>
                          </tr>

                          <tr>
                            <td className="bicm-field-name">Item Description</td>
                            <td>{displayValue(item.ariba_item?.description)}</td>
                            {showRctData && <td>{displayValue(item.receipt_item?.description)}</td>}
                            <td>{displayValue(item.invoice_item?.ItemDescription)}</td>
                            <td>{getStatusBadge(item.comparison?.ItemDescription_vs_description || 'mismatched')}</td>
                            <td>{displayValue(item.comparison?.error_comments?.ItemDescription)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.items[index]?.itemDescription || ''}
                                onChange={(e) => handleItemFieldChange(index, 'itemDescription', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={item.ariba_item?.description || ''}
                              />
                            </td>
                          </tr>

                          <tr>
                            <td className="bicm-field-name">QTY</td>
                            <td>{displayValue(item.ariba_item?.quantity)}</td>
                            {showRctData && <td>{displayValue(item.receipt_item?.quantity)}</td>}
                            <td>{displayValue(item.invoice_item?.QTY)}</td>
                            <td>{getStatusBadge(item.comparison?.QTY_vs_quantity || 'mismatched')}</td>
                            <td>{displayValue(item.comparison?.error_comments?.QTY)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.items[index]?.qty || ''}
                                onChange={(e) => handleItemFieldChange(index, 'qty', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={String(item.ariba_item?.quantity || '')}
                              />
                            </td>
                          </tr>

                          <tr>
                            <td className="bicm-field-name">Unit</td>
                            <td>{displayValue(item.ariba_item?.unit_of_measure)}</td>
                            {showRctData && <td>{displayValue(item.receipt_item?.unit_of_measure)}</td>}
                            <td>{displayValue(item.invoice_item?.Unit)}</td>
                            <td>{getStatusBadge(item.comparison?.Unit_vs_unit_of_measure || 'matched')}</td>
                            <td>{displayValue(item.comparison?.error_comments?.Unit)}</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.items[index]?.unit || ''}
                                onChange={(e) => handleItemFieldChange(index, 'unit', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={item.ariba_item?.unit_of_measure || ''}
                              />
                            </td>
                          </tr>

                          <tr>
                            <td className="bicm-field-name">Line Total</td>
                            <td>{displayValue(item.ariba_item?.line_total)}</td>
                            {showRctData && <td>{displayValue(item.receipt_item?.line_total)}</td>}
                            <td>{displayValue(item.invoice_item?.line_total)}</td>
                            <td>—</td>
                            <td>—</td>
                            <td>
                              <input
                                type="text"
                                value={editableData.items[index]?.lineTotal ?? ''}
                                onChange={(e) => handleItemFieldChange(index, 'lineTotal', e.target.value)}
                                className="bicm-editable-input"
                                placeholder={String(item.ariba_item?.line_total ?? '')}
                                aria-label={`Item ${item.line_item_comparison_number} line total input`}
                              />
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </>
            ) : renderWorkflowData()}
          </div>
        </div>

        <div className="bicm-modal-footer">
          {stage === 'submitted' ? (
            <>
              <button className="bicm-footer-button bicm-footer-button-secondary" onClick={onClose}>
                Close
              </button>
              <button
                className="bicm-footer-button bicm-footer-button-primary"
                onClick={handleUploadS4}
                disabled={isUploadingS4}
              >
                {isUploadingS4 ? 'Uploading to S4...' : 'Upload S4'}
              </button>
            </>
          ) : stage === 'review' ? (
            <>
              <button className="bicm-footer-button bicm-footer-button-secondary" onClick={handleBackToComparison} disabled={isSubmitting}>
                Back to Edit
              </button>
              <button className="bicm-footer-button bicm-footer-button-primary" onClick={() => handleSubmit()} disabled={isSubmitting}>
                {isSubmitting ? 'Submitting...' : 'Submit'}
              </button>
            </>
          ) : (
            <>
              <button className="bicm-footer-button bicm-footer-button-secondary" onClick={onClose}>
                Close
              </button>
              <button
                className="bicm-footer-button bicm-footer-button-primary"
                onClick={handleReview}
                disabled={!hasDataToPush() || isAlreadyProcessed || isSubmitting}
                title={isAlreadyProcessed ? 'This invoice is already processed in SAP.' : 'Review invoice data'}
              >
                {isSubmitting ? (
                  <>
                    <RefreshCw size={14} className="bicm-spin" />
                    Reviewing...
                  </>
                ) : (
                  'Review'
                )}
              </button>
            </>
          )}
        </div>
      </div>

      {isPoConfirmationOpen && (
        <div className="bicm-confirm-overlay" role="presentation">
          <div
            className="bicm-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="bicm-confirm-title"
          >
            <h3 id="bicm-confirm-title">Confirm PO update</h3>
            <p>Are you sure you want to update this invoice with PO {pendingSupplierPo}?</p>
            <div className="bicm-confirm-actions">
              <button
                type="button"
                className="bicm-footer-button bicm-footer-button-secondary"
                onClick={handleCancelSupplierPo}
              >
                Cancel
              </button>
              <button
                type="button"
                className="bicm-footer-button bicm-footer-button-primary"
                onClick={handleConfirmSupplierPo}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default BuyerInvoiceCompareModal;
