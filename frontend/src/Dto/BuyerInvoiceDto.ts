export interface ExecutePdfResultDto {
  Purchase_Order_ID: string;
  Invoice_No: string;
  Customer_Invoice_ID: string;
  Message: string;
  pdf_id: string;
}


export interface ExecutePdfResponseDto {
  status: string;
  results: ExecutePdfResultDto[];
}