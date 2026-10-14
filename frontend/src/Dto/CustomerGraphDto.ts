export interface CustomerGraphRequestDto {
  page?: number;
  page_size?: number;
  Field?: string;
  po_number?: string;
  supplier?: string;
  company_code?: string;
  status?: string;
  product_type_code?: string | number;
  invoice_start_date?: string;
  invoice_end_date?: string;
  process_start_date?: string;
  process_end_date?: string;
}

export interface CustomerGraphRecord {
  id: number;
  user_id: number;
  filename: string;
  pdf_id: string;
  SupplierName: string;
  CUSTOMER_INVOICE_ID: string;
  status: string;
  invoice_date: string;
  processed_date: string;
  source: string;
  comments: string;
  folder_name: string;
  brand: string;
  PONumber: string;
  product_type_code?: string | number | null;
  DocumentCurrency?: string | null;
  Total: number;
  InvoiceNumber: string;
  PR_Supplier: string;
  Company_Code: string;
  Ariba_comments: string;
}

export function getProductTypeShortLabel(value: unknown): string {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === '1' || normalized === 'material') return 'M';
  if (normalized === '2' || normalized === 'service') return 'S';
  return '-';
}

export interface CustomerGraphResponseDto {
  Field: string;
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
  records: CustomerGraphRecord[];
  status_counts: Record<string, number>;
  filters_applied: Record<string, any>;
}

// ==========================================
// PO RECEIPTS SUMMARY (RC DETAILS) ENDPOINT
// ==========================================

export interface PoReceiptLineItemDto {
  line_item: string;
  description: string;
  quantity: number;
  unit_of_measure: string;
  amount_accepted: number;
  gross_amount_accepted: number;
}

export interface PoReceiptDto {
  receipt_id: string;
  total_line_items: number;
  total_gross_amount: number;
  items: PoReceiptLineItemDto[];
}

export interface PoReceiptsSummaryResponseDto {
  po_number: string;
  total_rc_count: number;
  receipts: PoReceiptDto[];
}

// ==========================================
// ARIBA COMPARISON DTOs
// ==========================================

export interface PoNumberComparisonDTO {
  invoice_po_number: string;
  ariba_po_number: string;
  receipt_po_number: string;
  status: string;
  error_comment: string | null;
}

export interface TotalComparisonDTO {
  invoice_total: string | number;
  ariba_total: string | number;
  receipt_total: string | number;
  status: string;
  error_comment: string | null;
}

export interface LineItemComparisonDTO {
  line_item_comparison_number: number;
  invoice_item: {
    line_item: string | null;
    line_total?: string | number | null;
    ItemCode: string | null;
    ItemDescription: string | null;
    QTY: string | null;
    Unit: string | null;
  };
  ariba_item: {
    line_item: string | null;
    line_total?: string | number | null;
    description: string | null;
    quantity: number | null;
    unit_of_measure: string | null;
  };
  receipt_item: {
    line_item: string | null;
    line_total?: string | number | null;
    description: string | null;
    quantity: number | null;
    unit_of_measure: string | null;
  };
  comparison: {
    ItemCode_vs_line_item: string;
    ItemDescription_vs_description: string;
    QTY_vs_quantity: string;
    Unit_vs_unit_of_measure: string;
    error_comments: Record<string, string>;
  };
}

export interface AribaReferenceLineItemDTO {
  line_item: string;
  description: string;
  quantity: number;
  unit_of_measure: string;
  unit_price: number;
  price: number;
  tax_amount: number;
  line_total: number;
  tax_percentage: number;
  product_type_code?: string;
  po_balance_amount?: any;
  po_number?: string;
}

export interface AribaReferenceDataDTO {
  ariba_po_number: string;
  ariba_total: number;
  ariba_line_items: AribaReferenceLineItemDTO[];
}

export interface AribaResponseDTO {
  po_number_comparison?: PoNumberComparisonDTO;
  total_comparison?: TotalComparisonDTO;
  Comparision_of_lineItems?: LineItemComparisonDTO[];
  ariba_reference_data?: AribaReferenceDataDTO;
  poNumber?: string;
  comparison?: any[];
  message?: string;
  po_type?: string;
  product_type_code?: string;
  summary?: any;
  service_po_allocation?: any[];
  invoice_data?: any;
  receipt_data?: any;
  pdf_id?: string;
  raw_response?: any;
}

export function isServicePoOrTypeCode2(data: any): boolean {
  if (!data) return false;

  // Check top-level po_type
  if (data.po_type && String(data.po_type).trim().toLowerCase() === 'service') return true;
  if (data.raw_response?.po_type && String(data.raw_response.po_type).trim().toLowerCase() === 'service') return true;

  // Check top-level product_type_code
  if (data.product_type_code && String(data.product_type_code).trim() === '2') return true;
  if (data.raw_response?.product_type_code && String(data.raw_response.product_type_code).trim() === '2') return true;

  // Check ariba_reference_data.ariba_line_items
  const aribaItems =
    data.ariba_reference_data?.ariba_line_items ||
    data.raw_response?.ariba_reference_data?.ariba_line_items;
  if (Array.isArray(aribaItems) && aribaItems.length > 0) {
    if (aribaItems.some((item: any) => item && String(item.product_type_code).trim() === '2')) {
      return true;
    }
  }

  // Check Comparision_of_lineItems
  const compItems = data.Comparision_of_lineItems || data.raw_response?.Comparision_of_lineItems;
  if (Array.isArray(compItems) && compItems.length > 0) {
    if (
      compItems.some(
        (item: any) =>
          item &&
          (String(item.product_type_code).trim() === '2' ||
            String(item.ariba_item?.product_type_code).trim() === '2')
      )
    ) {
      return true;
    }
  }

  // Check service_po_allocation
  if (Array.isArray(data.service_po_allocation) && data.service_po_allocation.length > 0) {
    return true;
  }
  if (Array.isArray(data.raw_response?.service_po_allocation) && data.raw_response.service_po_allocation.length > 0) {
    return true;
  }

  return false;
}

export function formatPoNumbers(val: any): string {
  if (val === null || val === undefined) return '';
  const str = String(val).trim();
  if (!str) return '';

  if (str.includes(',')) {
    return str
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .join(', ');
  }

  const matches = str.match(/\d{10}/g);
  if (matches && matches.length > 1 && matches.join('') === str.replace(/\s+/g, '')) {
    return matches.join(', ');
  }

  return str;
}

export function transformAribaResponse(response: any): AribaResponseDTO {
  if (!response) return {};

  const refPoNumber =
    response.ariba_reference_data?.ariba_po_number ||
    response.po_numbers;
  const refTotal =
    response.ariba_reference_data?.ariba_total ??
    response.total_comparison?.ariba_total ??
    '';

  const aribaLineItems = Array.isArray(response.ariba_reference_data?.ariba_line_items)
    ? response.ariba_reference_data.ariba_line_items
    : [];
  const usedAribaIndices = new Set<number>();

  const poCompAriba = response.po_number_comparison?.ariba_po_number || refPoNumber;
  const totalCompAriba = response.total_comparison?.ariba_total ?? refTotal;

  const isTotalMatched = (): boolean => {
    if (response.total_comparison?.status) {
      return response.total_comparison.status === 'matched';
    }
    if (response.summary?.total_match !== undefined) {
      return Boolean(response.summary.total_match);
    }
    const invTotal = Number(response.invoice_data?.invoice_total ?? response.total_comparison?.invoice_total);
    const arTotal = Number(refTotal);
    if (!isNaN(invTotal) && !isNaN(arTotal)) {
      return Math.abs(invTotal - arTotal) <= 0.01;
    }
    return false;
  };

  return {
    po_number_comparison: response.po_number_comparison
      ? {
          invoice_po_number: formatPoNumbers(response.po_number_comparison.invoice_po_number),
          ariba_po_number: formatPoNumbers(poCompAriba),
          receipt_po_number: formatPoNumbers(response.po_number_comparison.receipt_po_number),
          status: response.po_number_comparison.status || 'mismatched',
          error_comment: response.po_number_comparison.error_comment || null,
        }
      : refPoNumber
      ? {
          invoice_po_number: formatPoNumbers(response.po_numbers || response.invoice_data?.invoice_po_number),
          ariba_po_number: formatPoNumbers(refPoNumber),
          receipt_po_number: formatPoNumbers(response.receipt_data?.receipt_po_number),
          status: response.summary?.po_match !== undefined ? (response.summary.po_match ? 'matched' : 'mismatched') : 'matched',
          error_comment: null,
        }
      : undefined,
    total_comparison: response.total_comparison
      ? {
          invoice_total: response.total_comparison.invoice_total ?? response.invoice_data?.invoice_total ?? '',
          ariba_total: totalCompAriba,
          receipt_total: response.total_comparison.receipt_total ?? response.receipt_data?.receipt_total ?? '',
          status: response.total_comparison.status || (isTotalMatched() ? 'matched' : 'mismatched'),
          error_comment: response.total_comparison.error_comment || null,
        }
      : refTotal !== ''
      ? {
          invoice_total: response.invoice_data?.invoice_total ?? '',
          ariba_total: refTotal,
          receipt_total: response.receipt_data?.receipt_total ?? '',
          status: isTotalMatched() ? 'matched' : 'mismatched',
          error_comment: null,
        }
      : undefined,
    Comparision_of_lineItems: Array.isArray(response.Comparision_of_lineItems) && response.Comparision_of_lineItems.length > 0
        ? response.Comparision_of_lineItems.map((item: any, idx: number) => {
          const isService = isServicePoOrTypeCode2(response);

          let matchedAriba: any =
            item.ariba_item && (item.ariba_item.description || item.ariba_item.line_item)
              ? item.ariba_item
              : undefined;

          if (!matchedAriba) {
            let matchedAribaIndex = aribaLineItems.findIndex((a: any, aIdx: number) =>
              !usedAribaIndices.has(aIdx) &&
              ((a.description && item.invoice_item?.ItemDescription &&
                (a.description.toLowerCase().includes(item.invoice_item.ItemDescription.toLowerCase().slice(0, 4)) ||
                 item.invoice_item.ItemDescription.toLowerCase().includes(a.description.toLowerCase().slice(0, 4)))) ||
               (a.description && item.receipt_item?.description &&
                (a.description.toLowerCase().includes(item.receipt_item.description.toLowerCase().slice(0, 4)) ||
                 item.receipt_item.description.toLowerCase().includes(a.description.toLowerCase().slice(0, 4)))))
            );

            if (matchedAribaIndex === -1) {
              if (isService) {
                if (aribaLineItems[idx] && !usedAribaIndices.has(idx)) {
                  matchedAribaIndex = idx;
                } else if (aribaLineItems.length === 1 && !usedAribaIndices.has(0)) {
                  matchedAribaIndex = 0;
                }
              } else if (idx < aribaLineItems.length && !usedAribaIndices.has(idx)) {
                matchedAribaIndex = idx;
              }
            }

            if (matchedAribaIndex !== -1) {
              matchedAriba = aribaLineItems[matchedAribaIndex];
              usedAribaIndices.add(matchedAribaIndex);
            }
          }

          return {
            line_item_comparison_number: item.line_item_comparison_number,
            invoice_item: {
              line_item: item.invoice_item?.line_item ?? null,
              line_total: item.invoice_item?.line_total ?? item.invoice_item?.Amount ?? null,
              ItemCode: item.invoice_item?.ItemCode ?? item.invoice_item?.line_item ?? null,
              ItemDescription: item.invoice_item?.ItemDescription ?? null,
              QTY: item.invoice_item?.QTY ?? null,
              Unit: item.invoice_item?.Unit ?? null,
            },
            ariba_item: {
              line_item: item.ariba_item?.line_item ?? matchedAriba?.line_item ?? null,
              line_total: item.ariba_item?.line_total ?? matchedAriba?.line_total ?? null,
              description: item.ariba_item?.description ?? matchedAriba?.description ?? null,
              quantity: item.ariba_item?.quantity ?? matchedAriba?.quantity ?? null,
              unit_of_measure: item.ariba_item?.unit_of_measure ?? matchedAriba?.unit_of_measure ?? null,
            },
            receipt_item: {
              line_item: item.receipt_item?.line_item ?? null,
              line_total: item.receipt_item?.line_total ?? null,
              description: item.receipt_item?.description ?? null,
              quantity: item.receipt_item?.quantity ?? null,
              unit_of_measure: item.receipt_item?.unit_of_measure ?? null,
            },
            comparison: {
              ItemCode_vs_line_item: item.comparison?.line_item_vs_line_item || item.comparison?.ItemCode_vs_line_item || 'mismatched',
              ItemDescription_vs_description: item.comparison?.ItemDescription_vs_description || 'mismatched',
              QTY_vs_quantity: item.comparison?.QTY_vs_quantity || 'mismatched',
              Unit_vs_unit_of_measure: item.comparison?.Unit_vs_unit_of_measure || 'mismatched',
              error_comments: item.comparison?.error_comments || {},
            },
          };
        })
      : Array.isArray(response.service_po_allocation) &&
        response.service_po_allocation.length > 0 &&
        !(Array.isArray(response.invoice_data?.invoice_line_items) && response.invoice_data.invoice_line_items.length > 0)
      ? response.service_po_allocation.map((alloc: any, idx: number) => {
          const matchedAriba =
            aribaLineItems.find((item: any) => String(item.line_item) === String(alloc.line_item_number)) ||
            aribaLineItems[idx] ||
            aribaLineItems[0];
          const invoiceItems = Array.isArray(response.invoice_data?.invoice_line_items)
            ? response.invoice_data.invoice_line_items
            : [];
          const invoiceItem = invoiceItems.find(
            (item: any) => String(item.line_item) === String(alloc.line_item_number)
          ) || invoiceItems[idx];
          const lineNumber = String(alloc.line_item_number ?? invoiceItem?.line_item ?? idx + 1);

          return {
            line_item_comparison_number: Number(lineNumber) || idx + 1,
            invoice_item: {
              line_item: invoiceItem?.line_item ?? lineNumber,
              line_total: invoiceItem?.Amount ?? invoiceItem?.line_total ?? alloc.amount_allocated ?? null,
              ItemCode: invoiceItem?.ItemCode ?? invoiceItem?.line_item ?? lineNumber,
              ItemDescription: invoiceItem?.ItemDescription ?? null,
              QTY: invoiceItem?.QTY ?? null,
              Unit: invoiceItem?.Unit ?? null,
            },
            ariba_item: {
              line_item: matchedAriba?.line_item ?? lineNumber,
              line_total: matchedAriba?.line_total ?? alloc.amount_allocated ?? null,
              description: matchedAriba?.description ?? alloc.description ?? null,
              quantity: matchedAriba?.quantity ?? invoiceItem?.QTY ?? null,
              unit_of_measure: matchedAriba?.unit_of_measure ?? null,
            },
            receipt_item: {
              line_item: null,
              line_total: null,
              description: null,
              quantity: null,
              unit_of_measure: null,
            },
            comparison: {
              ItemCode_vs_line_item: response.summary?.total_match === true ? 'matched' : 'mismatched',
              ItemDescription_vs_description: response.summary?.total_match === true ? 'matched' : 'mismatched',
              QTY_vs_quantity: response.summary?.total_match === true ? 'matched' : 'mismatched',
              Unit_vs_unit_of_measure: response.summary?.total_match === true ? 'matched' : 'mismatched',
              error_comments: {},
            },
          };
        })
      : Array.isArray(response.invoice_data?.invoice_line_items) && response.invoice_data.invoice_line_items.length > 0
      ? response.invoice_data.invoice_line_items.map((invItem: any, idx: number) => {
          const isPassed =
            response.comparison_status === 'success' ||
            response.summary?.total_match === true ||
            response.summary?.total_mismatches === 0;
          const matchedAllocation = Array.isArray(response.service_po_allocation)
            ? response.service_po_allocation.find(
                (allocation: any) => String(allocation.line_item_number) === String(invItem.line_item)
              )
            : undefined;
          const matchedAriba = aribaLineItems.find(
            (item: any) => String(item.line_item) === String(matchedAllocation?.line_item_number ?? invItem.line_item)
          );

          return {
            line_item_comparison_number: Number(invItem.line_item) || (idx + 1),
            invoice_item: {
              line_item: invItem.line_item !== null && invItem.line_item !== undefined ? String(invItem.line_item) : String(idx + 1),
              line_total: invItem.Amount ?? invItem.line_total ?? matchedAllocation?.amount_allocated ?? null,
              ItemCode: invItem.ItemCode ?? invItem.line_item ?? String(idx + 1),
              ItemDescription: invItem.ItemDescription ?? null,
              QTY: invItem.QTY !== null && invItem.QTY !== undefined ? String(invItem.QTY) : null,
              Unit: invItem.Unit ?? null,
            },
            ariba_item: {
              line_item: matchedAriba?.line_item ?? null,
              line_total: matchedAriba?.line_total ?? matchedAllocation?.amount_allocated ?? null,
              description: matchedAriba?.description ?? null,
              quantity: matchedAriba?.quantity ?? null,
              unit_of_measure: matchedAriba?.unit_of_measure ?? null,
            },
            receipt_item: {
              line_item: null,
              line_total: null,
              description: null,
              quantity: null,
              unit_of_measure: null,
            },
            comparison: {
              ItemCode_vs_line_item: isPassed && matchedAriba ? 'matched' : (String(invItem.ItemCode || invItem.line_item) === String(matchedAriba?.line_item) ? 'matched' : 'mismatched'),
              ItemDescription_vs_description: isPassed && matchedAriba ? 'matched' : 'mismatched',
              QTY_vs_quantity: isPassed && matchedAriba ? 'matched' : (Number(invItem.QTY) === Number(matchedAriba?.quantity) ? 'matched' : 'mismatched'),
              Unit_vs_unit_of_measure: isPassed && matchedAriba ? 'matched' : 'mismatched',
              error_comments: {},
            },
          };
        })
      : [],
    ariba_reference_data: response.ariba_reference_data
      ? {
          ariba_po_number: response.ariba_reference_data.ariba_po_number,
          ariba_total: response.ariba_reference_data.ariba_total,
          ariba_line_items: Array.isArray(response.ariba_reference_data.ariba_line_items)
            ? response.ariba_reference_data.ariba_line_items.map((item: any) => ({
                line_item: item.line_item,
                description: item.description,
                quantity: item.quantity,
                unit_of_measure: item.unit_of_measure,
                unit_price: item.unit_price,
                price: item.price,
                tax_amount: item.tax_amount,
                line_total: item.line_total,
                tax_percentage: item.tax_percentage,
                product_type_code: item.product_type_code !== undefined && item.product_type_code !== null ? String(item.product_type_code) : undefined,
                po_balance_amount: item.po_balance_amount,
                po_number: item.po_number,
              }))
            : [],
        }
      : undefined,
    poNumber: response.poNumber ?? undefined,
    message: response.message ?? undefined,
    po_type: response.po_type,
    product_type_code: response.product_type_code || response.ariba_reference_data?.ariba_line_items?.[0]?.product_type_code,
    summary: response.summary,
    service_po_allocation: response.service_po_allocation,
    invoice_data: response.invoice_data,
    receipt_data: response.receipt_data,
    pdf_id: response.pdf_id,
    raw_response: response,
  };
}
