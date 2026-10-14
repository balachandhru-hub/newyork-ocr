import React from "react";
import "./BankDetailsModal.css";
import type { BankDetailsDto } from "../Dto/InvoiceExtractionDto";

interface BankDetailsModalProps {
  isOpen: boolean;
  onClose: () => void;
  bankDetails: BankDetailsDto;
  supplierName?: string;
}

const BankDetailsModal: React.FC<BankDetailsModalProps> = ({
  isOpen,
  onClose,
  bankDetails,
  supplierName = "Supplier",
}) => {
  if (!isOpen) return null;

  const formatValue = (value: string | null | undefined): string => {
    return value && value.trim() ? value : "—";
  };

  return (
    <div className="bank-modal-overlay" onClick={onClose}>
      <div className="bank-modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="bank-modal-header">
          <h2>Bank Details</h2>
          <span className="bank-modal-close" onClick={onClose}>
            ✕
          </span>
        </div>

        <div className="bank-modal-body">
          <div className="bank-supplier-name">
            <strong>{supplierName}</strong>
          </div>

          <div className="bank-details-grid">
            <div className="bank-detail-row">
              <label>Bank Account Name:</label>
              <span>{formatValue(bankDetails.BankAccountName)}</span>
            </div>

            <div className="bank-detail-row">
              <label>Bank Name:</label>
              <span>{formatValue(bankDetails.BankName)}</span>
            </div>

            <div className="bank-detail-row">
              <label>Bank Branch:</label>
              <span>{formatValue(bankDetails.BankBranch)}</span>
            </div>

            <div className="bank-detail-row">
              <label>Branch Code:</label>
              <span>{formatValue(bankDetails.BranchCode)}</span>
            </div>

            <div className="bank-detail-row">
              <label>Account Number:</label>
              <span>{formatValue(bankDetails.AccountNumber)}</span>
            </div>

            <div className="bank-detail-row">
              <label>IBAN:</label>
              <span>{formatValue(bankDetails.IBAN)}</span>
            </div>

            <div className="bank-detail-row">
              <label>Swift Code:</label>
              <span>{formatValue(bankDetails.SwiftCode)}</span>
            </div>
          </div>
        </div>

        <div className="bank-modal-footer">
          <button className="bank-modal-close-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default BankDetailsModal;