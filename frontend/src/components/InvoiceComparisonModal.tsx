import React, { useState, useMemo, useEffect } from "react";
import "./InvoiceComparisonModal.css";
import type { AribaResponseDTO, LineItemComparisonDTO } from "../Dto/InvoiceExtractionDto";
import { isServicePoOrTypeCode2 } from "../Dto/InvoiceExtractionDto";
import { FaEdit, FaTimes, FaCheck, FaExclamationTriangle } from "react-icons/fa";
import { LuCopy } from "react-icons/lu";
import { toast } from "react-toastify";
import { saveDraftReviewData, getInvoiceDataFromPdfId, updateInvoiceData, createInvoiceInS4, cleanPayload, formatDateToYYYYMMDD, type SaveDraftPayload, type UpdateInvoicePayload, type S4InvoiceCreatePayload } from "../api/invoiceExtract";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  data: AribaResponseDTO;
  poNumber: string;
  invoiceNumber?: string;
  pdfId: string | null;
  onAfterSave?: (updatedData: any) => void;
}

const stringifyOrEmpty = (value: string | number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value);

const stringifyOrNull = (value: string | number | null | undefined): string | null =>
  value === null || value === undefined ? null : String(value);

interface EditableDataState {
  poNumber: string;
  invoiceNumber: string;
  total: string;
  items: Record<number, {
    itemCode: string;
    itemDescription: string;
    qty: string;
    unit: string;
    line_item?: string;
    poNumber?: string;
    poTotal?: string | number;
    unitPrice?: string | number;
    amount?: string | number;
    lineTotal?: string | number;
    discount?: string | number;
    vat?: string | number;
    vatAmount?: string | number;
    amountInclVAT?: string | number;
  }>;
}

interface ReviewData {
  poNumber: string;
  invoiceNumber: string;
  total: string;
  lineItems: {
    lineItemIndex: number;
    itemCode: string;
    itemDescription: string;
    qty: string;
    unit: string;
    poNumber?: string;
    poTotal?: string | number;
    unitPrice?: string | number;
    amount?: string | number;
    lineTotal?: string | number;
    discount?: string | number;
    vat?: string | number;
    vatAmount?: string | number;
    amountInclVAT?: string | number;
  }[];
}

type FilterStatus = "all" | "matched" | "mismatched";

const InvoiceComparisonModal: React.FC<Props> = ({
  isOpen,
  onClose,
  data,
  poNumber,
  invoiceNumber = '',
  pdfId,
  onAfterSave,
}) => {
  const [editableData, setEditableData] = useState<EditableDataState>({
    poNumber: '',
    invoiceNumber: invoiceNumber,
    total: '',
    items: {},
  });

  const [isReviewing, setIsReviewing] = useState(false);
  const [reviewData, setReviewData] = useState<ReviewData | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [isEdited, setIsEdited] = useState(false);
  const [isSubmittedStage, setIsSubmittedStage] = useState(false);
  const [finalSubmittedData, setFinalSubmittedData] = useState<any>(null);
  const [isUploadingS4, setIsUploadingS4] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setIsReviewing(false);
      setReviewData(null);
      setIsSubmittedStage(false);
      setFinalSubmittedData(null);
      setIsEdited(false);
      setEditableData({
        poNumber: '',
        invoiceNumber: invoiceNumber,
        total: '',
        items: {},
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, pdfId]);

  if (!isOpen) return null;

  const poComparison = data.po_number_comparison;
  const totalComparison = data.total_comparison;
  const allLineItems: LineItemComparisonDTO[] = data.Comparision_of_lineItems || [];
  const showRctData = !isServicePoOrTypeCode2(data);


  const displayValue = (value: any) => {
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) return value.join(', ');
    return value;
  };

  const getPoNumberValue = (val: any) => {
    if (Array.isArray(val)) return val.join(', ');
    return val || '';
  };

  const getStatusBadge = (status: string) => {
    const isMatched = status === "matched";
    const statusClass = isMatched ? "icm-status-matched" : "icm-status-mismatched";
    const StatusIcon = isMatched ? FaCheck : FaExclamationTriangle;
    const statusText = isMatched ? "Matched" : "Mismatched";

    return (
      <span className={`icm-status-badge ${statusClass}`}>
        <span className="icm-status-icon">
          <StatusIcon />
        </span>
        {statusText}
      </span>
    );
  };

  const handleFilterChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    setFilterStatus(event.target.value as FilterStatus);
  };

  const shouldShowHeaderRow = (status: string) => {
    if (filterStatus === "all") return true;
    if (filterStatus === "matched") return status === "matched";
    if (filterStatus === "mismatched") return status !== "matched";
    return false;
  };

  const filteredLineItems = useMemo(() => {
    return allLineItems;
  }, [allLineItems]);

  const handleCopyAribaRCT = () => {
    const newEditableData: EditableDataState = {
      poNumber: String(poComparison?.receipt_po_number || ''),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(totalComparison?.receipt_total || ''),
      items: {}
    };

    allLineItems.forEach((item, idx) => {
      const rItem = item.receipt_item;
      if (rItem && (rItem.line_item || rItem.description || rItem.quantity || rItem.unit_of_measure)) {
        newEditableData.items[idx] = {
          itemCode: String(rItem.line_item || ''),
          itemDescription: String(rItem.description || ''),
          qty: String(rItem.quantity || ''),
          unit: String(rItem.unit_of_measure || '')
        };
      }
    });

    setEditableData(newEditableData);
    setIsEdited(true);
    toast.success("RCT Data copied successfully!");
  };

  const handleCopyAribaRef = () => {
    const newEditableData: EditableDataState = {
      poNumber: getPoNumberValue(data.ariba_reference_data?.ariba_po_number),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(data.ariba_reference_data?.ariba_total || ''),
      items: {}
    };

    allLineItems.forEach((item, idx) => {
      const refItem = item.ariba_item?.description || item.ariba_item?.line_item
        ? item.ariba_item
        : data.ariba_reference_data?.ariba_line_items?.find(
        (ri) =>
          String(ri.line_item) === String(item.line_item_comparison_number) ||
          (item.receipt_item?.line_item && String(ri.line_item) === String(item.receipt_item.line_item))
        ) || data.ariba_reference_data?.ariba_line_items?.[idx];

      if (refItem && (refItem.line_item || refItem.description || refItem.quantity || refItem.unit_of_measure)) {
          const optionalPoFields = refItem as typeof refItem & {
            unit_price?: string | number | null;
            tax_percentage?: string | number | null;
            tax_amount?: string | number | null;
          };
        newEditableData.items[idx] = {
          itemCode: String(refItem.line_item || ''),
          itemDescription: String(refItem.description || ''),
          qty: String(refItem.quantity || ''),
          unit: String(refItem.unit_of_measure || ''),
          poNumber: refItem.po_number || newEditableData.poNumber,
          poTotal: refItem.line_total ?? undefined,
          unitPrice: optionalPoFields.unit_price ?? undefined,
          amount: refItem.line_total ?? undefined,
          lineTotal: refItem.line_total ?? undefined,
          vat: optionalPoFields.tax_percentage ?? undefined,
          vatAmount: optionalPoFields.tax_amount ?? undefined,
        };
      }
    });

    setEditableData(newEditableData);
    setIsEdited(true);
    toast.success("Ariba Reference Data copied successfully!");
  };

  const handleCopyInvoice = () => {
    const newEditableData: EditableDataState = {
      poNumber: String(poComparison?.invoice_po_number || ''),
      invoiceNumber: editableData.invoiceNumber || invoiceNumber || '',
      total: String(totalComparison?.invoice_total || ''),
      items: {}
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
    setIsEdited(true);
    toast.success("Invoice Data copied successfully!");
  };

  const handleItemFieldChange = (itemIndex: number, field: string, value: string) => {
    setEditableData((prev: EditableDataState) => ({
      ...prev,
      items: {
        ...prev.items,
        [itemIndex]: {
          ...(prev.items[itemIndex] || { itemCode: '', itemDescription: '', qty: '', unit: '' }),
          [field]: value
        }
      }
    }));
    setIsEdited(true);
  };

  const handleHeaderFieldChange = (field: keyof Omit<EditableDataState, 'items'>, value: string) => {
    setEditableData((prev: EditableDataState) => ({
      ...prev,
      [field]: value
    }));
    setIsEdited(true);
  };

  const CopyButton = ({
    label,
    onClick,
  }: {
    label: string;
    onClick: () => void;
  }) => (
    <button className="icm-copy-button" onClick={onClick}>
      <span className="icm-copy-icon">
        <LuCopy />
      </span>
      {label}
    </button>
  );

  const hasDataToPush = () => {
    const hasHeaderData = editableData.poNumber || editableData.invoiceNumber || editableData.total;
    const hasLineItemData = Object.keys(editableData.items).length > 0 &&
      Object.values(editableData.items).some(item =>
        item.itemCode || item.itemDescription || item.qty || item.unit
      );
    return hasHeaderData || hasLineItemData;
  };

  const handleSaveDraft = async () => {
    if (!hasDataToPush()) {
      toast.warning("Nothing to save. Please enter data in the 'Data to be Pushed into Ariba' column before saving as draft.");
      return;
    }

    // Build line items with proper typing
    const lineItemsForDraft = allLineItems
      .map((item, idx) => {
        const itemData = editableData.items[idx];

        if (!itemData || (!itemData.itemCode && !itemData.itemDescription && !itemData.qty && !itemData.unit)) {
          return null;
        }

        return {
          Line_item: String(item.line_item_comparison_number),
          Item_description: itemData.itemDescription || '',
          QTY: itemData.qty || '',
          Unit: itemData.unit || '',
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null);
    const guaranteedPoNumber = editableData.poNumber ||
      getPoNumberValue(data.ariba_reference_data?.ariba_po_number) ||
      poComparison?.ariba_po_number ||
      poNumber ||
      '';
    if (!guaranteedPoNumber) {
      toast.error("PO Number is required but missing. Please contact support.");
      return;
    }

    const payload: SaveDraftPayload = {
      pdfId: pdfId ? String(pdfId) : 'N/A',
      poNumber: String(poNumber),
      Invoice_Number: editableData.invoiceNumber || '',
      total: editableData.total || '',
      items: lineItemsForDraft,
    };

    try {
      toast.loading("Saving data to draft...", { toastId: 'draft-save' });

      const response = await saveDraftReviewData(payload);

      toast.dismiss('draft-save');

      if (response && 'statusCode' in response && response.statusCode >= 400) {
        toast.error(`${response.message}${response.description ? `: ${response.description}` : ''}`, {
          autoClose: 5000
        });
      } else {
        toast.success("Data saved in Draft successfully!", { autoClose: 3000 });
        if (pdfId && onAfterSave) {
          try {
            const updatedData = await getInvoiceDataFromPdfId(pdfId);
            onAfterSave(updatedData);
          } catch (e) {
          }
        }

        onClose();
      }
    } catch (error) {
      toast.dismiss('draft-save');
      toast.error("An unexpected network error occurred while saving the draft.", { autoClose: 5000 });
    }
  };

  const handleSubmit = async () => {
    if (!hasDataToPush()) {
      toast.warning("Nothing to submit.");
      return;
    }
    
    if (!pdfId) {
      toast.error("PDF ID is missing. Cannot update.");
      return;
    }

    try {
      setIsSubmitting(true);
      toast.loading("Updating invoice data...", { toastId: 'update-invoice' });

      let currentInvoice: any = {};
      try {
        const currentDataRes = await getInvoiceDataFromPdfId(pdfId);
        if ('results' in currentDataRes && currentDataRes.results.length > 0) {
          currentInvoice = currentDataRes.results[0].invoices[0] || {};
        }
      } catch (e) {
        console.warn("Failed to fetch current invoice, proceeding with available data.");
      }

      const itemsToUpdate = reviewData?.lineItems || [];
      let updatedItems: any[] = [];

      if (itemsToUpdate.length > 0) {
        updatedItems = itemsToUpdate.map((rItem) => {
          const existingItem = (currentInvoice.Items || []).find(
            (ex: any, idx: number) =>
              String(ex.LineItemNumber) === String(rItem.lineItemIndex) ||
              String(idx + 1) === String(rItem.lineItemIndex)
          );

          if (existingItem) {
            return {
              ...existingItem,
              ItemCode: rItem.itemCode || existingItem.ItemCode,
              ItemDescription: rItem.itemDescription || existingItem.ItemDescription,
              QTY: rItem.qty || existingItem.QTY,
              Unit: rItem.unit || existingItem.Unit,
              PONumber: rItem.poNumber || reviewData?.poNumber || existingItem.PONumber,
              Po_Total: stringifyOrNull(rItem.poTotal ?? existingItem.Po_Total),
              UnitPrice: rItem.unitPrice ?? existingItem.UnitPrice,
              Amount: stringifyOrEmpty(rItem.lineTotal ?? rItem.amount ?? existingItem.Amount),
              Discount: rItem.discount ?? existingItem.Discount,
              VAT: rItem.vat ?? existingItem.VAT,
              VATAmount: stringifyOrEmpty(rItem.vatAmount ?? existingItem.VATAmount),
              AmountInclVAT: rItem.amountInclVAT ?? existingItem.AmountInclVAT,
            };
          }

          return {
            LineItemNumber: String(rItem.lineItemIndex),
            ItemCode: rItem.itemCode,
            ItemDescription: rItem.itemDescription,
            QTY: rItem.qty,
            Unit: rItem.unit,
            PONumber: rItem.poNumber || reviewData?.poNumber,
            Po_Total: stringifyOrNull(rItem.poTotal),
            UnitPrice: rItem.unitPrice,
            Amount: stringifyOrEmpty(rItem.lineTotal ?? rItem.amount),
            Discount: rItem.discount,
            VAT: rItem.vat,
            VATAmount: stringifyOrEmpty(rItem.vatAmount),
            AmountInclVAT: rItem.amountInclVAT,
          };
        });
      } else {
        updatedItems = currentInvoice.Items || [];
      }

      const payload: UpdateInvoicePayload = {
        ...currentInvoice,
        PONumber: reviewData?.poNumber || currentInvoice.PONumber,
        InvoiceNumber: reviewData?.invoiceNumber || currentInvoice.InvoiceNumber,
        Invoice_Total: reviewData?.total || currentInvoice.Total,
        items: updatedItems,
      };

      if (payload && (payload as any).Total !== undefined) {
        delete (payload as any).Total;
      }
      if (payload && (payload as any).Items !== undefined) {
        delete (payload as any).Items;
      }

      const response = await updateInvoiceData(pdfId, payload);

      toast.dismiss('update-invoice');

      if (response && 'statusCode' in response && response.statusCode >= 400) {
        toast.error(`${response.message}${response.description ? `: ${response.description}` : ''}`, { autoClose: 5000 });
      } else {
        toast.success("Invoice updated successfully!", { autoClose: 3000 });
        try {
          const updatedData = await getInvoiceDataFromPdfId(pdfId);
          if (onAfterSave) {
            onAfterSave(updatedData);
          }
        } catch (e) {
        }
        setFinalSubmittedData(payload);
        setIsSubmittedStage(true);
      }
    } catch (error) {
      toast.dismiss('update-invoice');
      toast.error("An unexpected network error occurred while updating the invoice.", { autoClose: 5000 });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReview = () => {
    if (!hasDataToPush()) {
      toast.warning("Please fill in data to be pushed before reviewing.");
      return;
    }

    const finalLineItems: ReviewData['lineItems'] = allLineItems
      .map((item, idx) => {
        const itemData = editableData.items[idx];

        if (!itemData || (!itemData.itemCode && !itemData.itemDescription && !itemData.qty && !itemData.unit)) {
          return null;
        }

        return {
          lineItemIndex: item.line_item_comparison_number,
          itemCode: itemData.itemCode || '',
          itemDescription: itemData.itemDescription || '',
          qty: itemData.qty || '',
          unit: itemData.unit || '',
          poNumber: itemData.poNumber,
          poTotal: itemData.poTotal,
          unitPrice: itemData.unitPrice,
          amount: itemData.amount,
          lineTotal: itemData.lineTotal,
          discount: itemData.discount,
          vat: itemData.vat,
          vatAmount: itemData.vatAmount,
          amountInclVAT: itemData.amountInclVAT,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null);

    // ✅ Use guaranteed PO number for review
    const guaranteedPoNumber = editableData.poNumber ||
      getPoNumberValue(data.ariba_reference_data?.ariba_po_number) ||
      poComparison?.ariba_po_number ||
      poNumber ||
      '';

    const reviewPayload: ReviewData = {
      poNumber: guaranteedPoNumber,
      invoiceNumber: editableData.invoiceNumber,
      total: editableData.total,
      lineItems: finalLineItems,
    };
    setIsReviewing(true);
    setReviewData(reviewPayload);
    toast.info("Review mode activated. Please verify the data before submitting.");
  };

  const handleBackToComparison = () => {
    setIsReviewing(false);
    setReviewData(null);
    toast.info("Returned to Comparison view. You can edit the data.");
  };

  const handleUploadS4 = async () => {
    try {
      setIsUploadingS4(true);
      toast.loading("Uploading data to S4...", { toastId: 'upload-s4' });

      let invoiceObj: any = finalSubmittedData || {};

      if (pdfId) {
        try {
          const res = await getInvoiceDataFromPdfId(pdfId);
          if ('results' in res && res.results.length > 0 && res.results[0].invoices.length > 0) {
            const freshInvoice = res.results[0].invoices[0];
            const editedItems = invoiceObj.items || invoiceObj.Items || [];

            invoiceObj = {
              ...freshInvoice,
              ...invoiceObj,
              Items: (freshInvoice.Items || []).map((freshItem: any, idx: number) => {
                const editedItem = editedItems[idx] || {};
                return {
                  ...freshItem,
                  ...editedItem,
                  PONumber: freshItem.PONumber || editedItem.PONumber || '',
                  QTY: editedItem.QTY || editedItem.qty || freshItem.QTY,
                  ItemDescription: editedItem.ItemDescription || editedItem.itemDescription || freshItem.ItemDescription,
                  Unit: editedItem.Unit || editedItem.unit || freshItem.Unit,
                  ItemCode: editedItem.ItemCode || editedItem.itemCode || freshItem.ItemCode,
                };
              })
            };
          }
        } catch (err) {
          console.warn("Failed to fetch fresh invoice for S4 upload", err);
        }
      }

      const items = invoiceObj.Items || invoiceObj.items || [];

      const rawDocDate = invoiceObj.InvoiceDate || "";
      const formattedDocDate = formatDateToYYYYMMDD(rawDocDate);
      const rawDueBaseDate = invoiceObj.DueCalculationBaseDate || invoiceObj.InvoiceDate || "";
      const formattedDueBaseDate = formatDateToYYYYMMDD(rawDueBaseDate);

      const rawS4Payload: any = {
        CompanyCode: invoiceObj.CompanyCode ? String(invoiceObj.CompanyCode) : undefined,
        DocumentDate: formattedDocDate || undefined,
        DueCalculationBaseDate: formattedDueBaseDate || undefined,
        InvoiceGrossAmount: (invoiceObj.Invoice_Total || invoiceObj.Total || reviewData?.total || editableData.total)
          ? String(invoiceObj.Invoice_Total || invoiceObj.Total || reviewData?.total || editableData.total)
          : undefined,
        SupplierInvoiceIDByInvcgParty: (invoiceObj.InvoiceNumber || reviewData?.invoiceNumber || editableData.invoiceNumber)
          ? String(invoiceObj.InvoiceNumber || reviewData?.invoiceNumber || editableData.invoiceNumber)
          : undefined,
          DocumentHeaderText: items[0]?.ItemDescription || items[0]?.itemDescription || reviewData?.lineItems?.[0]?.itemDescription || '',
        TaxIsCalculatedAutomatically: true,
        DocumentCurrency: invoiceObj.DocumentCurrency ? String(invoiceObj.DocumentCurrency) : undefined,
        to_SuplrInvcItemPurOrdRef: {
          results: items.map((item: any, index: number) => {
            const rawPo = String(item.PONumber || item.PurchaseOrder || "");
            const singlePo = rawPo.includes(",") ? rawPo.split(",")[0].trim() : rawPo.trim();

            return {
              SupplierInvoiceItem: String(index + 1).padStart(2, '0'),
              PurchaseOrder: singlePo || undefined,
              PurchaseOrderItem: (item.purchase_order_item || item.PurchaseOrderItem) ? String(item.purchase_order_item || item.PurchaseOrderItem) : undefined,
              DocumentCurrency: (item.DocumentCurrency || invoiceObj.DocumentCurrency) ? String(item.DocumentCurrency || invoiceObj.DocumentCurrency) : undefined,
              TaxCode: (item.tax_code || item.TaxCode) ? String(item.tax_code || item.TaxCode) : undefined,
              QuantityInPurchaseOrderUnit: (item.QTY || item.qty || item.QuantityInPurchaseOrderUnit) ? String(item.QTY || item.qty || item.QuantityInPurchaseOrderUnit) : undefined,
              SupplierInvoiceItemAmount: (item.Amount || item.amount || item.SupplierInvoiceItemAmount) ? String(item.Amount || item.amount || item.SupplierInvoiceItemAmount) : undefined,
              PurchaseOrderQuantityUnit: (item.PurchaseOrderQuantityUnit || item.Unit || item.unit) ? String(item.PurchaseOrderQuantityUnit || item.Unit || item.unit) : undefined,
              TaxJurisdiction: (item.tax_jurisdiction || item.TaxJurisdiction) ? String(item.tax_jurisdiction || item.TaxJurisdiction) : undefined,
            };
          }),
        },
        to_SupplierInvoiceTax: {
          results: [
            {
              TaxCode: (items[0]?.tax_code || items[0]?.TaxCode) ? String(items[0].tax_code || items[0].TaxCode) : undefined,
              TaxJurisdiction: (items[0]?.tax_jurisdiction || items[0]?.TaxJurisdiction) ? String(items[0].tax_jurisdiction || items[0].TaxJurisdiction) : undefined,
              DocumentCurrency: invoiceObj.DocumentCurrency ? String(invoiceObj.DocumentCurrency) : undefined,
              TaxAmount: '0.00',
            },
          ],
        },
      };

      const s4Payload: S4InvoiceCreatePayload = cleanPayload(rawS4Payload);
      const response = await createInvoiceInS4(s4Payload, pdfId || undefined);

      toast.dismiss('upload-s4');

      const responseStatus = Number(response?.statusCode ?? response?.status ?? 200);
      const responseMessage = response?.message || response?.detail || response?.error || 'S4 upload failed.';
      const responseDescription = response?.description || response?.errors;
      const hasUploadError = responseStatus >= 400 || response?.success === false || Boolean(response?.error);

      if (hasUploadError) {
        const details = responseDescription
          ? `: ${typeof responseDescription === 'string' ? responseDescription : JSON.stringify(responseDescription)}`
          : '';
        toast.error(`${responseMessage}${details}`, { autoClose: 5000 });
      } else {
        toast.success("Uploaded to S4 successfully!", { autoClose: 3000 });
        onClose();
      }
    } catch (error: any) {
      toast.dismiss('upload-s4');
      const errorData = error?.response?.data;
      const errorMessage = errorData?.detail || errorData?.message || error?.message || "An unexpected network error occurred while uploading to S4.";
      toast.error(errorMessage, { autoClose: 5000 });
    } finally {
      setIsUploadingS4(false);
    }
  };

  const renderSubmittedData = () => (
    <div className="icm-review-section">
      <h3>Submitted Invoice Data</h3>

      <table className="icm-review-table icm-comparison-table">
        <thead>
          <tr>
            <th className="icm-field-column">Field</th>
            <th className="icm-data-column">Value</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="icm-field-name">PO Number</td>
            <td>{displayValue(reviewData?.poNumber)}</td>
          </tr>
          <tr>
            <td className="icm-field-name">Invoice Number</td>
            <td>{displayValue(reviewData?.invoiceNumber)}</td>
          </tr>
          <tr>
            <td className="icm-field-name">Invoice Date</td>
            <td>{displayValue(finalSubmittedData?.InvoiceDate)}</td>
          </tr>
          <tr>
            <td className="icm-field-name">Total</td>
            <td>{displayValue(reviewData?.total)}</td>
          </tr>
        </tbody>
      </table>

      {finalSubmittedData?.items && finalSubmittedData.items.length > 0 && (
        <>
          <h4>Line Items</h4>
          <table className="icm-review-table icm-comparison-table">
            <thead>
              <tr>
                <th>Line</th>
                <th>Item Code</th>
                <th>Description</th>
                <th>QTY</th>
                <th>Unit</th>
              </tr>
            </thead>
            <tbody>
              {finalSubmittedData.items.map((item: any, index: number) => (
                <tr key={index}>
                  <td>{item.LineItemNumber}</td>
                  <td>{displayValue(item.ItemCode)}</td>
                  <td>{displayValue(item.ItemDescription)}</td>
                  <td>{displayValue(item.QTY)}</td>
                  <td>{displayValue(item.Unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );

  const renderReviewTable = () => (
    <div className="icm-review-section">
      <h3>Reviewed Data to be Pushed</h3>

      <table className="icm-review-table icm-comparison-table">
        <thead>
          <tr>
            <th className="icm-field-column">Field</th>
            <th className="icm-data-column">Value</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="icm-field-name">PO Number</td>
            <td>{displayValue(reviewData?.poNumber)}</td>
          </tr>
          <tr>
            <td className="icm-field-name">Invoice Number</td>
            <td>{displayValue(reviewData?.invoiceNumber)}</td>
          </tr>
          <tr>
            <td className="icm-field-name">Total</td>
            <td>{displayValue(reviewData?.total)}</td>
          </tr>
        </tbody>
      </table>

      {reviewData?.lineItems && reviewData.lineItems.length > 0 ? (
        <>
          <h4>Line Items</h4>
          <table className="icm-review-table icm-comparison-table">
            <thead>
              <tr>
                <th>Line</th>
                <th>Item Code</th>
                <th>Description</th>
                <th>QTY</th>
                <th>Unit</th>
              </tr>
            </thead>
            <tbody>
              {reviewData.lineItems.map((item, index) => (
                <tr key={index}>
                  <td>{item.lineItemIndex}</td>
                  <td>{displayValue(item.itemCode)}</td>
                  <td>{displayValue(item.itemDescription)}</td>
                  <td>{displayValue(item.qty)}</td>
                  <td>{displayValue(item.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="icm-no-data-message">
          No data was entered in the "Data to be Pushed" column.
          Please go back and fill the fields.
        </p>
      )}
    </div>
  );

  return (
    <div className="icm-modal-overlay">
      <div className="icm-modal-container">
        <div className="icm-modal-header">
          <h2 className="icm-modal-title">
            {isSubmittedStage ? "Submitted Invoice Data" : isReviewing ? "Review Data" : "Data Comparison"}
          </h2>
          <button onClick={onClose} className="icm-close-button" aria-label="Close modal">
            <FaTimes />
          </button>
        </div>

        {!isReviewing && !isSubmittedStage && (
          <div className="icm-action-buttons">
            <div className="icm-dropdown-wrapper">
              <select
                className="icm-dropdown"
                value={filterStatus}
                onChange={handleFilterChange}
                aria-label="Filter status"
              >
                <option value="all">Show All</option>
                <option value="matched">Show Matched</option>
                <option value="mismatched">Show Mismatched</option>
              </select>
            </div>

            <div className="icm-buttons-wrapper">
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
              <button className="icm-edit-button" aria-label="Edit mode">
                <FaEdit className="icm-edit-icon" />
                Edit
              </button>
            </div>
          </div>
        )}

        <div className="icm-modal-content">
          {isSubmittedStage ? (
            renderSubmittedData()
          ) : isReviewing ? (
            renderReviewTable()
          ) : (
            <>
              <div className="icm-comparison-section">
                <table className="icm-comparison-table">
                  <thead>
                    <tr>
                      <th className="icm-field-column">Field</th>
                      <th className="icm-data-column">PO Data</th>
                      {showRctData && <th className="icm-data-column">RCT Data</th>}
                      <th className="icm-data-column">Invoice Extracted Data</th>
                      <th className="icm-status-column">Status</th>
                      <th className="icm-comments-column">Comments</th>
                      <th className="icm-data-column">Data to be Pushed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {poComparison && shouldShowHeaderRow(poComparison.status) && (
                      <tr>
                        <td className="icm-field-name">PO Number</td>
                        <td>{displayValue(data.ariba_reference_data?.ariba_po_number)}</td>
                        {showRctData && <td>{displayValue(poComparison.receipt_po_number)}</td>}
                        <td>{displayValue(poComparison.invoice_po_number)}</td>
                        <td>{getStatusBadge(poComparison.status)}</td>
                        <td>{displayValue(poComparison.error_comment)}</td>
                        <td>
                          <input
                            type="text"
                            value={editableData.poNumber || getPoNumberValue(data?.ariba_reference_data?.ariba_po_number) || poComparison?.ariba_po_number || ''}
                            onChange={(e) => handleHeaderFieldChange('poNumber', e.target.value)}
                            className="icm-editable-input"
                            placeholder=""
                            aria-label="PO Number input"
                            disabled={true}
                            style={{ backgroundColor: '#f5f5f5', cursor: 'not-allowed' }}
                          />
                        </td>
                      </tr>
                    )}

                    {shouldShowHeaderRow("matched") && (
                      <tr>
                        <td className="icm-field-name">Invoice Number</td>
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
                            className="icm-editable-input"
                            placeholder=""
                            aria-label="Invoice Number input"
                          />
                        </td>
                      </tr>
                    )}

                    {totalComparison && shouldShowHeaderRow(totalComparison.status) && (
                      <tr>
                        <td className="icm-field-name">Total</td>
                        <td>{displayValue(data.ariba_reference_data?.ariba_total)}</td>
                        {showRctData && <td>{displayValue(totalComparison.receipt_total)}</td>}
                        <td>{displayValue(totalComparison.invoice_total)}</td>
                        <td>{getStatusBadge(totalComparison.status)}</td>
                        <td>{displayValue(totalComparison.error_comment)}</td>
                        <td>
                          <input
                            type="text"
                            value={editableData.total}
                            onChange={(e) => handleHeaderFieldChange('total', e.target.value)}
                            className="icm-editable-input"
                            placeholder=""
                            aria-label="Total input"
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {(() => {
                const shouldShowRow = (status: string) => {
                  if (filterStatus === "all") return true;
                  if (filterStatus === "matched") return status === "matched";
                  if (filterStatus === "mismatched") return status !== "matched";
                  return false;
                };

                let hasAnyVisibleContent = false;
                const itemsContent = filteredLineItems?.map((item, idx) => {
                  const itemCodeStatus = item?.comparison?.ItemCode_vs_line_item || "unmatched";
                  const itemDescStatus = item?.comparison?.ItemDescription_vs_description || "unmatched";
                  const qtyStatus = item?.comparison?.QTY_vs_quantity || "unmatched";
                  const unitStatus = item?.comparison?.Unit_vs_unit_of_measure || "unmatched";
                  const hasVisibleRows =
                    shouldShowRow(itemCodeStatus) ||
                    shouldShowRow(itemDescStatus) ||
                    shouldShowRow(qtyStatus) ||
                    shouldShowRow(unitStatus);
                  if (!hasVisibleRows) return null;

                  hasAnyVisibleContent = true;

                  return (
                    <div key={idx} className="icm-item-section">
                      <div className="icm-item-header">
                        <h3>Item {item.line_item_comparison_number}</h3>
                      </div>

                      <table className="icm-comparison-table">
                        <thead>
                          <tr>
                            <th className="icm-field-column">Field</th>
                            <th className="icm-data-column">PO Data</th>
                            {showRctData && <th className="icm-data-column">RCT Data</th>}
                            <th className="icm-data-column">Invoice Extracted Data</th>
                            <th className="icm-status-column">Status</th>
                            <th className="icm-comments-column">Comments</th>
                            <th className="icm-data-column">Data to be Pushed</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(() => {
                            const refLineItem =
                              item.ariba_item?.description || item.ariba_item?.line_item
                                ? item.ariba_item
                                : data.ariba_reference_data?.ariba_line_items?.find(
                                    (ri) =>
                                      String(ri.line_item) === String(item.line_item_comparison_number) ||
                                      (item.receipt_item?.line_item && String(ri.line_item) === String(item.receipt_item.line_item))
                                  ) ||
                                  undefined;

                            return (
                              <>
                                <tr>
                                  <td className="icm-field-name">PO Number</td>
                                  <td>{displayValue(refLineItem?.po_number)}</td>
                                  {showRctData && <td>{displayValue(item.receipt_item?.po_number)}</td>}
                                  <td>{displayValue(item.invoice_item?.po_number)}</td>
                                  <td>—</td>
                                  <td>—</td>
                                  <td>
                                    <input
                                      type="text"
                                      value={editableData.items[idx]?.poNumber || refLineItem?.po_number || editableData.poNumber || ''}
                                      onChange={(e) => handleItemFieldChange(idx, 'poNumber', e.target.value)}
                                      className="icm-editable-input"
                                      aria-label={`Item ${item.line_item_comparison_number} PO number input`}
                                    />
                                  </td>
                                </tr>
                                {shouldShowRow(itemCodeStatus) && (
                                  <tr>
                                    <td className="icm-field-name">Line item</td>
                                    <td>{displayValue(refLineItem?.line_item)}</td>
                                    {showRctData && <td>{displayValue(item.receipt_item?.line_item)}</td>}
                                    <td>{displayValue(item.invoice_item?.ItemCode)}</td>
                                    <td>{getStatusBadge(itemCodeStatus)}</td>
                                    <td>
                                      {displayValue(
                                        item.comparison?.error_comments?.ItemCode_vs_line_item
                                      )}
                                    </td>
                                    <td>
                                      <input
                                        type="text"
                                        value={editableData.items[idx]?.itemCode || ''}
                                        onChange={(e) => handleItemFieldChange(idx, 'itemCode', e.target.value)}
                                        className="icm-editable-input"
                                        aria-label={`Item ${item.line_item_comparison_number} code input`}
                                      />
                                    </td>
                                  </tr>
                                )}
                                {shouldShowRow(itemDescStatus) && (
                                  <tr>
                                    <td className="icm-field-name">Item Description</td>
                                    <td>{displayValue(refLineItem?.description)}</td>
                                    {showRctData && <td>{displayValue(item.receipt_item?.description)}</td>}
                                    <td>{displayValue(item.invoice_item?.ItemDescription)}</td>
                                    <td>{getStatusBadge(itemDescStatus)}</td>
                                    <td>
                                      {displayValue(
                                        item.comparison?.error_comments?.ItemDescription_vs_description
                                      )}
                                    </td>
                                    <td>
                                      <input
                                        type="text"
                                        value={editableData.items[idx]?.itemDescription || ''}
                                        onChange={(e) => handleItemFieldChange(idx, 'itemDescription', e.target.value)}
                                        className="icm-editable-input"
                                        placeholder=""
                                        aria-label={`Item ${item.line_item_comparison_number} description input`}
                                      />
                                    </td>
                                  </tr>
                                )}
                                {shouldShowRow(qtyStatus) && (
                                  <tr>
                                    <td className="icm-field-name">QTY</td>
                                    <td>{displayValue(refLineItem?.quantity)}</td>
                                    {showRctData && <td>{displayValue(item.receipt_item?.quantity)}</td>}
                                    <td>{displayValue(item.invoice_item?.QTY)}</td>
                                    <td>{getStatusBadge(qtyStatus)}</td>
                                    <td>
                                      {displayValue(
                                        item.comparison?.error_comments?.QTY_vs_quantity
                                      )}
                                    </td>
                                    <td>
                                      <input
                                        type="text"
                                        value={editableData.items[idx]?.qty || ''}
                                        onChange={(e) => handleItemFieldChange(idx, 'qty', e.target.value)}
                                        className="icm-editable-input"
                                        placeholder=""
                                        aria-label={`Item ${item.line_item_comparison_number} quantity input`}
                                      />
                                    </td>
                                  </tr>
                                )}
                                {shouldShowRow(unitStatus) && (
                                  <tr>
                                    <td className="icm-field-name">Unit</td>
                                    <td>{displayValue(refLineItem?.unit_of_measure)}</td>
                                    {showRctData && <td>{displayValue(item.receipt_item?.unit_of_measure)}</td>}
                                    <td>{displayValue(item.invoice_item?.Unit)}</td>
                                    <td>{getStatusBadge(unitStatus)}</td>
                                    <td>
                                      {displayValue(
                                        item.comparison?.error_comments?.Unit_vs_unit_of_measure
                                      )}
                                    </td>
                                    <td>
                                      <input
                                        type="text"
                                        value={editableData.items[idx]?.unit || ''}
                                        onChange={(e) => handleItemFieldChange(idx, 'unit', e.target.value)}
                                        className="icm-editable-input"
                                        placeholder=""
                                        aria-label={`Item ${item.line_item_comparison_number} unit input`}
                                      />
                                    </td>
                                    </tr>
                                  )}
                                  <tr>
                                    <td className="icm-field-name">Line Total</td>
                                    <td>{displayValue(refLineItem?.line_total)}</td>
                                    {showRctData && <td>{displayValue(item.receipt_item?.line_total)}</td>}
                                    <td>{displayValue(item.invoice_item?.line_total ?? (item.invoice_item as any)?.Amount)}</td>
                                    <td>—</td>
                                    <td>—</td>
                                    <td>
                                      <input
                                        type="text"
                                        value={editableData.items[idx]?.lineTotal ?? ''}
                                        onChange={(e) => handleItemFieldChange(idx, 'lineTotal', e.target.value)}
                                        className="icm-editable-input"
                                        aria-label={`Item ${item.line_item_comparison_number} line total input`}
                                      />
                                    </td>
                                  </tr>
                                </>
                            );
                          })()}
                        </tbody>
                      </table>
                    </div>
                  );
                });

                if (!hasAnyVisibleContent && allLineItems.length > 0) {
                  return (
                    <p className="icm-no-data-message">
                      No line item fields found for the <strong>{filterStatus}</strong> filter status.
                    </p>
                  );
                }

                return itemsContent;
              })()}
            </>
          )}
        </div>

        <div className="icm-modal-footer">
          {isSubmittedStage ? (
            <button
              className="icm-footer-button icm-footer-button-primary"
              onClick={handleUploadS4}
              disabled={isUploadingS4}
            >
              {isUploadingS4 ? 'Uploading to S4...' : 'Upload S4'}
            </button>
          ) : isReviewing ? (
            <>
              <button
                className="icm-footer-button icm-footer-button-secondary"
                onClick={handleBackToComparison}
                disabled={isSubmitting}
              >
                Back to Edit
              </button>
              <button
                className="icm-footer-button icm-footer-button-primary"
                onClick={handleSubmit}
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Submitting...' : 'Submit'}
              </button>
            </>
          ) : (
            <>
              <button
                className="icm-footer-button icm-footer-button-secondary"
                onClick={handleSaveDraft}
              >
                Save as Draft
              </button>
              <button
                className={`icm-footer-button icm-footer-button-primary ${(!isEdited || !hasDataToPush()) ? 'icm-disabled' : ''}`}
                onClick={handleReview}
                disabled={!isEdited || !hasDataToPush()}
              >
                Review
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default InvoiceComparisonModal;