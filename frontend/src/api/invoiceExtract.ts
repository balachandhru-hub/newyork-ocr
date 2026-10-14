import { toast } from 'react-toastify';
import type { ErrorResponseDto } from '../Dto/ErrorResponseDto';
import type { ApiResponse, AribaResponseDTO, PurchaseOrderDTO } from '../Dto/InvoiceExtractionDto';
import { isApiResponse, transformAribaResponse, transformPurchaseOrder } from '../Dto/InvoiceExtractionDto';
import axios from './axiosInstance';

export interface ProcessedInvoiceItem {
  LineItemNumber: number;
  ItemCode: string;
  ItemDescription: string;
  QTY: string;
  Unit: string | null;
  UnitPrice: string;
  Amount: string;
  Discount: string | null;
  VAT: string;
  VATAmount: string;
  AmountInclVAT: string | null;
}

export interface ProcessedInvoiceData {
  supplier_id?: string;
  InvoiceNumber: string;
  SupplierName: string;
  CustomerName: string;
  PONumber: string;
  InvoiceDate: string;
  SupplierTRN: string | null;
  CustomerTRN: string | null;
  Email: string;
  Total: string;
  Items: ProcessedInvoiceItem[];
  user_id: string;
  pdf_id: number;
  source_file: string;
  invoice_id: number;
  is_trusted: boolean;
}

export interface ProcessedResult {
  filename: string;
  user_id: string;
  input_id: number;
  pdf_id: number;
  invoices: ProcessedInvoiceData[];
  status: string;
}

export interface ProcessLocalPdfsResponse {
  message: string;
  results: ProcessedResult[];
}

interface lineItemData{
  Line_item: string;
  Item_description: string;
  QTY: string;
  Unit: string;
}

export interface SaveDraftPayload {
  pdfId: string;
  poNumber: string;
  Invoice_Number: string;
  total: string;
  items: lineItemData[];
}

export interface UpdateInvoicePayload {
  user_id?: string;
  source_file?: string;
  invoice_id?: string | number;
  InvoiceNumber?: string;
  PONumber?: string;
  SupplierName?: string;
  CustomerName?: string;
  InvoiceDate?: string;
  SupplierTRN?: string;
  CustomerTRN?: string;
  Email?: string;
  Invoice_Total?: string | number;
  is_trusted?: string | boolean;
  BankAccountName?: string;
  BankName?: string;
  BankBranch?: string;
  AccountNumber?: string;
  IBAN?: string;
  SwiftCode?: string;
  BranchCode?: string;
  Tax_invoice?: string;
  tax_invoice?: string;
  Stamp?: string;
  Customer_address?: string;
  Supplier_address?: string;
  items?: {
    LineItemNumber?: string | number;
    ItemCode?: string;
    ItemDescription?: string;
    QTY?: string | number;
    Unit?: string;
    UnitPrice?: string | number;
    Amount?: string | number;
    Discount?: string | number;
    VAT?: string | number;
    VATAmount?: string | number;
    AmountInclVAT?: string | number;
    PONumber?: string;
    Po_Total?: string | number;
  }[];
}

export const getInvoiceData = async (
  payload: FormData
): Promise<any> => {
  try {
    const response = await axios.post<any>(
      "/api/v1/newyork-ocr/extract-invoice",
      payload,
      {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      }
    );
    return response.data;
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error",
        description:
          err.detail ||
          err.description ||
          JSON.stringify(err.errors) ||
          "No details provided",
      };
    }
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Submission failed.",
    };
  }
};

export const getInvoiceDataFromPdfId = async (
  pdf_id: number | string
): Promise<ApiResponse | ErrorResponseDto> => {
  try {
    const response = await axios.get<ApiResponse>(
      `/api/v1/newyork-ocr/invoice/${pdf_id}`,
    );

    if (isApiResponse(response.data)) {
      const modifiedData: ApiResponse = {
        ...response.data,
        results: response.data.results.map(result => ({
          ...result,
          invoices: result.invoices.map(inv => ({
            ...inv,
            is_Submit: false, // add the extra flag
          })),
        })),
      };
      return modifiedData;
    } else {
      const legacyData = response.data as any;
      return {
        results: [{
          filename: "unknown",
          user_id: "unknown",
          input_id: 0,
          pdf_id: 0,
          invoices: (Array.isArray(legacyData) ? legacyData : [legacyData]).map((inv: any) => ({
            ...inv,
            is_Submit: false, // add the extra flag
          })),
          status: "success"
        }]
      };
    }
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error",
        description:
          err.detail ||
          err.description ||
          JSON.stringify(err.errors) ||
          "No details provided",
      };
    }
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Submission failed.",
    };
  }
};

export const saveDraftReviewData = async (
  payload: SaveDraftPayload
): Promise<any | ErrorResponseDto> => {
  try {
    const response = await axios.post<any>(
      "/api/v1/newyork-ocr/save-draft",
      payload,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    );
    
    return response.data;
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error during draft save",
        description:
          err.detail ||
          err.description ||
          (err.errors ? JSON.stringify(err.errors) : "") ||
          "No details provided",
      };
    }
    
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Draft submission failed.",
    };
  }
};

export const updateInvoiceData = async (
  pdfId: string | number,
  payload: UpdateInvoicePayload
): Promise<any | ErrorResponseDto> => {
  try {
    const response = await axios.put<any>(
      `/api/v1/newyork-ocr/update-invoice/${pdfId}`,
      payload,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    );
    return response.data;
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error during invoice update",
        description: err.detail || err.description || JSON.stringify(err.errors) || "No details provided",
      };
    }
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Invoice update failed.",
    };
  }
};

export const formatDateToYYYYMMDD = (dateStr: string | null | undefined): string => {
  if (!dateStr || typeof dateStr !== 'string') return '';
  const trimmed = dateStr.trim();
  if (!trimmed) return '';

  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return trimmed;
  }

  const dmYMatch = trimmed.match(/^(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})$/);
  if (dmYMatch) {
    const day = dmYMatch[1].padStart(2, '0');
    const month = dmYMatch[2].padStart(2, '0');
    const year = dmYMatch[3];
    return `${year}-${month}-${day}`;
  }

  const YmdMatch = trimmed.match(/^(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})$/);
  if (YmdMatch) {
    const year = YmdMatch[1];
    const month = YmdMatch[2].padStart(2, '0');
    const day = YmdMatch[3].padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  const parsedDate = new Date(trimmed);
  if (!isNaN(parsedDate.getTime())) {
    const year = parsedDate.getFullYear();
    const month = String(parsedDate.getMonth() + 1).padStart(2, '0');
    const day = String(parsedDate.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  return trimmed;
};

export interface S4LineItemPayload {
  SupplierInvoiceItem: string;
  PurchaseOrder: string;
  PurchaseOrderItem: string;
  DocumentCurrency: string;
  TaxCode: string;
  QuantityInPurchaseOrderUnit: string;
  SupplierInvoiceItemAmount: string;
  PurchaseOrderQuantityUnit: string;
  TaxJurisdiction: string;
}

export interface S4TaxPayload {
  TaxCode: string;
  TaxJurisdiction: string;
  DocumentCurrency: string;
  TaxAmount: string;
}

export interface S4InvoiceCreatePayload {
  pdf_id?: string;
  CompanyCode: string;
  DocumentDate: string;
  DueCalculationBaseDate: string;
  DocumentHeaderText: string;
  InvoiceGrossAmount: string;
  SupplierInvoiceIDByInvcgParty: string;
  TaxIsCalculatedAutomatically: boolean;
  DocumentCurrency: string;
  TaxDeterminationDate?: string;
  PaymentTerms?: string;
  to_SuplrInvcItemPurOrdRef: {
    results: S4LineItemPayload[];
  };
  to_SupplierInvoiceTax: {
    results: S4TaxPayload[];
  };
}

export const cleanPayload = (obj: any): any => {
  if (Array.isArray(obj)) {
    return obj.map(cleanPayload);
  }
  if (obj !== null && typeof obj === 'object') {
    const cleaned: Record<string, any> = {};
    for (const [key, value] of Object.entries(obj)) {
      if (value !== null && value !== undefined && value !== '') {
        cleaned[key] = typeof value === 'object' ? cleanPayload(value) : value;
      }
    }
    return cleaned;
  }
  return obj;
};

export const createInvoiceInS4 = async (
  payload: S4InvoiceCreatePayload,
  pdfId?: string | number
): Promise<any | ErrorResponseDto> => {
  try {
    const sanitizedPayload = cleanPayload({
      ...payload,
      pdf_id: payload.pdf_id ?? (pdfId !== undefined ? String(pdfId) : undefined),
    });
    const response = await axios.post<any>(
      "/api/v1/s4-data-apis/invoice_create_in_s4",
      sanitizedPayload,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    );
    return response.data;
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error during S4 upload",
        description:
          err.detail ||
          err.description ||
          (err.errors ? JSON.stringify(err.errors) : "") ||
          "No details provided",
      };
    }
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "S4 upload failed.",
    };
  }
};

interface PdfResponse {
  file_base64: string;
}

export const viewPdf = async (payload: number | string, setLoading: React.Dispatch<React.SetStateAction<boolean>>) => {
  setLoading(true);

  try {
    const response = await axios.get<PdfResponse>(
      `/api/v1/newyork-ocr/base64-for-view-icon/${payload}`,
      { headers: { Accept: "application/json" } }
    );

    const base64Data = response.data.file_base64;

    if (!base64Data) throw new Error("No base64 data received");

    const detectMimeType = (base64: string): string => {
      const signatures: Record<string, string> = {
        JVBERi: "application/pdf",
        iVBORw: "image/png",
        "/9j/":  "image/jpeg",
        UEsDB:  "application/zip",
        R0lGOD: "image/gif",
      };
      for (const [signature, mimeType] of Object.entries(signatures)) {
        if (base64.startsWith(signature)) return mimeType;
      }
      return "application/octet-stream";
    };

    const mimeType = detectMimeType(base64Data);

    // ✅ Convert to blob instead of data URL (browsers block data: URLs in new tab)
    const byteArray = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
    const blob = new Blob([byteArray], { type: mimeType });
    const blobUrl = URL.createObjectURL(blob);

    if (mimeType === "application/zip") {
      // ✅ ZIP — force download
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = "download.zip";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } else {
      // ✅ PDF / image — open in new tab
      window.open(blobUrl, "_blank");
    }

    // ✅ Clean up after 30s
    setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);

  } catch (error) {
    throw error;
  } finally {
    setLoading(false);
  }
};

export const aribaData = async (
  poNumber: string
): Promise<PurchaseOrderDTO | ErrorResponseDto> => {
  try {
    const response = await axios.get<PurchaseOrderDTO>(
      `/api/v1/newyork-ocr/fetch-ariba-data/${poNumber}`
    );

    const [rawInvoice] = Array.isArray(response?.data)
      ? response.data
      : [response.data];

    return transformPurchaseOrder(rawInvoice);
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error",
        description:
          err.detail ||
          err.description ||
          JSON.stringify(err.errors) ||
          "No details provided",
      };
    }

    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Submission failed.",
    };
  }
};

export const getPoNumbersByPdfId = async (
  pdfId: string | number
): Promise<string[]> => {
  const response = await axios.get<any>(
    `/api/v1/newyork-ocr/invoice/${pdfId}/po-numbers`
  );
  if (response.data && Array.isArray(response.data.po_numbers)) {
    return response.data.po_numbers;
  }
  return Array.isArray(response.data) ? response.data : [];
};

export const aribaDataCompare = async (
  poNumber: string | string[],
  pdfId?: string | number
): Promise<AribaResponseDTO | ErrorResponseDto> => {
  try {
    let poArray: string[];

    // If pdfId is provided, fetch po_numbers from the new API
    if (pdfId) {
      const fetchedPoNumbers = await getPoNumbersByPdfId(pdfId);
      poArray = fetchedPoNumbers.length > 0
        ? fetchedPoNumbers
        : (Array.isArray(poNumber)
            ? poNumber
            : poNumber.split(',').map(po => po.trim()).filter(Boolean));
    } else {
      poArray = Array.isArray(poNumber)
        ? poNumber
        : poNumber.split(',').map(po => po.trim()).filter(Boolean);
    }

    const queryParams = new URLSearchParams();
    
    poArray.forEach(po => {
      queryParams.append('po_numbers', po);
    });
    
    if (pdfId) {
      queryParams.append('pdf_id', String(pdfId));
    }

    const url = `/api/v1/newyork-ocr/compare-invoice-ariba-receipt_id?${queryParams.toString()}`;

    const response = await axios.get<AribaResponseDTO>(url);

    if (!response.data) {
      return {
        statusCode: 404,
        message: "No Data Found",
        description: "The API returned empty data",
      };
    }

    const transformedData = transformAribaResponse(response.data);
    return transformedData;
  } catch (error: any) {
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "API Error",
        description:
          err.detail ||
          err.description ||
          JSON.stringify(err.errors) ||
          "No details provided",
      };
    }

    return {
      statusCode: 500,
      message: "Network Error",
      description: error.message || "Failed to connect to API",
    };
  }
};

export interface InvoiceDtoSaveResponse {
  message: string;
}

export const saveChangesData = async(
  payload: any
): Promise<InvoiceDtoSaveResponse | ErrorResponseDto> => {
  try {
    const response = await axios.post<InvoiceDtoSaveResponse>(
      "/api/v1/newyork-ocr/save-pre-rpa-data/",
      payload,
      {
        headers: {
          "Content-Type": "application/json",
        },
      }
    );
    
    return response?.data;
  } catch (error: any) {
    toast.error("Submission failed.");
    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Unknown error",
        description:
          err.detail ||
          err.description ||
          JSON.stringify(err.errors) ||
          "No details provided",
      };
    }
    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Submission failed.",
    };
  }
}

export const processLocalPdfs = async (): Promise<
  ProcessLocalPdfsResponse | ErrorResponseDto
> => {
  try {
    const response = await axios.post<ProcessLocalPdfsResponse>(
      "/process-uploads-pdfs/",
      {},
      {
        headers: {
          accept: "application/json",
        },
      }
    );

    if (!response.data) {
      return {
        statusCode: 404,
        message: "No Data Found",
        description: "The API returned empty data",
      };
    }

    return response.data;
  } catch (error: any) {
    // Show error toast
    toast.error("Failed to process local PDFs");

    if (error.response?.data) {
      const err = error.response.data;
      return {
        statusCode: err.status || error.response.status || 500,
        message: err.title || err.message || "Processing failed",
        description:
          err.detail ||
          err.description ||
          (err.errors ? JSON.stringify(err.errors) : "") ||
          "No details provided",
      };
    }

    return {
      statusCode: 500,
      message: "Unexpected Error",
      description: error.message || "Failed to process local PDFs.",
    };
  }
};

export interface SupplierPurchaseOrder {
  purchase_order: string;
  po_total?: number;
  purchase_order_date?: string;
  percentage?: string;
}

export interface SupplierPoNumbersResult {
  ponumbers: string[];
  purchaseOrders: SupplierPurchaseOrder[];
}

export const getAllPoNumbersForSupplier = async (
  supplierId: string,
  invoiceTotal: string | number
): Promise<SupplierPoNumbersResult> => {
  const response = await axios.post<{
    ponumbers?: string[];
    purchase_orders?: SupplierPurchaseOrder[];
  }>(
    "/api/v1/newyork-ocr/get_all_Po_numbers_for_supplier",
    {
      supplier_name: supplierId,
      invoice_total: invoiceTotal,
    },
    {
      headers: {
        "Content-Type": "application/json",
      },
    }
  );

  const ponumbers = Array.isArray(response.data?.ponumbers) ? response.data.ponumbers : [];
  const purchaseOrders = Array.isArray(response.data?.purchase_orders) && response.data.purchase_orders.length > 0
    ? response.data.purchase_orders
    : ponumbers.map((po) => ({ purchase_order: po }));

  return { ponumbers, purchaseOrders };
};
