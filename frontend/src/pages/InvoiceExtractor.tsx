declare global {
  interface Window {
    hasUnsavedChanges?: boolean;
  }
}

import React, { useState, useRef, useCallback, useMemo, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "./InvoiceExtractor.css";
import { Header } from "../components/Header";
import { FiArrowLeft, FiImage } from "react-icons/fi";
import { toast } from "react-toastify";
import {
  getInvoiceData,
  getInvoiceDataFromPdfId,
  saveChangesData,
  processLocalPdfs,
  type ProcessLocalPdfsResponse
} from "../api/invoiceExtract";
import {
  transformApiResponse,
  isApiResponse,
  hasValidInvoices,
  type InvoiceDto
} from "../Dto/InvoiceExtractionDto";
import InvoiceExtractionTable from "../components/InvoiceExtractionTable";
// import {fetchSupplierInvoicesById } from "../api/rpaInvoiceApi";
// import {executeInvoicePdf} from "../api/invoiceapi-table"
// import InvoiceExtractionTable from "./InvoiceExtractionTable";

interface InvoiceDetail {
  sno: number;
  [key: string]: string | number | null | undefined;
}

interface DynamicInvoice {
  id: number;
  action: string;
  details: InvoiceDetail[];
  pdf_id?: string | number;
  is_Submit?: boolean;
  [key: string]: string | number | null | undefined | InvoiceDetail[] | boolean;
}

interface CustomerInvoiceRecord {
  No: number;
  po_number: number;
  supplier_invoice_no: string;
  customer_invoice_no: string;
  processed_date: string;
  flags: number;
}

const ALLOWED_FILE_TYPES = new Set([
  "application/pdf",
  "application/zip",
  "application/x-zip-compressed",
  "image/jpeg",
  "image/png",
  "image/jpg",
]);
const MAX_FILE_SIZE = 50 * 1024 * 1024;

const InvoiceExtractor: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const statePdfId = location.state?.pdfId;
  const statePoNumber = location.state?.poNumber;
  const hasLoadedRef = useRef(false);

  const [originalInvoices, setOriginalInvoices] = useState<DynamicInvoice[]>([]);
  const [editableInvoices, setEditableInvoices] = useState<DynamicInvoice[]>([]);
  const [submittedInvoices, setSubmittedInvoices] = useState<DynamicInvoice[]>([]);
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectedReportIds, setSelectedReportIds] = useState<number[]>([]);
  const [showUnsavedModal, setShowUnsavedModal] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const [stamp, setStamp] = useState<Boolean>(false);


  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isCloudLoading, setIsCloudLoading] = useState<boolean>(false);
  const [mainHeaders, setMainHeaders] = useState<string[]>([]);
  const [detailHeaders, setDetailHeaders] = useState<string[]>([]);
  const [showFirstTable, setShowFirstTable] = useState<boolean>(false);
  const [hasApiData, setHasApiData] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [getafterSaveData, setAfterSaveData] = useState<any[]>([])
  const [customerInvoices, setCustomerInvoices] = useState<CustomerInvoiceRecord[]>([]);
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);
  const [isUploadingToAriba, setIsUploadingToAriba] = useState(false);

  useEffect(() => {
    let hasUnsaved = false;

    Object.defineProperty(window, "hasUnsavedChanges", {
      get() {
        return hasUnsaved;
      },
      set(val: boolean) {
        if (val && !hasUnsaved) {
          window.history.pushState(null, document.title, window.location.pathname);
        }
        hasUnsaved = val;
      },
      configurable: true,
    });

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (window.hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = "";
      }
    };

    const handlePopState = () => {
      if (window.hasUnsavedChanges) {
        setShowUnsavedModal(true);
        window.history.pushState(null, document.title, window.location.pathname);
        setPendingNavigation(() => () => window.history.back());
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    window.addEventListener("popstate", handlePopState);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      window.removeEventListener("popstate", handlePopState);
      try {
        delete (window as any).hasUnsavedChanges;
      } catch (e) {}
    };
  }, []);

  const [currentInvoiceNumber, setCurrentInvoiceNumber] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showInvoiceTable, setShowInvoiceTable] = useState(false);

  const displayValue = useCallback(
    (value: string | number | null | undefined): string => {
      if (value === null || value === undefined || value === "") return "—";
      return String(value);
    },
    []
  );

  const validateFile = useCallback((file: File): string | null => {
    if (!ALLOWED_FILE_TYPES.has(file.type)) {
      return "Only PDF, ZIP, PNG , JPEG and JPG files are allowed.";
    }
    if (file?.size > MAX_FILE_SIZE) {
      return "File size must be less than 50MB.";
    }
    return null;
  }, []);

  const extractHeaders = useCallback((invoices: InvoiceDto[]) => {
    try {
      const mainHeaders: string[] = [];
      const detailHeaders: string[] = [];

      if (invoices.length > 0) {
        const firstInvoice = invoices[0];

        Object.entries(firstInvoice).forEach(([key, value]) => {
          if (
            key !== "Items" &&
            key !== "source_file" &&
            key !== "pdf_id" &&
            key !== "user_id" &&
            key !== "invoice_id" &&
            key !== "is_trusted" &&
            key !== "PONumber" &&          // PONumber shown in detail table, not main table
            typeof value !== "object" &&
            value !== null &&
            value !== undefined
          ) {
            mainHeaders.push(key);
          }
        });

        if (Array.isArray(firstInvoice.Items) && firstInvoice.Items.length > 0) {
          const firstItem = firstInvoice.Items[0];
          if (firstItem && typeof firstItem === "object") {
            const itemKeys = Object.keys(firstItem as unknown as Record<string, unknown>);

            // Fields to exclude from detail table columns (reference/internal fields)
            const excludeFromDetail = new Set([
              "PO_Number",
              "ReferenceDocument", "ReferenceDocumentItem", "ReferenceDocumentFiscalYear",
              "VATAmount", "AmountInclVAT",
            ]);

            // Order: PONumber first, then LineItemNumber, then rest of item fields, then Po_Total last
            const sorted = [
              // PONumber before LineItemNumber (from item data)
              ...itemKeys.filter(k => k === "PONumber"),
              ...itemKeys.filter(k => k === "LineItemNumber"),
              ...itemKeys.filter(k => k !== "PONumber" && k !== "LineItemNumber" && k !== "Po_Total" && !excludeFromDetail.has(k)),
              ...itemKeys.filter(k => k === "Po_Total"),
            ];
            sorted.forEach((key) => {
              detailHeaders.push(key);
            });

            // If PONumber wasn't in item keys, add it as first column anyway
            if (!detailHeaders.includes("PONumber")) {
              detailHeaders.unshift("PONumber");
            }
          }
        }
      }

      return { mainHeaders, detailHeaders };
    } catch (error) {
      return { mainHeaders: [], detailHeaders: [] };
    }
  }, []);

  const transformInvoiceData = useCallback(
    (invoice: InvoiceDto, mainHeaders: string[], invoiceId: number) => {
      try {
        const transformedInvoice: DynamicInvoice = {
          id: invoiceId,
          action: "Edit",
          details: [],
          pdf_id: invoice.pdf_id,
          BankAccountName: (invoice as any).BankAccountName || null,
          BankName: (invoice as any).BankName || null,
          BankBranch: (invoice as any).BankBranch || null,
          BranchCode: (invoice as any).BranchCode || null,
          AccountNumber: (invoice as any).AccountNumber || null,
          IBAN: (invoice as any).IBAN || null,
          SwiftCode: (invoice as any).SwiftCode || null,
          Customer_address: (invoice as any).Customer_address || null,
          Supplier_address: (invoice as any).Supplier_address || null,
          Tax_invoice: (invoice as any).Tax_invoice || null,
          Stamp: (invoice as any).Stamp || null,
        };

        mainHeaders?.forEach((header) => {
          const value = invoice[header as keyof InvoiceDto];
          if (Array.isArray(value)) {
            transformedInvoice[header] = "—";
          } else if (typeof value === 'boolean') {
            transformedInvoice[header] = String(value);
          } else {
            transformedInvoice[header] = value !== undefined ? value : "—";
          }
        });

        if (Array.isArray(invoice.Items)) {
          transformedInvoice.details = invoice.Items.map((item, index) => {
            if (item && typeof item === "object") {
              const rawItem = item as unknown as Record<string, unknown>;
              // Place PONumber first, then LineItemNumber, then the rest
              const { PONumber, LineItemNumber, ...rest } = rawItem as any;
              return {
                sno: index + 1,
                ...(PONumber !== undefined ? { PONumber } : {}),
                ...(LineItemNumber !== undefined ? { LineItemNumber } : {}),
                ...rest,
              } as InvoiceDetail;
            }
            return { sno: index + 1 } as InvoiceDetail;
          });
        }

        return transformedInvoice;
      } catch (error) {
        throw new Error("Failed to process invoice data");
      }
    },
    []
  );

  const handleBrowseClick = useCallback((): void => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>): void => {
      try {
        const files = e.target?.files ? Array.from(e.target.files) : [];
        if (files.length === 0) {
          toast.error("No files selected.");
          return;
        }

        for (const file of files) {
          const validationError = validateFile(file);
          if (validationError) {
            toast.error(`${file.name}: ${validationError}`);
            e.target.value = "";
            return;
          }
        }

        resetStates();
        setSelectedFiles(files);
        window.hasUnsavedChanges = files.length > 0;
        toast.success(`${files.length} file(s) selected successfully.`);
      } catch (error) {
        toast.error("Error selecting file. Please try again.");
      } finally {
        e.target.value = "";
      }
    },
    [validateFile]
  );

  const resetStates = useCallback(() => {
    setShowFirstTable(false);
    setHasApiData(null);
    clearInvoices();
    setMainHeaders([]);
    setDetailHeaders([]);
    setCustomerInvoices([]);
    setCurrentInvoiceNumber(null);
    setShowInvoiceTable(false);
    setSelectedIds([]);
    setIsSubmitted(false);
    setSubmittedInvoices([]);
    setSelectedReportIds([]);
    setIsSaved(false);
  }, []);

  const clearInvoices = useCallback(() => {
    setOriginalInvoices([]);
    setEditableInvoices([]);
    setSubmittedInvoices([]);
    setIsSubmitted(false);
  }, []);

  const processInvoiceResponse = useCallback((apiResponse: any, stamp?: boolean) => {
    if ('statusCode' in apiResponse) {
      const errorMessage =
        (apiResponse as any).message || "Unknown error occurred";
      setHasApiData(false);
      toast.error(`API Error: ${errorMessage}`);
      return false;
    }

    if (isApiResponse(apiResponse)) {
      if (!hasValidInvoices(apiResponse)) {
        setHasApiData(false);
        toast.error("No valid invoice data found in the uploaded files");
        return false;
      }

      const invoices = transformApiResponse(apiResponse, stamp);

      const { mainHeaders, detailHeaders } = extractHeaders(invoices);

      if (mainHeaders.length === 0) {
        setHasApiData(false);
        toast.error("No invoice data found in the file(s)");
        return false;
      }

      setMainHeaders(mainHeaders);
      setDetailHeaders(detailHeaders);

      const transformedInvoices = invoices.map((invoice, index) =>
        transformInvoiceData(invoice, mainHeaders, index + 1)
      );

      setOriginalInvoices(transformedInvoices);
      setEditableInvoices(transformedInvoices);
      setHasApiData(true);
      setShowFirstTable(true);
      window.hasUnsavedChanges = true;
      setIsSaved(false);

      toast.success(`Successfully processed ${transformedInvoices.length} invoice(s).`);
      return true;
    } else {
      setHasApiData(false);
      toast.error("Invalid API response format");
      return false;
    }
  }, [extractHeaders, transformInvoiceData, setOriginalInvoices, setEditableInvoices]);

  useEffect(() => {
    if (statePdfId && !hasLoadedRef.current) {
      hasLoadedRef.current = true;
      const loadInvoice = async () => {
        setIsLoading(true);
        resetStates();
        setAfterSaveData([]);
        try {
          const res = await getInvoiceDataFromPdfId(statePdfId);
          if (res && 'statusCode' in res) {
            const errorMessage = (res as any).message || "Unknown error occurred";
            setHasApiData(false);
            toast.error(`API Error: ${errorMessage}`);
            return;
          }

          const apiRes = res as import('../Dto/InvoiceExtractionDto').ApiResponse;
          const extractedStamp = (apiRes?.results?.[0]?.invoices?.[0] as any)?.Stamp;
          setStamp(extractedStamp);

          const success = processInvoiceResponse(apiRes, extractedStamp);
          if (success && statePoNumber) {
            setOriginalInvoices(prev => prev.map(inv => ({ ...inv, PONumber: statePoNumber })));
            setEditableInvoices(prev => prev.map(inv => ({ ...inv, PONumber: statePoNumber })));
          }
        } catch (error) {
          setHasApiData(false);
          toast.error(error instanceof Error ? error.message : "Load failed");
        } finally {
          setIsLoading(false);
        }
      };
      loadInvoice();
    }
  }, [statePdfId, statePoNumber, processInvoiceResponse]);

  const getFileData = useCallback(async () => {
    if (selectedFiles.length === 0) {
      toast.error("Please select files first.");
      return;
    }
    setAfterSaveData([]);
    const formData = new FormData();
    selectedFiles.forEach((file) => {
      formData.append("files", file);
    });
    setIsLoading(true);
    resetStates();
    try {
      const apiResponses = await getInvoiceData(formData);
      if ('statusCode' in apiResponses) {
        const errorMessage = (apiResponses as any).message || "Unknown error occurred";
        setHasApiData(false);
        toast.error(`API Error: ${errorMessage}`);
        setIsLoading(false);
        return;
      }
    const pdfIds = apiResponses?.results
      ?.map((r: any) => r?.pdf_id)
      .filter((id: any) => id !== undefined && id !== null);

    if (!pdfIds || pdfIds.length === 0) {
      toast.error("No PDF IDs found");
      setIsLoading(false);
      return;
    }

    const extractedStamp = apiResponses?.results?.[0]?.invoices?.[0]?.Stamp;
    setStamp(extractedStamp);

    const responses = await Promise.all(
      pdfIds.map((id: string | number) => getInvoiceDataFromPdfId(id))
    );

    const mergedResponse = {
      results: responses.flatMap((res: any) => res?.results || [])
    };

    processInvoiceResponse(mergedResponse, extractedStamp);
    } catch (error) {
      setHasApiData(false);
      const errorMessage = error instanceof Error ? error.message : "Upload failed";
      toast.error(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [selectedFiles, resetStates, processInvoiceResponse]);

  const handleExtractFromCloud = useCallback(async () => {
    setIsCloudLoading(true);
    resetStates();
    setAfterSaveData([])
    try {
      const apiResponse = await processLocalPdfs();

      if ('statusCode' in apiResponse) {
        const errorMessage = apiResponse.message || "Unknown error occurred";
        setHasApiData(false);
        toast.error(`API Error: ${errorMessage}`);
        return;
      }

      const successResponse = apiResponse as ProcessLocalPdfsResponse;

      const transformedResponse = {
        results: successResponse.results.map((result: any) => ({
          filename: result.filename,
          user_id: result.user_id,
          input_id: result.input_id,
          pdf_id: result.pdf_id,
          invoices: result.invoices,
          status: result.status
        }))
      };

      processInvoiceResponse(transformedResponse);
    } catch (error) {
      setHasApiData(false);
      const errorMessage =
        error instanceof Error ? error.message : "Failed to extract from cloud";
      toast.error(errorMessage);
    } finally {
      setIsCloudLoading(false);
    }
  }, [selectedFiles, resetStates, extractHeaders, transformInvoiceData, processInvoiceResponse]);

  const handleDetailChange = useCallback(
    (invoiceId: number, detailIndex: number, field: string, value: string) => {
      try {
        setEditableInvoices(prev =>
          prev.map(inv =>
            inv.id === invoiceId
              ? {
                ...inv,
                details: inv.details.map((det, idx) =>
                  idx === detailIndex ? { ...det, [field]: value } : det
                ),
              }
              : inv
          )
        );
        window.hasUnsavedChanges = true;
        setIsSaved(false);
      } catch (error) {
        toast.error("Failed to update detail. Please try again.");
      }
    },
    []
  );

  const handleInvoiceChange = useCallback(
    (invoiceId: number, field: string, value: string) => {
      try {
        setEditableInvoices(prev =>
          prev.map(inv => (inv.id === invoiceId ? { ...inv, [field]: value } : inv))
        );
        window.hasUnsavedChanges = true;
        setIsSaved(false);
      } catch (error) {
        toast.error("Failed to update invoice. Please try again.");
      }
    },
    []
  );

  const handlePreviewDetailChange = useCallback(
    (invoiceId: number, detailIndex: number, field: string, value: string) => {
      try {
        setSubmittedInvoices(prev =>
          prev.map(inv =>
            inv.id === invoiceId
              ? {
                ...inv,
                details: inv.details.map((det, idx) =>
                  idx === detailIndex ? { ...det, [field]: value } : det
                ),
              }
              : inv
          )
        );
        window.hasUnsavedChanges = true;
        setIsSaved(false);
      } catch (error) {
        toast.error("Failed to update detail. Please try again.");
      }
    },
    []
  );

  const handlePreviewInvoiceChange = useCallback(
    (invoiceId: number, field: string, value: string) => {
      try {
        setSubmittedInvoices(prev =>
          prev.map(inv => (inv.id === invoiceId ? { ...inv, [field]: value } : inv))
        );
        window.hasUnsavedChanges = true;
        setIsSaved(false);
      } catch (error) {
        toast.error("Failed to update invoice. Please try again.");
      }
    },
    []
  );

  // const handleSubmit = useCallback(() => {
  //   try {
  //     if (selectedIds.length === 0) {
  //       toast.error("Please select at least one invoice to submit.");
  //       return;
  //     }


  //     const updatedInvoices = editableInvoices.map(inv => ({
  //       ...inv,
  //       is_Submit: selectedIds.includes(inv.id),
  //     }));

  //     setEditableInvoices(updatedInvoices);

  //     const selectedInvoices = updatedInvoices?.filter(inv => inv.is_Submit === true);
  //     setSubmittedInvoices(selectedInvoices);
  //     setIsSubmitted(true);
  //     // setSelectedIds([]);
  //     window.hasUnsavedChanges = true;
  //     setIsSaved(false);
  //     toast.success("Data submitted successfully!");
  //   } catch (error) {
  //     toast.error("Failed to submit invoice data. Please try again.");
  //   }
  // }, [editableInvoices, selectedIds]);

  const handlePushToAriba = useCallback(async () => {
    const selectedInvoices = submittedInvoices.filter(inv => selectedReportIds.includes(inv.id));

    if (selectedInvoices.length === 0) {
      toast.error("Please select at least one invoice to upload.");
      return;
    }

    const invoiceNumber = selectedInvoices[0].InvoiceNumber || selectedInvoices[0].sup_Invoice_Number || "";

    if (!invoiceNumber) {
      toast.error("Invoice Number not found in the selected invoice.");
      return;
    }

    const pdfIds: (string | number)[] = selectedInvoices
      .map(inv => inv.pdf_id)
      .filter((id): id is string | number => id !== undefined && id !== null);

    if (pdfIds.length === 0) {
      toast.error("PDF ID not found in the selected invoice(s).");
      return;
    }

    setCurrentInvoiceNumber(String(invoiceNumber));
    setCustomerInvoices([]);
    setShowInvoiceTable(true);
    setIsUploadingToAriba(true);

    // Show initial message about process starting
    toast.info("Uploading Process started please wait, this may take some time.");

    // try {
    //   const result = await executeInvoicePdf(pdfIds);

    //   // Check if the result indicates success
    //   if (result && typeof result === "object" && "statusCode" in result) {
    //     if (result.statusCode === 200) {
    //       toast.success("Uploading to Ariba is Completed Successfully!");
    //       setIsUploadingToAriba(false);
    //     } else {
    //       // If statusCode is not 200, it's an error
    //       const errorMessage = (result as any)?.message || "Failed to upload to Ariba";
    //       toast.error(errorMessage);
    //       setIsUploadingToAriba(false);
    //     }
    //   } else {
    //     toast.success("Uploading to Ariba is Completed Successfully!");
    //     setIsUploadingToAriba(false);
    //   }
    // } catch (error) {
    //   setIsUploadingToAriba(false);
    //   toast.error("Background process error: " + (error instanceof Error ? error.message : "Unknown error"));
    // }

  }, [submittedInvoices, selectedReportIds]);


  const handleRefreshStatus = useCallback(async () => {
    if (!currentInvoiceNumber) {
      toast.error("No Invoice number available to fetch status.");
      return;
    }

    setIsRefreshing(true);

    // try {
    //   const result = await fetchSupplierInvoicesById(currentInvoiceNumber);

    //   if (typeof result === "object" && result !== null && "statusCode" in result && result.statusCode === 200) {
    //     const invoices: CustomerInvoiceRecord[] =
    //       typeof result === "object" && result !== null && "description" in result && typeof (result as any).description === "string"
    //         ? JSON.parse((result as any).description)
    //         : [];

    //     const hasProcessingRecords = invoices.some(inv => inv.flags === 0);

    //     if (invoices.length === 0) {
    //       toast.info("No records found. The process may still be running.");
    //       setCustomerInvoices([]);
    //     } else if (hasProcessingRecords) {
    //       toast.info("Still processing. Please try clicking on Refresh Status button.");
    //       setCustomerInvoices([]);
    //     } else {
    //       const completedInvoices = invoices.filter(inv => inv.flags === 1);
    //       setCustomerInvoices(completedInvoices);
    //       toast.success(`Found ${completedInvoices.length} completed invoice record(s)`);
    //     }
    //     window.hasUnsavedChanges = false;
    //   } else {
    //     toast.error((typeof result === "object" && result !== null && "message" in result && typeof (result as any).message === "string")
    //       ? (result as any).message
    //       : "Failed to fetch invoice status");
    //   }

    // } catch (error) {
    //   toast.error("Error fetching invoice status. Please try again.");
    // } finally {
    //   setIsRefreshing(false);
    // }
  }, [currentInvoiceNumber]);

  // const handleReset = useCallback(() => {
  //   try {
  //     if (originalInvoices.length === 0) {
  //       toast.info("No original data to reset to.");
  //       return;
  //     }

  //     setEditableInvoices([...originalInvoices]);
  //     window.hasUnsavedChanges = true;
  //     setIsSaved(false);
  //     toast.success("Changes have been reset.");
  //   } catch (error) {
  //     toast.error("Failed to reset invoice data. Please try again.");
  //   }
  // }, [originalInvoices]);

  const handleAfterSave = useCallback((updatedData: any) => {
    if (updatedData) {
      processInvoiceResponse(updatedData, stamp as boolean | undefined);
    }
  }, [processInvoiceResponse, stamp]);

  const handleRemoveFiles = useCallback(() => {
    setSelectedFiles([]);
    resetStates();
    window.hasUnsavedChanges = false;
    toast.info("Files removed.");
  }, [resetStates]);

  const handleSelect = useCallback((id: number, selected: boolean) => {
    setSelectedIds(prev =>
      selected ? [...prev, id] : prev.filter(i => i !== id)
    );
  }, []);



  const handleSelectAll = useCallback((selected: boolean) => {
    setSelectedIds(selected ? editableInvoices.map(inv => inv.id) : []);
  }, [editableInvoices]);

  const handleReportSelect = useCallback((id: number, selected: boolean) => {
    setSelectedReportIds(prev =>
      selected ? [...prev, id] : prev.filter(i => i !== id)
    );
  }, []);

  const handleReportSelectAll = useCallback((selected: boolean) => {
    setSelectedReportIds(selected ? submittedInvoices.map(inv => inv.id) : []);
  }, [submittedInvoices]);

  const statusMessage = useMemo(() => {
    if (hasApiData === false) {
      return "No data found. Please try uploading a different file.";
    }
    // if (selectedFiles.length === 0 && !isLoading && !isCloudLoading && !showFirstTable) {
    //   return "Select PDF/ZIP/PNG/JPEG or JPG files to show results.";
    // }
    return null;
  }, [hasApiData, selectedFiles, isLoading, isCloudLoading, showFirstTable]);

  const fileUploadSection = useMemo(
    () => (
      <div className="upload-box">
        <FiImage 
          size={80} 
          color="#76d3b3" 
          strokeWidth={1} 
          style={{ marginBottom: "10px", opacity: 0.8 }} 
        />
        <p
          style={{ fontWeight: 500, cursor: "pointer" }}
          onClick={handleBrowseClick}
        >
          <span style={{ color: "#0f68e6" }}>Browse</span> your files
        </p>
        <small>Supports: .pdf, .zip, .png, .jpeg, .jpg (multiple files)</small>
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: "none", marginTop: 5 }}
          accept=".pdf,.zip,image/*"
          multiple
          onChange={handleFileChange}
        />
        {selectedFiles.length > 0 && (
          <div className="upload-status" style={{ marginTop: "15px" }}>
            <span
              className="remove-icon"
              onClick={handleRemoveFiles}
              title="Remove files"
              style={{ cursor: "pointer", marginRight: "6px" }}
            >
              ✖
            </span>
            <span title={selectedFiles.map((f) => f.name).join(", ")}>
              {selectedFiles.length} file(s) selected
            </span>
          </div>
        )}
      </div>
    ),
    [selectedFiles, handleBrowseClick, handleFileChange, handleRemoveFiles]
  );

  const saveChanges = () => {
    if (submittedInvoices?.length > 0) {
      const selectedInvoices = submittedInvoices.filter(inv => selectedReportIds.includes(inv.id));
      const transformed = selectedInvoices.flatMap(invoice =>
        invoice.details.map(item => ({
          PO_Number: (item.PONumber as string) || (invoice.PONumber as string) || "",
          sup_Invoice_Number: invoice.InvoiceNumber || "",
          INVOICE_DATE: invoice.InvoiceDate || "",
          No: item?.LineItemNumber?.toString() || item?.sno?.toString() || "",
          Description: item.ItemDescription || "",
          Qty_Invoice: item.QTY || "",
          Unit: item.Unit || "",
          Price_AED: item.UnitPrice || "",
          Amount_AED: item.Amount || item.amount || "",
          Discount_AED: item.Discount || "",
          Charges: "",
          Taxes_AED: "",
          Gross_Amount_AED: "",
          Accounting: "",
          Details: "",
          Tax_code: "v2",
          is_process: 0,
          processed_date: "",
          user_id: 1?.toString() || "",
          pdf_id: invoice.pdf_id !== undefined && invoice.pdf_id !== null ? String(invoice.pdf_id) : "",
          base_64_string: "",
          additionalProp1: {}
        }))
      );
      return transformed;
    } else {
      return [];
    }
  };

  const sendSaveValue = async () => {
    try {
      const payload = saveChanges();

      if (payload.length > 0) {
        await saveChangesData(payload);

        setIsSaved(true);
        toast.success(`Successfully saved ${payload.length} record${payload.length > 1 ? 's' : ''}!`);
        setAfterSaveData(prev => [...prev, ...payload]);
      } else {
        toast.error("No changes to save.");
      }
    } catch (error) {
      toast.error("Failed to save data. Please try again.");
    }
  };


  const handleModalConfirm = () => {
    setShowUnsavedModal(false);
    window.hasUnsavedChanges = false;
    if (pendingNavigation) {
      pendingNavigation();
      setPendingNavigation(null);
    }
  };

  const handleModalCancel = () => {
    setShowUnsavedModal(false);
    setPendingNavigation(null);
    window.history.pushState(null, document.title, window.location.pathname);
  };
  return (
    <>
      <Header />
      <div className="ie-container">
        <div className="ie-header">
          <button
            className="ie-back-btn"
            onClick={() => navigate("/dashboard")}
            aria-label="Back to Dashboard"
            title="Back to Dashboard"
          >
            <FiArrowLeft aria-hidden="true" />
          </button>
          <h1 className="ie-title">Supplier Invoice Management</h1>
        </div>

      {fileUploadSection}

      <div className="start-btn" style={{ display: 'flex', gap: '0.5rem', justifyContent: 'center' }}>
        <button
          onClick={getFileData}
          disabled={selectedFiles.length === 0 || isLoading || isCloudLoading}
          style={{
            opacity: selectedFiles.length === 0 || isLoading || isCloudLoading ? 0.6 : 1,
            cursor: selectedFiles.length === 0 || isLoading || isCloudLoading ? "not-allowed" : "pointer",
          }}
        >
          {isLoading ? "Processing..." : "Start"}
        </button>

        <button
          className="ie-extract-btn"
          onClick={handleExtractFromCloud}
          disabled={isLoading || isCloudLoading}
          style={{
            opacity: isLoading || isCloudLoading ? 0.6 : 1,
            cursor: isLoading || isCloudLoading ? "not-allowed" : "pointer",
          }}
        >
          {isCloudLoading ? "Extracting..." : "Extract from Cloud"}
        </button>
      </div>

      {showFirstTable && hasApiData === true && editableInvoices.length > 0 && (
        <>
          <InvoiceExtractionTable
            invoices={editableInvoices}
            mainHeaders={mainHeaders}
            detailHeaders={detailHeaders}
            handleDetailChange={handleDetailChange}
            handleInvoiceChange={handleInvoiceChange}
            displayValue={displayValue}
            originalInvoices={originalInvoices}
            readOnly={false}
            isShowing={true}
            setLoading={setLoading}
            loading={loading}
            showCheckbox={true}
            selectedIds={selectedIds}
            onSelect={handleSelect}
            onSelectAll={handleSelectAll}
            editableInvoices={editableInvoices}
            getafterSaveData={getafterSaveData}
            onAfterSave={handleAfterSave}
            autoComparePoNumber={statePoNumber}
          />

          {/* <div className="footer-buttons" style={{ marginTop: "1rem" }}>
            <button
              className="submit-btn"
              onClick={handleSubmit}
              disabled={selectedIds.length === 0}
            >
              Submit
            </button>
            <button
              className="ie-reset-btn"
              onClick={handleReset}
              style={{ marginLeft: "0.5rem" }}
              disabled={originalInvoices.length === 0}
            >
              Reset
            </button>
          </div> */}
        </>
      )}

      {statusMessage && (
        <div style={{ textAlign: "center", margin: "2rem 0", color: "#666" }}>
          <p>{statusMessage}</p>
        </div>
      )}

      {isSubmitted && submittedInvoices.length > 0 && (
        <>
          <h2 className="ie-report">Preview</h2>

          <InvoiceExtractionTable
            invoices={submittedInvoices}
            mainHeaders={mainHeaders}
            detailHeaders={detailHeaders}
            handleDetailChange={handlePreviewDetailChange}
            handleInvoiceChange={handlePreviewInvoiceChange}
            displayValue={displayValue}
            originalInvoices={originalInvoices}
            readOnly={false}
            isShowing={false}
            setLoading={setLoading}
            loading={loading}
            showCheckbox={true}
            selectedIds={selectedReportIds}
            onSelect={handleReportSelect}
            onSelectAll={handleReportSelectAll}
            editableInvoices={editableInvoices}
            getafterSaveData={[]}
          />

          <div className="footer-buttons" style={{ marginTop: "1rem" }}>
            <button className="submit-btn" onClick={() => sendSaveValue()}>
              Save Changes
            </button>
            <button
              className="submit-btn"
              onClick={handlePushToAriba}
              disabled={selectedReportIds.length === 0 || !isSaved || isUploadingToAriba}
              style={{
                marginLeft: "0.5rem",
                opacity: (selectedReportIds.length === 0 || !isSaved || isUploadingToAriba) ? 0.6 : 1,
                cursor: (selectedReportIds.length === 0 || !stamp || !isSaved || isUploadingToAriba) ? "not-allowed" : "pointer",
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '0.5rem'
              }}
              title={!isSaved ? "Please save changes first" : isUploadingToAriba ? "Uploading in progress..." : ""}
            >
              {isUploadingToAriba ? (
                <>
                  <span className="loader-spinner" style={{
                    display: 'inline-block',
                    width: '16px',
                    height: '16px',
                    border: '2px solid rgba(255, 255, 255, 0.3)',
                    borderRadius: '50%',
                    borderTop: '2px solid white',
                    animation: 'spin 1s linear infinite'
                  }} />
                  Uploading...
                </>
              ) : (
                "Upload"
              )}
            </button>
          </div>
        </>
      )}

      {showInvoiceTable && (
        <>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: '2rem',
            marginBottom: '1rem'
          }}>
            <h2 className="ie-report">Processing Status</h2>
            <button
              onClick={handleRefreshStatus}
              disabled={isRefreshing}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor: '#007bff',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: isRefreshing ? 'not-allowed' : 'pointer',
                opacity: isRefreshing ? 0.6 : 1,
              }}
            >
              {isRefreshing ? 'Refreshing...' : '🔄 Refresh Status'}
            </button>
          </div>

          {customerInvoices.length === 0 ? (
            <div style={{
              textAlign: 'center',
              padding: '2rem',
              backgroundColor: '#f8f9fa',
              borderRadius: '8px',
              margin: '1rem 0'
            }}>
              <p style={{ color: '#6c757d', fontSize: '1.1rem' }}>
                Click 'Refresh Status' to check for updates.
              </p>
              {currentInvoiceNumber && (
                <p style={{ color: '#6c757d', fontSize: '0.9rem', marginTop: '0.5rem' }}>
                  Tracking Invoice Number: <strong>{currentInvoiceNumber}</strong>
                </p>
              )}
            </div>
          ) : (
            <table className="ariba-result-table">
              <thead>
                <tr>
                  <th>No</th>
                  <th>PO Number</th>
                  <th>Supplier Invoice No</th>
                  <th>Customer Invoice No</th>
                  <th>Processed Date</th>
                </tr>
              </thead>
              <tbody>
                {customerInvoices.map((record, idx) => (
                  <tr key={idx}>
                    <td>{record.No}</td>
                    <td>{record.po_number}</td>
                    <td>{record.supplier_invoice_no}</td>
                    <td>{record.customer_invoice_no || "—"}</td>
                    <td>{new Date(record.processed_date).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {showUnsavedModal && (
        <div className="confirmation-modal-overlay">
          <div className="confirmation-modal">
            <div className="confirmation-modal-header">
              <h3>Unsaved Changes</h3>
            </div>
            <div className="confirmation-modal-body">
              <p>
                You have unsaved changes on this page. Are you sure you want to leave? Your changes will be lost.
              </p>
            </div>
            <div className="confirmation-modal-actions">
              <button className="confirmation-cancel-btn" onClick={handleModalCancel}>
                Stay on Page
              </button>
              <button className="confirmation-confirm-btn reverse" onClick={handleModalConfirm}>
                Leave Page
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </>
  );
};

export default InvoiceExtractor;
