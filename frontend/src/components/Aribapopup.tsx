import React from "react";
import { Tooltip } from "react-tooltip";
import type { PurchaseOrderDTO } from "../Dto/InvoiceExtractionDto";
import "./Aribapopup.css";

interface AribaPopupProps {
  data: PurchaseOrderDTO[] | null;
  isOpen: boolean;
  onClose: () => void;
}

const formatHeaderName = (header: string): string =>
  header
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

const AribaPopup: React.FC<AribaPopupProps> = ({ data, isOpen, onClose }) => {
  if (!isOpen) return null;

  const hasData = Array.isArray(data) && data.length > 0;

  return (
    <div className="iet-modal-overlay">
      <div className="iet-modal-content">
        <button className="iet-close-btn" onClick={onClose}>×</button>
        <h2 className="iet-modal-title">Details</h2>

        {!hasData ? (
          <div className="iet-no-data">
            <p>No data found for this Purchase Order.</p>
          </div>
        ) : (
          <div className="iet-table-wrapper">
            <table className="iet-modal-table">
              <thead>
                <tr>
                  {Object.keys(data[0]).map((key) => (
                    <th key={key}>{formatHeaderName(key)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {Object.entries(row).map(([_key, value], cellIndex) => {
                      const displayValue =
                        value === null || value === undefined || value === "null" || value === ""
                          ? "—"
                          : String(value);

                      const needsTooltip = displayValue !== "—" && displayValue.length > 20;

                      return (
                        <td
                          key={cellIndex}
                          className="iet-modal-cell"
                          data-tooltip-id={needsTooltip ? "ariba-tooltip" : undefined}
                          data-tooltip-content={needsTooltip ? displayValue : undefined}
                        >
                          {displayValue}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Tooltip
        id="ariba-tooltip"
        place="top"
        className="iet-tooltip"
      />
    </div>
  );
};

export default AribaPopup;