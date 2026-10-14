import axios from './axiosInstance';
import type {
  CustomerGraphRequestDto,
  CustomerGraphResponseDto,
  AribaResponseDTO,
  PoReceiptsSummaryResponseDto,
} from '../Dto/CustomerGraphDto';
import { transformAribaResponse } from '../Dto/CustomerGraphDto';
import type { ErrorResponseDto } from '../Dto/ErrorResponseDto';

export const fetchCustomerGraph = async (
  payload: CustomerGraphRequestDto
): Promise<CustomerGraphResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.post<CustomerGraphResponseDto>(
      `/api/v1/newyork-ocr/customer_graph`,
      payload,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.detail || errData.message || 'Failed to fetch customer graph records',
        description: errData.description || 'No details provided',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while fetching customer graph records.',
    };
  }
};

export const fetchDistinctSuppliers = async (source: string = 'manual'): Promise<string[]> => {
  try {
    const response = await axios.get(`/api/v1/newyork-ocr/distinct-suppliers/`, {
      params: { source },
      withCredentials: true,
    });
    if (Array.isArray(response.data)) return response.data;
    if (response.data && Array.isArray(response.data.suppliers)) return response.data.suppliers;
    return [];
  } catch (error) {
    console.error('Failed to fetch distinct suppliers:', error);
    return [];
  }
};

export const fetchDistinctPoNumbers = async (source: string = 'manual'): Promise<string[]> => {
  try {
    const response = await axios.get(`/api/v1/newyork-ocr/distinct-po-numbers/`, {
      params: { source },
      withCredentials: true,
    });
    if (Array.isArray(response.data)) return response.data;
    if (response.data && Array.isArray(response.data.po_numbers)) return response.data.po_numbers;
    return [];
  } catch (error) {
    console.error('Failed to fetch distinct PO numbers:', error);
    return [];
  }
};

export const fetchDistinctCompanyCodes = async (source: string = 'manual'): Promise<string[]> => {
  try {
    const response = await axios.get(`/api/v1/newyork-ocr/distinct-company-codes/`, {
      params: { source },
      withCredentials: true,
    });
    if (Array.isArray(response.data)) return response.data;
    if (response.data && Array.isArray(response.data.company_codes)) return response.data.company_codes;
    return [];
  } catch (error) {
    console.error('Failed to fetch distinct company codes:', error);
    return [];
  }
};

export interface PdfBase64ResponseDto {
  file_base64?: string;
  pdf_base64?: string;
  base64?: string;
  filename?: string;
}

export const fetchPdfBase64 = async (
  pdfId: string | number
): Promise<PdfBase64ResponseDto | ErrorResponseDto> => {
  try {
    const response = await axios.get<PdfBase64ResponseDto>(
      `/api/v1/newyork-ocr/base64-for-view-icon/${pdfId}`,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.detail || errData.message || 'Failed to fetch PDF data',
        description: errData.description || 'No details provided',
      };
    }

    return {
      statusCode: 500,
      message: 'Unexpected Error',
      description: error.message || 'Something went wrong while fetching PDF data.',
    };
  }
};

export const addAribaComment = async (
  pdf_id: string,
  ariba_comment: string
): Promise<any> => {
  try {
    const payload = {
      pdf_id: String(pdf_id),
      Ariba_Comments: ariba_comment,
    };
    const response = await axios.post(
      `/api/v1/newyork-ocr/Ariba-comments/`,
      payload,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      throw {
        message: error.response.data.message || 'Failed to save Ariba comment',
        description: error.response.data.detail || 'Error occurred while saving comment',
      };
    }
    throw {
      message: 'Unexpected error',
      description: 'An unexpected error occurred while saving comment',
    };
  }
};

export const aribaDataCompare = async (
  poNumber: string,
  pdfId?: string | number
): Promise<AribaResponseDTO | ErrorResponseDto> => {
  try {
    const params: Record<string, any> = {
      tolerance: 0.99,
    };
    if (poNumber && poNumber.trim() !== '') {
      params.po_numbers = poNumber.replace(/\s+/g, '');
    }
    if (pdfId) {
      params.pdf_id = pdfId;
    }

    const response = await axios.get<AribaResponseDTO>(
      `/api/v1/newyork-ocr/compare-invoice-ariba-receipt_id`,
      {
        params,
        withCredentials: true,
      }
    );

    if (!response.data) {
      return {
        statusCode: 404,
        message: 'No Data Found',
        description: 'The API returned empty data',
      };
    }

    return transformAribaResponse(response.data);
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.message || errData.detail || 'API Error',
        description: errData.description || 'Failed to compare invoice data',
      };
    }
    return {
      statusCode: 500,
      message: 'Network Error',
      description: error.message || 'Failed to connect to comparison API',
    };
  }
};

export const getPoReceiptsSummary = async (
  poNumber: string
): Promise<PoReceiptsSummaryResponseDto | ErrorResponseDto> => {
  try {
    const cleanPo = (poNumber || '').replace(/\s+/g, '');
    const response = await axios.get<PoReceiptsSummaryResponseDto>(
      `/api/v1/newyork-ocr/po-receipts-summary/${cleanPo}`,
      { withCredentials: true }
    );
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.message || errData.detail || 'Failed to fetch RC details',
        description: errData.description || 'Error occurred while fetching RC details',
      };
    }
    return {
      statusCode: 500,
      message: 'Unexpected error',
      description: error.message || 'An unexpected error occurred while fetching RC details',
    };
  }
};

export const uploadPurchaseOrders = async (file: File): Promise<{ message?: string; detail?: string } | ErrorResponseDto> => {
  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await axios.post(`/api/v1/UploadPurchaseOrders`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      withCredentials: true,
    });
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.message || errData.detail || 'Upload Failed',
        description: errData.description || 'Failed to upload Purchase Orders file',
      };
    }
    return {
      statusCode: 500,
      message: 'Upload Error',
      description: error.message || 'An unexpected error occurred while uploading Purchase Orders file.',
    };
  }
};

export const uploadPurchaseOrdersAndReceipts = async (file: File): Promise<{ message?: string; detail?: string } | ErrorResponseDto> => {
  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await axios.post(`/api/v1/UploadPurchaseOrdersAndReceipts`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      withCredentials: true,
    });
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.message || errData.detail || 'Upload Failed',
        description: errData.description || 'Failed to upload file',
      };
    }
    return {
      statusCode: 500,
      message: 'Upload Error',
      description: error.message || 'An unexpected error occurred while uploading file.',
    };
  }
};

export const uploadAribaReceipts = async (file: File): Promise<{ message?: string; detail?: string } | ErrorResponseDto> => {
  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await axios.post(`/api/v1/UploadAribaReceipts`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      withCredentials: true,
    });
    return response.data;
  } catch (error: any) {
    if (error.response && error.response.data) {
      const errData = error.response.data;
      return {
        statusCode: error.response.status || 500,
        message: errData.message || errData.detail || 'Upload Failed',
        description: errData.description || 'Failed to upload Ariba Receipts file',
      };
    }
    return {
      statusCode: 500,
      message: 'Upload Error',
      description: error.message || 'An unexpected error occurred while uploading Ariba Receipts file.',
    };
  }
};


export const submitPoData = async (data: any): Promise<any> => {
  try {
    // Placeholder URL for PO Data submission
    const response = await axios.post('/api/v1/placeholder-po-submit-url', data);
    return response.data;
  } catch (error: any) {
    console.error('Error submitting PO Data:', error);
    return { error: true, message: error?.response?.data?.message || 'Failed to submit PO Data' };
  }
};

export const submitReceiptData = async (data: any): Promise<any> => {
  try {
    // Placeholder URL for Receipt Data submission
    const response = await axios.post('/api/v1/placeholder-receipt-submit-url', data);
    return response.data;
  } catch (error: any) {
    console.error('Error submitting Receipt Data:', error);
    return { error: true, message: error?.response?.data?.message || 'Failed to submit Receipt Data' };
  }
};

export const downloadExcelTemplate = async () => {
  try {
    const response = await axios.get('/api/v1/DownloadExcelTemplate', { responseType: 'blob' });
    return response.data;
  } catch (error) {
    console.error('Error downloading template:', error);
    throw error;
  }
};

export const exportInvoicesExcel = async (payload: any) => {
  try {
    const response = await axios.post('/api/v1/newyork-ocr/export_invoices_excel', payload, {
      responseType: 'blob',
    });
    return response.data;
  } catch (error) {
    console.error('Error exporting invoices excel:', error);
    throw error;
  }
};

export const syncAllS4Data = async (): Promise<{ message?: string; detail?: string }> => {
  const response = await axios.post<{ message?: string; detail?: string }>(
    '/api/v1/s4-data-apis/sync-all',
    {},
    { withCredentials: true }
  );
  return response.data;
};

