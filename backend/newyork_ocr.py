# ============================================================================
# 1. IMPORTS
# ============================================================================
import os
import io
import json
import time
import uuid
import base64
import zipfile
import logging
import asyncio
import concurrent.futures
import re
import difflib
import email as email_module
from email.header import decode_header, make_header
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, Any, List, Optional, Tuple, Union
import pyodbc
import paramiko
import requests
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fastapi import (
    APIRouter, UploadFile, File, HTTPException, Depends, Request,
    Query, Path
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from google import genai
from google.genai import types
import pandas as pd
from PIL import Image

from auth import get_person_details



# ============================================================================
# 2. LOGGING CONFIGURATION
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]
)


# ============================================================================
# 3. ENVIRONMENT VARIABLES
# ============================================================================
load_dotenv()


# ============================================================================
# 4. CONSTANTS & CONFIGURATION
# ============================================================================
USER_ID = os.getenv("DEFAULT_USER_ID", "system")
MAX_FILE_SIZE = 200 * 1024 * 1024
MAX_WORKERS = 10

SFTP_HOST = os.getenv("SFTP_HOST")
SFTP_USER = os.getenv("SFTP_USER")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD")


IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")

# Scheduler on/off switches, flipped in code (same pattern as ocr_sftp.py's
# SFTP_SCHEDULER) - set to False to disable a scheduler without removing the
# code that starts it.
SFTP_SCHEDULER = True             # 30-min SFTP folder scan (process_sftp_folder_invoices)
FETCH_EMAIL_SCHEDULER = True      # 10-min mailbox poll (fetch_invoices_from_email)

# ============================================================================
# 5. DATABASE / SERVICE CONFIGURATION
# ============================================================================
DB_CONNECTION_STRING = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={os.getenv('SERVER_NAME')};"
    f"DATABASE={os.getenv('Spain_DB_NAME')};"
    f"UID={os.getenv('DB_USER')};"
    f"PWD={os.getenv('DB_PASSWORD')};"
    f"TrustServerCertificate=yes;"
)

# ============================================================================
# 6. GEMINI CLIENT INITIALIZATION
# ============================================================================
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
_description_cache: Dict[str, Tuple[str, str]] = {}



async def get_current_user(request: Request) -> dict:
    result = get_person_details(request)
    return result["data"]


# ============================================================================
# 7. PYDANTIC MODELS
# ============================================================================
class PreRPAData(BaseModel):
    PO_Number: Optional[str] = Field(None)
    sup_Invoice_Number: Optional[str] = Field(None)
    INVOICE_DATE: Optional[str] = Field(None)
    No: str = Field(...)
    Description: str = Field(...)
    Qty_Invoice: str = Field(...)
    Unit: Optional[str] = Field(None)
    Price_AED: str = Field(...)
    Amount_AED: str = Field(...)
    Discount_AED: Optional[str] = Field(None)
    Charges: Optional[str] = Field(None)
    Taxes_AED: Optional[str] = Field(None)
    Gross_Amount_AED: Optional[str] = Field(None)
    Accounting: Optional[str] = Field(None)
    Details: Optional[str] = Field(None)
    Tax_code: Optional[str] = Field(None)
    is_process: Optional[int] = Field(None)
    processed_date: Optional[str] = Field(None)
    user_id: Optional[str] = Field(None, min_length=1)
    pdf_id: Optional[str] = Field(None)
    base_64_string: Optional[str] = Field(None)
    model_config = ConfigDict(extra="allow")


class Item(BaseModel):
    Line_item: Optional[int] = None
    Item_description: Optional[str] = None
    QTY: Optional[float] = None
    Unit: Optional[str] = None


class DraftRequest(BaseModel):
    pdfId: Optional[str] = None
    poNumber: Optional[str] = None
    Po_Total: Optional[float] = None
    Invoice_Number: Optional[str] = None
    items: Optional[List[Item]] = None


class PDFRequest(BaseModel):
    pdf_id: str

class AribaCommentRequest(BaseModel):
    pdf_id: str
    Ariba_Comments: Optional[str] = None

class CustomerGraphRequest(BaseModel):
    page: int = 1
    page_size: int = 10
    Field: Optional[str] = None
    po_number: Optional[str] = None
    supplier: Optional[str] = None
    company_code: Optional[str] = None
    status: Optional[str] = None
    product_type_code: Optional[str] = None
    invoice_start_date: Optional[str] = None
    invoice_end_date: Optional[str] = None    
    process_start_date: Optional[str] = None  
    process_end_date: Optional[str] = None    
   # NEW: filter by created_at (file input) end



# ============================================================================
# 8. DATABASE HELPER FUNCTIONS
# ============================================================================
def get_db_connection():
    return pyodbc.connect(DB_CONNECTION_STRING)


def _to_pyodbc_named(sql: str, params: Optional[Dict[str, Any]] = None) -> Tuple[str, tuple]:
    """Convert :name placeholders into pyodbc-compatible positional ? placeholders.

    This keeps the SQL text readable and aligned with the team preference for
    named parameters while preserving compatibility with the SQL Server driver
    used by the project.
    """
    if params is None:
        return sql, ()
    if not isinstance(params, dict):
        return sql, tuple(params)

    ordered = []
    pattern = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

    def repl(match):
        name = match.group(1)
        if name not in params:
            raise KeyError(f"Missing named parameter '{name}' for SQL query")
        ordered.append(params[name])
        return "?"

    return pattern.sub(repl, sql), tuple(ordered)


def execute_named(cursor, sql: str, params: Optional[Dict[str, Any]] = None):
    converted_sql, converted_params = _to_pyodbc_named(sql, params)
    return cursor.execute(converted_sql, converted_params)


def esc(val):
    if val is None or str(val).strip() == "":
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def _first_nonzero_amount(*values):
    """Return the first value in `values` that is present and parses to a
    non-zero number - e.g. S4_Purchase_Order_Data.effective_amount takes
    priority over gross_amount (and gross_amount over net_amount) wherever a
    PO line's monetary amount is read, but effective_amount is frequently
    NULL/'0.00'/blank for older PO lines, in which case the next value in
    the list should be used instead. Falls back to the last value given
    (even if also zero/blank) so the caller still gets a value to parse."""
    for value in values[:-1]:
        if value in (None, ""):
            continue
        try:
            if float(str(value).replace(",", "")) != 0:
                return value
        except (TypeError, ValueError):
            continue
    return values[-1]


def generate_pdf_id() -> str:
    """Generate a new pdf_id for a manually-uploaded invoice (prefix 'M')."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT NEXT VALUE FOR pdf_id_seq")
        next_num = cursor.fetchone()[0]
        return f"M{next_num}"
    finally:
        cursor.close()
        conn.close()


def get_last_email_fetch_entry() -> Optional[dict]:
    """Most recent newyork_email_ocr row, or None if the table is empty."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT TOP 1 id, from_date, to_date, total_invoice, processed_count,
                   failed_count, created_at, process_status
            FROM newyork_email_ocr ORDER BY id DESC
        """)
        row = cursor.fetchone()
        if row:
            return dict(zip(
                ['id', 'from_date', 'to_date', 'total_invoice', 'processed_count',
                 'failed_count', 'created_at', 'process_status'], row
            ))
        return None
    finally:
        cursor.close()
        conn.close()


def get_last_successful_email_to_date():
    """to_date of the most recent fetch run that completed with process_status = '1' (success)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT TOP 1 to_date FROM newyork_email_ocr
            WHERE process_status = '1'
            ORDER BY to_date DESC
        """)
        row = cursor.fetchone()
        return row[0] if row and row[0] else None
    finally:
        cursor.close()
        conn.close()


def insert_email_fetch(from_dt, to_dt, total_invoice: int, processed_count: int,
                        failed_count: int, process_status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO newyork_email_ocr (from_date, to_date, total_invoice, processed_count, failed_count, process_status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            from_dt.strftime('%Y-%m-%d %H:%M:%S'),
            to_dt.strftime('%Y-%m-%d %H:%M:%S'),
            total_invoice, processed_count, failed_count, process_status
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to log email fetch: {e}")
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# 8.1 SFTP CONNECTION HELPERS (used by /process-sftp-folder-invoices)
# ============================================================================
def _ny_create_sftp_connection():
    """Open an SSH+SFTP connection to the invoice file storage server,
    mirroring ocr_main.py's _create_sftp_connection pattern."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=SFTP_HOST, port=22, username=SFTP_USER, password=SFTP_PASSWORD, timeout=30)
    sftp = ssh.open_sftp()
    return ssh, sftp


def clean_name(name):
    if not name:
        return ""
    # '&' and ',' must become word SEPARATORS, not just vanish - one side
    # (OCR extraction or the DB row) frequently omits the surrounding space
    # (e.g. "WINE&SPIRITS", "DIST,LLC") while the other has it spaced out
    # ("Wine & Spirits", "DIST, LLC"), so stripping them to "" the same way
    # as other punctuation would leave "WINESPIRITS"/"DISTLLC" on one side
    # and "WINE SPIRITS"/"DIST LLC" on the other and never match. A comma
    # always separates two distinct words/clauses, unlike a period inside an
    # abbreviation (e.g. "S.L.", "L.L.C."), which is still stripped to ""
    # below so it correctly collapses to a legal-suffix token like "SL" (see
    # _get_supplier_ids_by_cleaned_name).
    name = name.replace("&", " ").replace(",", " ")
    name = re.sub(r"[^A-Za-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip().upper()
def save_invoice_receipt_mapping(pdf_id: str, receipt_ids: List[str]) -> List[str]:
    """Store which RC_No values were matched for a given invoice pdf_id.
    Returns the RC_Nos actually claimed (skips ones already taken by another pdf_id)."""
    if not receipt_ids:
        return []
    conn = get_db_connection()
    cursor = conn.cursor()
    claimed = []
    try:
        for rc_no in receipt_ids:
            try:
                cursor.execute(
                    "INSERT INTO Invoice_Receipt_Mapping (pdf_id, RC_No) VALUES (?, ?)",
                    (pdf_id, rc_no)
                )
                conn.commit()
                claimed.append(rc_no)
            except pyodbc.IntegrityError:
                conn.rollback()
                logging.warning(f"âš ï¸ RC={rc_no} already claimed by another invoice â€” skipping for pdf_id={pdf_id}")
        logging.info(f"Saved {len(claimed)} RC_No(s) for pdf_id={pdf_id}: {claimed}")
        return claimed
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to save Invoice_Receipt_Mapping for pdf_id={pdf_id}: {e}")
        return claimed
    finally:
        cursor.close()
        conn.close()

def get_used_receipt_ids() -> set:
    """RC_No values already consumed by previously matched invoices."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DISTINCT RC_No FROM Invoice_Receipt_Mapping")
        return {str(row[0]) for row in cursor.fetchall() if row[0]}
    except Exception as e:
        logging.error(f"Failed to fetch used receipt ids: {e}")
        return set()
    finally:
        cursor.close()
        conn.close()  

def _is_already_created_message(message: str) -> bool:
    if not message:
        return False
    m = message.lower()
    return "already created" in m or "already exists" in m


# ============================================================================
# 9. FIELD NORMALIZATION MAPS (used to normalize NEWYORK_PROMPT extraction output)
# ============================================================================
HEADER_MAP = {
    "SupplierAddress": "Supplier_address",
    "CustomerAddress": "Customer_address",
    "Deliver To": "Customer_address",
    "Delivery Address": "Customer_address",
    "Supplier Address": "Supplier_address",
    "Head Office": "Supplier_address",
    "Head Office Dubai": "Supplier_address",
    "SupplierEmail": "Email",
    "DocumentDate": "InvoiceDate",
    "Date": "InvoiceDate",
    "InvoiceTotal": "Invoice_Total",
    "TotalAmount": "Invoice_Total",
    "GrandTotal": "Invoice_Total",
    "AmountDue": "Invoice_Total",
    "TotalPayable": "Invoice_Total",
    "TotalAmountDue": "Invoice_Total",
    "GrossTotal": "Invoice_Total",
    "GrandTotalAmount": "Invoice_Total",
    "NetTotal": "Invoice_Total",
    "VATTotal": "Invoice_Total",
    "TotalExcludingVAT": "Invoice_Total",
    "TotalVATAmount": "Invoice_Total",
    "TotalIncludingVAT": "Invoice_Total",
    "TotalGrossAmount": "Invoice_Total",
    "NetToPay": "Invoice_Total",
    "TotalAmountIncludingTax": "Invoice_Total",
    "BalanceDue": "Invoice_Total",
    "GrandAmountPayable": "Invoice_Total",
    "TotalBeforeDiscount": "Invoice_Total",
    "NetTotalAED": "Invoice_Total",
    "TotalInclVAT": "Invoice_Total",
    "Total incl VAT": "Invoice_Total",
    "GrandTotalwithVAT": "Invoice_Total",
    "TotalAmountIncVAT": "Invoice_Total",
    "GrandTotalWithVAT": "Invoice_Total",
    "TotalTaxAmount": "Invoice_Total",
    "TotalInclVat": "Invoice_Total",
    "TotalNetAmount": "Invoice_Total",
    "TotalAmountIncludingVAT": "Invoice_Total",
    "TotalIncludingTax": "Invoice_Total",
    "BALANCE": "Invoice_Total",
    "TotalWithTax": "Invoice_Total",
    "AccountName": "BankAccountName",
    "BankAccountNumber": "AccountNumber",
    "TaxInvoice": "Tax_invoice",
    "DiscountAmount": "Discount",
    "DiscountTotal": "Discount",
    "InvoiceNo": "InvoiceNumber",
    "TaxInvoiceNo": "InvoiceNumber",
    "InvoiceNumber": "InvoiceNumber",
    "DocumentNumber": "InvoiceNumber",
    "Stamp": "Stamp",
    "StampDate": "StampDate",
    "ReceivingStampDate": "StampDate",
    "Stamp_Date": "StampDate",
    "RefNo": "PONumber",
    "PONumber": "PONumber"
}

ITEM_MAP = {
    "Description": "ItemDescription",
    "Quantity": "QTY",
    "Qty": "QTY",
    "QTY": "QTY",
    "QTY.": "QTY",
    "Q'ty": "QTY",
    "QTYOrdered": "QTY",
    "QtyOrdered": "QTY",
    "OrderedQty": "QTY",
    "OrderQty": "QTY",
    "DeliveredQty": "QTY",
    "DeliveryQty": "QTY",
    "UOM": "Unit",
    "Uom": "Unit",
    "UnitOfMeasure": "Unit",
    "Price": "UnitPrice",
    "Value": "Amount",
    "TotalNetAmount": "Amount",
    "LineExtensionAmount": "Amount",
    "NetAmount": "Amount",
    "LineAmount": "Amount",
    "VATRate": "VAT",
    "RateOfVAT": "VAT",
    "TaxRate": "VAT",
    "VATPercentage": "VAT",
    "VATAmountLineItem": "VATAmount",
    "TotalVAT": "VATAmount",
    "LineTotal": "AmountInclVAT",
    "TotalAmount": "AmountInclVAT",
    "TotalAmountInclVAT": "AmountInclVAT",
    "ItemTotalExcludingVAT": "Amount",
    "ItemVATAmount": "VATAmount",
    "ItemTotalIncludingVAT": "AmountInclVAT",
    "ItemCode": "ItemCode",
    "TaxAmount": "VATAmount",
    "LineItemVATAmount": "VATAmount",
    "Price_AED": "UnitPrice",
    "Price(AED)": "UnitPrice",
    "Quantity_Cases_MT_KG_GRAM": "QTY",
    "Quantity_PCS": "QTY",
    "Amount Before Tax": "Amount",
    "AmountBeforeTax": "Amount",
    "Pack": "Unit",
    "GrossTotal": "AmountInclVAT",
    "ItemTotal": "Amount",
    "ItemVATRate": "VAT",
    "ItemTotalWithVAT": "AmountInclVAT",
    "VATAmount": "VATAmount",
    "TotalLineAmount": "AmountInclVAT",
    "GrossAmount": "AmountInclVAT"
}

# ============================================================================
# 9.1 GEMINI OCR PROMPT
# ============================================================================
# This is the only extraction prompt used by process_file_with_gemini; its output
# is normalized via HEADER_MAP/ITEM_MAP above.
NEWYORK_PROMPT = """Extract invoice data as JSON from US (New York) PDF invoices. Return ONLY valid JSON.

HEADER FIELDS (global for the entire invoice):
SupplierName, SupplierAddress, SupplierTRN, Email,
CustomerName, CustomerAddress, CustomerPhone, CustomerTRN,
InvoiceNumber, InvoiceReference, InvoiceDate,
SONo, SalesPerson,
TaxInvoice, Stamp, StampDate, InvoiceTotal
Bank Details: BankAccountName, BankName, BankBranch, AccountNumber, IBAN, SwiftCode

InvoiceTotal is the single bottom-line grand total for the ENTIRE document ("Total Due",
"Balance Due", "Amount Due", "Total"), regardless of how many PO groups the invoice contains.
It is independent of, and must not be confused with, the per-PO-group "Total" described below
under LINE ITEMS.

LINE ITEMS (per PO group):
Most US invoices bill against a single Purchase Order, but some combine several POs onto one
invoice (e.g. a monthly statement-style bill). Group line items by PO exactly like this:

PONumber - the PO reference for that group (labels: "PO Number", "P.O. #", "Purchase Order",
    "Customer PO", "PO No.")

GroupDate - the ship date / delivery date printed on THIS group's packing-slip or delivery-
    ticket line, if the invoice prints one per PO group (labels: "Ship Date", "Date Shipped",
    "Delivery Date", "Packing Slip Date"). This is the goods-movement date, which can differ
    from the header InvoiceDate (billing date). Format dd-mm-yyyy, same conversion rules as
    InvoiceDate below. Leave null if the group has no date of its own.

Total - the subtotal for THIS PO group only (its own line total before tax/shipping), NOT the
    document-wide grand total. The document-wide grand total always goes in the header-level
    InvoiceTotal field, never here.

TaxAmount - sales tax for that group (most US invoices apply one sales-tax rate at the
    invoice/header level rather than per line - if there is only one tax figure on the whole
    document, assign it to the first/only group and leave InvoiceTotal as the document total)

Discount - discount for that group

Freight - shipping/freight charges for that group

LineItems - array of individual items with:

LineItemNumber, ItemCode, ItemDescription, QTY, Unit, UnitPrice, Amount, VAT, VATAmount, AmountInclVAT

(US invoices rarely show a per-line VAT/VAT% - VAT, VATAmount, and AmountInclVAT will usually be
null for US invoices; do not invent a value for them. Sales tax, if shown, goes in the group's
TaxAmount field described above, not on individual lines.)

EXTRACTION RULES

1. Customer Priority
Priority 1: "Ship To" address
Priority 2: "Bill To" / "Sold To" address
Priority 3: any other customer/company block on the invoice

2. SupplierName Extraction
Extract the registered legal company name (e.g. "Acme Foods, Inc.", "Empire Produce, LLC"),
which is normally printed in the header near the company's Tax ID/EIN, or in fine print near a
"Remit To" block. Do NOT extract a logo's brand name or marketing slogan instead - e.g. if a
logo reads "ACME - QUALITY YOU CAN TASTE", extract only "Acme Foods, Inc." from the actual
company-name text, never append or substitute the slogan. The legal name usually includes a
company-type suffix like Inc., LLC, Corp., or Co. when the invoice shows one - keep that suffix,
do not drop it.

3. PONumber Extraction - MULTIPLE PO SUPPORT
Important: This invoice may contain one or more Purchase Order numbers. You must:

Identify all distinct PO numbers appearing on the document.

Packing-slip, BOL (Bill of Lading), delivery-ticket, and order-confirmation references (e.g.
"Packing Slip #4471", "BOL# 88213", "Delivery Ticket 5502") are NOT PO numbers, even when they
sit right next to the line items and group them. Never copy one of these into PONumber. If a
group's own PO field is blank, absent, or only shows one of these references, set that group's
PONumber to null instead of substituting it.

If line items have a PO column, use that to assign each line item to its corresponding PO.

If no PO column exists but there is a single header PO that applies to all items, use that PO
for all items.

If multiple POs appear in the header, treat them as separate groups - create one group per PO
in the output.

Extraction guidelines (no prefix restrictions):

Do not assume any specific numeric prefix. Extract the PO number exactly as it appears; internal
matching will normalize/validate its shape later.

Look for labels: "PO Number", "P.O. #", "Purchase Order", "Customer PO", "PO No.", "Order No."

If a PO number is crossed out and a handwritten correction exists, use the handwritten value.

Handling multiple POs - grouping:
The output "Items" array must have one object per distinct PO group. Each object contains
"PONumber" and its associated line items. If a line item has no PO assigned, assign it to the
first PO found, or to a null PONumber if no PO exists at all.

Examples:
Invoice has a single PO "PO-2026-001" and 5 line items -> one group with PONumber = "PO-2026-001".
Invoice has two POs "PO-123" and "PO-456"; line items 1-3 belong to PO-123, items 4-6 to PO-456
-> two groups.

4. InvoiceDate Formatting
MUST be returned in dd-mm-yyyy format ONLY (2-digit day, 2-digit month, 4-digit year, separated
by hyphens) - this matches the rest of the pipeline.

US invoices print dates MONTH-FIRST (MM/DD/YYYY) - do not misread US dates as day-first.
"03/07/2026" on a US invoice means March 7, 2026, and must be converted to "07-03-2026", NOT
"03-07-2026".

If a date is written out ("March 7, 2026" or "Mar 7, 2026"), convert the same way.

If a 2-digit year, expand to 4 digits (assume 20YY unless clearly 19YY).

Examples:
March 7, 2026 -> 07-03-2026
03/07/2026 -> 07-03-2026
2026-03-07 -> 07-03-2026 (ISO dates are already unambiguous - just reformat)

5. TaxInvoice Detection
Set to "True" if the document clearly states it is a tax invoice or shows a Sales Tax /
Use Tax breakdown. Otherwise "False".

6. Total Extraction
Always extract the document-wide grand total (after tax and shipping) from the bottom of the
invoice into the header-level InvoiceTotal field. Look for "Total Due", "Balance Due",
"Amount Due", "Total". This is a SINGLE value per document, always populated.

Separately, if an invoice has multiple PO groups and each block prints its own subtotal, extract
that subtotal into the "Total" field of that specific group only - do NOT copy the document-wide
grand total into every group, and do NOT sum/derive group totals from InvoiceTotal. If a group
has no printed subtotal of its own, leave that group's "Total" null.

7. Discount, Freight, TaxAmount
Extract discount, freight/shipping, and sales tax. If they are per group, assign accordingly;
else assign to the first group.

8. InvoiceNumber Exception
If a standard "Invoice #" is missing, look for "Invoice No.", "Document Number", or a packing-
slip/order number used as the bill reference, and use that.
Quotes/bids/reservations (e.g. rental companies such as Steeldeck) have no "Invoice #" - the
document identifier is printed as "Bid#", "Quote#", "Estimate#" or "Ticket#" (also "Res#"/
"Reservation#" on reservation tickets). When several are printed, prefer in this order: Invoice #,
Bid#, Quote#, Estimate#, Ticket#, Res#. InvoiceNumber must NEVER be null if any such number is on
the document. Return only the number itself, without the label (e.g. "82565", not "Bid# 82565").

9. Handwritten/Crossed-out Rules
Handwritten corrections override printed values. Crossed-out values are ignored; look for a
nearby handwritten replacement. This applies to PONumber, InvoiceNumber, and any numeric field.

10. Bank Details Extraction
Look for: Bank Name, Account Name, Account Number, Routing Number (map to AccountNumber if no
separate IBAN/SwiftCode field exists), IBAN, SWIFT/BIC. Remove spaces/dashes from account
numbers.

11. Stamp Detection
If you see a rectangular receiving stamp with words like "Received", "Verified", or a company
stamp, set Stamp = "True" and extract any date from the stamp in YYYY-MM-DD format. If no stamp,
set Stamp = "False", StampDate = null.

12. US NUMERIC FORMAT: All monetary and quantity values (QTY, UnitPrice, Amount, VAT, VATAmount,
AmountInclVAT, Total, InvoiceTotal, Discount, Freight, TaxAmount) must be formatted using
standard US notation (comma "," as thousands separator, period "." as decimal separator) -
e.g. output "1,234.56" exactly as printed. These values must be returned as strings. Remove
currency symbols ($, etc.) and trailing spaces, but do NOT swap commas and periods - US notation
already matches the rest of the pipeline.

12b. UnitPrice vs Amount consistency
UnitPrice is always the printed per-unit price copied as-is - never Amount divided by QTY.
Amount is the line's own printed extended price, copied as-is too - never QTY times UnitPrice if
a different value is actually printed. Extract each independently from what is printed; do not
derive one field from the other.

13. Line Item Numbering
Use the printed line/item number if present; otherwise auto-assign sequential numbers
(1, 2, 3...) within each PO group.

14. Output Structure
The JSON has a top-level HeaderFields object (global invoice data).

Then an "Items" array, where each element represents one PO group and contains:
"PONumber": the PO number for that group
"GroupDate": the ship/delivery date printed on this group's packing-slip block (dd-mm-yyyy), or null
"Total": this PO group's own subtotal only (null if the document doesn't print one per group)
"TaxAmount": group sales tax
"Discount": group discount
"Freight": group freight/shipping
"LineItems": array of line items for that PO

If there is only one PO -> the Items array will have one element.
If there are multiple POs -> create one element per PO, each with its own line items."""


def _clean_supplier_search_prefix(supplier_name: Optional[str]) -> Optional[str]:
    """
    Strip trailing "LLC"/"L.L.C." variants and stray periods after single
    capital letters (mirrors supplier-name cleanup used elsewhere in this
    file, e.g. clean_name()), then return the first 3 words to use as a
    LIKE-prefix search term. Shared by both PO-fallback tiers so they agree
    on what counts as "the same supplier".
    """
    if not supplier_name:
        return None
    clean_supplier = re.sub(
        r'\s*\(?\s*L\s*\.?\s*L\s*\.?\s*C?\s*\.?\s*\)?\.?\s*$',
        '', str(supplier_name), flags=re.IGNORECASE
    )
    clean_supplier = re.sub(r'(?<=[A-Z])\.(?=\s)', '', clean_supplier).strip()
    if not clean_supplier:
        return None
    return " ".join(clean_supplier.split(" ")[:3])


def _normalize_description_for_match(desc: Optional[str]) -> str:
    """Lowercase, strip punctuation/units noise so descriptions from two
    different systems (supplier's invoice wording vs our internal PO
    wording) compare fairly, e.g. 'Clear Plastic Sign Inserts, 24x36"'
    vs 'CLEAR PLASTIC SIGN INSERTS - 24 X 36"'."""
    if not desc:
        return ""
    s = str(desc).lower()
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _description_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = _normalize_description_for_match(a), _normalize_description_for_match(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _get_supplier_ids_by_name(cursor, search_prefix: str) -> List[str]:
    """
    Resolve a supplier_name prefix to supplier_id(s) via S4_Suppliers.

    Both PO-fallback tiers used to LIKE-match supplier_name directly in
    S4_Purchase_Order_Data. Instead, look the name up in S4_Suppliers first
    and filter S4_Purchase_Order_Data by its supplier column (the FK to
    S4_Suppliers.supplier_id), which is the correct join key. Can return
    more than one supplier_id if the prefix matches multiple suppliers -
    callers pass all of them into an IN (...) filter.
    """
    execute_named(
        cursor,
        """
        SELECT DISTINCT supplier_id
        FROM [final_pulse_DBprod].[dbo].[S4_Suppliers_prod]
        WHERE supplier_name LIKE :search_prefix OR supplier_name LIKE :search_prefix_upper
        """,
        {
            "search_prefix": f"{search_prefix}%",
            "search_prefix_upper": f"{search_prefix.upper()}%",
        },
    )
    return [str(r[0]) for r in cursor.fetchall() if r[0] is not None]


def find_candidate_pos_by_line_items(
    supplier_name: Optional[str],
    items: List[dict],
    price_tolerance: float = 0.01,
    min_match_fraction: float = 0.5,
) -> Tuple[List[str], dict]:
    """
    Second-tier PO fallback, used when find_candidate_pos_by_supplier
    (receipt-based) returns no candidates - e.g. because no S4_GRN_Data
    row exists yet for the PO.

    In practice, an invoice line's description almost never matches our
    internal S4_Purchase_Order_Data description word-for-word (different
    wording, casing, unit formatting - e.g. supplier writes
    'CLEAR PLASTIC SIGN INSERTS - 24 X 36"', our PO line says
    'Clear Plastic Sign Inserts, 24x36"'), and quantities can differ too
    (partial shipments, backorders). unit_price, however, is a negotiated
    number that both sides record identically, so it's the reliable key.
    Description similarity is used only as a confidence signal /
    tie-breaker on top of the price match, never as a hard filter on its
    own.

    Algorithm:
      1. Pull every active PO line for the supplier (prefix match on
         cleaned supplier name, same cleanup as tier 1).
      2. For each invoice line with a parseable unit price, find PO lines
         from that pool within `price_tolerance` of it.
      3. Group matches by purchase_order_id. A PO's score = (# distinct
         invoice lines it has a price match for) + (avg description
         similarity of those matches, as a fractional tie-breaker so it
         only matters between POs tied on price-match count).
      4. Keep POs that matched at least `min_match_fraction` of the
         invoice's priced line items. Return the PO(s) with the top score;
         more than one means a genuine tie (ambiguous - caller decides).

    Returns (candidate_po_numbers, debug_info).
    """
    search_prefix = _clean_supplier_search_prefix(supplier_name)
    if not search_prefix:
        return [], {"reason": "supplier_name empty after cleanup"}

    # Pull (description, unit_price) for each invoice line we can price-match on.
    priced_items = []
    for it in items or []:
        desc = it.get("ItemDescription") or it.get("Item_description") or it.get("description")
        price_raw = it.get("UnitPrice") or it.get("Price_AED") or it.get("unit_price")
        if price_raw is None:
            continue
        try:
            price_val = float(str(price_raw).replace(",", ""))
        except (ValueError, TypeError):
            continue
        priced_items.append({"description": desc, "unit_price": price_val})

    if not priced_items:
        return [], {"reason": "no invoice line items had a parseable unit_price"}

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        supplier_ids = _get_supplier_ids_by_name(cursor, search_prefix)
        if not supplier_ids:
            return [], {
                "supplier_search_prefix": search_prefix,
                "priced_item_count": len(priced_items),
                "candidate_count": 0,
                "reason": "no matching supplier_id found in S4_Suppliers",
            }
        placeholders = ",".join("?" for _ in supplier_ids)
        cursor.execute(
            f"""
            SELECT purchase_order, purchase_order_item_text, unit_price
            FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
            WHERE supplier IN ({placeholders})
              AND purchase_order IS NOT NULL
            """,
            supplier_ids,
        )
        po_lines = cursor.fetchall()
    except Exception as e:
        logging.error(f"find_candidate_pos_by_line_items query failed: {e}")
        return [], {"reason": f"query failed: {e}"}
    finally:
        cursor.close()
        conn.close()

    if not po_lines:
        return [], {
            "supplier_search_prefix": search_prefix,
            "priced_item_count": len(priced_items),
            "candidate_count": 0,
            "reason": "no PO lines found for supplier",
        }

    # po_id -> {"matched_lines": set(invoice-line-index), "similarity_sum": float}
    po_scores: Dict[str, Dict[str, Any]] = {}
    for po_id, po_desc, po_unit_price in po_lines:
        if po_unit_price is None:
            continue
        try:
            po_price_val = float(po_unit_price)
        except (ValueError, TypeError):
            continue

        for idx, inv_item in enumerate(priced_items):
            if abs(po_price_val - inv_item["unit_price"]) > price_tolerance:
                continue
            sim = _description_similarity(inv_item["description"], po_desc)
            entry = po_scores.setdefault(str(po_id), {"matched_lines": {}, "best_sim": {}})
            # Keep the best similarity seen for this invoice line against this PO
            if idx not in entry["best_sim"] or sim > entry["best_sim"][idx]:
                entry["best_sim"][idx] = sim

    if not po_scores:
        return [], {
            "supplier_search_prefix": search_prefix,
            "priced_item_count": len(priced_items),
            "po_line_count": len(po_lines),
            "candidate_count": 0,
            "reason": "no PO line prices matched any invoice line unit_price",
        }

    min_matches_needed = max(1, round(len(priced_items) * min_match_fraction))
    ranked = []
    for po_id, entry in po_scores.items():
        match_count = len(entry["best_sim"])
        if match_count < min_matches_needed:
            continue
        avg_sim = sum(entry["best_sim"].values()) / match_count
        # Primary sort: how many invoice lines this PO explains by price.
        # Secondary sort: average description similarity, as a tie-breaker.
        ranked.append((match_count, avg_sim, po_id))

    if not ranked:
        return [], {
            "supplier_search_prefix": search_prefix,
            "priced_item_count": len(priced_items),
            "po_line_count": len(po_lines),
            "candidate_count": 0,
            "reason": f"no PO cleared the {min_matches_needed}/{len(priced_items)} price-match threshold",
        }

    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top_match_count, top_avg_sim = ranked[0][0], ranked[0][1]
    top_candidates = [
        po_id for match_count, avg_sim, po_id in ranked
        if match_count == top_match_count and avg_sim == top_avg_sim
    ]

    return top_candidates, {
        "supplier_search_prefix": search_prefix,
        "priced_item_count": len(priced_items),
        "po_line_count": len(po_lines),
        "candidate_count": len(top_candidates),
        "top_match_count": top_match_count,
        "top_avg_description_similarity": round(top_avg_sim, 3),
        "min_matches_needed": min_matches_needed,
    }


def find_candidate_pos_by_supplier(
    supplier_name: Optional[str],
    invoice_date_str: Optional[str],
    total: Any,
    line_item_count: int,
) -> Tuple[List[str], dict]:
    """
    Fallback PO finder used when OCR found no PO number on the invoice
    (PONumbers came back empty from process_file_with_gemini).

    Matches S4_Purchase_Order_Data rows by supplier name, then narrows down
    using S4_GRN_Data grouped by (purchase_order, material_document) whose:
      - distinct receipt-line count matches the invoice's line item count
      - receipt date is on or before the invoice date

    NOTE: S4_GRN_Data has no monetary column (unlike the legacy AribaReceipts.
    GrossAmountAccepted this function originally matched against), so the
    invoice-total match this function used to do is NOT possible with the
    current schema and has been dropped. `total` is accepted for interface
    compatibility and echoed back in the debug info, but no longer filters
    anything - prefer find_po_for_group_by_description_and_price for real PO
    resolution, which does have a reliable price match.

    Queries final_pulse_DBprod.dbo (same DB/schema as the rest of this router).

    Returns (candidate_po_numbers, debug_info). candidate_po_numbers is
    empty if inputs are missing/unparseable or no PO satisfies the
    conditions; it can contain more than one PO number if the match is
    ambiguous (caller decides what to do with an ambiguous result).
    """
    if not supplier_name or not invoice_date_str or not total or not line_item_count:
        return [], {"reason": "missing supplier_name/invoice_date/total/line_item_count"}

    try:
        invoice_date = datetime.strptime(str(invoice_date_str).strip(), "%d-%m-%Y").date()
    except ValueError:
        return [], {"reason": f"unparseable invoice date: {invoice_date_str}"}

    try:
        total_val = float(str(total).replace(",", ""))
    except (ValueError, TypeError):
        return [], {"reason": f"unparseable total: {total}"}

    search_prefix = _clean_supplier_search_prefix(supplier_name)
    if not search_prefix:
        return [], {"reason": "supplier_name empty after cleanup"}

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        supplier_ids = _get_supplier_ids_by_name(cursor, search_prefix)
        if not supplier_ids:
            return [], {
                "supplier_search_prefix": search_prefix,
                "reason": "no matching supplier_id found in S4_Suppliers",
            }
        in_clause = ", ".join(f":supplier_id_{idx}" for idx in range(len(supplier_ids)))
        query = f"""
            WITH results AS (
                SELECT
                    g.purchase_order AS ponumber,
                    g.material_document,
                    CAST(MAX(g.posting_date) AS date) AS RecentReceiveDate,
                    COUNT(DISTINCT g.material_document_item) AS rc_row_count
                FROM [final_pulse_DBprod].[dbo].[S4_GRN_Data] g
                WHERE g.purchase_order IN (
                    SELECT purchase_order
                    FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
                    WHERE supplier IN ({in_clause})
                )
                GROUP BY g.purchase_order, g.material_document
            )
            SELECT DISTINCT ponumber
            FROM results
            WHERE rc_row_count = :line_item_count
              AND RecentReceiveDate <= :invoice_date
        """
        params = {f"supplier_id_{idx}": sid for idx, sid in enumerate(supplier_ids)}
        params.update({
            "line_item_count": line_item_count,
            "invoice_date": invoice_date,
        })
        execute_named(cursor, query, params)
        rows = cursor.fetchall()
        candidates = [str(r[0]) for r in rows if r[0]]
        return candidates, {
            "supplier_search_prefix": search_prefix,
            "supplier_id_count": len(supplier_ids),
            "total_used": total_val,
            "line_item_count": line_item_count,
            "invoice_date": str(invoice_date),
            "candidate_count": len(candidates),
        }
    except Exception as e:
        logging.error(f"find_candidate_pos_by_supplier failed: {e}")
        return [], {"reason": f"query failed: {e}"}
    finally:
        cursor.close()
        conn.close()

def normalize_po_number(po_str: Any) -> Optional[str]:
    if not po_str:
        return None
    # Convert to string and strip spaces or trailing decimal noise
    clean_po = str(po_str).strip().split('.')[0]
    # Remove any non-numeric characters picked up by OCR
    clean_po = re.sub(r'\D', '', clean_po)
    
    length = len(clean_po)
    if length == 10:
        return clean_po
    elif length == 6:
        return f"4500{clean_po}"
    elif length == 7:
        return f"450{clean_po}"
        
    return clean_po if clean_po else None


def _normalize_line_item_number(value, fallback_index: int) -> str:
    """
    Newyork_Invoice_extracted_data.LineItemNumber is an INT column, but OCR sometimes
    extracts non-numeric line labels (e.g. 'A', 'B', 'Item 3'). Strip to digits
    if any are present; otherwise fall back to the row's sequential position
    so the INSERT never fails with a varchar->int conversion error.
    """
    if value is None:
        return str(fallback_index)
    s = str(value).strip()
    if not s:
        return str(fallback_index)
    digits = re.sub(r'\D', '', s)
    return digits if digits else str(fallback_index)

def _normalize_grn_po_line(raw: Any) -> Optional[str]:
    """
    S4_GRN_Data.purchase_order_item keeps the raw SAP PO line number (10, 20,
    30, ...), while S4_Purchase_Order_Data.purchase_order_item and the
    invoice's own LineItemNumber use the /10-normalized form (1, 2, 3, ...) -
    the same normalization s4_data_apis.po_normalize_line_item applies when PO
    data is imported. Apply it here too so a GRN row can be joined to the
    correct invoice line instead of just "any" GRN row for the whole PO.
    """
    if raw in (None, ""):
        return None
    try:
        num = int(str(raw).strip())
    except (TypeError, ValueError):
        return str(raw).strip()
    return str(num // 10) if num != 0 and num % 10 == 0 else str(num)


def normalize(data, mapping):
    return {mapping.get(k, k): v for k, v in data.items()}


# ============================================================================
# 9.2 NEW YORK OUTPUT NORMALIZATION HELPERS
# ============================================================================
def _ny_clean_number(val):
    """
    US invoices already use standard '.' = decimal, ',' = thousands notation -
    the OPPOSITE convention from Spain's comma-decimal invoices. Unlike Spain's
    number cleaner, this never swaps '.' and ',' - it only strips currency
    symbols and thousands-separator commas, e.g. "$1,234.56" -> "1234.56".
    Leaves non-numeric / already-standard values untouched.
    """
    if val is None:
        return val
    s = str(val).strip()
    if not s:
        return val
    s = re.sub(r"[$]", "", s).strip()
    s = s.replace(",", "")
    return s


def _normalize_po_number(po_str: Any) -> Optional[str]:
    """Preserve the meaningful PO reference and remove formatting noise (dashes, slashes, spaces)."""
    if not po_str:
        return None
    s = str(po_str).strip()
    s = re.sub(r"[-/\s]+", "", s) if re.fullmatch(r"[\d\-/\s]+", s) else re.sub(r"\s+", " ", s).strip()
    return s if s else None


def _is_plausible_po_number(po_str: Optional[str]) -> bool:
    """
    Real S4 PO references are always 10 digits starting with '45' (e.g. 4500003324).
    Gemini sometimes mis-extracts a group's packing-slip/BOL/delivery-ticket
    reference (e.g. 'BOL# 88213') as PONumber when the invoice has no actual PO
    number. Reject anything that doesn't match the real PO shape so the caller
    nulls it out and lets the DB fallback (find_po_for_group_by_description_and_price)
    run instead of trusting a wrong extraction.
    """
    if not po_str:
        return False
    digits = re.sub(r"\D", "", str(po_str))
    return len(digits) == 10 and digits.startswith("45")


_MIN_SUPPLIER_MATCH_RATIO = 0.5


def _resolve_supplier_id_and_name(cursor, supplier_name: Optional[str]) -> Optional[Tuple[str, str]]:
    """
    Resolve a supplier_name to the single best-matching (supplier_id,
    canonical_supplier_name) pair in S4_Suppliers_prod, comparing cleaned
    (special-characters-stripped) names so OCR punctuation/wording noise -
    e.g. "Airbnb Payments, Inc." from the invoice vs the DB's plain
    "Airbnb", or "ANTONIO GUASCH, S.L." vs stored "ANTONIO GUASCH S.L" -
    doesn't break the match. canonical_supplier_name is the RAW
    S4_Suppliers_prod.supplier_name value (not the cleaned/uppercased
    version), meant to replace the invoice's own extracted SupplierName once
    resolved, so everything downstream keys off the same identity SAP uses.

    First narrows to candidates whose cleaned name STARTS WITH THE SAME
    FIRST WORD as the target (e.g. target "AG Acquisition 1 LLC" only
    considers candidates starting with "AG" - "AG Acquisition",
    "AG Production Services, Inc.", "AG Security Group Inc." - never
    "AGDP Holding Inc." or "VierD AG", whose first word isn't "AG"), then
    scores only that narrowed set on the FULL cleaned name: exact match
    scores highest, one name being a leading-word prefix of the other
    scores next (OCR/LLM extraction frequently drops or adds a trailing
    legal suffix like SL/S.L./SA/SLU/Inc./LLC, or extra wording like
    "Payments"), and anything else falls back to a plain string-similarity
    ratio, only accepted above _MIN_SUPPLIER_MATCH_RATIO.

    The first-word narrowing is what keeps two unrelated companies that
    only share a common industry word (e.g. "Frick Engineering" vs
    "H & O Engineering DPC" - different first words "FRICK" vs "H") from
    ever being scored against each other at all - scoring the full name
    across every supplier in the table let a coincidentally-high
    character-similarity ratio (0.6+, purely from sharing "ENGINEERING")
    win by default even though the companies are unrelated.

    An earlier version of this first-word filter ran the LIKE clause in SQL
    against the RAW, uncleaned supplier_name column, which broke whenever
    clean_name() collapsed punctuation INSIDE the first word with no
    adjacent space (e.g. "The.Team: SME Music LLC" cleans to "THETEAM SME
    MUSIC LLC", but that cleaned "THETEAM" fragment never matches the raw
    column, which still literally contains "The.Team" with the period).
    This version fetches and cleans the whole table first (same as before),
    then filters on cleaned-vs-cleaned first words in Python, so that
    mismatch can't happen.

    Returns None if no candidate shares the target's first word, or none
    of the same-first-word candidates clears _MIN_SUPPLIER_MATCH_RATIO.
    """
    cleaned_target = clean_name(supplier_name)
    if not cleaned_target:
        return None
    target_first_word = cleaned_target.split(" ", 1)[0]

    cursor.execute(
        """
        SELECT supplier_id, supplier_name
        FROM [final_pulse_DBprod].[dbo].[S4_Suppliers_prod]
        """
    )
    all_suppliers = [
        (str(sid), name, clean_name(name))
        for sid, name in cursor.fetchall()
        if sid is not None and clean_name(name)
    ]
    candidates = [
        (sid, raw_name, cleaned_name)
        for sid, raw_name, cleaned_name in all_suppliers
        if cleaned_name.split(" ", 1)[0] == target_first_word
    ]
    if not candidates:
        logging.info(
            f"[Supplier match] target={cleaned_target!r} -> no S4_Suppliers_prod row starts "
            f"with first word {target_first_word!r} among {len(all_suppliers)} supplier(s) - no match"
        )
        return None

    def _score(cleaned_name: str) -> float:
        if cleaned_name == cleaned_target:
            return 2.0
        if cleaned_name.startswith(cleaned_target + " ") or cleaned_target.startswith(cleaned_name + " "):
            return 1.5
        return difflib.SequenceMatcher(None, cleaned_target, cleaned_name).ratio()

    scored = sorted(
        ((sid, raw_name, _score(cleaned_name)) for sid, raw_name, cleaned_name in candidates),
        key=lambda c: c[2],
        reverse=True,
    )
    best_sid, best_raw_name, best_score = scored[0]
    if best_score < _MIN_SUPPLIER_MATCH_RATIO:
        logging.info(
            f"[Supplier match] target={cleaned_target!r} -> best same-first-word candidate "
            f"{best_raw_name!r} (score={best_score:.2f}) below minimum {_MIN_SUPPLIER_MATCH_RATIO} - no match"
        )
        return None
    logging.info(
        f"[Supplier match] target={cleaned_target!r} -> best match {best_raw_name!r} "
        f"(supplier_id={best_sid}, score={best_score:.2f}) among {len(candidates)} same-first-word candidate(s)"
    )
    return best_sid, best_raw_name


def _get_supplier_ids_by_cleaned_name(cursor, supplier_name: Optional[str]) -> List[str]:
    """Back-compat wrapper around _resolve_supplier_id_and_name for callers
    that only need the supplier_id (as a single-element list, to plug into
    an existing IN (...) filter) - see that function for the matching
    algorithm."""
    resolved = _resolve_supplier_id_and_name(cursor, supplier_name)
    return [resolved[0]] if resolved else []


def _resolve_supplier_alias(cursor, supplier_name: Optional[str]) -> Optional[str]:
    """
    Check the curated Newyork_Supplier_Aliases table for a known name
    mismatch BEFORE the normal S4_Suppliers_prod fuzzy match
    (_resolve_supplier_id_and_name) runs. Some invoices print only a
    subsidiary/underwriter's legal name in fine print (e.g. a
    UnitedHealthcare/Oxford insurance statement's "Underwritten by Oxford
    Health Insurance, Inc." disclaimer) while the actual billing/operating
    company recognized in SAP's supplier master is the parent brand
    ("UnitedHealthcare") - the two names share no common word at all, so no
    amount of fuzzy string matching against S4_Suppliers_prod can bridge
    that gap on its own. This table is a manually curated, exact mapping
    for cases like that as they're discovered.

    Matches the extracted name against each row's raw_supplier_name via
    clean_name() (so punctuation/case don't matter) - exact match, or the
    extracted name being a whole-word-prefix extension of it (e.g. extracted
    "Oxford Health Insurance, Inc. of NY" still matches a curated row for
    "Oxford Health Insurance, Inc.").

    Returns the matched row's canonical_supplier_name (which is then itself
    resolved against S4_Suppliers_prod as normal), or None if the table
    doesn't exist yet or nothing matches - never raises, so a missing/empty
    alias table just means no alias applies.
    """
    cleaned_target = clean_name(supplier_name)
    if not cleaned_target:
        return None
    try:
        cursor.execute("""
            SELECT raw_supplier_name, canonical_supplier_name
            FROM [final_pulse_DBprod].[dbo].[Newyork_Supplier_Aliases]
        """)
        rows = cursor.fetchall()
    except Exception as e:
        logging.warning(f"[Supplier alias] Lookup failed (has scripts/create_supplier_aliases_table.sql been run?): {e}")
        return None

    for raw_name, canonical_name in rows:
        cleaned_raw = clean_name(raw_name)
        if not cleaned_raw or not canonical_name:
            continue
        if cleaned_target == cleaned_raw or cleaned_target.startswith(cleaned_raw + " "):
            return canonical_name
    return None


def _resolve_supplier_name_variants(cursor, supplier_query: Optional[str]) -> List[str]:
    """
    Expand a supplier filter value (typed or picked from the deduplicated
    /distinct-suppliers/ list) into every raw SupplierName spelling stored in
    Newyork_FileInput that's the SAME supplier once clean_name() strips
    punctuation/case noise - e.g. picking "WME Entertainment LLC" should
    also match rows literally stored as "WME Entertainment, LLC", since
    different invoices from the same supplier were extracted before/without
    the SupplierName canonicalization fix and still carry the raw wording.

    Used to build a LIKE '%variant%' OR-list across every matching spelling,
    instead of a single LIKE against exactly what the caller passed in.
    Falls back to [supplier_query] itself (so the caller's existing
    LIKE '%...%' partial-search behavior is preserved) when nothing in the
    table shares the same cleaned name - e.g. a genuine free-text search for
    a supplier not typed exactly.
    """
    if not supplier_query:
        return [supplier_query]
    cleaned_target = clean_name(supplier_query)
    if not cleaned_target:
        return [supplier_query]
    cursor.execute("""
        SELECT DISTINCT SupplierName
        FROM [final_pulse_DBprod].[dbo].[Newyork_FileInput]
        WHERE SupplierName IS NOT NULL AND SupplierName != ''
    """)
    variants = [row[0] for row in cursor.fetchall() if row[0] and clean_name(row[0]) == cleaned_target]
    return variants or [supplier_query]


def _po_lookup_price_candidates(item: dict) -> List[str]:
    """
    OCR extraction of UnitPrice occasionally drifts (e.g. computing Amount / QTY
    instead of copying the printed per-unit price column), which makes the PO
    fallback price match miss even though the item is genuinely on file. Build
    an ordered list of unit-price values to try: the extracted UnitPrice first,
    then AmountInclVAT / QTY and Amount / QTY as reconstructed fallbacks, so a
    single bad UnitPrice extraction doesn't sink the whole PO lookup.
    """
    candidates = []
    unit_price = item.get("UnitPrice")
    if unit_price not in (None, ""):
        candidates.append(str(unit_price))

    try:
        qty_val = float(str(item.get("QTY")).replace(",", "").strip())
    except (TypeError, ValueError):
        qty_val = None

    if qty_val:
        for amount_field in ("AmountInclVAT", "Amount"):
            amount_raw = item.get(amount_field)
            if amount_raw in (None, ""):
                continue
            try:
                amount_val = float(str(amount_raw).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            candidates.append(f"{amount_val / qty_val:.2f}")

    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _parse_sap_date(value: Any):
    """
    Parse an S4 date-like column value into a plain `date` for comparison.
    S4_Purchase_Order_Data.purchase_order_date comes back from pyodbc as a
    plain string (e.g. '2026-07-31') rather than a real datetime/date object -
    unlike columns such as S4_GRN_Data.creation_date_time, which are true
    DATETIME2 columns - so a naive `date_value - some_date` blows up with
    "unsupported operand type(s) for -: 'str' and 'datetime.date'". Handles
    both cases (already a datetime/date object, or a string) and returns
    None if the value is empty/unparseable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def find_po_for_group_by_description_and_price(
    supplier_name: Optional[str],
    group_items: List[dict],
    price_tolerance: float = 0.01,
    min_match_fraction: float = 0.5,
    invoice_date_str: Optional[str] = None,
    target_total: Any = None,
    current_pdf_id: Optional[str] = None,
) -> Optional[str]:
    """
    Fallback PO-number lookup for a whole packing-slip/delivery group of
    invoice line items that don't carry their own PO number (most invoices
    don't). Matches the GROUP as a unit rather than one line at a time: the
    same product at the same negotiated price often repeats identically
    across two POs from the same supplier (e.g. a coffee-capsule line at
    42.53 appearing unchanged on back-to-back POs), so a single line's
    (description, price) pair alone can be genuinely ambiguous between POs -
    only the group's full combination of lines, matched together, pins down
    which PO it actually belongs to.

    Only considers OPEN POs: a PO is excluded entirely when EVERY one of its
    lines has IsFinallyInvoiced = True (fully invoiced/closed) - a PO with
    even one line still False/NULL stays a candidate. A closed PO can't be
    what an unmatched invoice belongs to, so it's dropped before any price/
    description matching runs.

    Also excludes service POs (product_type_code='2') entirely - a service/
    lump-sum PO carries one line for the whole contracted job while the
    invoice itemizes it into many separate service lines, so price/
    description matching against it is never meaningful and can only ever
    produce a coincidental false positive. Total (PO total vs invoice
    total) is the only signal that means anything for a service PO, so
    those are left for find_po_by_supplier_and_total - the caller in
    _flatten_newyork_items tries this function first, then falls back to
    that one, which sees every PO (service or not) and never filters on
    product_type_code.

    Approach:
      1. Resolve supplier_name to supplier_id(s) in [final_pulse_DBprod].[dbo].[S4_Suppliers_prod]
         via _get_supplier_ids_by_cleaned_name (special-characters stripped so
         OCR punctuation doesn't break the match).
      2. Pull every PO line for that supplier from [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data].
      3. For each group line with a parseable unit price (via
         _po_lookup_price_candidates, so a drifted UnitPrice extraction
         doesn't sink the match), find PO lines within `price_tolerance` and
         record, per purchase_order, which group-line indices it explains
         and the best description similarity seen for each.
      4. Score each PO by how many distinct group lines it explains by
         price, requiring at least `min_match_fraction` of the group's
         priced lines; break ties by average description similarity, then
         by whether the candidate has a goods receipt on file in
         [final_pulse_DBprod].[dbo].[S4_GRN_Data], then (if still tied - e.g.
         several service POs with no GRN at all, like a fresh PO raised each
         month for the same recurring rent) by whichever tied PO's
         purchase_order_date is closest to `invoice_date_str` (dd-mm-yyyy),
         then (if STILL tied - e.g. two POs for the identical amount raised
         on the identical date, which no amount/date signal can tell apart)
         by preferring whichever tied PO isn't already attached to a
         DIFFERENT invoice in Newyork_Invoice_extracted_data (pass
         `current_pdf_id` so this invoice's own prior row, if re-processing,
         doesn't count against itself).
      5. Sanity-check the winner: a common item/price (e.g. a $500 item that
         also happens to be one line of a much bigger PO) can make a small
         invoice satisfy the >= min_match_fraction line-price threshold
         against a PO whose own total is nothing like the invoice's total -
         e.g. a $1,500 invoice matching one $500 line, so the "matched" PO's
         real total is $500, not anywhere near $1,500. If `target_total` is
         given (the group's own subtotal, or the document total for a
         single-PO-group invoice) and it doesn't fit within this PO's own
         total (allowing for a legitimate partial shipment - i.e.
         target_total may be less than or equal to the PO's total, but not
         more), the match is rejected and this returns None rather than a
         plausible-looking but wrong PO.

    Returns the matched PO number, or None if no PO clears the match
    threshold (caller should leave PONumber blank in that case).
    """
    if not supplier_name or not group_items:
        logging.info(
            f"[PO fallback] Skipped - supplier_name={supplier_name!r}, "
            f"group_items={len(group_items) if group_items else 0}"
        )
        return None

    logging.info(f"[PO fallback] Invoice has no PONumber. Searching by supplier_name={supplier_name!r}")

    priced_items = []
    for it in group_items:
        price_candidates = _po_lookup_price_candidates(it)
        if not price_candidates:
            continue
        try:
            price_val = float(price_candidates[0])
        except (TypeError, ValueError):
            continue
        priced_items.append({"description": it.get("ItemDescription"), "unit_price": price_val})

    if not priced_items:
        logging.info(f"[PO fallback] No group line had a parseable unit price - giving up for supplier_name={supplier_name!r}")
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        supplier_ids = _get_supplier_ids_by_cleaned_name(cursor, supplier_name)
        if not supplier_ids:
            logging.info(
                f"[PO fallback] No S4_Suppliers_prod match for supplier_name={supplier_name!r} "
                f"(cleaned={clean_name(supplier_name)!r}) - no PO can be resolved"
            )
            return None
        logging.info(f"[PO fallback] supplier_name={supplier_name!r} -> resolved supplier_id(s)={supplier_ids}")

        placeholders = ",".join("?" for _ in supplier_ids)
        cursor.execute(
            f"""
            SELECT [purchase_order], [purchase_order_item_text], [unit_price],
                   [effective_amount], [gross_amount], [net_amount], [is_finally_invoiced],
                   [product_type_code]
            FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
            WHERE [supplier] IN ({placeholders})
              AND [purchase_order] IS NOT NULL
            """,
            supplier_ids,
        )
        po_lines = cursor.fetchall()
        if not po_lines:
            logging.info(f"[PO fallback] supplier_id(s)={supplier_ids} has no PO lines in S4_Purchase_Order_Data")
            return None
        distinct_pos_for_supplier = sorted({str(row[0]) for row in po_lines})
        logging.info(
            f"[PO fallback] Found {len(po_lines)} PO line(s) across "
            f"{len(distinct_pos_for_supplier)} PO(s) for this supplier: {distinct_pos_for_supplier}"
        )

        # Open-PO filter: a PO is only "closed" (fully invoiced) when EVERY
        # one of its lines has IsFinallyInvoiced = True. If even one line is
        # False/NULL, the whole PO stays OPEN and stays a candidate - only
        # fully-closed POs are dropped before any price/description matching.
        po_all_invoiced: Dict[str, bool] = {}
        for row in po_lines:
            po_id = str(row[0])
            po_all_invoiced[po_id] = po_all_invoiced.get(po_id, True) and bool(row[6])
        closed_pos = {po for po, all_invoiced in po_all_invoiced.items() if all_invoiced}
        if closed_pos:
            logging.info(
                f"[PO fallback] Excluding {len(closed_pos)} fully-invoiced (closed) PO(s): {sorted(closed_pos)}"
            )
            po_lines = [row for row in po_lines if str(row[0]) not in closed_pos]
            distinct_pos_for_supplier = sorted({str(row[0]) for row in po_lines})
        if not po_lines:
            logging.info(
                f"[PO fallback] All PO(s) for supplier_id(s)={supplier_ids} are fully invoiced (closed) - "
                "no open PO to match"
            )
            return None

        # Service-PO filter: service/lump-sum POs (product_type_code='2')
        # carry a single line for the whole contracted job while the
        # invoice itemizes it into many separate service lines - none of
        # which ever meaningfully price-match a PO line, and a coincidental
        # price match here is exactly the false-positive this function's own
        # target_total sanity check exists to catch. Rather than rely on
        # that check alone, don't offer service POs to price/description
        # matching at all - only find_po_by_supplier_and_total (a pure
        # total-vs-total comparison) is a meaningful way to match one, and
        # that fallback still sees every PO, service or not, since it never
        # filters on product_type_code.
        po_is_service: Dict[str, bool] = {}
        for row in po_lines:
            po_id = str(row[0])
            if row[7] == "2":
                po_is_service[po_id] = True
        if po_is_service:
            logging.info(
                f"[PO fallback] Excluding {len(po_is_service)} service PO(s) (product_type_code='2') from "
                f"price/description matching - total-only matching is the meaningful comparison for these: "
                f"{sorted(po_is_service)}"
            )
            po_lines = [row for row in po_lines if str(row[0]) not in po_is_service]
            distinct_pos_for_supplier = sorted({str(row[0]) for row in po_lines})
        if not po_lines:
            logging.info(
                f"[PO fallback] All open PO(s) for supplier_id(s)={supplier_ids} are service POs - "
                "no PO left for price/description matching"
            )
            return None

        # po_number -> {group_line_index: best_description_similarity_seen}
        po_scores: Dict[str, Dict[int, float]] = {}
        # po_number -> sum of ALL its own lines' amounts, used for the
        # target_total sanity check below - not just the lines that happened
        # to price-match this group.
        po_totals: Dict[str, float] = {}
        for po, po_desc, po_unit_price, effective_amount, gross_amount, net_amount, _is_finally_invoiced, _product_type_code in po_lines:
            raw_amount = _first_nonzero_amount(effective_amount, gross_amount, net_amount)
            try:
                amount_val = float(str(raw_amount).replace(",", "")) if raw_amount not in (None, "") else 0.0
            except (TypeError, ValueError):
                amount_val = 0.0
            po_totals[str(po)] = po_totals.get(str(po), 0.0) + amount_val

            if po_unit_price is None:
                continue
            try:
                po_price_val = float(po_unit_price)
            except (TypeError, ValueError):
                continue

            for idx, inv_item in enumerate(priced_items):
                if abs(po_price_val - inv_item["unit_price"]) > price_tolerance:
                    continue
                sim = _description_similarity(inv_item["description"], po_desc)
                entry = po_scores.setdefault(str(po), {})
                if idx not in entry or sim > entry[idx]:
                    entry[idx] = sim

        if not po_scores:
            logging.info(
                f"[PO fallback] None of the {len(distinct_pos_for_supplier)} PO(s) had a line priced within "
                f"{price_tolerance} of any invoice line - candidates: {distinct_pos_for_supplier}"
            )
            return None

        min_matches_needed = max(1, round(len(priced_items) * min_match_fraction))
        ranked = []
        for po, matches in po_scores.items():
            avg_sim = sum(matches.values()) / len(matches)
            ranked.append((len(matches), avg_sim, po))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        logging.info(
            f"[PO fallback] Candidate PO(s) by price/description match "
            f"(need >= {min_matches_needed}/{len(priced_items)} lines explained): "
            + ", ".join(f"PO={po} matched_lines={count} avg_sim={sim:.0%}" for count, sim, po in ranked)
        )

        ranked = [(count, sim, po) for count, sim, po in ranked if count >= min_matches_needed]
        if not ranked:
            logging.info(f"[PO fallback] No PO cleared the {min_matches_needed}-line threshold - no PO selected")
            return None

        top_count, top_sim = ranked[0][0], ranked[0][1]
        top_candidates = [po for count, sim, po in ranked if count == top_count and sim == top_sim]

        if len(top_candidates) == 1:
            selected_po = top_candidates[0]
            logging.info(f"[PO fallback] SELECTED PO={selected_po} (unique best match: {top_count}/{len(priced_items)} lines, avg_sim={top_sim:.0%})")
        else:
            selected_po = None
            logging.info(f"[PO fallback] Tie between {top_candidates} - breaking tie using S4_GRN_Data (goods receipt on file)")
            placeholders = ",".join("?" for _ in top_candidates)
            cursor.execute(
                f"""
                SELECT DISTINCT [purchase_order]
                FROM [final_pulse_DBprod].[dbo].[S4_GRN_Data]
                WHERE [purchase_order] IN ({placeholders})
                """,
                top_candidates,
            )
            grn_confirmed = {str(row[0]) for row in cursor.fetchall()}
            for po in top_candidates:
                if po in grn_confirmed:
                    selected_po = po
                    logging.info(f"[PO fallback] SELECTED PO={po} (tie broken by GRN goods-receipt match)")
                    break

            if selected_po is None:
                # Still tied, and no GRN on file for any of them (typical for
                # service POs, e.g. a fresh PO raised each month for the same
                # recurring rent - identical price, no receipt at all). Break
                # the tie by whichever candidate's purchase_order_date is
                # closest to this invoice's own date, since a PO is normally
                # raised for the same billing period as the invoice it covers.
                invoice_date = None
                if invoice_date_str:
                    try:
                        invoice_date = datetime.strptime(str(invoice_date_str).strip(), "%d-%m-%Y").date()
                    except ValueError:
                        invoice_date = None

                if invoice_date:
                    cursor.execute(
                        f"""
                        SELECT [purchase_order], MIN([purchase_order_date])
                        FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
                        WHERE [purchase_order] IN ({placeholders})
                        GROUP BY [purchase_order]
                        """,
                        top_candidates,
                    )
                    po_dates = {str(po): parsed for po, d in cursor.fetchall() if (parsed := _parse_sap_date(d)) is not None}
                    if po_dates:
                        date_ranked = sorted(
                            ((po, abs((d - invoice_date).days)) for po, d in po_dates.items()),
                            key=lambda c: c[1],
                        )
                        logging.info(
                            f"[PO fallback] Breaking tie by purchase_order_date vs invoice_date={invoice_date}: "
                            + ", ".join(f"PO={po} day_diff={diff}" for po, diff in date_ranked)
                        )
                        selected_po = date_ranked[0][0]

            if selected_po is None:
                # Still tied even by date (e.g. two POs for the identical
                # amount raised on the identical date - nothing left to go
                # on from the PO data itself). Prefer whichever tied
                # candidate isn't already attached to a DIFFERENT invoice,
                # so two distinct invoices don't both keep landing on the
                # same one PO while its sibling sits unused.
                cursor.execute(
                    f"""
                    SELECT DISTINCT [PONumber]
                    FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
                    WHERE [PONumber] IN ({placeholders}) AND [pdf_id] <> ?
                    """,
                    top_candidates + [current_pdf_id or ""],
                )
                already_claimed = {str(row[0]) for row in cursor.fetchall() if row[0]}
                unclaimed = [po for po in top_candidates if po not in already_claimed]
                if len(unclaimed) == 1:
                    selected_po = unclaimed[0]
                    logging.info(
                        f"[PO fallback] SELECTED PO={selected_po} (tie broken - the only one of "
                        f"{top_candidates} not already used by a different invoice; already claimed: "
                        f"{sorted(already_claimed) or 'none'})"
                    )

            if selected_po is None:
                # Still tied with nothing left to disambiguate on - best effort on description/price alone.
                selected_po = top_candidates[0]
                logging.info(f"[PO fallback] SELECTED PO={selected_po} (still tied after GRN/date/claim check - arbitrary pick from {top_candidates})")

        # Sanity check: the winning PO's own total should plausibly cover
        # this group - reject a match where a common item/price happened to
        # clear the line-match threshold but the PO itself is nowhere near
        # big enough (e.g. a $1,500 invoice "matching" a PO whose only line
        # is $500). A legitimate partial shipment can have target_total <
        # the PO's total, just never > it (by more than `price_tolerance`
        # scaled up to whole-total tolerance, 0.99).
        if target_total not in (None, ""):
            try:
                target_val = float(str(target_total).replace(",", ""))
            except (TypeError, ValueError):
                target_val = None
            if target_val is not None:
                selected_po_total = po_totals.get(selected_po, 0.0)
                if target_val - selected_po_total > 0.99:
                    logging.info(
                        f"[PO fallback] REJECTED PO={selected_po} (own total={selected_po_total} is less than "
                        f"this group's target_total={target_val} - a common item/price price-matched, but "
                        f"the PO itself can't cover the invoice) - no PO selected"
                    )
                    return None

        return selected_po
    except Exception as e:
        logging.warning(
            f"Group PO fallback lookup failed for supplier_name={supplier_name!r}: {e}"
        )
        return None
    finally:
        cursor.close()
        conn.close()


def _po_total_match_comment(po: str, po_total: float, target_val: float) -> str:
    """
    Human-readable comment describing how closely a candidate PO's own total
    lines up with the invoice/group total it was matched against - always
    stated as a percentage so a reviewer can see at a glance whether the PO
    is over or under the invoice, e.g. "PO 4503 is 2.0% lesser than the
    invoice total". Used both for an exact (within-tolerance) match and for
    the nearest-by-total fallback below.
    """
    diff = po_total - target_val
    abs_diff = abs(diff)
    pct = (abs_diff / abs(target_val) * 100) if target_val else 0.0
    if abs_diff <= 0.01:
        return f"PO {po} total matches the invoice total exactly (Invoice: {target_val:.2f}, PO: {po_total:.2f})."
    direction = "more" if diff > 0 else "lesser"
    return (
        f"PO {po} total is {pct:.1f}% {direction} than the invoice total "
        f"(Invoice: {target_val:.2f}, PO: {po_total:.2f}, Diff: {abs_diff:.2f})."
    )


_PO_MATCH_COMMENT_LINE_RE = re.compile(
    r"^PO \S+ total (?:is \d+(?:\.\d+)?% (?:more|lesser) than the invoice total|"
    r"matches the invoice total exactly) \(Invoice: .*?\)\.$",
    re.MULTILINE,
)


def _carry_forward_po_match_comment(pdf_id: str, new_comments: str) -> str:
    """
    _po_total_match_comment's percentage explanation of how a PO was
    resolved (e.g. "PO 4503 total is 2.0% lesser than the invoice total...")
    is only produced once, at extraction time
    (find_po_by_supplier_and_total / process_file_with_gemini) when that
    fallback actually ran. A later, independent re-comparison - most
    notably /compare-invoice-ariba-receipt_id, which is hit by pdf_id alone
    with no access to that extraction-time context - recomputes `comments`
    from scratch and would otherwise silently drop that reasoning the
    moment it overwrites Newyork_FileInput.comments.

    If `new_comments` doesn't already carry a PO-match line, look up
    whatever is currently stored for this pdf_id, pull out any PO-match
    line(s) from it, and re-prepend them so the "how we picked this PO"
    explanation survives every subsequent comparison, not just the first.
    """
    if not new_comments or _PO_MATCH_COMMENT_LINE_RE.search(new_comments):
        return new_comments
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT comments FROM Newyork_FileInput WHERE pdf_id = ?", (pdf_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    existing = row[0] if row and row[0] else None
    if not existing:
        return new_comments
    carried = "\n".join(dict.fromkeys(_PO_MATCH_COMMENT_LINE_RE.findall(existing)))
    return f"{carried}\n{new_comments}" if carried else new_comments


def find_po_by_supplier_and_total(
    supplier_name: Optional[str],
    target_total: Any,
    tolerance: float = 0.99,
    invoice_date_str: Optional[str] = None,
    allow_nearest_fallback: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Second-tier PO fallback, tried when find_po_for_group_by_description_and_price
    finds no match. Some POs (typically service/lump-sum POs, product_type_code=2)
    carry a single line for the whole contracted job (e.g. "KASKADE LASERS" at
    17475.00) while the supplier's invoice itemizes that same job into many
    separate service lines (day rate, flight, hotel, per-unit laser rental,
    etc.) - none of which will ever price-match a PO line individually. In
    that case the invoice's own total (or, for a multi-PO-group invoice, the
    group's own subtotal) is the only reliable signal: sum each candidate
    PO's line amounts and compare against `target_total`.

    Resolves supplier_name to supplier_id(s) the same way
    find_po_for_group_by_description_and_price does, sums
    COALESCE(gross_amount, net_amount) per purchase_order for that supplier,
    and returns (matched_po, match_comment) for the PO whose total is
    closest to `target_total` within `tolerance` - match_comment always
    states the percentage the PO's total is over/under the target (0% for
    an exact match). Returns (None, None) if no PO's total is that close and
    `allow_nearest_fallback` is False, or target_total itself is missing/
    unparseable.

    Only considers OPEN POs: a PO is excluded entirely when EVERY one of its
    lines has IsFinallyInvoiced = True (fully invoiced/closed) - a PO with
    even one line still False/NULL stays a candidate, for both the exact and
    the nearest-by-total match.

    A recurring monthly service (e.g. venue rent) generates a fresh PO each
    month with the SAME total, so more than one PO can tie on total alone -
    e.g. a July invoice and an August invoice for the identical $354,167.80
    rent, against a July-dated PO and an August-dated PO of the same amount.
    When `invoice_date_str` (dd-mm-yyyy, matching this pipeline's InvoiceDate
    format) is given and more than one PO ties on total, the tie is broken
    by purchase_order_date: whichever tied PO's date is closest (by absolute
    day difference) to the invoice date wins, since a PO is normally raised
    for the same billing period as the invoice it covers.

    When `allow_nearest_fallback` is True and no PO clears `tolerance`, the
    PO whose total is numerically CLOSEST to `target_total` (above or below)
    is selected anyway - e.g. candidate POs totalling 3000/3500/3300 against
    an invoice total of 3100 picks the 3300 PO - and match_comment explains
    the percentage gap so a reviewer can see this was a nearest-match, not
    an exact one. Use with care: this trades the stricter safety check below
    (only an exact total match is trustworthy evidence) for always resolving
    a PO, so only pass it when the caller wants a best-effort pick.
    """
    if not supplier_name or not target_total:
        return None, None
    try:
        target_val = float(str(target_total).replace(",", ""))
    except (ValueError, TypeError):
        return None, None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        supplier_ids = _get_supplier_ids_by_cleaned_name(cursor, supplier_name)
        if not supplier_ids:
            logging.info(
                f"[PO fallback/total] No S4_Suppliers_prod match for supplier_name={supplier_name!r} "
                f"(cleaned={clean_name(supplier_name)!r}) - no PO can be resolved"
            )
            return None, None

        placeholders = ",".join("?" for _ in supplier_ids)
        cursor.execute(
            f"""
            SELECT [purchase_order], [effective_amount], [gross_amount], [net_amount],
                   [purchase_order_date], [is_finally_invoiced]
            FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
            WHERE [supplier] IN ({placeholders})
              AND [purchase_order] IS NOT NULL
            """,
            supplier_ids,
        )
        # effective_amount/gross_amount/net_amount are stored as varchar, not
        # numeric - summing them in SQL via SUM(COALESCE(..., 0)) lets SQL
        # Server's implicit type precedence force the varchar values to int
        # (since the literal 0 outranks varchar), which blows up on any
        # non-integer amount like '17475.0'. Parse and sum in Python instead,
        # same as every other amount read in this file.
        po_totals: Dict[str, float] = {}
        po_dates: Dict[str, Any] = {}
        # Open-PO filter: a PO is only "closed" (fully invoiced) when EVERY
        # one of its lines has IsFinallyInvoiced = True - if even one line is
        # False/NULL, the whole PO stays OPEN. Tracked alongside po_totals so
        # a closed PO's total match doesn't win against a genuinely open one.
        po_all_invoiced: Dict[str, bool] = {}
        for po, effective_amount, gross_amount, net_amount, purchase_order_date, is_finally_invoiced in cursor.fetchall():
            po_key = str(po)
            po_all_invoiced[po_key] = po_all_invoiced.get(po_key, True) and bool(is_finally_invoiced)
            raw = _first_nonzero_amount(effective_amount, gross_amount, net_amount)
            try:
                amount = float(str(raw).replace(",", "")) if raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                continue
            po_totals[po_key] = po_totals.get(po_key, 0.0) + amount
            if purchase_order_date is not None:
                po_dates.setdefault(po_key, purchase_order_date)

        closed_pos = {po for po, all_invoiced in po_all_invoiced.items() if all_invoiced}
        if closed_pos:
            logging.info(
                f"[PO fallback/total] Excluding {len(closed_pos)} fully-invoiced (closed) PO(s): {sorted(closed_pos)}"
            )
            for po in closed_pos:
                po_totals.pop(po, None)
                po_dates.pop(po, None)

        if not po_totals:
            logging.info(
                f"[PO fallback/total] supplier_id(s)={supplier_ids} has no OPEN PO lines in S4_Purchase_Order_Data"
            )
            return None, None

        candidates = sorted(
            ((po, total, abs(round(total, 2) - round(target_val, 2))) for po, total in po_totals.items()),
            key=lambda c: c[2],
        )
        logging.info(
            f"[PO fallback/total] target_total={target_val} - candidate PO(s): "
            + ", ".join(f"PO={po} total={total} diff={diff}" for po, total, diff in candidates)
        )

        best_po, best_total, best_diff = candidates[0]
        if best_diff <= tolerance:
            tied = [po for po, total, diff in candidates if diff == best_diff]
            if len(tied) > 1:
                invoice_date = None
                if invoice_date_str:
                    try:
                        invoice_date = datetime.strptime(str(invoice_date_str).strip(), "%d-%m-%Y").date()
                    except ValueError:
                        invoice_date = None

                tied_with_dates = [(po, _parse_sap_date(po_dates.get(po))) for po in tied]
                if invoice_date and any(d is not None for _, d in tied_with_dates):
                    ranked = sorted(
                        ((po, abs((d - invoice_date).days)) for po, d in tied_with_dates if d is not None),
                        key=lambda c: c[1],
                    )
                    logging.info(
                        f"[PO fallback/total] Tie between {tied} on total match - breaking tie by "
                        f"purchase_order_date vs invoice_date={invoice_date}: "
                        + ", ".join(f"PO={po} day_diff={diff}" for po, diff in ranked)
                    )
                    winner = ranked[0][0]
                    return winner, _po_total_match_comment(winner, po_totals[winner], target_val)

                logging.info(
                    f"[PO fallback/total] Tie between {tied} on total match - no usable invoice_date to "
                    f"break the tie by purchase_order_date - arbitrary pick {tied[0]}"
                )
                winner = tied[0]
                return winner, _po_total_match_comment(winner, po_totals[winner], target_val)

            logging.info(f"[PO fallback/total] SELECTED PO={best_po} (total={best_total} vs target={target_val}, diff={best_diff})")
            return best_po, _po_total_match_comment(best_po, best_total, target_val)

        # No PO's own total matches the target within tolerance. Picking
        # "whichever PO happens to have a total >= target" here is NOT
        # inherently safe: a supplier can have several unrelated POs
        # (different service, different month) where one simply happens to
        # have a total a little bigger than this invoice's - e.g. a $18,250
        # invoice for "Professional Services"/"Administrative Fee"
        # incorrectly matching a same-supplier PO for "Marketing - PR
        # online" just because that PO's total ($26,538.44) was the
        # closest-above amount on file. By default only an EXACT (within
        # `tolerance`) total match is trustworthy evidence this invoice
        # belongs to a given PO - anything looser is left unmatched here so
        # the invoice falls through to 'failed' and gets checked against
        # S4_Invoice_Data (by supplier + invoice number) instead of being
        # pinned to the wrong PO.
        #
        # When the caller explicitly opts in via `allow_nearest_fallback`
        # (e.g. picking between 4501=3000/4502=3500/4503=3300 for an
        # invoice total of 3100), select the numerically closest PO anyway
        # and hand back a percentage comment ("2.0% more"/"2.0% lesser") so
        # the mismatch is visible in the stored comments rather than silent.
        if allow_nearest_fallback:
            logging.info(
                f"[PO fallback/total] No PO within tolerance {tolerance} - allow_nearest_fallback=True, "
                f"SELECTING closest PO={best_po} (total={best_total}, diff={best_diff}) anyway"
            )
            return best_po, _po_total_match_comment(best_po, best_total, target_val)

        logging.info(
            f"[PO fallback/total] Closest PO={best_po} (total={best_total}) is off by "
            f"{best_diff} - exceeds tolerance {tolerance} - no PO selected"
        )
        return None, None
    except Exception as e:
        logging.warning(f"PO total-match fallback failed for supplier_name={supplier_name!r}: {e}")
        return None, None
    finally:
        cursor.close()
        conn.close()


def _flatten_newyork_items(extracted: dict) -> Tuple[dict, list]:
    """
    New York invoices are extracted grouped by PO ("Items" = list of PO groups, each with
    its own "LineItems"). The rest of the pipeline (DB insert, PO/receipt matching,
    line-item comparison, etc.) expects the FLAT structure: a single "Items" list of
    line items plus a header-level "PONumbers" list.

    This converts the grouped structure into that flat shape so every downstream
    function - PO normalization, insert_invoice_to_db, comparison logic - works without
    any further changes.
    """
    groups = extracted.get("Items", []) or []
    flat_items = []
    po_numbers = []
    group_tax = group_discount = group_freight = None
    seq = 1

    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        po = group.get("PONumber")
        if po and str(po).strip():
            po_numbers.append(str(po).strip())

        po_total = group.get("Total")
        if group_tax is None and group.get("TaxAmount"):
            group_tax = group.get("TaxAmount")
        if group_discount is None and group.get("Discount"):
            group_discount = group.get("Discount")
        if group_freight is None and group.get("Freight"):
            group_freight = group.get("Freight")

        group_date = group.get("GroupDate")

        for line in (group.get("LineItems") or []):
            line = dict(line) if isinstance(line, dict) else {}
            if po and not line.get("PONumber"):
                line["PONumber"] = po
            if po_total is not None and not line.get("Po_Total"):
                line["Po_Total"] = po_total
            if group_date and not line.get("GroupDate"):
                line["GroupDate"] = group_date
            if not line.get("LineItemNumber"):
                line["LineItemNumber"] = str(seq)
            # Internal-only: which PO group this line was printed under, used by
            # the PO fallback below to match a whole group at once instead of one
            # line in isolation. Stripped before the response is returned.
            line["_po_group_idx"] = group_index
            flat_items.append(line)
            seq += 1

    header_extra = {}
    if po_numbers:
        header_extra["PONumbers"] = po_numbers
    if group_tax is not None:
        header_extra.setdefault("TaxAmount", group_tax)
    if group_discount is not None:
        header_extra.setdefault("Discount", group_discount)
    if group_freight is not None:
        header_extra.setdefault("Freight", group_freight)

    return header_extra, flat_items



# ============================================================================
# 11. DATABASE INSERT FUNCTIONS
# ============================================================================
def insert_invoice_to_db(invoice_data: dict, pdf_id: str, user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    source_file = esc(invoice_data.get("source_file"))
    invoice_number = esc(invoice_data.get("InvoiceNumber"))
    supplier_name = esc(invoice_data.get("SupplierName"))
    customer_name = esc(invoice_data.get("CustomerName"))
    po_list = invoice_data.get("PONumbers") or invoice_data.get("PONumber") or []
    if isinstance(po_list, str):
        po_list = [po_list] if po_list.strip() else []
    po_number = esc(", ".join(po_list)) if po_list else esc(None)
    invoice_date = esc(invoice_data.get("InvoiceDate"))
    supplier_trn = esc(invoice_data.get("SupplierTRN"))
    customer_trn = esc(invoice_data.get("CustomerTRN"))
    email = esc(invoice_data.get("Email"))
    invoice_total = esc(invoice_data.get("Invoice_Total") or invoice_data.get("InvoiceTotal") or invoice_data.get("TotalIncludingVAT") or invoice_data.get("BalanceDue") or "")
    customer_addr = esc(invoice_data.get("Customer_address"))
    supplier_addr = esc(invoice_data.get("Supplier_address"))

    tax_invoice_val = invoice_data.get("Tax_invoice")
    if isinstance(tax_invoice_val, str):
        tax_invoice = "'true'" if tax_invoice_val.lower() in ("true", "yes", "1") else "'false'"
    else:
        tax_invoice = "'true'" if tax_invoice_val else "'false'"

    stamp_val = invoice_data.get("Stamp")
    if isinstance(stamp_val, str):
        stamp = "'true'" if stamp_val.lower() in ("true", "yes", "1", "present") else "'false'"
    else:
        stamp = "'true'" if stamp_val else "'false'"

    bank_acct_name = esc(invoice_data.get("BankAccountName"))
    bank_name = esc(invoice_data.get("BankName"))
    bank_branch = esc(invoice_data.get("BankBranch"))
    account_number = esc(invoice_data.get("AccountNumber"))
    iban = esc(invoice_data.get("IBAN"))
    swift_code = esc(
        invoice_data.get("SwiftCode") or invoice_data.get("SWIFTCode")
        or invoice_data.get("SwiftBIC") or invoice_data.get("BIC")
    )
    discount = esc(invoice_data.get("Discount"))

    items_to_insert = invoice_data.get("Items") or [{}]

    for idx, item in enumerate(items_to_insert, start=1):
        # New York invoices carry their own PONumber per line item (one PO group
        # per set of lines) - use that for the row instead of the joined
        # header PONumbers string. Items with no per-item PONumber fall back to
        # the joined header string (e.g. "4500212663, 4500224399" repeated on
        # every row).
        item_po = item.get("PONumber")
        row_po_number = esc(str(item_po).strip()) if item_po else po_number
        po_total = esc(item.get("Po_Total") or "")

        line_item_num = esc(_normalize_line_item_number(item.get("LineItemNumber"), idx))
        item_code = esc(item.get("ItemCode") or item.get("Item_Code") or item.get("item_code") or "")
        item_desc = esc(item.get("ItemDescription"))
        qty = esc(item.get("QTY"))
        unit = esc(item.get("Unit"))
        unit_price = esc(item.get("UnitPrice"))
        amount = esc(item.get("Amount") or item.get("ItemTotalExcludingVAT") or "")

        vat_raw = item.get("VAT") or item.get("VATRate") or item.get("VAT%") or item.get("TaxRate") or item.get("VATPercentage") or ""
        vat_clean = vat_raw.replace("%", "").strip() if isinstance(vat_raw, str) else str(vat_raw) if vat_raw else ""
        vat = esc(vat_clean)

        vat_amount = esc(item.get("VATAmount") or item.get("ItemVATAmount") or "")
        amount_incl_vat = esc(item.get("AmountInclVAT") or item.get("ItemTotalIncludingVAT") or "")

        query = f"""
            INSERT INTO Newyork_Invoice_extracted_data (
                pdf_id, source_file, InvoiceNumber, SupplierName, CustomerName,
                PONumber, InvoiceDate, SupplierTRN, CustomerTRN, Email, Invoice_Total, Po_Total,
                Customer_address, Supplier_address, Tax_invoice, Stamp,
                BankAccountName, BankName, BankBranch, AccountNumber, IBAN, SwiftCode,
                LineItemNumber, ItemCode, ItemDescription, QTY, Unit,
                UnitPrice, Amount, VAT, VATAmount, AmountInclVAT,
                user_id, invoice_id, is_trusted, Discount, BranchCode, created_at
            ) VALUES (
                {esc(pdf_id)}, {source_file}, {invoice_number}, {supplier_name}, {customer_name},
                {row_po_number}, {invoice_date}, {supplier_trn}, {customer_trn}, {email}, {invoice_total}, {po_total},
                {customer_addr}, {supplier_addr}, {tax_invoice}, {stamp},
                {bank_acct_name}, {bank_name}, {bank_branch}, {account_number}, {iban}, {swift_code},
                {line_item_num}, {item_code}, {item_desc}, {qty}, {unit},
                {unit_price}, {amount}, {vat}, {vat_amount}, {amount_incl_vat},
                {esc(user_id)}, NULL, NULL, {discount}, NULL, GETDATE()
            )
        """
        cursor.execute(query)

    conn.commit()
    cursor.close()
    conn.close()

def insert_fileinput_to_db(
    filename: str, pdf_id: str, supplier_name: str, pdf_bytes: bytes,
    status: str, user_id: str,
    invoice_number: str = None,
) -> int:
    """Insert or update a manually-uploaded file's row in the Newyork_FileInput table."""
    conn = get_db_connection()
    cursor = conn.cursor()

    filename_sql = (filename or "").replace("'", "''")
    sup_name_sql = (supplier_name or "").replace("'", "''")
    file_b64_sql = (base64.b64encode(pdf_bytes).decode("utf-8") if pdf_bytes else "").replace("'", "''")
    status_sql = (status or "").replace("'", "''")
    pdf_id_sql = "NULL" if pdf_id is None else f"'{pdf_id}'"

    inv_num_clean = (invoice_number or "").strip()
    existing = None
    if inv_num_clean:
        execute_named(cursor, """
            SELECT TOP 1 fi.id
            FROM Newyork_FileInput fi
            JOIN Newyork_Invoice_extracted_data inv ON fi.pdf_id = inv.pdf_id
            WHERE fi.filename = :filename AND inv.InvoiceNumber = :invoice_number AND fi.user_id = :user_id
            ORDER BY fi.created_at DESC
        """, {
            "filename": filename,
            "invoice_number": inv_num_clean,
            "user_id": user_id,
        })
        existing = cursor.fetchone()

    if existing:
        existing_id = existing[0]
        execute_named(cursor, """
            UPDATE Newyork_FileInput
            SET pdf_id = :pdf_id, SupplierName = :supplier_name, status = :status, file_base64 = :file_base64, user_id = :user_id, created_at = GETDATE()
            WHERE id = :id
        """, {
            "pdf_id": pdf_id,
            "supplier_name": supplier_name,
            "status": status,
            "file_base64": base64.b64encode(pdf_bytes).decode("utf-8") if pdf_bytes else "",
            "user_id": user_id,
            "id": existing_id,
        })
        conn.commit()
        cursor.close()
        conn.close()
        logging.info(f"Updated existing Newyork_FileInput record id={existing_id} for filename='{filename}'")
        return existing_id

    query = f"""
        INSERT INTO Newyork_FileInput (
            user_id, filename, pdf_id, SupplierName, status, file_base64, created_at
        ) OUTPUT INSERTED.id VALUES (
            '{user_id}', '{filename_sql}', {pdf_id_sql}, '{sup_name_sql}',
            '{status_sql}', '{file_b64_sql}', GETDATE()
        )
    """
    cursor.execute(query)
    input_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    logging.info(f"Inserted new Newyork_FileInput record id={input_id} for filename='{filename}'")
    return input_id



def find_duplicate_invoice(
    invoice_number: str, supplier_name: str,
    invoice_total: Optional[str] = None, invoice_date: Optional[str] = None,
):
    """Check if this invoice was already ingested, regardless of filename or
    which user uploaded it. Returns (existing_id, existing_pdf_id,
    existing_status) tuple or None.

    When OCR failed to extract an InvoiceNumber, falls back to matching on
    SupplierName + Invoice_Total + InvoiceDate (needs all three), so a
    re-upload of the same document isn't stored as a brand-new record just
    because the number was missed this time. Processed records win ties."""
    if not supplier_name:
        return None
    if not (invoice_number or "").strip():
        total = _ny_clean_number(invoice_total)
        if not total or not (invoice_date or "").strip():
            return None
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            execute_named(cursor, """
                SELECT TOP 1 fi.id, fi.pdf_id, fi.status
                FROM Newyork_FileInput fi
                JOIN Newyork_Invoice_extracted_data inv ON fi.pdf_id = inv.pdf_id
                WHERE fi.SupplierName = :supplier_name
                  AND REPLACE(REPLACE(inv.Invoice_Total, ',', ''), '$', '') = :invoice_total
                  AND inv.InvoiceDate = :invoice_date
                ORDER BY CASE WHEN LOWER(LTRIM(RTRIM(fi.status))) = 'processed' THEN 0 ELSE 1 END,
                         fi.created_at DESC
            """, {
                "supplier_name": supplier_name,
                "invoice_total": total,
                "invoice_date": invoice_date.strip(),
            })
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        execute_named(cursor, """
            SELECT TOP 1 fi.id, fi.pdf_id, fi.status
            FROM Newyork_FileInput fi
            JOIN Newyork_Invoice_extracted_data inv ON fi.pdf_id = inv.pdf_id
            WHERE inv.InvoiceNumber = :invoice_number
              AND fi.SupplierName = :supplier_name
            ORDER BY fi.created_at DESC
        """, {
            "invoice_number": invoice_number.strip(),
            "supplier_name": supplier_name,
        })
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def delete_invoice_extracted_rows(pdf_id: str):
    """Delete every Newyork_Invoice_extracted_data line-item row for a
    pdf_id, so it can be re-inserted fresh (full replace) when re-ingesting
    a duplicate invoice under its existing pdf_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM Newyork_Invoice_extracted_data WHERE pdf_id = ?", (pdf_id,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def update_fileinput_record_for_reupload(
    existing_id: int, filename: str, supplier_name: str, pdf_bytes: bytes, user_id: str
):
    """Refresh an existing Newyork_FileInput row in place (same id/pdf_id)
    with a re-uploaded copy of the same invoice (matched by InvoiceNumber +
    SupplierName) - used instead of insert_fileinput_to_db, which would
    insert a second row since its own dedup check is keyed on filename and
    this re-upload's filename is expected to differ."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        execute_named(cursor, """
            UPDATE Newyork_FileInput
            SET filename = :filename, SupplierName = :supplier_name, status = 'Success',
                comments = NULL, file_base64 = :file_base64, user_id = :user_id, created_at = GETDATE()
            WHERE id = :id
        """, {
            "filename": filename,
            "supplier_name": supplier_name,
            "file_base64": base64.b64encode(pdf_bytes).decode("utf-8") if pdf_bytes else "",
            "user_id": user_id,
            "id": existing_id,
        })
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def handle_possible_duplicate_invoice(
    invoice_data: dict, filename: str, file_bytes: bytes, user_id: str
) -> Optional[dict]:
    """Check whether this invoice (matched by InvoiceNumber + SupplierName,
    regardless of filename or who uploaded it) already exists in
    Newyork_FileInput:

    - If the existing record's status is 'processed' (case-insensitive), the
      invoice has already gone all the way through (e.g. submitted to SAP) -
      this upload is a pure duplicate and is NOT inserted or updated at all.
    - If the existing record has any other status (Success, failed, pending,
      rpa_pending, ...), it hasn't been finalized yet - refresh it in place
      (same pdf_id, line items fully replaced) with this upload's data
      instead of creating a second record for the same invoice.

    Returns a result dict the caller should return as-is (skipping its own
    insert) if this was a duplicate, or None if this is a genuinely new
    invoice and the caller should proceed with its normal insert.
    """
    dup = find_duplicate_invoice(
        invoice_data.get("InvoiceNumber"), invoice_data.get("SupplierName"),
        invoice_total=invoice_data.get("Invoice_Total") or invoice_data.get("InvoiceTotal"),
        invoice_date=invoice_data.get("InvoiceDate"),
    )
    if not dup:
        # No existing Newyork_FileInput record for this invoice at all - but
        # it may still already be posted to SAP (submitted through a pdf_id
        # that's since been cleaned up, or outside this pipeline entirely).
        # Check S4_Invoice_Data BEFORE inserting anything, so a first-time
        # re-upload of an already-posted invoice doesn't create a brand-new
        # Newyork_FileInput/Newyork_Invoice_extracted_data row either.
        po_numbers = list(dict.fromkeys(
            it.get("PONumber") for it in (invoice_data.get("Items") or []) if it.get("PONumber")
        ))
        existing_supplier_invoice = find_existing_s4_invoice_for_values(
            invoice_data.get("InvoiceNumber"),
            po_numbers=po_numbers or None,
            supplier_name=invoice_data.get("SupplierName"),
        )
        if existing_supplier_invoice:
            return {
                "filename": filename,
                "pdf_id": None,
                "status": "duplicate",
                "invoices": [invoice_data],
                "message": (
                    f"Duplicate : Invoice Already Posted in SAP (supplier_invoice={existing_supplier_invoice}) "
                    "- not ingested"
                ),
            }
        return None

    existing_id, existing_pdf_id, existing_status = dup
    if (existing_status or "").strip().lower() == "processed":
        return {
            "filename": filename,
            "pdf_id": existing_pdf_id,
            "status": "duplicate",
            "invoices": [invoice_data],
            "message": f"Invoice already processed as pdf_id={existing_pdf_id} - not re-ingested",
        }

    delete_invoice_extracted_rows(existing_pdf_id)
    insert_invoice_to_db(invoice_data, existing_pdf_id, user_id)
    update_fileinput_record_for_reupload(
        existing_id, filename, invoice_data.get("SupplierName"), file_bytes, user_id
    )
    comparison_status, compare_comments = run_po_comparison_and_update_status(
        existing_pdf_id, filename, existing_id,
        po_match_comment=invoice_data.get("PO_Match_Comment"),
    )
    return {
        "filename": filename,
        "pdf_id": existing_pdf_id,
        "status": "updated",
        "invoices": [invoice_data],
        "comparison_status": comparison_status,
        "comparison_comments": compare_comments,
        "message": (
            f"Existing invoice pdf_id={existing_pdf_id} was updated with this upload "
            f"(previous status: {existing_status or 'unknown'})"
        ),
    }

def update_file_status_and_comment(input_id: int, status: str, comment: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE Newyork_FileInput SET status = ?, comments = ?, created_at = GETDATE() WHERE id = ?", (status, comment, input_id))
    conn.commit()
    cursor.close()
    conn.close()

def _fetch_po_numbers_for_sap_invoice(supplier_invoice: str) -> List[str]:
    """
    Once an invoice is recognized as already posted in SAP (matched via
    find_existing_s4_invoice/find_existing_s4_invoice_for_values against
    S4_Invoice_Data), look up which PO(s) SAP actually posted it against via
    S4_Invoice_PurOrdRef_Data (synced from API_SUPPLIERINVOICE_PROCESS_SRV's
    to_SuplrInvcItemPurOrdRef expansion, s4_data_apis.py) - SAP's own
    ground-truth PO reference. This matters because an invoice recognized
    this way short-circuits straight to status='processed'/duplicate and
    never runs our own OCR/heuristic PO-matching, so PONumber would
    otherwise be left blank even though SAP already knows exactly which
    PO(s) it was posted against.
    """
    if not supplier_invoice:
        return []
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT DISTINCT purchase_order
            FROM final_pulse_DBprod.dbo.S4_Invoice_PurOrdRef_Data
            WHERE supplier_invoice = ? AND purchase_order IS NOT NULL
            """,
            (supplier_invoice,),
        )
        return [str(row[0]).strip() for row in cursor.fetchall() if row[0]]
    except Exception as e:
        logging.error(f"Failed to fetch PO number(s) for SAP supplier_invoice={supplier_invoice!r}: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def _backfill_po_number_from_sap(pdf_id: str, supplier_invoice: str):
    """
    Set Newyork_Invoice_extracted_data.PONumber for every row of this pdf_id
    to S4_Invoice_PurOrdRef_Data's PO reference(s) for the matched SAP
    supplier_invoice - UNCONDITIONALLY, even overwriting a PO our own
    extraction/fallback matching already resolved (price/description match,
    exact-total match, or a nearest-total guess). This only ever runs once
    S4_Invoice_Data has confirmed the invoice is already posted in SAP, at
    which point S4_Invoice_PurOrdRef_Data is ground truth - strictly more
    reliable than any of our own heuristics, so it should win even over an
    already-resolved PONumber, not just fill in a blank one.
    """
    po_numbers = _fetch_po_numbers_for_sap_invoice(supplier_invoice)
    if not po_numbers:
        return
    po_number_str = ", ".join(po_numbers)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE final_pulse_DBprod.dbo.Newyork_Invoice_extracted_data
            SET PONumber = ?
            WHERE pdf_id = ?
            """,
            (po_number_str, pdf_id),
        )
        conn.commit()
        logging.info(
            f"Set Newyork_Invoice_extracted_data.PONumber = '{po_number_str}' for pdf_id={pdf_id} "
            f"(from SAP supplier_invoice={supplier_invoice}, overwriting any prior heuristic PO) - "
            f"{cursor.rowcount} row(s) updated"
        )
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to backfill PONumber for pdf_id={pdf_id}: {e}")
    finally:
        cursor.close()
        conn.close()


def update_customer_invoice_id(pdf_id: str, customer_invoice_id: str):
    """
    Write the resolved Customer_Invoice_ID back to the Newyork_FileInput row,
    then backfill Newyork_Invoice_extracted_data.PONumber from SAP's own
    S4_Invoice_PurOrdRef_Data (see _backfill_po_number_from_sap) - called
    wherever an invoice is recognized as already posted in SAP.
    """
    if not customer_invoice_id:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE Newyork_FileInput SET CUSTOMER_INVOICE_ID = ? WHERE pdf_id = ?", (customer_invoice_id, pdf_id))
        conn.commit()
        logging.info(f"Updated Newyork_FileInput.CUSTOMER_INVOICE_ID = '{customer_invoice_id}' for pdf_id={pdf_id}")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to update CUSTOMER_INVOICE_ID for pdf_id={pdf_id}: {e}")
    finally:
        cursor.close()
        conn.close()

    _backfill_po_number_from_sap(pdf_id, customer_invoice_id)

def update_fileinput_status_by_pdf_id(pdf_id: str, status: str, comment: str) -> bool:
    """
    Handles duplicate InvoiceNumber + SupplierName records.
    Logic:
    1. Find the current invoice using pdf_id.
    2. Check if another older record exists with the same InvoiceNumber + SupplierName.
    3. If duplicate exists:
       - If OLD record status = 'Processed':
           Keep OLD record and delete NEW/current record.
       - If OLD record status != 'Processed':
           Delete OLD record and keep NEW/current record.
    4. If no duplicate exists:
       - Update the current record's status and comments.
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ---------------------------------------------------------
        # 1. Get invoice number and supplier name for current/new PDF
        # ---------------------------------------------------------
        cursor.execute(
            """
            SELECT TOP 1 InvoiceNumber, SupplierName
            FROM Newyork_Invoice_extracted_data
            WHERE pdf_id = ?
            """,
            (pdf_id,)
        )

        header_row = cursor.fetchone()

        invoice_number = (header_row[0] or "").strip() if header_row else ""
        supplier_name = (header_row[1] or "").strip() if header_row else ""

        # ---------------------------------------------------------
        # 2. Check for duplicate old record
        # ---------------------------------------------------------
        duplicate_pdf_id = None
        duplicate_status = None

        if invoice_number and supplier_name:

            cursor.execute(
                """
                SELECT TOP 1
                    FI.pdf_id,
                    FI.status
                FROM Newyork_FileInput FI
                INNER JOIN Newyork_Invoice_extracted_data IE
                    ON FI.pdf_id = IE.pdf_id
                WHERE FI.pdf_id <> ?
                  AND IE.InvoiceNumber = ?
                  AND IE.SupplierName = ?
                ORDER BY FI.created_at ASC
                """,
                (
                    pdf_id,
                    invoice_number,
                    supplier_name
                )
            )

            dup_row = cursor.fetchone()

            if dup_row:
                duplicate_pdf_id = dup_row[0]
                duplicate_status = (dup_row[1] or "").strip()

        # ---------------------------------------------------------
        # 3. Duplicate found
        # ---------------------------------------------------------
        if duplicate_pdf_id:

            if duplicate_status.lower() == "processed":

                # -------------------------------------------------
                # OLD record is already Processed
                # Keep OLD and DELETE NEW/current record
                # -------------------------------------------------

                cursor.execute(
                    """
                    DELETE FROM Newyork_FileInput
                    WHERE pdf_id = ?
                    """,
                    (pdf_id,)
                )

                logging.info(
                    f"Duplicate found. OLD record is Processed. "
                    f"Keeping old pdf_id={duplicate_pdf_id} and "
                    f"deleting new pdf_id={pdf_id}. "
                    f"InvoiceNumber='{invoice_number}', "
                    f"SupplierName='{supplier_name}'"
                )

            else:

                # -------------------------------------------------
                # OLD record is NOT Processed
                # Delete OLD and KEEP NEW/current record
                # -------------------------------------------------

                cursor.execute(
                    """
                    DELETE FROM Newyork_FileInput
                    WHERE pdf_id = ?
                    """,
                    (duplicate_pdf_id,)
                )

                # Update the NEW/current record
                cursor.execute(
                    """
                    UPDATE Newyork_FileInput
                    SET status = ?, comments = ?
                    WHERE pdf_id = ?
                    """,
                    (
                        status,
                        comment,
                        pdf_id
                    )
                )

                logging.info(
                    f"Duplicate found. OLD record is not Processed "
                    f"(status='{duplicate_status}'). "
                    f"Deleting old pdf_id={duplicate_pdf_id} and "
                    f"keeping new pdf_id={pdf_id}. "
                    f"InvoiceNumber='{invoice_number}', "
                    f"SupplierName='{supplier_name}'"
                )

            conn.commit()

        # ---------------------------------------------------------
        # 4. No duplicate found
        # ---------------------------------------------------------
        else:

            cursor.execute(
                """
                UPDATE Newyork_FileInput
                SET status = ?, comments = ?
                WHERE pdf_id = ?
                """,
                (
                    status,
                    comment,
                    pdf_id
                )
            )

            conn.commit()

        return True

    except Exception as e:

        if conn:
            conn.rollback()

        logging.error(
            f"Failed to update status/comment for pdf_id={pdf_id}: {e}"
        )

        return False

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()



# ============================================================================
# 12. FILE PROCESSING FUNCTIONS
# ============================================================================
def _build_invoice_data_from_extracted(extracted: dict, filename: str, pdf_id: str, token_usage: dict) -> dict:
    """
    Turn ONE Gemini-extracted invoice object (already unwrapped from any
    top-level list - see process_file_with_gemini) into the flat
    invoice_data shape the rest of the pipeline expects: flattens PO
    groups, normalizes header/item field names and numbers, canonicalizes
    SupplierName against S4_Suppliers_prod, and runs the PO-matching
    fallback chain (price/description -> exact total -> nearest total) for
    any PO group still missing a PONumber.
    """
    header_extra, flat_items = _flatten_newyork_items(extracted)
    header_raw = {**header_extra, **extracted.get("HeaderFields", {})}
    header = normalize(header_raw, HEADER_MAP)
    items = [normalize(item, ITEM_MAP) for item in flat_items]

    # Strip a leading label the model sometimes keeps ("Bid# 82565" ->
    # "82565") so the same document always yields the same InvoiceNumber and
    # the InvoiceNumber+SupplierName duplicate check can match it.
    if header.get("InvoiceNumber"):
        cleaned_inv_no = re.sub(
            r"^\s*(?:bid|quote|estimate|ticket|res(?:ervation)?)\s*(?:no\.?|number)?\s*[#:]\s*",
            "", str(header["InvoiceNumber"]), flags=re.IGNORECASE,
        ).strip()
        header["InvoiceNumber"] = cleaned_inv_no or None

    for f in ("Invoice_Total", "Discount", "Freight", "TaxAmount"):
        if header.get(f) is not None:
            header[f] = _ny_clean_number(header[f])
    for it in items:
        for f in ("QTY", "UnitPrice", "Amount", "VAT", "VATAmount", "AmountInclVAT", "Po_Total"):
            if it.get(f) is not None:
                it[f] = _ny_clean_number(it[f])

    raw_all_pos = header.get("PONumbers") or ([header.get("PONumber")] if header.get("PONumber") else [])
    if isinstance(raw_all_pos, str):
        raw_all_pos = [raw_all_pos]
    normalized_pos = [_normalize_po_number(p) for p in raw_all_pos]
    normalized_pos = [p for p in dict.fromkeys(p for p in normalized_pos if p and _is_plausible_po_number(p))]

    for it in items:
        if it.get("PONumber"):
            normalized_item_po = _normalize_po_number(it.get("PONumber"))
            it["PONumber"] = normalized_item_po if _is_plausible_po_number(normalized_item_po) else None

    # Most invoices arrive with no PO number at all. For any PO group still
    # missing one, fall back to matching the WHOLE group against
    # S4_Purchase_Order_Data / S4_GRN_Data for the resolved supplier (by
    # unit price, using description similarity only as a rank/tie-break) -
    # matched per group rather than per line, since the same product/price
    # can repeat identically across multiple POs from the same supplier and
    # only the group's full combination disambiguates which PO it belongs to.
    supplier_name_for_po_lookup = header.get("SupplierName")

    # Canonicalize the OCR-extracted SupplierName to SAP's own
    # S4_Suppliers_prod.supplier_name up front, e.g. "Airbnb Payments, Inc."
    # -> "Airbnb" - so the invoice is stored under the same supplier
    # identity SAP uses (find_duplicate_invoice, find_existing_s4_invoice,
    # customer_graph's supplier filter, etc. all key off SupplierName as
    # stored), rather than whatever wording this particular document
    # happened to print. Only overwrites when a match is actually found;
    # otherwise the raw extracted name is kept as-is.
    #
    # A curated alias check runs FIRST, before the fuzzy S4_Suppliers_prod
    # match: some invoices (e.g. a UnitedHealthcare/Oxford insurance
    # statement) only print a subsidiary/underwriter's legal name
    # ("Underwritten by Oxford Health Insurance, Inc.") while the real
    # billing company in SAP's supplier master is the parent brand
    # ("UnitedHealthcare") - the two names share no word in common, so no
    # amount of fuzzy matching alone can bridge that; see
    # _resolve_supplier_alias / Newyork_Supplier_Aliases.
    if supplier_name_for_po_lookup:
        name_conn = get_db_connection()
        name_cursor = name_conn.cursor()
        try:
            alias_canonical_name = _resolve_supplier_alias(name_cursor, supplier_name_for_po_lookup)
            lookup_name = alias_canonical_name or supplier_name_for_po_lookup
            if alias_canonical_name:
                logging.info(
                    f"[Supplier alias] {supplier_name_for_po_lookup!r} -> {alias_canonical_name!r} "
                    "(curated Newyork_Supplier_Aliases match)"
                )
            resolved_supplier = _resolve_supplier_id_and_name(name_cursor, lookup_name)
        finally:
            name_cursor.close()
            name_conn.close()

        final_supplier_name = resolved_supplier[1] if resolved_supplier else alias_canonical_name
        if final_supplier_name and final_supplier_name.strip() != supplier_name_for_po_lookup.strip():
            logging.info(
                f"[Supplier match] Canonicalizing SupplierName "
                f"{supplier_name_for_po_lookup!r} -> {final_supplier_name!r}"
            )
            header["SupplierName"] = final_supplier_name
            supplier_name_for_po_lookup = final_supplier_name

    groups_by_idx: Dict[Any, List[dict]] = {}
    for it in items:
        groups_by_idx.setdefault(it.get("_po_group_idx"), []).append(it)

    for group_items in groups_by_idx.values():
        if all(it.get("PONumber") for it in group_items):
            continue

        # Ordered list of totals worth trying against a candidate PO: the
        # group's own subtotal (Po_Total - printed pre-tax/pre-freight)
        # first, then - only when there's just one PO group on the whole
        # invoice - the document's overall Invoice_Total too. Some
        # suppliers' invoices print a subtotal that's smaller than what SAP
        # actually recorded as the PO's amount, because SAP's PO total
        # bundles freight/tax in with it (e.g. a $600 rental subtotal + $250
        # freight + $53.25 tax = the $903.25 PO actually on file) - so the
        # group subtotal alone isn't always the number that reconciles.
        candidate_totals = []
        group_po_total = group_items[0].get("Po_Total") if group_items else None
        if group_po_total:
            candidate_totals.append(group_po_total)
        if len(groups_by_idx) == 1 and header.get("Invoice_Total"):
            candidate_totals.append(header.get("Invoice_Total"))
        seen_totals = set()
        candidate_totals = [t for t in candidate_totals if not (t in seen_totals or seen_totals.add(t))]
        primary_target_total = candidate_totals[0] if candidate_totals else None

        matched_po = find_po_for_group_by_description_and_price(
            supplier_name_for_po_lookup, group_items,
            invoice_date_str=header.get("InvoiceDate"),
            target_total=primary_target_total,
            current_pdf_id=pdf_id,
        )
        match_comment = None
        if not matched_po:
            # Price/description matching only works when the PO's own lines
            # mirror the invoice's lines. A lump-sum/service PO (a single
            # line covering the whole job) never will, so fall back to
            # matching a total against each candidate PO's total instead -
            # trying each of candidate_totals in turn until one finds an
            # exact match.
            for candidate_total in candidate_totals:
                matched_po, match_comment = find_po_by_supplier_and_total(
                    supplier_name_for_po_lookup, candidate_total,
                    invoice_date_str=header.get("InvoiceDate"),
                )
                if matched_po:
                    break

            # None of the candidate totals matched a PO exactly. Rather than
            # leaving the group unmatched, pick whichever PO's own total is
            # numerically CLOSEST to the primary target total (e.g. PO
            # totals 3000/3500/3300 against an invoice total of 3100 picks
            # the 3300 PO) and record how far off it is as a percentage, so
            # the mismatch is visible in the stored comments instead of the
            # invoice simply falling through unmatched.
            if not matched_po and primary_target_total:
                matched_po, match_comment = find_po_by_supplier_and_total(
                    supplier_name_for_po_lookup, primary_target_total,
                    invoice_date_str=header.get("InvoiceDate"),
                    allow_nearest_fallback=True,
                )
        if matched_po:
            for it in group_items:
                if not it.get("PONumber"):
                    it["PONumber"] = matched_po
                    it["PONumber_matched"] = True
                    if match_comment:
                        it["PONumber_match_comment"] = match_comment
            if match_comment:
                header.setdefault("_po_match_comments", []).append(match_comment)

    for it in items:
        it.pop("_po_group_idx", None)

    header.pop("PONumbers", None)
    header.pop("PONumber", None)
    header.pop("PONumber_fallback", None)

    po_match_comments = header.pop("_po_match_comments", None)
    if po_match_comments:
        header["PO_Match_Comment"] = "\n".join(dict.fromkeys(po_match_comments))

    invoice_data = {"source_file": filename, **header, "Items": items, "token_usage": token_usage}

    if not any(key in header for key in ["BankName", "IBAN", "AccountNumber", "BankAccountName"]):
        for bank_key in ["BankName", "BankAccountName", "AccountNumber", "IBAN", "SWIFTCode", "BankBranch"]:
            invoice_data.pop(bank_key, None)

    return invoice_data


def process_file_with_gemini(file_bytes: bytes, filename: str, user_id: str, pdf_id: str) -> List[Tuple[str, dict]]:
    """
    Extracts one PDF/image via Gemini and returns a list of (pdf_id,
    invoice_data) pairs - almost always exactly one, but more than one when
    the source document genuinely contains multiple invoices (some
    suppliers routinely send a single PDF covering several distinct
    InvoiceNumbers). The caller-given `pdf_id` is used for the first
    invoice found; a fresh one (generate_pdf_id()) is minted for each
    additional distinct InvoiceNumber, so every invoice gets its own
    Newyork_FileInput/Newyork_Invoice_extracted_data record and its own
    independent PO/receipt comparison - callers are expected to loop over
    the returned list and run the normal per-invoice ingestion pipeline for
    each entry.
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "pdf"
    mime_map = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
    mime_type = mime_map.get(ext, "application/pdf")

    active_prompt = NEWYORK_PROMPT
    logging.info(f"File: {filename} | New York-only processing")

    file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    MAX_RETRIES = 5
    response = None
    token_usage = {}

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[active_prompt, file_part],
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            )

            try:
                if hasattr(response, 'usage_metadata') and response.usage_metadata is not None:
                    usage = response.usage_metadata
                    token_usage = {
                        "pdf_id": pdf_id,
                        "filename": filename,
                        "prompt_tokens": getattr(usage, 'prompt_token_count', 0),
                        "candidates_tokens": getattr(usage, 'candidates_token_count', 0),
                        "thoughts_tokens": getattr(usage, 'thoughts_token_count', 0),
                        "total_tokens": getattr(usage, 'total_token_count', 0),
                        "cached_tokens": getattr(usage, 'cached_content_token_count', 0)
                    }
                    if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                        for detail in usage.prompt_tokens_details:
                            if hasattr(detail, 'modality') and hasattr(detail, 'token_count'):
                                modality = str(detail.modality).replace('MediaModality.', '')
                                token_usage[f"{modality}_tokens"] = detail.token_count

                    logging.info(f"TOKEN USAGE for {pdf_id}:")
                    logging.info(f"   Prompt tokens: {token_usage['prompt_tokens']:,}")
                    logging.info(f"   Candidates tokens: {token_usage['candidates_tokens']:,}")
                    logging.info(f"   Thoughts tokens: {token_usage['thoughts_tokens']:,}")
                    logging.info(f"   Cached tokens: {token_usage['cached_tokens']:,}")
                    logging.info(f"   TOTAL tokens: {token_usage['total_tokens']:,}")
                else:
                    logging.info(f"Processed file: {pdf_id} - {filename}")
                    logging.info("Token usage not available")
            except Exception as token_error:
                logging.warning(f"Could not retrieve token usage: {token_error}")
                logging.info(f"Processed file: {pdf_id} - {filename}")

            break

        except Exception as e:
            if "503" in str(e) and attempt < MAX_RETRIES - 1:
                logging.info(f"Gemini busy... retrying (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(3 ** attempt)
            else:
                logging.error(f"Gemini API error for {pdf_id}: {str(e)}")
                raise e

    if response is None:
        raise Exception(f"Failed to get response from Gemini after {MAX_RETRIES} attempts")

    extracted = json.loads(response.text)

    # Gemini's response_mime_type="application/json" has no schema-level
    # constraint forcing a single object - for a document that genuinely
    # contains more than one invoice (e.g. two distinct InvoiceNumbers
    # printed on one scanned PDF, which some suppliers send routinely), it
    # can return a top-level JSON ARRAY of invoice objects instead of one.
    # Normalize to always work off a list from here on, one element per
    # invoice.
    if not isinstance(extracted, list):
        extracted = [extracted]

    results: List[Tuple[str, dict]] = []
    seen_invoice_numbers: Dict[str, str] = {}  # InvoiceNumber -> pdf_id already assigned
    for element in extracted:
        if not isinstance(element, dict):
            logging.warning(f"[{filename}] Skipping a non-object entry in Gemini's extracted list: {element!r}")
            continue

        # The caller-given pdf_id is spent on the first invoice actually
        # kept; every additional distinct invoice mints its own.
        invoice_pdf_id = pdf_id if not results else generate_pdf_id()
        invoice_data = _build_invoice_data_from_extracted(element, filename, invoice_pdf_id, token_usage)

        invoice_number = (invoice_data.get("InvoiceNumber") or "").strip()
        if invoice_number and invoice_number in seen_invoice_numbers:
            # The SAME InvoiceNumber appearing twice in one Gemini response
            # is a duplicate read of the same invoice, not a second one -
            # keep only the first occurrence rather than minting a second
            # pdf_id for what's really the same document.
            logging.info(
                f"[{filename}] InvoiceNumber '{invoice_number}' already seen in this extraction - "
                f"skipping duplicate entry (keeping pdf_id={seen_invoice_numbers[invoice_number]})"
            )
            continue
        if invoice_number:
            seen_invoice_numbers[invoice_number] = invoice_pdf_id

        results.append((invoice_pdf_id, invoice_data))

    if not results:
        raise ValueError(f"Gemini returned no usable invoice object(s) for '{filename}'")

    if len(results) > 1:
        logging.info(
            f"[{filename}] Gemini extracted {len(results)} distinct invoices from one file: "
            + ", ".join(f"pdf_id={pid} InvoiceNumber={inv.get('InvoiceNumber')!r}" for pid, inv in results)
        )

    return results



def _invoice_total_is_zero(invoice_data: dict) -> bool:
    """
    True when the extracted Invoice_Total parses to exactly 0 - some
    automated billing systems (e.g. a Starlink/roaming service notice) emit
    a $0.00 placeholder document that isn't a real payable invoice. These
    are skipped entirely (no Newyork_FileInput or
    Newyork_Invoice_extracted_data row, and an existing duplicate record is
    left untouched rather than being overwritten with $0 data), since a $0
    invoice can never usefully be PO/receipt-matched or paid. A missing/
    blank Invoice_Total is NOT treated as zero here - that's a different,
    already-handled case (falls through to the normal 'failed' PO-matching
    path with real comments).
    """
    raw_total = invoice_data.get("Invoice_Total")
    if raw_total in (None, ""):
        return False
    try:
        return float(str(raw_total).replace(",", "")) == 0.0
    except (TypeError, ValueError):
        return False


def _prepare_file_bytes_for_storage(filename: str, file_bytes: bytes) -> bytes:
    """
    The stored file_base64 (served via GET /base64-for-view-icon/{pdf_id})
    is always rendered by the frontend as a PDF - an image (.png/.jpg/
    .jpeg) stored as-is fails to load there ("Failed to load PDF
    document"). Converting any image to a single-page PDF before storing
    means the viewer always gets a real PDF regardless of what was
    actually uploaded/scanned, with no frontend change needed.

    Only affects what gets PERSISTED for viewing - the caller must keep
    using the ORIGINAL file_bytes for the Gemini extraction call itself
    (process_file_with_gemini already handles images directly and doesn't
    need this conversion; converting before extraction would just be
    unnecessary lossy rework).
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ("png", "jpg", "jpeg"):
        return file_bytes
    try:
        image = Image.open(io.BytesIO(file_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PDF")
        return buffer.getvalue()
    except Exception as e:
        logging.warning(f"[{filename}] Failed to convert image to PDF for storage - storing original bytes: {e}")
        return file_bytes


def _ingest_extracted_invoice(pdf_id: str, invoice_data: dict, filename: str, file_bytes: bytes, user_id: str) -> dict:
    """
    Shared per-invoice ingestion pipeline (zero-total check -> SAP-duplicate
    check -> DB insert -> PO/receipt comparison) used by every entry point
    (manual upload, ZIP, SFTP single-file, SFTP folder scan). Factored out
    of what used to be duplicated in each of those so a multi-invoice PDF
    (see process_file_with_gemini) can run this once per extracted invoice
    instead of once per file - each invoice gets its own pdf_id and its own
    full pipeline run, sharing only the same underlying `file_bytes`.
    """
    if _invoice_total_is_zero(invoice_data):
        return {
            "filename": filename, "pdf_id": pdf_id, "status": "skipped",
            "invoices": [invoice_data],
            "message": "Invoice_Total extracted as 0.00 - not a valid payable invoice, not stored",
        }

    # Convert once here (not in the caller) so both the fresh-insert path
    # below AND handle_possible_duplicate_invoice's re-upload/update path
    # persist a real PDF, regardless of what was actually uploaded/scanned.
    storage_bytes = _prepare_file_bytes_for_storage(filename, file_bytes)

    dup_result = handle_possible_duplicate_invoice(invoice_data, filename, storage_bytes, user_id)
    if dup_result:
        return dup_result

    insert_invoice_to_db(invoice_data, pdf_id, user_id)
    file_input_id = insert_fileinput_to_db(
        filename=filename, pdf_id=pdf_id,
        supplier_name=invoice_data.get("SupplierName"), pdf_bytes=storage_bytes,
        status="Success", user_id=user_id,
        invoice_number=invoice_data.get("InvoiceNumber"),
    )

    comparison_status, compare_comments = run_po_comparison_and_update_status(
        pdf_id, filename, file_input_id,
        po_match_comment=invoice_data.get("PO_Match_Comment"),
    )

    return {
        "filename": filename, "pdf_id": pdf_id, "status": "success",
        "invoices": [invoice_data],
        "comparison_status": comparison_status, "comparison_comments": compare_comments,
    }


def process_single_file_sync(filename, file_bytes, user_id, person_name, email, ariba_user_name):
    try:
        pdf_id = generate_pdf_id()
        extracted_invoices = process_file_with_gemini(file_bytes, filename, user_id, pdf_id)

        results = []
        for invoice_pdf_id, invoice_data in extracted_invoices:
            result = _ingest_extracted_invoice(invoice_pdf_id, invoice_data, filename, file_bytes, user_id)
            results.append({
                **result, "user_id": user_id, "person_name": person_name,
                "email": email, "ariba_user_name": ariba_user_name,
            })
        return results
    except Exception as e:
        return [{"filename": filename, "status": "failed", "error": str(e)}]

async def process_zip_file_endpoint(file_bytes, filename, user_id, pdf_id, person_name, email, ariba_user_name):
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zip_file:
            tasks = []
            for zip_info in zip_file.infolist():
                if zip_info.is_dir():
                    continue
                file_ext = os.path.splitext(zip_info.filename.lower())[1]
                if file_ext not in {".pdf", ".png", ".jpeg", ".jpg"}:
                    continue

                async def process_inner(zip_info=zip_info):
                    try:
                        file_pdf_id = generate_pdf_id()
                        with zip_file.open(zip_info) as inner_file:
                            inner_bytes = inner_file.read()
                        extracted_invoices = process_file_with_gemini(inner_bytes, zip_info.filename, user_id, file_pdf_id)

                        results = []
                        for invoice_pdf_id, invoice_data in extracted_invoices:
                            result = _ingest_extracted_invoice(
                                invoice_pdf_id, invoice_data, zip_info.filename, inner_bytes, user_id
                            )
                            results.append({
                                **result, "user_id": user_id, "person_name": person_name,
                                "email": email, "ariba_user_name": ariba_user_name,
                            })
                        return results
                    except Exception as e:
                        return [{"filename": zip_info.filename, "status": "failed", "error": str(e)}]

                tasks.append(process_inner())

            nested_results = await asyncio.gather(*tasks)
            flat_results = []
            for item in nested_results:
                if isinstance(item, list):
                    flat_results.extend(item)
                else:
                    flat_results.append(item)
            return flat_results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing ZIP: {str(e)}")


# ============================================================================
# 12.1 SFTP FOLDER PROCESSING (mirrors ocr_main.py's
# /process-local-folder-invoices/ contract, but over SFTP for a caller-given
# path instead of a local disk path)
# ============================================================================
NEWYORK_EMAIL_INVOICES_PATH = "/home/chervik/NewYork_ocr_invoices/email_invoices"


def _process_one_sftp_invoice(sftp, folder_path: str, filename: str) -> List[dict]:
    """Extract + insert + PO-compare every invoice found in a single PDF
    already confirmed to exist at '{folder_path}/{filename}' on the given
    open SFTP session - almost always one invoice, but more than one for a
    genuinely multi-invoice PDF (see process_file_with_gemini), each
    getting its own pdf_id and its own full ingestion pipeline. Writes ONE
    '.processed' marker next to the file covering all of them (the
    underlying source file is shared and can't be split). Shared by
    process_sftp_folder_invoices (whole-folder scan) and
    process_sftp_single_invoice (one named file)."""
    remote_path = f"{folder_path}/{filename}"
    with sftp.open(remote_path, "rb") as f:
        file_bytes = f.read()

    pdf_id = generate_pdf_id()
    extracted_invoices = process_file_with_gemini(file_bytes, filename, USER_ID, pdf_id)

    results = []
    for invoice_pdf_id, invoice_data in extracted_invoices:
        result = _ingest_extracted_invoice(invoice_pdf_id, invoice_data, filename, file_bytes, USER_ID)
        # Normalize so comparison_status/comparison_comments are always
        # present regardless of which branch _ingest_extracted_invoice
        # took (skip/duplicate results don't set them) - the folder scan's
        # own logging relies on both always being there.
        result.setdefault("comparison_status", result.get("status"))
        result.setdefault("comparison_comments", result.get("message", ""))
        results.append(result)

    marker_lines = [
        f"pdf_id={r.get('pdf_id')} ({r.get('comparison_status', r.get('status'))})" for r in results
    ]
    with sftp.open(f"{remote_path}.processed", "w") as marker:
        marker.write("\n".join(marker_lines) + "\n")

    return results


def process_sftp_single_invoice(filename: str, folder_path: str = NEWYORK_EMAIL_INVOICES_PATH) -> dict:
    """Extract + PO-compare exactly one named PDF sitting in `folder_path` on
    the SFTP server (default: the New York email_invoices inbox) - the
    single-file counterpart to process_sftp_folder_invoices, for
    testing/reprocessing one specific invoice on demand without scanning the
    whole folder."""
    ssh = sftp = None
    try:
        ssh, sftp = _ny_create_sftp_connection()
    except Exception as e:
        logging.error(f"[SFTP single] Could not connect to SFTP: {e}")
        return {"status": "error", "message": str(e), "folder_path": folder_path, "filename": filename}

    try:
        remote_path = f"{folder_path}/{filename}"
        try:
            sftp.stat(remote_path)
        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"'{filename}' not found in '{folder_path}'",
                "folder_path": folder_path, "filename": filename,
            }

        logging.info(f"[SFTP single] extracting '{filename}' from '{folder_path}'...")
        results = _process_one_sftp_invoice(sftp, folder_path, filename)
        logging.info(
            f"[SFTP single] OK -> {len(results)} invoice(s): "
            + ", ".join(f"pdf_id={r.get('pdf_id')} (comparison: {r.get('comparison_status')})" for r in results)
        )
        return {"status": "completed", "folder_path": folder_path, "filename": filename, "results": results}
    except Exception as e:
        logging.error(f"[SFTP single] FAILED '{filename}': {e}")
        return {"status": "error", "message": str(e), "folder_path": folder_path, "filename": filename}
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass


NEWYORK_SFTP_PARALLEL_WORKERS = 5


def _process_one_sftp_invoice_standalone(folder_path: str, filename: str) -> List[dict]:
    """Worker-thread entry point for the parallel folder scan below - opens
    its own SSH/SFTP connection rather than sharing the listing connection
    across threads (paramiko's SFTPClient isn't safe to use concurrently
    from multiple threads), mirroring ocr_sftp.py's
    _process_single_sftp_file pattern."""
    ssh = sftp = None
    try:
        ssh, sftp = _ny_create_sftp_connection()
        return _process_one_sftp_invoice(sftp, folder_path, filename)
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass


def process_sftp_folder_invoices(
    folder_path: str, max_files: int = 200, parallel_workers: int = NEWYORK_SFTP_PARALLEL_WORKERS
) -> dict:
    """
    Given a raw SFTP folder path, lists every PDF/image invoice file in it
    (.pdf, .png, .jpeg, .jpg - same set /extract-invoice accepts for manual
    upload), skips any that already have a '<filename>.processed' marker
    sitting next to them (same idiom ocr_main.py uses for both its
    local-folder and SFTP pipelines), and runs each remaining one through
    the normal extraction pipeline (process_file_with_gemini ->
    insert_invoice_to_db -> insert_fileinput_to_db), then compares it
    against PO/receipt data the same way /compare-invoice-ariba-receipt_id
    does (perform_comparison_and_check_match) and records the outcome on
    Newyork_FileInput.status/comments - "Success" and the match summary if
    it matched, "failed" and the mismatch comments if not. No RPA call, no
    further pipeline - extraction + comparison only.

    Processes up to `parallel_workers` files at a time (default 5), the same
    ThreadPoolExecutor pattern ocr_sftp.py's run_sftp_extraction uses - each
    worker opens its own SFTP connection instead of sharing one.

    On success (extraction + DB insert, regardless of match/mismatch), writes
    the marker so a repeat scan of the same folder skips it; on failure
    (extraction/DB error), no marker is written, so it's retried next call.
    """
    try:
        ssh, sftp = _ny_create_sftp_connection()
    except Exception as e:
        logging.error(f"[SFTP folder] Could not connect to SFTP: {e}")
        return {"status": "error", "message": str(e), "folder_path": folder_path, "summary": {}, "results": []}

    try:
        try:
            entries = sftp.listdir_attr(folder_path)
        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"folder_path '{folder_path}' does not exist on the SFTP server",
                "folder_path": folder_path, "summary": {}, "results": [],
            }

        allowed_extensions = (".pdf", ".png", ".jpeg", ".jpg")
        invoice_filenames = sorted(
            e.filename for e in entries if e.filename.lower().endswith(allowed_extensions)
        )
        # Marker files are always "<original_filename>.processed" regardless
        # of the original extension, so this just needs the ".processed"
        # suffix, not a per-extension check.
        marker_names = {e.filename for e in entries if e.filename.lower().endswith(".processed")}
        pending = [f for f in invoice_filenames if f"{f}.processed" not in marker_names]
        skipped_count = len(invoice_filenames) - len(pending)

        logging.info(
            f"[SFTP folder] '{folder_path}': {len(invoice_filenames)} file(s), "
            f"{skipped_count} already processed, {len(pending)} pending"
        )
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass

    to_process = pending[:max_files]
    results = []
    success_count = failed_count = 0

    logging.info(
        f"[SFTP folder] '{folder_path}': processing {len(to_process)} file(s) "
        f"with {parallel_workers} worker(s)..."
    )

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        future_to_filename = {
            executor.submit(_process_one_sftp_invoice_standalone, folder_path, filename): filename
            for filename in to_process
        }
        for future in as_completed(future_to_filename):
            filename = future_to_filename[future]
            try:
                file_results = future.result()
                results.extend(file_results)
                success_count += 1
                logging.info(
                    f"[SFTP folder] OK -> '{filename}' - {len(file_results)} invoice(s): "
                    + ", ".join(
                        f"pdf_id={r.get('pdf_id')} (comparison: {r.get('comparison_status')})"
                        for r in file_results
                    )
                )
            except Exception as e:
                results.append({"filename": filename, "status": "failed", "error": str(e)})
                failed_count += 1
                logging.error(f"[SFTP folder] FAILED '{filename}': {e}")

    logging.info(
        f"[SFTP folder] '{folder_path}' complete: success={success_count} "
        f"failed={failed_count} skipped={skipped_count}"
    )

    return {
        "status": "completed",
        "folder_path": folder_path,
        "summary": {
            "total_files": len(invoice_filenames),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "parallel_workers": parallel_workers,
        },
        "results": results,
    }


# ============================================================================
# 12.2 SFTP FOLDER SCHEDULER - scans NEWYORK_EMAIL_INVOICES_PATH for pending
# PDFs every NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES. Self-rescheduling
# rather than a fixed-interval trigger: each run only schedules the NEXT run
# once it finishes, so a run that takes longer than the interval never
# overlaps with, or immediately re-fires, the next one - the next run always
# starts `interval` minutes after the previous run's completion, not on a
# fixed clock cadence.
# ============================================================================
NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES = 30
NEWYORK_SFTP_SCHEDULER_JOB_ID = "newyork_sftp_invoice_processing"

newyork_sftp_scheduler = BackgroundScheduler()
_newyork_sftp_job_running = False


def insert_sftp_scheduler_log(
    folder_path: str, run_started_at, run_finished_at, status: str,
    total_files: int = None, success_count: int = None,
    failed_count: int = None, skipped_count: int = None,
    message: str = None, failed_files: List[dict] = None,
):
    """One row per _newyork_sftp_scheduled_job tick - lets 'is the SFTP
    scheduler actually running every NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES'
    be answered by querying Newyork_Sftp_Scheduler_Logs instead of digging
    through process logs. Mirrors newyork_email_ocr's role for
    FETCH_EMAIL_SCHEDULER. Logs every tick, including a run that found 0
    pending files - that's still evidence the scheduler fired.

    `failed_files` (optional list of {"filename", "error"}) - one exception
    per file that process_sftp_folder_invoices' ThreadPoolExecutor loop
    caught (a genuine code/extraction error, not a business-logic PO/total
    mismatch) - gets persisted into the Newyork_Sftp_Scheduler_Log_Errors
    child table, keyed by this run's log id, so "why did N files fail this
    run" is answerable from SQL without needing console/process-log access.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO Newyork_Sftp_Scheduler_Logs
                (folder_path, run_started_at, run_finished_at, status,
                 total_files, success_count, failed_count, skipped_count, message)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                folder_path, run_started_at, run_finished_at, status,
                total_files, success_count, failed_count, skipped_count, message,
            ),
        )
        log_id = cursor.fetchone()[0]

        for entry in (failed_files or []):
            cursor.execute(
                """
                INSERT INTO Newyork_Sftp_Scheduler_Log_Errors (log_id, filename, error_message)
                VALUES (?, ?, ?)
                """,
                (log_id, entry.get("filename"), entry.get("error")),
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to log SFTP scheduler run: {e}")
    finally:
        cursor.close()
        conn.close()


def _newyork_sftp_scheduled_job():
    global _newyork_sftp_job_running
    if _newyork_sftp_job_running:
        logging.warning("[NY SFTP scheduler] Previous run still in progress - skipping this tick")
        return

    _newyork_sftp_job_running = True
    run_started_at = datetime.now()
    try:
        logging.info(f"[NY SFTP scheduler] Run starting for '{NEWYORK_EMAIL_INVOICES_PATH}'")
        result = process_sftp_folder_invoices(NEWYORK_EMAIL_INVOICES_PATH)
        logging.info(f"[NY SFTP scheduler] Run complete: {result.get('summary')}")
        summary = result.get("summary") or {}
        failed_files = [
            {"filename": r.get("filename"), "error": r.get("error")}
            for r in (result.get("results") or [])
            if r.get("status") == "failed" and r.get("error")
        ]
        insert_sftp_scheduler_log(
            NEWYORK_EMAIL_INVOICES_PATH, run_started_at, datetime.now(),
            result.get("status", "completed"),
            total_files=summary.get("total_files"),
            success_count=summary.get("success_count"),
            failed_count=summary.get("failed_count"),
            skipped_count=summary.get("skipped_count"),
            message=result.get("message"),
            failed_files=failed_files,
        )
    except Exception as e:
        logging.error(f"[NY SFTP scheduler] Run failed: {e}", exc_info=True)
        insert_sftp_scheduler_log(
            NEWYORK_EMAIL_INVOICES_PATH, run_started_at, datetime.now(), "error",
            message=str(e),
        )
    finally:
        _newyork_sftp_job_running = False
        next_run = datetime.now() + timedelta(minutes=NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES)
        newyork_sftp_scheduler.add_job(
            _newyork_sftp_scheduled_job,
            trigger=DateTrigger(run_date=next_run),
            id=NEWYORK_SFTP_SCHEDULER_JOB_ID,
            replace_existing=True,
        )
        logging.info(f"[NY SFTP scheduler] Next run scheduled for {next_run.isoformat()}")


def start_newyork_sftp_scheduler():
    """Call once at app startup. Kicks off the first run immediately; every
    run after that reschedules itself NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES
    after it finishes. No-op if SFTP_SCHEDULER is False."""
    if not SFTP_SCHEDULER:
        logging.info("[NY SFTP scheduler] SFTP_SCHEDULER=False - not starting")
        return
    if not newyork_sftp_scheduler.running:
        newyork_sftp_scheduler.start()
    newyork_sftp_scheduler.add_job(
        _newyork_sftp_scheduled_job,
        trigger=DateTrigger(run_date=datetime.now()),
        id=NEWYORK_SFTP_SCHEDULER_JOB_ID,
        replace_existing=True,
    )
    logging.info(
        f"[NY SFTP scheduler] Started - first run now, then every "
        f"{NEWYORK_SFTP_SCHEDULER_INTERVAL_MINUTES} min after each run completes."
    )


# ============================================================================
# 12.3 EMAIL -> SFTP INGESTION
# Polls the mailbox (via Microsoft Graph) for "invoice" emails and drops
# their PDF attachments straight into NEWYORK_EMAIL_INVOICES_PATH on SFTP -
# no separate email-specific OCR/DB path. The regular SFTP folder scan
# (process_sftp_folder_invoices, on its own 30-min scheduler) picks these
# files up from there exactly like any other file dropped in that folder.
# ============================================================================
_graph_token_cache: Dict[str, Any] = {}


def _get_graph_api_access_token() -> str:
    """Exchange Azure AD app credentials for a Microsoft Graph access token
    (client-credentials flow), caching it until shortly before expiry."""
    now = time.time()
    if _graph_token_cache.get("access_token") and now < _graph_token_cache.get("expires_at", 0) - 60:
        return _graph_token_cache["access_token"]

    token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(token_url, data={
        "grant_type": "client_credentials",
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    })
    resp.raise_for_status()
    data = resp.json()

    _graph_token_cache["access_token"] = data["access_token"]
    _graph_token_cache["expires_at"] = now + int(data.get("expires_in", 3600))
    return _graph_token_cache["access_token"]


def _save_email_attachment_to_sftp(sftp, part, filename: str) -> dict:
    """Write one PDF attachment straight into NEWYORK_EMAIL_INVOICES_PATH on
    SFTP. A uuid prefix avoids collisions between different emails that
    happen to attach a file with the same name (e.g. every supplier sending
    'Invoice.pdf')."""
    try:
        file_bytes = part.get_payload(decode=True)
        if not file_bytes:
            return {"filename": filename, "status": "failed", "message": "Empty PDF attachment"}

        remote_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
        remote_path = f"{NEWYORK_EMAIL_INVOICES_PATH}/{remote_filename}"
        with sftp.open(remote_path, "wb") as f:
            f.write(file_bytes)

        return {"filename": filename, "remote_filename": remote_filename, "status": "saved"}
    except Exception as e:
        return {"filename": filename, "status": "failed", "message": str(e)}


 
def process_email_attachments_to_sftp(sftp, msg) -> List[dict]:
    """Extract every PDF and image attachment from one email message and save each to SFTP."""
    if not msg.is_multipart():
        return []
 
    subject = msg["subject"] or "No Subject"
    try:
        subject = str(make_header(decode_header(subject)))
    except Exception:
        pass
 
    attachments = [
        (part, part.get_filename())
        for part in msg.walk()
        if part.get_content_disposition() is not None
        and (
        part.get_content_type() == 'application/pdf'
            or part.get_content_maintype() == 'image'
        )
    ]
    if not attachments:
        return []
 
    logging.info(f"[Email->SFTP] '{subject}': {len(attachments)} PDF/image attachment(s)")
    return [_save_email_attachment_to_sftp(sftp, part, filename) for part, filename in attachments]
 


def fetch_invoices_from_email(from_datetime: datetime, end_datetime: datetime) -> Tuple[List[dict], Optional[str]]:
    """Poll the mailbox (via Microsoft Graph) for 'invoice' emails within
    [from_datetime, end_datetime) and save their PDF attachments to SFTP."""
    results: List[dict] = []
    error_message = None
    ssh = sftp = None
    try:
        IST = timezone(timedelta(hours=5, minutes=30))
        if from_datetime.tzinfo is None:
            from_datetime = from_datetime.replace(tzinfo=IST)
        if end_datetime.tzinfo is None:
            end_datetime = end_datetime.replace(tzinfo=IST)
        from_datetime = from_datetime.astimezone(timezone.utc)
        end_datetime = end_datetime.astimezone(timezone.utc)
 
        from_iso = from_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
 
        token = _get_graph_api_access_token()
        headers = {"Authorization": f"Bearer {token}"}
 
        url = f"https://graph.microsoft.com/v1.0/users/{EMAIL_USER}/mailFolders/inbox/messages"
        params = {
            "$filter": f"receivedDateTime ge {from_iso} and receivedDateTime lt {end_iso}",
            "$select": "id,subject",
            "$top": 50,
        }
 
        message_ids = []
        while url:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                return [], f"Failed to search emails: {resp.text[:300]}"
            data = resp.json()
            for m in data.get("value", []):
                subject_raw = m.get("subject") or ""
                
                if not re.search(r"\binvoices?\b",subject_raw, re.IGNORECASE):
                    continue
                message_ids.append(m["id"])
            url = data.get("@odata.nextLink")
            params = None  # nextLink already carries the query params
 
        if not message_ids:
            return [], None
 
        ssh, sftp = _ny_create_sftp_connection()
 
        for msg_id in message_ids:
            mime_resp = requests.get(
                f"https://graph.microsoft.com/v1.0/users/{EMAIL_USER}/messages/{msg_id}/$value",
                headers=headers, timeout=60,
            )
            if mime_resp.status_code != 200:
                continue
            msg = email_module.message_from_bytes(mime_resp.content)
            results.extend(process_email_attachments_to_sftp(sftp, msg))
            requests.patch(
                f"https://graph.microsoft.com/v1.0/users/{EMAIL_USER}/messages/{msg_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"isRead": True}, timeout=30,
            )
 
    except Exception as e:
        error_message = f"Error fetching invoices from email: {str(e)}"
        logging.error(error_message)
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
    return results, error_message

# ============================================================================
# 14. INVOICE COMPARISON HELPER FUNCTIONS
# ============================================================================
def fetch_invoice_data(pdf_id: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT PONumber, Invoice_Total, InvoiceDate, SupplierName, LineItemNumber, ItemCode, ItemDescription, QTY, Unit, UnitPrice, Amount,
               VAT, VATAmount, AmountInclVAT
        FROM final_pulse_DBprod.dbo.Newyork_Invoice_extracted_data WHERE pdf_id = ? ORDER BY id ASC
    """, (pdf_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    if not rows:
        return {}

    po_numbers = []
    for r in rows:
        raw_po = r.PONumber
        if not raw_po:
            continue
        po_numbers.extend([p.strip() for p in str(raw_po).split(",") if p.strip()])

    return {
        "invoice_po_number": list(dict.fromkeys(po_numbers)),
        "invoice_total": str(rows[0].Invoice_Total) if rows[0].Invoice_Total else None,
        "invoice_date_str": str(rows[0].InvoiceDate) if rows[0].InvoiceDate else None,
        "supplier_name": str(rows[0].SupplierName) if rows[0].SupplierName else None,
        "line_items": [{
            "po_number": str(r.PONumber) if r.PONumber else None,
            "line_item": str(r.LineItemNumber) if r.LineItemNumber is not None else None,
            "ItemCode": str(r.ItemCode) if r.ItemCode is not None else None,
            "ItemDescription": str(r.ItemDescription) if r.ItemDescription else None,
            "QTY": str(r.QTY) if r.QTY is not None else None,
            "Unit": str(r.Unit) if r.Unit else None,
            "UnitPrice": str(r.UnitPrice) if r.UnitPrice is not None else None,
            "Amount": str(r.Amount) if r.Amount is not None else None,
            "VAT": str(r.VAT) if r.VAT is not None else None,
            "VATAmount": str(r.VATAmount) if r.VATAmount is not None else None,
            "AmountInclVAT": str(r.AmountInclVAT) if r.AmountInclVAT is not None else None,
        } for r in rows]
    }


def find_existing_s4_invoice_for_values(
    invoice_number: Optional[str], po_numbers: Optional[List[str]] = None, supplier_name: Optional[str] = None
) -> Optional[str]:
    """
    Core SAP-duplicate lookup, keyed on values directly rather than a pdf_id -
    usable BEFORE anything has been inserted into Newyork_Invoice_extracted_data
    yet (e.g. right after OCR extraction), so a brand-new upload of an
    already-posted invoice can be recognized and skipped without ever being
    written to Newyork_FileInput at all. find_existing_s4_invoice() (the
    pdf_id-based version used once a record already exists) delegates here.

    Matches InvoiceNumber against S4_Invoice_Data.supplier_invoice_id_by_invcg_party,
    and confirms the vendor (a supplier_id) against S4_Invoice_Data.invoicing_party.
    The supplier_id is resolved two ways, tried in order:
      1. From po_numbers' own S4_Purchase_Order_Data.supplier, when a PO is
         known.
      2. Otherwise - most importantly, when NO PO could be resolved at all -
         directly from the invoice's own SupplierName via S4_Suppliers_prod
         (the same cleaned-name matching the PO fallback uses). This means
         a "no PO found" invoice still gets checked against SAP before
         being marked failed, in case it was genuinely already posted and
         our PO-matching heuristics just couldn't independently re-derive
         which PO it was against.

    Returns the matched row's `supplier_invoice` (SAP doc number) if found, else None.
    """
    if not invoice_number or (not po_numbers and not supplier_name):
        return None
    invoice_number = str(invoice_number).strip()
    if not invoice_number:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        supplier_ids: List[str] = []
        if po_numbers:
            placeholders = ", ".join("?" * len(po_numbers))
            cursor.execute(f"""
                SELECT DISTINCT supplier
                FROM final_pulse_DBprod.dbo.S4_Purchase_Order_Data
                WHERE purchase_order IN ({placeholders}) AND supplier IS NOT NULL
            """, po_numbers)
            supplier_ids = [str(r.supplier).strip() for r in cursor.fetchall() if r.supplier]

        if not supplier_ids and supplier_name:
            supplier_ids = _get_supplier_ids_by_cleaned_name(cursor, supplier_name)

        if not supplier_ids:
            return None

        placeholders = ", ".join("?" * len(supplier_ids))
        cursor.execute(f"""
            SELECT TOP 1 supplier_invoice
            FROM final_pulse_DBprod.dbo.S4_Invoice_Data
            WHERE supplier_invoice_id_by_invcg_party = ?
              AND invoicing_party IN ({placeholders})
        """, (invoice_number, *supplier_ids))
        match = cursor.fetchone()
        return str(match.supplier_invoice) if match and match.supplier_invoice else None
    finally:
        cursor.close()
        conn.close()


def find_existing_s4_invoice(
    pdf_id: str, po_numbers: Optional[List[str]] = None, supplier_name: Optional[str] = None
) -> Optional[str]:
    """pdf_id-based wrapper around find_existing_s4_invoice_for_values - looks
    up this pdf_id's InvoiceNumber (already inserted into
    Newyork_Invoice_extracted_data) and delegates. Use
    find_existing_s4_invoice_for_values directly when no row exists yet."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT TOP 1 InvoiceNumber
            FROM final_pulse_DBprod.dbo.Newyork_Invoice_extracted_data
            WHERE pdf_id = ?
        """, (pdf_id,))
        row = cursor.fetchone()
        invoice_number = str(row.InvoiceNumber).strip() if row and row.InvoiceNumber else None
    finally:
        cursor.close()
        conn.close()

    return find_existing_s4_invoice_for_values(invoice_number, po_numbers=po_numbers, supplier_name=supplier_name)


def fetch_document_currency(po_number: List[str]) -> Optional[str]:
    """Look up document_currency for a PO from S4_Purchase_Order_Data (no hardcoded currency)."""
    if not po_number:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        placeholders = ", ".join("?" * len(po_number))
        cursor.execute(
            f"""
            SELECT TOP 1 document_currency
            FROM final_pulse_DBprod.dbo.S4_Purchase_Order_Data
            WHERE purchase_order IN ({placeholders}) AND document_currency IS NOT NULL
            """,
            po_number,
        )
        row = cursor.fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        cursor.close()
        conn.close()


def fetch_ariba_data(po_number: List[str], invoice_line_items: List[dict] = None) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    if not po_number:
        return {}

    placeholders = ', '.join(['?'] * len(po_number))
    cursor.execute(f"""
        SELECT
            purchase_order,
            purchase_order_item,
            material,
            purchase_order_item_text,
            material_group,
            plant,
            base_unit,
            purchase_order_quantity_unit,
            quantity,
            unit_price,
            net_amount,
            gross_amount,
            effective_amount,
            product_type_code,
            po_balance_amount
        FROM final_pulse_DBprod.dbo.S4_Purchase_Order_Data
        WHERE purchase_order IN ({placeholders})
        ORDER BY purchase_order, purchase_order_item
    """, tuple(po_number))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    if not rows:
        return {"ariba_total": 0.0, "line_items": [], "price_map": {}}

    price_map = {}
    base_lines = {}
    ariba_total = 0.0

    for r in rows:
        po = str(r.purchase_order)
        li = int(r.purchase_order_item)
        qty = float(r.quantity or 0)
        net_amount = float(r.net_amount or 0)
        gross_amount = float(r.gross_amount or 0)
        # effective_amount takes priority over gross_amount (falling back to
        # gross_amount, then net_amount) wherever this PO line's amount is
        # needed - effective_amount is often NULL/0/blank on older PO lines.
        effective_amount = float(_first_nonzero_amount(r.effective_amount, r.gross_amount, r.net_amount) or 0)
        unit_price = float(r.unit_price or 0)

        if qty > 0 and unit_price == 0:
            unit_price = net_amount / qty

        line_total = effective_amount if effective_amount else (gross_amount if gross_amount else net_amount)
        ariba_total += line_total
        item_description = str(r.purchase_order_item_text or "").strip() or str(r.material or "").strip()

        price_map[(po, li)] = unit_price
        base_lines[(po, li)] = {
            'po_number': po,
            'description': item_description,
            'quantity': qty,
            'unit_of_measure': str(r.purchase_order_quantity_unit or r.base_unit or ''),
            'price': net_amount,
            'unit_price': unit_price,
            'tax_amount': max(line_total - net_amount, 0.0),
            'line_total': line_total,
            'tax_percentage': 0.0,
            'product_type_code': str(r.product_type_code).strip() if r.product_type_code else None,
            'po_balance_amount': float(r.po_balance_amount) if r.po_balance_amount not in (None, "") else None,
        }

    ariba_line_items = []
    for key in sorted(base_lines.keys(), key=lambda k: (k[0], k[1])):
        po, li = key
        line_data = base_lines.get(key, {})
        net_price = float(line_data.get('price', 0.0) or 0.0)
        tax_amount = float(line_data.get('tax_amount', 0.0) or 0.0)
        line_total = float(line_data.get('line_total', net_price + tax_amount) or 0.0)
        quantity = float(line_data.get('quantity', 1.0) or 0.0)
        unit_price = float(line_data.get('unit_price', net_price / quantity if quantity > 0 else 0.0) or 0.0)
        ariba_line_items.append({
            "line_item": str(li),
            "po_number": po,
            "description": line_data.get('description'),
            "quantity": quantity,
            "unit_of_measure": line_data.get('unit_of_measure'),
            "unit_price": round(unit_price, 2),
            "price": round(net_price, 2),
            "tax_amount": round(tax_amount, 2),
            "line_total": round(line_total, 2),
            "tax_percentage": line_data.get('tax_percentage', 0),
            "product_type_code": line_data.get('product_type_code'),
            "po_balance_amount": line_data.get('po_balance_amount'),
        })

    return {
        "ariba_po_number": po_number,
        "ariba_total": round(ariba_total, 2),
        "line_items": ariba_line_items,
        "price_map": price_map,
        "base_lines": base_lines,
        "tax_map": {},
    }


def po_lines_are_service(ariba_line_items: List[dict]) -> bool:
    """A PO is a service PO when every one of its fetched line items carries
    product_type_code '2' - service POs never get a goods receipt (GRN), so
    they can't go through the material flow's receipt-based comparison at
    all. A PO with no lines, or a mix of '1'/'2' lines, is treated as
    material (the existing, safer default)."""
    if not ariba_line_items:
        return False
    return all(li.get("product_type_code") == "2" for li in ariba_line_items)


def allocate_service_po_amount(
    ariba_line_items: List[dict], invoice_total, tolerance: float = 0.99
) -> Tuple[List[dict], float, bool]:
    """FIFO-allocate a service invoice's total across its PO's line items, in
    purchase_order_item order, consuming each line's remaining balance
    (po_balance_amount, or the line's full amount the first time it's ever
    consumed) until the invoice total is exhausted.

    Returns (allocation, unallocated_remainder, fully_allocated).
    """
    try:
        remaining = round(float(str(invoice_total).replace(",", "")), 2) if invoice_total else 0.0
    except (ValueError, TypeError):
        remaining = 0.0

    allocation = []
    for li in sorted(ariba_line_items, key=lambda x: (x["po_number"], int(x["line_item"]))):
        if remaining <= 0:
            break
        balance = li.get("po_balance_amount")
        available = round(float(balance) if balance is not None else float(li.get("line_total") or 0), 2)
        if available <= 0:
            continue
        take = min(available, remaining)
        allocation.append({
            "po_number": li["po_number"],
            "line_item": li["line_item"],
            "description": li.get("description"),
            "available_balance": available,
            "amount_allocated": round(take, 2),
        })
        remaining = round(remaining - take, 2)

    fully_allocated = remaining <= tolerance
    return allocation, remaining, fully_allocated


def _find_receipt_combination(rec_list: list, target_qty: float, tolerance: float = 0.001) -> Optional[list]:
    """
    Find a subset of receipt rows whose NumberAccepted sums to target_qty.
    Uses a greedy approach first (sorted by date ASC so oldest receipts are
    preferred), then falls back to brute-force subset search for small lists.
    Returns the matching subset or None if no combination found.
    """
    # Sort oldest first â€” an invoice typically covers the earliest unmatched deliveries
    sorted_recs = sorted(rec_list, key=lambda r: r.LastModified or datetime.min)

    # Greedy: accumulate from oldest until we hit the target
    greedy_combo = []
    running = 0.0
    for r in sorted_recs:
        qty = float(r.NumberAccepted or 0)
        if qty <= 0:
            continue
        if running + qty <= target_qty + tolerance:
            greedy_combo.append(r)
            running += qty
            if abs(running - target_qty) <= tolerance:
                return greedy_combo

    # Brute-force subset search (only practical for small lists â‰¤ 20 rows)
    if len(rec_list) <= 20:
        from itertools import combinations
        for size in range(1, len(rec_list) + 1):
            for combo in combinations(sorted_recs, size):
                total = sum(float(r.NumberAccepted or 0) for r in combo)
                if abs(total - target_qty) <= tolerance:
                    return list(combo)

    return None

def _pick_best_exact_match(exact_candidates: list, invoice_date_str: str = None, used_rc_ids: set = None):
    if not exact_candidates:
        return None
    used_rc_ids = used_rc_ids or set()

    def sort_key(r):
        return r.ReceiptLineDate or datetime.min

    unused = sorted([r for r in exact_candidates if str(r.ReceiptId) not in used_rc_ids], key=sort_key)
    used   = sorted([r for r in exact_candidates if str(r.ReceiptId) in used_rc_ids], key=sort_key)

    invoice_date = None
    if invoice_date_str:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
                    "%d-%b-%Y", "%d %b %Y", "%b %d, %Y"):
            try:
                invoice_date = datetime.strptime(str(invoice_date_str).strip(), fmt)
                if invoice_date.year < 100:
                    invoice_date = invoice_date.replace(year=2000 + invoice_date.year)
                break
            except ValueError:
                continue

    def pick_from(pool):
        if not pool:
            return None
        if invoice_date:
            on_or_before = [r for r in pool if r.ReceiptLineDate and r.ReceiptLineDate.date() <= invoice_date.date()]
            if on_or_before:
                return min(on_or_before, key=sort_key)
        return min(pool, key=sort_key)

    best = pick_from(unused)
    if best is None:
        best = pick_from(used)
        if best:
            logging.warning(f"âš ï¸ All exact-match RCs already used â€” reusing RC={best.ReceiptId}")
    else:
        logging.info(f"ðŸ“Œ _pick_best_exact_match: picked oldest UNUSED RC={best.ReceiptId} date={best.ReceiptLineDate}")
    return best

def fetch_receipt_data(po_number: List[str], ariba_data: dict, invoice_items: List[dict] = None, invoice_date: str = None) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()

    if not po_number:
        return {}

    placeholders = ', '.join(['?'] * len(po_number))
    query = f"""
        SELECT
            purchase_order,
            purchase_order_item,
            material,
            material_document,
            quantity_in_base_unit,
            quantity_in_entry_unit,
            entry_unit,
            plant,
            storage_location,
            document_date,
            posting_date,
            creation_date_time,
            company_code_currency
        FROM final_pulse_DBprod.dbo.S4_GRN_Data
        WHERE purchase_order IN ({placeholders})
        ORDER BY purchase_order, purchase_order_item, creation_date_time
    """
    cursor.execute(query, tuple(po_number))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return {"receipt_total": 0.0, "line_items": [], "matched_receipt_ids": []}

    po_material_lookup: Dict[tuple, dict] = {}
    po_conn = get_db_connection()
    po_cursor = po_conn.cursor()
    po_cursor.execute(f"""
        SELECT purchase_order, purchase_order_item, material, purchase_order_item_text, quantity, unit_price, net_amount, gross_amount, effective_amount
        FROM final_pulse_DBprod.dbo.S4_Purchase_Order_Data
        WHERE purchase_order IN ({placeholders})
    """, tuple(po_number))
    for po_row in po_cursor.fetchall():
        po_key = (str(po_row.purchase_order), str(po_row.material or "").strip())
        qty = float(po_row.quantity or 0)
        unit_price = float(po_row.unit_price or 0)
        net_amount = float(po_row.net_amount or 0)
        if qty > 0 and unit_price == 0:
            unit_price = net_amount / qty
        description = str(po_row.purchase_order_item_text or "").strip() or str(po_row.material or "").strip()
        po_material_lookup[po_key] = {
            "purchase_order_item": int(po_row.purchase_order_item or 0),
            "material": str(po_row.material or "").strip(),
            "description": description,
            "quantity": qty,
            "unit_price": unit_price,
            "line_total": float(_first_nonzero_amount(po_row.effective_amount, po_row.gross_amount, net_amount) or 0),
        }
    po_cursor.close()
    po_conn.close()

    po_line_lookup: Dict[str, List[dict]] = {}
    po_line_by_material: Dict[tuple, List[dict]] = {}
    if ariba_data and ariba_data.get("line_items"):
        for po_line in ariba_data["line_items"]:
            po = str(po_line.get("po_number") or "").strip()
            if not po:
                continue
            entry = {
                "po_number": po,
                "line_item": str(po_line.get("line_item") or ""),
                "description": str(po_line.get("description") or ""),
                "quantity": float(po_line.get("quantity") or 0),
                "unit_price": float(po_line.get("unit_price") or 0),
                "line_total": float(po_line.get("line_total") or po_line.get("price") or 0),
            }
            po_line_lookup.setdefault(po, []).append(entry)
            material = str(entry["description"] or "").strip()
            if material:
                po_line_by_material.setdefault((po, material), []).append(entry)

    records_by_line: Dict[tuple, List] = {}
    for row in rows:
        row_po = str(row.purchase_order)
        # S4_GRN_Data.purchase_order_item is the raw SAP line (10, 20, 30, ...);
        # normalize to 1, 2, 3, ... the same way _normalize_grn_po_line does
        # for ReferenceDocumentItem, so it lines up with S4_Purchase_Order_Data
        # and the invoice's own LineItemNumber.
        po_line_number = int(_normalize_grn_po_line(row.purchase_order_item) or 0)
        records_by_line.setdefault((row_po, po_line_number), []).append(row)

    receipt_total = 0.0
    receipt_line_items = []

    for po_line_key in sorted(records_by_line.keys(), key=lambda k: (k[0], k[1])):
        row_po, po_line_number = po_line_key
        rec_list = records_by_line.get(po_line_key, [])
        if not rec_list:
            continue

        material = str(rec_list[0].material or "").strip()
        material_row = po_material_lookup.get((row_po, material), {})
        po_material = material_row.get("description") or material
        # PO quantities are stated in the order unit (SAP's "entry unit" for
        # the goods receipt), not the stock-keeping base unit - e.g. a PO for
        # 10 cases receives as quantity_in_entry_unit=10/entry_unit=CV *and*
        # quantity_in_base_unit=60/material_base_unit=EA (10 cases x 6 each).
        # Using quantity_in_base_unit here while labelling it with entry_unit
        # mismatches the quantity and its unit, inflating totals 6x and
        # breaking the PO-quantity match below.
        qty_in_entry_unit = getattr(rec_list[0], "quantity_in_entry_unit", None)
        if qty_in_entry_unit is not None:
            qty_received = float(qty_in_entry_unit)
            uom = str(rec_list[0].entry_unit) if rec_list[0].entry_unit else None
        else:
            qty_received = float(rec_list[0].quantity_in_base_unit or 0)
            uom = None

        matched_candidate = None
        if material_row:
            matched_candidate = {
                "po_number": row_po,
                "line_item": str(material_row["purchase_order_item"]),
                "description": material_row["description"],
                "quantity": material_row["quantity"],
                "unit_price": material_row["unit_price"],
                "line_total": material_row["line_total"],
            }

        if matched_candidate is None:
            for candidate in po_line_lookup.get(row_po, []):
                if abs(float(candidate.get("quantity") or 0) - qty_received) < 0.01:
                    matched_candidate = candidate
                    break

        if matched_candidate is None:
            for candidate in po_line_lookup.get(row_po, []):
                qty = float(candidate.get("quantity") or 0)
                if qty > 0 and abs(qty - qty_received) <= 0.05:
                    matched_candidate = candidate
                    break

        # matched_candidate["unit_price"] is SAP's NetPriceAmount, which is
        # priced per PriceUnit (e.g. "$33.85 per case of 24"), not per base
        # unit - multiplying it by qty_received (in base units, e.g. EA)
        # inflates the total by the PriceUnit factor. matched_candidate's own
        # line_total (net_amount/gross_amount) is already correct, so derive
        # the effective per-base-unit price from that instead of trusting the
        # raw unit_price field.
        po_quantity = float((matched_candidate or {}).get("quantity", 0.0) or 0.0)
        po_line_total = float((matched_candidate or {}).get("line_total", 0.0) or 0.0)
        if po_quantity > 0:
            unit_price = po_line_total / po_quantity
        else:
            unit_price = float((matched_candidate or {}).get("unit_price", 0.0) or 0.0)

        matched_amount = qty_received * unit_price if qty_received > 0 and unit_price else 0.0
        receipt_total += matched_amount

        receipt_line_items.append({
            "line_item": str(po_line_number),
            "po_number": row_po,
            "po_line_number": po_line_number,
            "description": po_material,
            "quantity": round(qty_received, 4),
            "unit_of_measure": uom,
            "line_total": round(matched_amount, 2),
            "net_line_total": round(matched_amount, 2),
            "gross_line_total": round(matched_amount, 2),
            "unit_price": round(unit_price, 2),
            "total_receipt_qty": round(qty_received, 4),
            "total_receipt_amount_net": round(matched_amount, 2),
            "total_receipt_amount_gross": round(matched_amount, 2),
            "rc_count": len(rec_list),
        })

    return {
        "receipt_po_number": po_number,
        "receipt_total": round(receipt_total, 2),
        "line_items": receipt_line_items,
        "rc_id_by_line": {},
        "_all_rc_ids": [],
        "_rc_by_line_all": {},
    }


# ============================================================================
# 18B. METHOD 3 â€” AGGREGATE RC-TOTAL MATCH (no desc/qty/price checking)
# ============================================================================

def fetch_receipt_totals_by_rc(po_number: Union[str, List[str]]) -> Dict[str, dict]:
    """
    Group S4_GRN_Data rows by material_document (the closest equivalent of the old RC/receipt group)
    and sum the PO-equivalent line totals for each document.
    """
    po_list = [po_number] if isinstance(po_number, str) else list(po_number)
    if not po_list:
        return {}

    placeholders = ', '.join(['?'] * len(po_list))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT
            g.material_document,
            g.purchase_order,
            g.purchase_order_item,
            g.quantity_in_base_unit,
            g.quantity_in_entry_unit,
            g.entry_unit,
            g.material,
            p.purchase_order_item_text
        FROM final_pulse_DBprod.dbo.S4_GRN_Data g
        LEFT JOIN final_pulse_DBprod.dbo.S4_Purchase_Order_Data p
            ON p.purchase_order = g.purchase_order
           AND p.purchase_order_item = g.purchase_order_item
        WHERE g.purchase_order IN ({placeholders}) AND g.material_document IS NOT NULL
        ORDER BY g.material_document, g.purchase_order, g.purchase_order_item
    """, tuple(po_list))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    po_line_lookup = {}
    po_cursor = get_db_connection().cursor()
    po_cursor.execute(f"""
        SELECT purchase_order, purchase_order_item, purchase_order_item_text, quantity, unit_price, net_amount, gross_amount
        FROM final_pulse_DBprod.dbo.S4_Purchase_Order_Data
        WHERE purchase_order IN ({placeholders})
    """, tuple(po_list))
    for r in po_cursor.fetchall():
        k = (str(r.purchase_order), int(r.purchase_order_item))
        qty = float(r.quantity or 0)
        unit_price = float(r.unit_price or 0)
        net_amount = float(r.net_amount or 0)
        if qty > 0 and unit_price == 0:
            unit_price = net_amount / qty
        po_line_lookup[k] = {
            "description": str(r.purchase_order_item_text or "").strip() or str(r.material or "").strip(),
            "quantity": qty,
            "unit_price": unit_price,
            "net_amount": net_amount,
            "gross_amount": float(r.gross_amount or 0),
        }
    po_cursor.close()
    get_db_connection().close()

    rc_map: Dict[str, dict] = {}
    for row in rows:
        rc_no = str(row.material_document)
        rc_map.setdefault(rc_no, {"total": 0.0, "line_items": []})

        po_key = (str(row.purchase_order), int(row.purchase_order_item))
        po_amount = po_line_lookup.get(po_key, {})
        unit_price = float(po_amount.get('unit_price', 0.0) or 0.0)
        # See fetch_receipt_data: PO quantities are in the order unit
        # (entry_unit), not the stock-keeping base unit - use
        # quantity_in_entry_unit when available to avoid inflating totals
        # by the base-unit conversion factor (e.g. a case pack size).
        qty_in_entry_unit = getattr(row, "quantity_in_entry_unit", None)
        if qty_in_entry_unit is not None:
            qty = float(qty_in_entry_unit)
            uom = str(row.entry_unit) if row.entry_unit else "EA"
        else:
            qty = float(row.quantity_in_base_unit or 0)
            uom = "EA"
        effective = qty * unit_price

        rc_map[rc_no]["total"] += effective
        po_description = po_line_lookup.get(po_key, {}).get("description") or str(row.purchase_order_item_text or "").strip() or str(row.material or "").strip()

        rc_map[rc_no]["line_items"].append({
            "line_item": str(row.purchase_order_item) if row.purchase_order_item is not None else None,
            "description": po_description,
            "quantity": qty,
            "unit_of_measure": uom,
            "line_total": round(effective, 2),
            "unit_price": round(unit_price, 2) if qty > 0 else 0.0,
        })

    for rc_no in rc_map:
        rc_map[rc_no]["total"] = round(rc_map[rc_no]["total"], 2)

    return rc_map


def try_method3_rc_total_match(
    po_number: Union[str, List[str]],
    invoice_total: Any,
    tolerance: float = 0.99,
) -> Optional[Tuple[str, dict]]:
    """
    METHOD 3 â€” Aggregate RC-level match.

    Ignores description, quantity, and unit price entirely.
    Sums every RC's line items into one number and compares that number
    against the INVOICE TOTAL. If exactly one RC's total falls within
    tolerance, that RC is the match.

    Example:
        invoice_total = 200
        RC01 total = 190  -> no match
        RC02 total = 200  -> match -> RC02 is returned
    """
    try:
        inv_total = float(str(invoice_total).replace(",", "")) if invoice_total else None
    except (ValueError, TypeError):
        inv_total = None
    if inv_total is None:
        logging.info("Method 3: invoice total missing/invalid â€” skipping")
        return None

    rc_totals = fetch_receipt_totals_by_rc(po_number)
    if not rc_totals:
        logging.info(f"Method 3: no receipts found for PO {po_number}")
        return None

    candidates = []
    for rc_no, data in rc_totals.items():
        diff = abs(round(data["total"], 2) - round(inv_total, 2))
        logging.info(f"Method 3: RC={rc_no} total={data['total']} vs invoice={inv_total} (diff={diff})")
        if diff <= tolerance:
            candidates.append((rc_no, data, diff))

    if not candidates:
        logging.info(f"Method 3: no RC total matched invoice total {inv_total} for PO {po_number}")
        return None

    candidates.sort(key=lambda x: x[2])
    best_rc_no, best_data, best_diff = candidates[0]
    logging.info(
        f"âœ… Method 3 MATCH: RC={best_rc_no} total={best_data['total']} "
        f"vs invoice total={inv_total} (diff={best_diff})"
    )
    return best_rc_no, best_data


def fetch_ariba_data_for_po(po_number: str) -> List[Dict]:
    try:
        if not re.match(r'^45\d{8}$', po_number):
            raise HTTPException(status_code=400, detail="PO Number must be a 10-digit number starting with '45'")
        conn = get_db_connection()
        cursor = conn.cursor()
        # Same source table as fetch_ariba_data() - S4_Purchase_Order_Data,
        # de-duped per PO line the same way the old purchase_order_master
        # query did (latest row wins), using last_change_date_time since
        # this table has no date_updated/date_created columns.
        cursor.execute("""
            SELECT p.purchase_order, p.purchase_order_item, p.purchase_order_item_text,
                p.base_unit, p.unit_price, p.quantity, p.net_amount
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY purchase_order, purchase_order_item
                    ORDER BY last_change_date_time DESC
                ) AS rn
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
                WHERE purchase_order = ?
            ) p WHERE p.rn = 1 ORDER BY p.purchase_order_item
        """, (po_number,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        if not rows:
            return []
        result = []
        for r in rows:
            result.append({
                "purchase_order_id": str(r.purchase_order) if r.purchase_order is not None else None,
                "line_item": str(r.purchase_order_item) if r.purchase_order_item is not None else None,
                "description": r.purchase_order_item_text,
                "unit_of_measure": r.base_unit,
                "unit_price": float(r.unit_price) if r.unit_price is not None else None,
                "quantity": float(r.quantity) if r.quantity is not None else None,
                "price": float(r.net_amount) if r.net_amount is not None else None,
            })
        return result
    except Exception as e:
        logging.error(f"Error fetching Ariba data for PO {po_number}: {str(e)}")
        return []



# ============================================================================
# 15. COMPARISON FUNCTIONS (Invoice vs Receipt ONLY)
# ============================================================================
def _reorder_line_items_like_invoice(
    items: List[dict], invoice_line_items: List[dict]
) -> List[dict]:
    """
    ariba_reference_data.ariba_line_items and receipt_data.receipt_line_items
    are each fetched with their own SQL ordering (by purchase_order, then
    line) and cover every line on the PO/GRN - including lines the invoice
    never bills (e.g. other products on the same PO). Filter `items` down to
    only the (po_number, line_item) pairs that actually appear on this
    invoice, and order them to follow invoice_data.line_items' sequence -
    the same order Comparision_of_lineItems is built in - so all three lists
    (Comparision_of_lineItems, ariba_line_items, receipt_line_items) line up
    row-for-row with the invoice for a reader comparing them side by side.
    """
    order_index = {
        (str(inv.get("po_number") or ""), str(inv.get("line_item") or "")): idx
        for idx, inv in enumerate(invoice_line_items)
    }

    matching = [
        item for item in items
        if (str(item.get("po_number") or ""), str(item.get("line_item") or "")) in order_index
    ]
    matching.sort(key=lambda item: order_index[(str(item.get("po_number") or ""), str(item.get("line_item") or ""))])
    return matching


def compare_po_numbers_invoice_receipt_only(invoice_po, receipt_po):
    """
    invoice_po / receipt_po may each be a single PO string, a comma-separated
    string, or a list (an invoice can carry multiple POs — e.g. one PO Number
    per packing-slip group). Compare as SETS so order, duplicates, and
    list-vs-string formatting don't cause false mismatches.
    """
    def _to_set(po):
        if po is None:
            return set()
        items = po if isinstance(po, (list, tuple, set)) else str(po).split(",")
        return {str(p).strip() for p in items if p is not None and str(p).strip()}

    inv_set = _to_set(invoice_po)
    rec_set = _to_set(receipt_po)

    missing_from_receipt = inv_set - rec_set   # invoice PO with no matching receipt PO
    extra_in_receipt     = rec_set - inv_set   # receipt PO not referenced on the invoice

    all_match = bool(inv_set) and not missing_from_receipt

    if all_match:
        error_comment = None
    elif not inv_set:
        error_comment = "Invoice PO number is missing"
    else:
        error_comment = (
            f"Invoice PO(s) {sorted(missing_from_receipt)} not found in receipt PO(s) "
            f"{sorted(rec_set) if rec_set else 'None'}"
        )

    return {
        "invoice_po_number": sorted(inv_set) if inv_set else invoice_po,
        "receipt_po_number": sorted(rec_set) if rec_set else receipt_po,
        "status": "matched" if all_match else "not_matched",
        "error_comment": error_comment,
        "extra_receipt_po_numbers": sorted(extra_in_receipt) if extra_in_receipt else None,
    }

def compare_totals_invoice_receipt_only(invoice_total, receipt_total: float, tolerance: float = 0.99, matched_receipt_items: List[dict] = None, currency: Optional[str] = None):
    """
    Compare invoice total against receipt total.
    If matched_receipt_items is provided, only sum those items (ignoring extra receipts).
    """
    try:
        inv_total_float = float(str(invoice_total).replace(",", "")) if invoice_total else None
    except (ValueError, TypeError):
        inv_total_float = None

    # If we have matched receipt items, only sum those
    if matched_receipt_items:
        matched_total = sum(float(item.get('line_total', 0) or 0) for item in matched_receipt_items)
        receipt_to_compare = matched_total
        receipt_type = "matched items"
    else:
        receipt_to_compare = receipt_total
        receipt_type = "all items"

    if inv_total_float is not None:
        diff = abs(round(inv_total_float, 2) - round(receipt_to_compare, 2))
        all_match = diff <= tolerance
        currency_suffix = f" {currency}" if currency else ""
        error_comment = None if all_match else f"Total mismatch - Invoice: {inv_total_float:.2f} vs {receipt_type}: {receipt_to_compare:.2f} (Diff: {diff:.2f}{currency_suffix})"
    else:
        all_match = False
        error_comment = "Invoice total is missing or invalid"

    return {
        "invoice_total": str(invoice_total) if invoice_total else None,
        "receipt_total": round(receipt_to_compare, 2),
        "receipt_type": receipt_type,
        "status": "matched" if all_match else "not_matched",
        "tolerance_limit": tolerance,
        "error_comment": error_comment,
    }


def safe_str_compare(a, b) -> str:
    if a is None or b is None:
        return "not_matched"
    return "matched" if str(a).strip().lower() == str(b).strip().lower() else "not_matched"


def safe_num_compare(a, b) -> str:
    try:
        fa = float(str(a).replace(",", "")) if a is not None else None
        fb = float(str(b).replace(",", "")) if b is not None else None
        if fa is None or fb is None:
            return "not_matched"
        return "matched" if round(fa, 4) == round(fb, 4) else "not_matched"
    except (ValueError, TypeError):
        return "not_matched"


def qty_coverage_status(invoice_qty, receipt_qty) -> str:
    """A PO/receipt can span several invoices, so invoicing less than what
    was received (a partial shipment) is valid - only invoicing MORE than
    was received is a real mismatch."""
    try:
        inv_f = float(str(invoice_qty).replace(",", "")) if invoice_qty is not None else None
        rec_f = float(str(receipt_qty).replace(",", "")) if receipt_qty is not None else None
        if inv_f is None or rec_f is None:
            return "not_matched"
        return "matched" if inv_f <= rec_f + 1e-6 else "not_matched"
    except (ValueError, TypeError):
        return "not_matched"


_UNIT_SYNONYMS = {
    "pcs": "ea", "pc": "ea", "pkt": "ea", "pkts": "ea", "unit": "ea", "units": "ea",
    "box": "ea", "boxes": "ea", "ctn": "ea", "ctns": "ea", "carton": "ea", "cartons": "ea",
    "bgs": "bag", "bags": "bag",
    "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "ltr": "l", "liter": "l", "litre": "l", "liters": "l", "litres": "l",
}


def _normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return ""
    u = str(unit).strip().lower()
    return _UNIT_SYNONYMS.get(u, u)


def compare_units(invoice_unit: Optional[str], receipt_unit: Optional[str], description_match: bool = False) -> Tuple[str, Optional[str]]:
    inv_u = (invoice_unit or "").strip()
    rec_u = (receipt_unit or "").strip()

    if not inv_u or not rec_u:
        return "matched", None

    if _normalize_unit(inv_u) == _normalize_unit(rec_u):
        return "matched", None

    if description_match:
        return "matched", None

    return "not_matched", f"Unit mismatch - Invoice: '{invoice_unit}' vs Receipt: '{receipt_unit}'"


def extract_size_volume(text: str) -> set:
    if not text:
        return set()
    text_lower = text.lower()
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)', text_lower)
    normalized = set()

    weight_to_kg = {
        'g': 0.001, 'gm': 0.001, 'gms': 0.001, 'gram': 0.001, 'grams': 0.001,
        'kg': 1.0, 'kgs': 1.0, 'kilo': 1.0, 'kilos': 1.0,
        'kilogram': 1.0, 'kilograms': 1.0,
        'lb': 0.453592, 'lbs': 0.453592, 'pound': 0.453592, 'pounds': 0.453592,
        'oz': 0.0283495,
    }
    volume_to_ltr = {
        'l': 1.0, 'ltr': 1.0, 'liter': 1.0, 'litre': 1.0,
        'liters': 1.0, 'litres': 1.0, 'ml': 0.001,
    }

    for value, unit in matches:
        try:
            val = float(value)
            unit_l = unit.lower().strip()
            if unit_l in weight_to_kg:
                kg_val = round(val * weight_to_kg[unit_l], 2)
                normalized.add(f"{kg_val}kg")
            elif unit_l in volume_to_ltr:
                ltr_val = round(val * volume_to_ltr[unit_l], 2)
                normalized.add(f"{ltr_val}ltr")
            else:
                normalized.add(f"{val}{unit_l}")
        except Exception:
            continue
    return normalized


def sizes_conflict(inv_sizes: set, rec_sizes: set, tolerance_pct: float = 0.05) -> Optional[str]:
    if not inv_sizes or not rec_sizes:
        return None

    def parse(sizes):
        out = []
        for s in sizes:
            m = re.match(r'^([\d.]+)([a-zA-Z]+)$', s)
            if m:
                out.append((float(m.group(1)), m.group(2)))
        return out

    inv_parsed = parse(inv_sizes)
    rec_parsed = parse(rec_sizes)

    for iv, iu in inv_parsed:
        same_unit = [rv for rv, ru in rec_parsed if ru == iu]
        if not same_unit:
            continue
        tol = max(iv * tolerance_pct, 0.05)
        if not any(abs(iv - rv) <= tol for rv in same_unit):
            return f"Size mismatch - Invoice: {inv_sizes} vs Receipt: {rec_sizes}"
    return None


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[^\w\s]', '', text)
    # Insert a space at digit/letter boundaries so "MDH100gm" tokenizes
    # the same way as "MDH-100 GM" ("mdh 100 gm" in both cases)
    text = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', text)
    text = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


_ABBREVIATION_EXPANSIONS = {
    "s/on": "skin on", "son": "skin on",
    "b/l": "boneless", "bl": "boneless",
    "fzn": "frozen", "frz": "frozen",
    "c/s": "case", "cs": "case",
    "w/o": "without", "w/": "with",
    "skls": "skinless", "skl": "skinless",
    "bnls": "boneless",
    "gm": "g", "gms": "g",
    "ltr": "l",
    "pkt": "packet",
    "ea": "each",
}


def _expand_abbreviations(text: str) -> str:
    if not text:
        return ""
    words = text.split()
    expanded = [_ABBREVIATION_EXPANSIONS.get(w, w) for w in words]
    return " ".join(expanded)


def _compare_descriptions_word_based(invoice_desc, receipt_desc, inv_clean: str = None, rec_clean: str = None) -> Tuple[str, str]:
    inv_clean = inv_clean if inv_clean is not None else clean_text(invoice_desc)
    rec_clean = rec_clean if rec_clean is not None else clean_text(receipt_desc)

    inv_expanded = _expand_abbreviations(inv_clean)
    rec_expanded = _expand_abbreviations(rec_clean)

    invoice_words = set(inv_expanded.split())
    receipt_words = set(rec_expanded.split())

    receipt_words = {w for w in receipt_words if not re.match(r'^\[?[A-Z]{2,}-\d{4,}\]?$', w, re.IGNORECASE)}

    inv_core = {w for w in invoice_words if len(w) > 2}
    rec_core = {w for w in receipt_words if len(w) > 2}

    if not inv_core:
        return "matched", "No meaningful words to compare"

    def words_match_with_prefix(inv_words, rec_words):
        matched = 0
        for iw in inv_words:
            if iw in rec_words:
                matched += 1
            else:
                for rw in rec_words:
                    if len(rw) >= 3 and (iw.startswith(rw) or rw.startswith(iw)):
                        matched += 1
                        break
                    min_len = min(len(iw), len(rw))
                    if min_len >= 5 and iw[:min_len] == rw[:min_len]:
                        matched += 1
                        break
        return matched

    matched_count = words_match_with_prefix(inv_core, rec_core)
    overlap_ratio = matched_count / len(inv_core)

    union = inv_core | rec_core
    intersection = inv_core & rec_core
    jaccard = len(intersection) / len(union) if union else 0

    receipt_is_subset = rec_core.issubset(inv_core)
    invoice_is_subset = inv_core.issubset(rec_core)

    is_match = (
        overlap_ratio >= 0.55
        or receipt_is_subset
        or invoice_is_subset
        or jaccard >= 0.35
    )

    if is_match:
        return "matched", f"Descriptions sufficiently similar (overlap: {overlap_ratio:.0%}, jaccard: {jaccard:.0%})"
    else:
        missing = inv_core - rec_core
        return "not_matched", f"Low similarity (overlap: {overlap_ratio:.0%}) - Missing: {', '.join(sorted(missing))}"


def _compare_descriptions_via_gemini_api(invoice_desc: str, receipt_desc: str) -> Optional[Tuple[str, str]]:
    try:
        prompt = (
            "You are checking whether an invoice line item and a warehouse "
            "goods-receipt line item describe the SAME physical product.\n\n"
            f'Invoice description: "{invoice_desc}"\n'
            f'Receipt description: "{receipt_desc}"\n\n'
            "Rules:\n"
            "- Treat common abbreviations as equivalent to their full form "
            "(e.g. S/ON = Skin On, B/L = Boneless, FZN = Frozen, GM = Grams, "
            "PKT = Packet, EA = Each).\n"
            "- Ignore word order, supplier/brand names, item codes, and "
            "country-of-origin text unless they change the actual product.\n"
            "- Only treat it as a mismatch if the core product, cut/variant, "
            "or pack size is genuinely different.\n\n"
            'Respond with ONLY a JSON object like {"match": true, "reason": "short reason"} '
            'or {"match": false, "reason": "short reason"}.'
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
        parsed = json.loads(response.text)
        is_match = bool(parsed.get("match"))
        reason = str(parsed.get("reason") or "").strip()
        if is_match:
            return "matched", reason or "Gemini confirmed same product"
        return "not_matched", f"Description mismatch - {reason or 'Gemini found a genuine product difference'}"
    except Exception as e:
        logging.warning(f"Gemini description comparison failed, using local word match instead: {e}")
        return None


def compare_descriptions_with_gemini(invoice_desc, receipt_desc, use_cache: bool = True) -> Tuple[str, str]:
    global _description_cache
    if not invoice_desc or not receipt_desc:
        return "not_matched", "One or both descriptions are empty"

    inv_clean = clean_text(invoice_desc)
    rec_clean = clean_text(receipt_desc)
    cache_key = f"{inv_clean}|{rec_clean}"
    if use_cache and cache_key in _description_cache:
        return _description_cache[cache_key]

    inv_sizes = extract_size_volume(invoice_desc)
    rec_sizes = extract_size_volume(receipt_desc)
    size_error = sizes_conflict(inv_sizes, rec_sizes)
    if size_error:
        result = ("not_matched", size_error)
        if use_cache:
            _description_cache[cache_key] = result
        return result

    word_status, word_reason = _compare_descriptions_word_based(invoice_desc, receipt_desc, inv_clean, rec_clean)

    if word_status == "matched":
        result = (word_status, word_reason)
        if use_cache:
            _description_cache[cache_key] = result
        return result

    gemini_result = _compare_descriptions_via_gemini_api(invoice_desc, receipt_desc)
    if gemini_result is not None:
        result = gemini_result
    else:
        result = (word_status, word_reason)

    if use_cache:
        _description_cache[cache_key] = result
    return result

def parse_quantity(qty_str):
    """
    Parse quantity from various formats:
    - "2.0" -> 2.0
    - "2/0" -> 2.0 (treat as 2.0)
    - "2" -> 2.0
    - "2,0" -> 2.0
    """
    if qty_str is None:
        return None
    
    qty_str = str(qty_str).strip()
    if not qty_str:
        return None
    
    # Handle "2/0" pattern (should be 2.0)
    if '/' in qty_str:
        parts = qty_str.split('/')
        try:
            numerator = float(parts[0].strip() or 0)
            denominator = float(parts[1].strip() or 1)
            if denominator > 0:
                return numerator / denominator
        except:
            pass
    
    # Handle comma as decimal
    qty_str = qty_str.replace(',', '.')
    
    try:
        return float(qty_str)
    except ValueError:
        return None
    

# ============================================================================
# 16. COMPARISON FUNCTIONS â 2-METHOD LINE ITEM MATCHING
# ============================================================================

def compare_line_items_invoice_receipt_only(
    invoice_items: List[dict],
    receipt_items: List[dict],
    use_api_for_descriptions: bool = True,
) -> List[dict]:
    """
    Two-method line item matcher:

    METHOD 1 â€” Description similarity + Quantity match
        Pass: desc overlap â‰¥ 30 % AND |invoice_qty - receipt_qty| < 0.01
        Preferred because it confirms both *what* and *how much*.

    METHOD 2 â€” Description similarity (loose) + Line-total match
        Pass: |invoice_gross - receipt_gross| / max < 1 %
              (description overlap is scored but not hard-gated, so
               "Chicken Breakfast Sausage" vs "Chicken Sausage Box 10kg"
               still passes when totals agree â€” 262.50 = 262.50)

    If neither method finds a match the line is flagged as not_matched.
    """
    result = []
    matched_invoice: set = set()
    matched_receipt: set = set()
    matches: List[tuple] = []   # (inv_idx, rec_idx, method_label)

    # â”€â”€ helper: description overlap ratio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _desc_overlap(a: str, b: str) -> float:
        a_words = set(clean_text(a).split())
        b_words = set(clean_text(b).split())
        union = a_words | b_words
        return len(a_words & b_words) / len(union) if union else 0.0

    # â”€â”€ METHOD 1: Description + Quantity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for i, inv in enumerate(invoice_items):
        inv_qty = parse_quantity(inv.get("QTY"))
        if inv_qty is None:
            continue

        invoice_po = str(inv.get("po_number") or "").strip()
        best_j, best_overlap = None, -1.0

        for j, rec in enumerate(receipt_items):
            if j in matched_receipt:
                continue

            rec_po = str(rec.get("po_number") or "").strip()
            if invoice_po and rec_po and invoice_po != rec_po:
                continue

            rec_qty = float(rec.get("quantity") or 0)
            if abs(inv_qty - rec_qty) >= 0.01:
                continue

            overlap = _desc_overlap(
                inv.get("ItemDescription") or "",
                rec.get("description") or "",
            )
            if overlap >= 0.30 and overlap > best_overlap:
                best_overlap = overlap
                best_j = j

        if best_j is not None:
            matches.append((i, best_j, "method_1_desc_qty"))
            matched_invoice.add(i)
            matched_receipt.add(best_j)
            logging.info(
                f"âœ… M1 match: inv[{i}] '{inv.get('ItemDescription')}' "
                f"â†” rec[{best_j}] '{receipt_items[best_j].get('description')}' "
                f"(overlap={best_overlap:.0%}, qty={inv_qty})"
            )

    # â”€â”€ METHOD 1B: Partial quantity (invoice bills less than what was â”€â”€â”€â”€â”€â”€â”€
    # received) - a PO/receipt can span several invoices, so invoice_qty <
    # receipt_qty is a legitimate partial shipment, not a mismatch.
    # invoice_qty > receipt_qty is NOT allowed here (can't invoice more than
    # what's been received) and is left for the unmatched block, which
    # correctly reports it as not_matched.
    #
    # Distributor invoices carry only the distributor's own item codes/text,
    # which don't correspond to SAP's material numbers, so description
    # overlap here is often too weak to hard-gate on (short codes like
    # "C-2/12 12OZ" vs "12oz Can" score low on word-overlap despite being the
    # same item). Quantity headroom (rec_qty >= inv_qty) usually disambiguates
    # on its own - when only one same-PO receipt line can cover the invoiced
    # quantity, it's matched directly; description overlap is only needed to
    # break ties when several lines could otherwise cover it.
    for i, inv in enumerate(invoice_items):
        if i in matched_invoice:
            continue
        inv_qty = parse_quantity(inv.get("QTY"))
        if inv_qty is None or inv_qty <= 0:
            continue

        invoice_po = str(inv.get("po_number") or "").strip()
        candidates = []

        for j, rec in enumerate(receipt_items):
            if j in matched_receipt:
                continue

            rec_po = str(rec.get("po_number") or "").strip()
            if invoice_po and rec_po and invoice_po != rec_po:
                continue

            rec_qty = float(rec.get("quantity") or 0)
            if rec_qty <= 0 or inv_qty > rec_qty:
                continue

            overlap = _desc_overlap(
                inv.get("ItemDescription") or "",
                rec.get("description") or "",
            )
            candidates.append((j, overlap, rec_qty - inv_qty))

        best_j = None
        if len(candidates) == 1:
            best_j, best_overlap, _ = candidates[0]
        elif len(candidates) > 1:
            best_j, best_overlap, _ = max(candidates, key=lambda c: (c[1], -c[2]))

        if best_j is not None:
            matches.append((i, best_j, "method_1b_partial_qty"))
            matched_invoice.add(i)
            matched_receipt.add(best_j)
            logging.info(
                f"âœ… M1b partial match: inv[{i}] '{inv.get('ItemDescription')}' "
                f"â†” rec[{best_j}] '{receipt_items[best_j].get('description')}' "
                f"(candidates={len(candidates)}, overlap={best_overlap:.0%}, "
                f"invoice_qty={inv_qty}, receipt_qty={receipt_items[best_j].get('quantity')})"
            )

    # â”€â”€ METHOD 2: Same PO + closest total match â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for i, inv in enumerate(invoice_items):
        if i in matched_invoice:
            continue

        try:
            if inv.get("AmountInclVAT"):
                inv_gross = float(str(inv.get("AmountInclVAT")).replace(",", ""))
            else:
                base_amt = float(str(inv.get("Amount") or 0).replace(",", ""))
                vat_amt = float(str(inv.get("VATAmount") or 0).replace(",", ""))
                inv_gross = base_amt + vat_amt if vat_amt else base_amt
        except (ValueError, TypeError):
            inv_gross = 0.0

        if inv_gross <= 0:
            continue

        invoice_po = str(inv.get("po_number") or "").strip()
        best_j, best_desc, best_gap = None, -1.0, None

        for j, rec in enumerate(receipt_items):
            if j in matched_receipt:
                continue

            rec_po = str(rec.get("po_number") or "").strip()
            rec_gross = float(rec.get("net_line_total") or rec.get("line_total") or 0)
            if invoice_po and rec_po and invoice_po != rec_po:
                continue
            if rec_gross <= 0:
                continue

            diff_ratio = abs(inv_gross - rec_gross) / max(inv_gross, rec_gross)
            if diff_ratio >= 0.01:
                continue

            overlap = _desc_overlap(
                inv.get("ItemDescription") or "",
                rec.get("description") or "",
            )
            if overlap > best_desc or (overlap == best_desc and (best_gap is None or abs(inv_gross - rec_gross) < best_gap)):
                best_desc = overlap
                best_gap = abs(inv_gross - rec_gross)
                best_j = j

        if best_j is not None:
            matches.append((i, best_j, "method_2_desc_total"))
            matched_invoice.add(i)
            matched_receipt.add(best_j)
            logging.info(
                f"âœ… M2 match: inv[{i}] '{inv.get('ItemDescription')}' "
                f"â†” rec[{best_j}] '{receipt_items[best_j].get('description')}' "
                f"(gross={inv_gross}, desc_overlap={best_desc:.0%}, gap={best_gap})"
            )

    # â”€â”€ METHOD 3 (display-only): fallback pairing by PO line number â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # For any invoice item still unmatched, if there's a receipt line with the
    # same line_item number (PO line), pair them purely so the response can
    # SHOW the receipt data. This is NOT a real match â€” it's not added to
    # matched_invoice/matched_receipt, so totals, RC-saving, and the
    # all_matched flag in perform_comparison_and_check_match are unaffected.
    display_only_pairs: Dict[int, int] = {}
    for i, inv in enumerate(invoice_items):
        if i in matched_invoice:
            continue
        inv_line = str(inv.get("line_item") or inv.get("LineItemNumber") or "").strip()
        if not inv_line:
            continue
        for j, rec in enumerate(receipt_items):
            if j in matched_receipt:
                continue
            if str(rec.get("line_item") or "").strip() == inv_line:
                display_only_pairs[i] = j
                break

    # â”€â”€ Build result rows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    matched_dict = {inv_idx: (rec_idx, method) for inv_idx, rec_idx, method in matches}

    for i, inv in enumerate(invoice_items):
        invoice_desc = inv.get("ItemDescription") or ""
        invoice_unit = inv.get("Unit") or ""

        # Invoice line total
        try:
            inv_qty_f  = float(str(inv.get("QTY") or 0).replace(",", ""))
            inv_price_f = float(str(inv.get("UnitPrice") or 0).replace(",", ""))
            invoice_line_total = round(inv_qty_f * inv_price_f, 2)
        except Exception:
            invoice_line_total = None

        if i in matched_dict:
            rec_idx, method = matched_dict[i]
            rec = receipt_items[rec_idx]
            receipt_desc = rec.get("description") or ""
            receipt_unit = rec.get("unit_of_measure") or ""

            # The displayed receipt description must come from the S4 PO text field,
            # while the material code remains the internal key used for matching.
            receipt_desc_clean = str(receipt_desc or "").strip()
            invoice_desc_clean = str(invoice_desc or "").strip()
            is_material_code = bool(receipt_desc_clean.isdigit() and len(receipt_desc_clean) >= 4)

            if method in {"method_1_desc_qty", "method_1b_partial_qty", "method_2_desc_total"}:
                desc_status = "matched"
                desc_reason = (
                    "Matched by S4 PO item + qty/total; receipt description is taken from "
                    "S4_Purchase_Order_Data.purchase_order_item_text."
                    if method != "method_1b_partial_qty" else
                    "Matched by S4 PO item + partial qty (invoice bills less than the full "
                    "quantity received, e.g. a partial shipment across several invoices); "
                    "receipt description is taken from S4_Purchase_Order_Data.purchase_order_item_text."
                )
            elif use_api_for_descriptions and receipt_desc:
                desc_status, desc_reason = compare_descriptions_with_gemini(invoice_desc, receipt_desc)
            else:
                desc_status = "matched"
                desc_reason = f"Matched via {method}"

            final_description = (
                receipt_desc
                if desc_status == "matched" and receipt_desc
                else invoice_desc
            )
            final_unit = (
                receipt_unit
                if receipt_unit and receipt_unit.lower() != "none"
                else invoice_unit
            )

            qty_status = qty_coverage_status(inv.get("QTY"), rec.get("quantity"))

            # For a partial invoice, only the invoiced quantity's worth should
            # count toward totals - not the full received line_total (the
            # remainder is expected to appear on a later invoice). Track that
            # invoiced quantity separately as matched_quantity so the response
            # doesn't show a full "quantity" (e.g. 200 received) next to a
            # line_total that was actually computed for a smaller amount.
            receipt_line_total = rec.get("line_total")
            matched_quantity = rec.get("quantity")
            try:
                inv_qty_val = float(str(inv.get("QTY") or 0).replace(",", ""))
                rec_qty_val = float(rec.get("quantity") or 0)
                rec_full_line_total = float(rec.get("line_total") or 0)
                if inv_qty_val > 0 and rec_qty_val > 0 and inv_qty_val < rec_qty_val:
                    # inv_qty_val < rec_qty_val can mean either a genuine partial
                    # shipment (same unit, invoice bills less than received) or a
                    # unit mismatch (e.g. invoice bills 200 CASES, receipt records
                    # 4800 EA of the exact same goods - a full match). The invoice's
                    # own qty*price already equals the full receipt total in the
                    # unit-mismatch case, so check that before scaling down - scaling
                    # a full match down as if it were partial would wrongly shrink a
                    # correct total to a fraction of itself.
                    if (
                        invoice_line_total is not None
                        and rec_full_line_total > 0
                        and abs(invoice_line_total - rec_full_line_total) / rec_full_line_total <= 0.01
                    ):
                        receipt_line_total = rec_full_line_total
                        matched_quantity = rec_qty_val
                    else:
                        unit_price = float(rec.get("unit_price") or 0) or (rec_full_line_total / rec_qty_val)
                        receipt_line_total = round(inv_qty_val * unit_price, 2)
                        matched_quantity = inv_qty_val
            except (ValueError, TypeError, ZeroDivisionError):
                pass

            result.append({
                "line_item_comparison_number": i + 1,
                "po_number": inv.get("po_number") or rec.get("po_number"),
                "invoice_item": {
                    "po_number": inv.get("po_number"),
                    "line_item": inv.get("line_item") or inv.get("LineItemNumber"),
                    "ItemCode": inv.get("ItemCode"),
                    "ItemDescription": invoice_desc,
                    "QTY": inv.get("QTY"),
                    "Unit": invoice_unit,
                    "UnitPrice": inv.get("UnitPrice"),
                    "Amount": inv.get("Amount"),
                    "AmountInclVAT": inv.get("AmountInclVAT"),
                    "line_total": invoice_line_total,
                },
                "receipt_item": {
                    "po_number": rec.get("po_number"),
                    "line_item": rec.get("line_item"),
                    "description": receipt_desc,
                    "quantity": matched_quantity,
                    "total_received_quantity": rec.get("quantity"),
                    "unit_of_measure": receipt_unit,
                    "line_total": receipt_line_total,
                },
                "comparison": {
                    "match_method": method,
                    "ItemDescription_vs_description": desc_status,
                    "match_reason": desc_reason,
                    "final_description": final_description,
                    "final_unit": final_unit,
                    "original_invoice_unit": invoice_unit,
                    "original_receipt_unit": receipt_unit,
                    "QTY_vs_quantity": qty_status,
                    "error_comments": None,
                },
            })

        else:
            # No real match found via Method 1 or 2 â€” check display-only fallback
            # so receipt_item isn't null in the API response on mismatches.
            rec = None
            if i in display_only_pairs:
                rec = receipt_items[display_only_pairs[i]]

            result.append({
                "line_item_comparison_number": i + 1,
                "po_number": inv.get("po_number") or (rec.get("po_number") if rec else None),
                "invoice_item": {
                    "po_number": inv.get("po_number"),
                    "line_item": inv.get("line_item") or inv.get("LineItemNumber"),
                    "ItemCode": inv.get("ItemCode"),
                    "ItemDescription": invoice_desc,
                    "QTY": inv.get("QTY"),
                    "Unit": invoice_unit,
                    "UnitPrice": inv.get("UnitPrice"),
                    "Amount": inv.get("Amount"),
                    "AmountInclVAT": inv.get("AmountInclVAT"),
                    "line_total": invoice_line_total,
                },
                "receipt_item": {
                    "po_number": rec.get("po_number"),
                    "line_item": rec.get("line_item"),
                    "description": rec.get("description"),
                    "quantity": None,
                    "total_received_quantity": rec.get("quantity"),
                    "unit_of_measure": rec.get("unit_of_measure"),
                    "line_total": rec.get("line_total"),
                } if rec else None,
                "comparison": {
                    "match_method": None,
                    "ItemDescription_vs_description": "not_matched",
                    "match_reason": (
                        "No receipt line matched via Method 1 (desc+qty) or Method 2 (desc+total); "
                        "showing PO-line receipt data for reference"
                        if rec else
                        "No receipt line matched via Method 1 (desc+qty) or Method 2 (desc+total)"
                    ),
                    "final_description": invoice_desc,
                    "final_unit": invoice_unit,
                    "original_invoice_unit": invoice_unit,
                    "original_receipt_unit": rec.get("unit_of_measure") if rec else None,
                    "QTY_vs_quantity": "not_matched",
                    "error_comments": f"No receipt match found for: '{invoice_desc}'",
                },
            })

    logging.info(
        f"ðŸ“Š Line-item matching: {len(matched_invoice)} matched "
        f"({sum(1 for _,_,m in matches if m=='method_1_desc_qty')} via M1, "
        f"{sum(1 for _,_,m in matches if m=='method_1b_partial_qty')} via M1b, "
        f"{sum(1 for _,_,m in matches if m=='method_2_desc_total')} via M2), "
        f"{len(invoice_items)-len(matched_invoice)} unmatched"
    )
    return result
    
def perform_comparison_and_check_match(pdf_id: str, po_number: List[str]) -> Tuple[bool, str, List[dict]]:
    """
    Run the two-method comparison and return (all_passed, comments, matched_items_data).
    matched_items_data is returned to the caller for use when saving pre-RPA data.
    """
    try:
        invoice_data = fetch_invoice_data(pdf_id)
        if not invoice_data:
            return False, "No invoice data found", []

        ariba_data = fetch_ariba_data(po_number, invoice_data.get("line_items", []))

        # â”€â”€ Service POs (product_type_code=2) never get a goods receipt, so
        # they skip the whole receipt-based flow below. For now (Case 1
        # only) this is a direct total match: the invoice total must equal
        # this PO's own total (sum of its line totals) within tolerance -
        # no FIFO/partial-balance allocation (allocate_service_po_amount)
        # and no "closest bigger PO" fallback. Those looser cases were
        # matching invoices to the wrong same-supplier PO (e.g. a PO for an
        # unrelated month/service that merely had a close-enough total), so
        # they're disabled until a safer signal is added; an invoice that
        # doesn't exactly match falls through to 'failed' and the
        # S4_Invoice_Data duplicate check instead.
        if po_lines_are_service(ariba_data.get("line_items", [])):
            service_line_items = ariba_data.get("line_items", [])
            po_total = sum(float(li.get("line_total") or 0) for li in service_line_items)
            try:
                invoice_total_val = (
                    float(str(invoice_data.get("invoice_total")).replace(",", ""))
                    if invoice_data.get("invoice_total") else None
                )
            except (ValueError, TypeError):
                invoice_total_val = None

            matched_items_data = [{
                "line_item_number": li["line_item"],
                "po_number": li["po_number"],
                "description": li.get("description"),
                "amount_allocated": li.get("line_total"),
                "match_method": "service_po_total_amount",
            } for li in service_line_items]

            if invoice_total_val is not None and abs(round(po_total, 2) - round(invoice_total_val, 2)) <= 0.99:
                comment = (
                    f"All checks passed - Service PO total match "
                    f"(invoice total={invoice_total_val}, PO total={po_total})"
                )
                return True, comment, matched_items_data

            comment = (
                f"Total mismatch - Service PO total {po_total} vs invoice total "
                f"{invoice_data.get('invoice_total')}"
            )
            return False, comment, matched_items_data

        receipt_data = fetch_receipt_data(
            po_number, ariba_data,
            invoice_data.get("line_items", []),
            invoice_date=invoice_data.get("invoice_date_str"),
        )
        if not receipt_data:
            return False, "No receipt data found", []

        # â”€â”€ PO number check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        po_comparison = compare_po_numbers_invoice_receipt_only(
            invoice_data.get("invoice_po_number"),
            receipt_data.get("receipt_po_number"),
        )

        # â”€â”€ Line-item comparison (2-method) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        line_items_comparison = compare_line_items_invoice_receipt_only(
            invoice_data.get("line_items", []),
            receipt_data.get("line_items", []),
        )

        # â”€â”€ Total comparison (only matched receipt lines) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # NOTE: receipt_item can now be populated for display-only fallback pairs
        # (Method 3 display fallback in compare_line_items_invoice_receipt_only),
        # which have match_method == None. Those must NOT count toward the total.
        matched_receipt_items = [
            lc["receipt_item"]
            for lc in line_items_comparison
            if lc.get("receipt_item") and lc.get("comparison", {}).get("match_method")
        ]
        total_comparison = compare_totals_invoice_receipt_only(
            invoice_data.get("invoice_total"),
            receipt_data["receipt_total"],
            0.99,
            matched_receipt_items,
            fetch_document_currency(po_number),
        )

        # â”€â”€ Collect mismatches â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        mismatch_comments = []

        if po_comparison.get("status") == "not_matched":
            mismatch_comments.append(po_comparison.get("error_comment", "PO number mismatch"))

        if total_comparison.get("status") == "not_matched" and total_comparison.get("error_comment"):
            mismatch_comments.append(total_comparison.get("error_comment"))

        # Build matched_items_data for PRE-RPA mapping
        matched_items_data = []
        for lc in line_items_comparison:
            comp       = lc.get("comparison", {})
            inv_item   = lc.get("invoice_item", {})
            rec_item   = lc.get("receipt_item") or {}

            invoice_desc  = inv_item.get("ItemDescription", "")
            receipt_desc  = rec_item.get("description", "")
            invoice_unit  = inv_item.get("Unit") or ""
            receipt_unit  = rec_item.get("unit_of_measure") or ""
            receipt_qty   = rec_item.get("quantity") if rec_item else None
            final_desc    = comp.get("final_description") or invoice_desc
            final_unit    = comp.get("final_unit") or invoice_unit

            matched_items_data.append({
                "line_item_number":             inv_item.get("line_item"),
                "original_invoice_description": invoice_desc,
                "original_invoice_unit":        invoice_unit,
                "receipt_description":          receipt_desc,
                "receipt_unit":                 receipt_unit,
                "receipt_quantity":             receipt_qty,
                "final_description":            final_desc,
                "final_unit":                   final_unit,
                "description_status":           comp.get("ItemDescription_vs_description", "not_matched"),
                "match_method":                 comp.get("match_method"),
            })

            ec = comp.get("error_comments")
            if ec:
                mismatch_comments.append(
                    f"Item '{invoice_desc or receipt_desc}': {ec}"
                )

        # â”€â”€ METHOD 3 fallback â€” aggregate RC total vs invoice total â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Only tried if Method 1 / Method 2 line-item matching left mismatches.
        # Bypasses per-line description/qty/price checks entirely â€” works
        # purely on RC-total vs invoice-total.
        if mismatch_comments:
            m3_match = try_method3_rc_total_match(
                po_number, invoice_data.get("invoice_total")
            )
            if m3_match:
                rc_no, rc_data = m3_match

                # Method 3 only ever matches a single RC on total.
                # (No longer stored in Invoice_Receipt_Mapping.)

                matched_items_data = [{
                    "line_item_number": li.get("line_item"),
                    "original_invoice_description": None,
                    "original_invoice_unit": None,
                    "receipt_description": li.get("description"),
                    "receipt_unit": li.get("unit_of_measure"),
                    "receipt_quantity": li.get("quantity"),
                    "final_description": li.get("description"),
                    "final_unit": li.get("unit_of_measure"),
                    "description_status": "matched",
                    "match_method": "method_3_rc_total",
                } for li in rc_data["line_items"]]

                comment = (
                    f"All checks passed via Method 3 (RC total match) â€” "
                    f"RC={rc_no}, RC total={rc_data['total']}, "
                    f"invoice total={invoice_data.get('invoice_total')}"
                )
                return True, comment, matched_items_data

        # â”€â”€ All passed? â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not mismatch_comments:
            # Save RC IDs for matched lines
            rc_id_by_line  = receipt_data.get("rc_id_by_line", {})
            rc_by_line_all = receipt_data.get("_rc_by_line_all", {})
            matched_rc_ids = []

            for lc in line_items_comparison:
                ri = lc.get("receipt_item") or {}
                if not ri.get("line_item"):
                    continue
                try:
                    po_line = int(ri["line_item"])
                except (ValueError, TypeError):
                    continue
                rc = rc_id_by_line.get(po_line) or rc_by_line_all.get(po_line)
                if rc and rc not in matched_rc_ids:
                    matched_rc_ids.append(rc)

            # (No longer stored in Invoice_Receipt_Mapping.)

            methods_used = set(
                lc["comparison"].get("match_method")
                for lc in line_items_comparison
                if lc["comparison"].get("match_method")
            )
            comment = (
                f"All checks passed - Ready for RPA "
                f"(methods used: {', '.join(sorted(methods_used))})"
            )
            return True, comment, matched_items_data

        final_comment = "\n".join(mismatch_comments)
        return False, final_comment, matched_items_data

    except Exception as e:
        logging.error(f"Comparison failed for pdf_id={pdf_id}: {e}")
        return False, f"Comparison failed with error: {str(e)}", []


def run_po_comparison_and_update_status(
    pdf_id: str, filename: str, file_input_id: Optional[int] = None,
    po_match_comment: Optional[str] = None,
) -> Tuple[str, str]:
    """Run the automatic PO/receipt comparison for a just-ingested invoice
    and persist the outcome to Newyork_FileInput.status/comments - wired
    into every ingestion path (manual upload, ZIP, SFTP/email) so a fresh
    invoice never sits at the initial "Success" placeholder set at insert
    time, unmatched and uncommented, until someone manually re-triggers
    /compare-invoice-ariba-receipt_id.

    Either way, S4_Invoice_Data is checked FIRST for a pre-existing SAP
    invoice - matched on InvoiceNumber + a supplier_id resolved from the PO
    when one was found, or (when no PO could be resolved at all) directly
    from the invoice's own SupplierName via S4_Suppliers_prod - since a
    confirmed SAP fact takes priority over any heuristic outcome, including
    the "no PO found" failure:
      1. PO found, already in SAP -> 'processed' / duplicate comment.
      2. PO found, not in SAP -> run the normal receipt/PO comparison.
      3. No PO found, but the invoice is already in SAP under this
         supplier/invoice number -> still 'processed' / duplicate comment -
         our PO-matching heuristics failing to find the PO doesn't mean the
         invoice wasn't genuinely already posted.
      4. No PO found, not in SAP either -> 'failed' with an explanatory
         comment.

    Pass file_input_id (from insert_fileinput_to_db's return value) when the
    caller already knows the exact Newyork_FileInput row id; otherwise the
    status is written by pdf_id via update_fileinput_status_by_pdf_id (which
    also runs the duplicate-record cleanup that function does).

    `po_match_comment` is the percentage-based comment produced when this
    invoice's PONumber was resolved via the nearest-by-total PO fallback
    (find_po_by_supplier_and_total with allow_nearest_fallback=True) during
    extraction - e.g. "PO 4503 total is 2.0% lesser than the invoice total".
    When given, it's prepended to whatever comments this comparison itself
    produces, so the stored comments always show BOTH: how the PO was
    picked (percentage match) and the outcome of comparing against it.

    Returns (status, comments).
    """
    invoice_info = fetch_invoice_data(pdf_id)
    po_numbers = invoice_info.get("invoice_po_number") or []
    supplier_name = invoice_info.get("supplier_name")

    if po_numbers:
        existing_supplier_invoice = find_existing_s4_invoice(pdf_id, po_numbers=po_numbers)
    else:
        existing_supplier_invoice = find_existing_s4_invoice(pdf_id, supplier_name=supplier_name)

    if existing_supplier_invoice:
        status, comments = "processed", "Duplicate : Invoice Already Posted in SAP"
        update_customer_invoice_id(pdf_id, existing_supplier_invoice)
    elif po_numbers:
        is_matched, comments, _ = perform_comparison_and_check_match(pdf_id, po_numbers)
        status = "Success" if is_matched else "failed"
    else:
        status = "failed"
        comments = (
            f"No PO number could be found on invoice '{filename}' (pdf_id={pdf_id}), "
            "and the automatic PO-matching fallback (by supplier, description, and price) "
            "did not find a matching PO in SAP"
        )

    if existing_supplier_invoice:
        # SAP has independently confirmed this invoice is already posted -
        # keep the comment a clean, fixed string. The PO-match percentage
        # reasoning from extraction (how we guessed a PO via OCR heuristics)
        # is no longer relevant once a confirmed SAP fact takes priority.
        pass
    elif po_match_comment:
        comments = f"{po_match_comment}\n{comments}"
    else:
        comments = _carry_forward_po_match_comment(pdf_id, comments)

    if file_input_id is not None:
        update_file_status_and_comment(file_input_id, status, comments)
    else:
        update_fileinput_status_by_pdf_id(pdf_id, status, comments)

    return status, comments


# ============================================================================
# 17. ROUTER
# ============================================================================
# CORS and the app-level OpenAPI customization live in main.py (the single
# FastAPI() instance for the whole service). This module only exposes a
# router, mounted by parent.py under /api/v1, matching auth.py / pom_excel.py.
router = APIRouter(tags=["NewYork_ocr"], prefix="/newyork-ocr")

# ============================================================================
# 18. ENDPOINTS
# ============================================================================

@router.post("/extract-invoice")
async def extract_invoice(
    files: Annotated[List[UploadFile], File()],
    current_user: dict = Depends(get_current_user)
):
    ALLOWED_EXTENSIONS = {".pdf", ".zip", ".png", ".jpeg", ".jpg"}
    user_id = str(current_user.get("id"))
    person_name = current_user.get("name")
    email = current_user.get("email")
    ariba_user_name = current_user.get("userName")
    loop = asyncio.get_event_loop()

    try:
        tasks = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for file in files:
                file_extension = os.path.splitext(file.filename.lower())[1]
                if file_extension not in ALLOWED_EXTENSIONS:
                    tasks.append(asyncio.sleep(0, result={"filename": file.filename, "status": "failed", "error": "Invalid file type"}))
                    continue
                file_bytes = await file.read()
                if file_extension == ".zip":
                    tasks.append(process_zip_file_endpoint(file_bytes, file.filename, user_id, None, person_name, email, ariba_user_name))
                else:
                    tasks.append(loop.run_in_executor(executor, process_single_file_sync, file.filename, file_bytes, user_id, person_name, email, ariba_user_name))
            results = await asyncio.gather(*tasks)

        final_results = []
        for res in results:
            if isinstance(res, list):
                final_results.extend(res)
            else:
                final_results.append(res)

        # process_single_file_sync / process_zip_file_endpoint already run the
        # full PO/receipt comparison (run_po_comparison_and_update_status) and
        # persist status/comments to Newyork_FileInput as part of ingestion -
        # no further post-processing needed here.
        return JSONResponse(content={"results": final_results})
    except Exception as e:
        logging.error(f"Error in extract_invoice endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- 27.2 Drafts ------------------------------------------------------------
@router.post("/save-draft")
def save_draft(data: DraftRequest ,current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if data.items:
            for item in data.items:

                # Check whether this pdf_id and line item already exist
                cursor.execute(
                    """
                    SELECT id
                    FROM Save_as_draft
                    WHERE pdf_id = ?
                    AND Line_item = ?
                    """,
                    (data.pdfId, item.Line_item)
                )

                row = cursor.fetchone()

                if row:
                    # Update the existing line-item row
                    cursor.execute(
                        """
                        UPDATE Save_as_draft
                        SET
                            poNumber = ?,
                            Total = ?,
                            Invoice_Number = ?,
                            Item_description = ?,
                            QTY = ?,
                            Unit = ?
                        WHERE pdf_id = ?
                        AND Line_item = ?
                        """,
                        (
                            data.poNumber,
                            data.Po_Total,
                            data.Invoice_Number,
                            item.Item_description,
                            item.QTY,
                            item.Unit,
                            data.pdfId,
                            item.Line_item
                        )
                    )

                else:
                    # Insert a new row for the line item
                    cursor.execute(
                        """
                        INSERT INTO Save_as_draft
                        (
                            pdf_id,
                            poNumber,
                            Total,
                            Line_item,
                            Item_description,
                            Invoice_Number,
                            QTY,
                            Unit
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            data.pdfId,
                            data.poNumber,
                            data.Po_Total,
                            item.Line_item,
                            item.Item_description,
                            data.Invoice_Number,
                            item.QTY,
                            item.Unit
                        )
                    )

        conn.commit()

        return {
            "status": "success",
            "message": "Draft data processed successfully"
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {str(e)}"
        )

    finally:
        cursor.close()
        conn.close()



@router.get("/base64-for-view-icon/{pdf_id}")
async def get_base64_for_view_icon(pdf_id: str = Path(...), current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT file_base64 FROM Newyork_FileInput WHERE pdf_id = ?", (pdf_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=404, detail=f"No file found for pdf_id {pdf_id}")
        return JSONResponse(content={"file_base64": row[0]})
    finally:
        cursor.close()
        conn.close()


@router.post("/process-sftp-folder-invoices/")
async def process_sftp_folder_invoices_endpoint(
    folder_path: str = Query(..., description="Full SFTP folder path to scan and extract, e.g. /home/aribainv/invoices/NewYork_ocr_invoices"),
    max_files: int = Query(200, ge=1, le=1000, description="Max number of pending PDFs to extract in one call"),
    parallel_workers: int = Query(NEWYORK_SFTP_PARALLEL_WORKERS, ge=1, le=20, description="How many PDFs to extract concurrently"),
    current_user: dict = Depends(get_current_user),
):
    """
    Mirrors ocr_main.py's /process-local-folder-invoices/ endpoint (path in,
    walk + extract + marker-skip + summary out), but reaches the folder over
    SFTP instead of local disk, since NewYork_ocr's invoice folders live on a
    remote server. Pass the full path, e.g.
    /home/aribainv/invoices/NewYork_ocr_invoices, and every PDF/PNG/JPEG in
    it that doesn't already have a '<filename>.processed' marker gets
    OCR-extracted and inserted into the database - up to `parallel_workers`
    at once (default 5), same as ocr_sftp.py's folder processing.
    """
    result = process_sftp_folder_invoices(folder_path, max_files=max_files, parallel_workers=parallel_workers)
    return JSONResponse(content=result)


@router.post("/process-sftp-invoice/")
async def process_sftp_invoice_endpoint(
    filename: str = Query(..., description="PDF filename already sitting in the New York SFTP inbox, e.g. Invoice_370_2026-08-31.pdf"),
    current_user: dict = Depends(get_current_user),
):
    """
    Single-file counterpart to /process-sftp-folder-invoices/ - takes just a
    filename (no folder path needed), reads it from the fixed New York
    email_invoices inbox on SFTP, and runs the same extract + PO-compare
    pipeline on that one file. For testing or reprocessing a specific
    invoice without scanning the whole folder.
    """
    result = process_sftp_single_invoice(filename)
    return JSONResponse(content=result)


@router.post("/delete-processed-markers-for-failed-invoices/")
async def delete_processed_markers_for_failed_invoices(
    folder_path: str = Query(NEWYORK_EMAIL_INVOICES_PATH, description="SFTP folder the '.processed' markers live in"),
    current_user: dict = Depends(get_current_user),
):
    """
    Deletes ONLY the '<filename>.processed' marker file on SFTP for every
    Newyork_FileInput row with status = 'failed' - never the original PDF.

    process_sftp_folder_invoices (the 30-min SFTP scheduler) skips any PDF
    that already has a '.processed' marker next to it, so a failed invoice
    just sits there forever even after a fix that would let it match
    correctly on retry. Removing the marker makes the scheduler treat that
    file as pending again on its next scan, so it gets automatically
    re-extracted and re-compared - no manual rerun call needed, and nothing
    is deleted from SFTP except the marker itself.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DISTINCT filename FROM Newyork_FileInput WHERE status = 'failed'")
        failed_filenames = [row[0] for row in cursor.fetchall() if row[0]]
    finally:
        cursor.close()
        conn.close()

    if not failed_filenames:
        return JSONResponse(content={
            "status": "completed",
            "folder_path": folder_path,
            "failed_invoice_count": 0,
            "deleted_marker_count": 0,
            "deleted": [],
            "not_found": [],
            "errors": [],
        })

    try:
        ssh, sftp = _ny_create_sftp_connection()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not connect to SFTP: {e}")

    deleted, not_found, errors = [], [], []
    try:
        for filename in failed_filenames:
            marker_path = f"{folder_path}/{filename}.processed"
            try:
                sftp.remove(marker_path)
                deleted.append(filename)
            except FileNotFoundError:
                not_found.append(filename)
            except Exception as e:
                errors.append({"filename": filename, "error": str(e)})
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass

    logging.info(
        f"[Delete processed markers] folder='{folder_path}' failed_invoices={len(failed_filenames)} "
        f"deleted={len(deleted)} not_found={len(not_found)} errors={len(errors)}"
    )

    return JSONResponse(content={
        "status": "completed",
        "folder_path": folder_path,
        "failed_invoice_count": len(failed_filenames),
        "deleted_marker_count": len(deleted),
        "deleted": deleted,
        "not_found": not_found,
        "errors": errors,
    })


@router.post("/delete-all-processed-markers/")
async def delete_all_processed_markers(
    folder_path: str = Query(NEWYORK_EMAIL_INVOICES_PATH, description="SFTP folder the '.processed' markers live in"),
    current_user: dict = Depends(get_current_user),
):
    """
    Deletes EVERY '.processed' marker file directly under folder_path - never
    the original PDF/PNG/JPEG invoice files themselves (only entries whose
    name ends in '.processed' are touched). After this runs,
    process_sftp_folder_invoices will treat every invoice in the folder as
    pending again on its next scan and re-extract/re-compare all of them.
    """
    try:
        ssh, sftp = _ny_create_sftp_connection()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not connect to SFTP: {e}")

    deleted, errors = [], []
    try:
        try:
            entries = sftp.listdir_attr(folder_path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")

        marker_filenames = [e.filename for e in entries if e.filename.lower().endswith(".processed")]

        for marker_filename in marker_filenames:
            marker_path = f"{folder_path}/{marker_filename}"
            try:
                sftp.remove(marker_path)
                deleted.append(marker_filename)
            except Exception as e:
                errors.append({"filename": marker_filename, "error": str(e)})
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass

    logging.info(
        f"[Delete ALL processed markers] folder='{folder_path}' "
        f"deleted={len(deleted)} errors={len(errors)}"
    )

    return JSONResponse(content={
        "status": "completed",
        "folder_path": folder_path,
        "deleted_marker_count": len(deleted),
        "deleted": deleted,
        "errors": errors,
    })


@router.post("/backfill-po-numbers-for-processed-invoices/")
async def backfill_po_numbers_for_processed_invoices(current_user: dict = Depends(get_current_user)):
    """
    One-off catch-up for invoices marked status='processed' (matched to
    S4_Invoice_Data as already posted in SAP) whose
    Newyork_Invoice_extracted_data.PONumber isn't (or isn't yet) SAP's own
    confirmed PO reference - update_customer_invoice_id now does this
    automatically every time an invoice is recognized as already posted, but
    rows written before that change may still carry a blank PONumber, or one
    that was only ever an OCR heuristic guess (price/description match,
    exact-total match, or a nearest-total guess) that SAP's own data can now
    confirm or correct.

    Finds every Newyork_FileInput row with status='processed' and a
    CUSTOMER_INVOICE_ID, and OVERWRITES Newyork_Invoice_extracted_data.
    PONumber with S4_Invoice_PurOrdRef_Data's PO reference for that SAP
    invoice - regardless of whatever PONumber is currently stored, since
    S4_Invoice_PurOrdRef_Data is ground truth once SAP has confirmed the
    invoice is posted.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT fi.pdf_id, fi.CUSTOMER_INVOICE_ID
            FROM Newyork_FileInput fi
            WHERE fi.status = 'processed'
              AND fi.CUSTOMER_INVOICE_ID IS NOT NULL AND fi.CUSTOMER_INVOICE_ID <> ''
        """)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    updated, no_po_found = [], []
    for pdf_id, customer_invoice_id in rows:
        po_numbers = _fetch_po_numbers_for_sap_invoice(str(customer_invoice_id))
        if po_numbers:
            _backfill_po_number_from_sap(pdf_id, str(customer_invoice_id))
            updated.append(pdf_id)
        else:
            no_po_found.append(pdf_id)

    logging.info(
        f"[PO backfill] candidates={len(rows)} updated={len(updated)} no_po_found={len(no_po_found)}"
    )

    return JSONResponse(content={
        "status": "completed",
        "candidate_count": len(rows),
        "updated_count": len(updated),
        "updated_pdf_ids": updated,
        "no_po_found_count": len(no_po_found),
        "no_po_found_pdf_ids": no_po_found,
    })


class RerunInvoicesRequest(BaseModel):
    filenames: List[str]


@router.post("/rerun-invoices-by-filename")
async def rerun_invoices_by_filename(
    request: RerunInvoicesRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Re-run the full extraction + PO comparison pipeline for one or more
    already-ingested invoices, identified by their Newyork_FileInput.filename -
    for fixing invoices that were mismatched against the wrong PO (or missed
    a real PO) before a PO-matching bug fix, without needing the original PDF
    re-uploaded from the client.

    For each filename: looks up its most recent Newyork_FileInput row,
    decodes its stored file_base64, and re-runs process_file_with_gemini on
    those same bytes - so PONumber is resolved fresh with the CURRENT
    matching logic - then fully replaces that pdf_id's line items
    (delete_invoice_extracted_rows + insert_invoice_to_db) and reruns
    run_po_comparison_and_update_status, which overwrites whatever status
    was stored before (including a stale 'processed') with the freshly
    determined one - 'Success', 'failed', or 'processed' if it genuinely
    turns out to already be posted in SAP.
    """
    results = []
    for filename in request.filenames:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT TOP 1 id, pdf_id, user_id, file_base64
                FROM Newyork_FileInput
                WHERE filename = ?
                ORDER BY created_at DESC
            """, (filename,))
            row = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

        if not row:
            results.append({
                "filename": filename, "status": "error",
                "message": "No Newyork_FileInput record found for this filename",
            })
            continue

        file_input_id, pdf_id, owner_user_id, file_b64 = row
        if not file_b64:
            results.append({
                "filename": filename, "pdf_id": pdf_id, "status": "error",
                "message": "No stored file_base64 to re-extract from",
            })
            continue

        try:
            file_bytes = base64.b64decode(file_b64)
            extracted_invoices = process_file_with_gemini(file_bytes, filename, owner_user_id, pdf_id)
            # This endpoint refreshes ONE already-existing pdf_id in place -
            # it doesn't mint new pdf_ids for extra invoices the way fresh
            # ingestion does. Take whichever extracted invoice was assigned
            # this pdf_id (always the first - see process_file_with_gemini);
            # if the file turns out to hold more than one distinct invoice,
            # only that first one is refreshed here and the rest are logged,
            # not silently dropped.
            invoice_data = next((inv for pid, inv in extracted_invoices if pid == pdf_id), extracted_invoices[0][1])
            if len(extracted_invoices) > 1:
                logging.warning(
                    f"[rerun-invoices-by-filename] '{filename}' (pdf_id={pdf_id}) re-extracted as "
                    f"{len(extracted_invoices)} distinct invoices - only the one matching this pdf_id was "
                    f"refreshed; the others were not created (this endpoint only refreshes an existing pdf_id)"
                )

            if _invoice_total_is_zero(invoice_data):
                results.append({
                    "filename": filename, "pdf_id": pdf_id, "status": "skipped",
                    "message": "Invoice_Total extracted as 0.00 on re-extraction - existing record left untouched",
                })
                continue

            delete_invoice_extracted_rows(pdf_id)
            insert_invoice_to_db(invoice_data, pdf_id, owner_user_id)

            comparison_status, compare_comments = run_po_comparison_and_update_status(
                pdf_id, filename, file_input_id,
                po_match_comment=invoice_data.get("PO_Match_Comment"),
            )

            results.append({
                "filename": filename,
                "pdf_id": pdf_id,
                "status": "reprocessed",
                "comparison_status": comparison_status,
                "comparison_comments": compare_comments,
            })
        except Exception as e:
            logging.error(f"[rerun-invoices-by-filename] Failed to reprocess '{filename}' (pdf_id={pdf_id}): {e}")
            results.append({
                "filename": filename, "pdf_id": pdf_id, "status": "error", "message": str(e),
            })

    return JSONResponse({"results": results})


def _parse_email_fetch_datetime(value: str, field_name: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"{field_name} must be in 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' format")


def run_email_fetch_cycle(from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
    """
    Polls the mailbox (Microsoft Graph) for 'invoice' emails and saves their
    PDF attachments to the New York SFTP inbox (NEWYORK_EMAIL_INVOICES_PATH) -
    no OCR/comparison happens here; the SFTP folder scan/scheduler picks the
    saved files up on its normal cycle like any other file dropped there.

    With no from_date/to_date, resumes from the to_date of the last run that
    completed with process_status = '1' (or today 00:00 UTC if the table is
    empty), and always fetches up to the current UTC time. Shared by
    /fetch-invoices-from-email/ and the 10-min email-fetch scheduler below.
    """
    now = datetime.now(timezone.utc)

    if from_date and to_date:
        start = _parse_email_fetch_datetime(from_date, "from_date")
        end = _parse_email_fetch_datetime(to_date, "to_date")
    else:
        last = get_last_email_fetch_entry()
        last_success_to_date = get_last_successful_email_to_date()

        if not last:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = last_success_to_date if last_success_to_date else last['to_date']
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)

        end = now

    if start >= end:
        return {"message": "No new range to fetch", "results": []}

    results, error_message = fetch_invoices_from_email(start, end)

    total_attachments = len(results)
    saved_count = sum(1 for r in results if r.get("status") == "saved")
    failed_count = sum(1 for r in results if r.get("status") == "failed")

    # "1" = every attachment found was saved to SFTP (including none found at
    # all); "0" = at least one attachment failed to save.
    process_status = "0" if failed_count > 0 else "1"

    insert_email_fetch(start, end, total_attachments, saved_count, failed_count, process_status)

    return {"results": results, "error_message": error_message}


# ============================================================================
# EMAIL FETCH SCHEDULER - polls the mailbox every
# FETCH_EMAIL_SCHEDULER_INTERVAL_MINUTES on a fixed interval (unlike the SFTP
# scan above, this doesn't need to wait out long-running work between ticks).
# Overlap is still guarded so a slow run never runs concurrently with itself.
# ============================================================================
FETCH_EMAIL_SCHEDULER_INTERVAL_MINUTES = 10
FETCH_EMAIL_SCHEDULER_JOB_ID = "newyork_email_fetch"

newyork_email_scheduler = BackgroundScheduler()
_newyork_email_fetch_job_running = False


def _newyork_email_fetch_scheduled_job():
    global _newyork_email_fetch_job_running
    if _newyork_email_fetch_job_running:
        logging.warning("[Email fetch scheduler] Previous run still in progress - skipping this tick")
        return

    _newyork_email_fetch_job_running = True
    try:
        logging.info("[Email fetch scheduler] Run starting")
        result = run_email_fetch_cycle()
        logging.info(f"[Email fetch scheduler] Run complete: {result}")
    except Exception as e:
        logging.error(f"[Email fetch scheduler] Run failed: {e}", exc_info=True)
    finally:
        _newyork_email_fetch_job_running = False


def start_newyork_email_fetch_scheduler():
    """Call once at app startup. No-op if FETCH_EMAIL_SCHEDULER is False."""
    if not FETCH_EMAIL_SCHEDULER:
        logging.info("[Email fetch scheduler] FETCH_EMAIL_SCHEDULER=False - not starting")
        return
    if not newyork_email_scheduler.running:
        newyork_email_scheduler.start()
    newyork_email_scheduler.add_job(
        _newyork_email_fetch_scheduled_job,
        trigger=IntervalTrigger(minutes=FETCH_EMAIL_SCHEDULER_INTERVAL_MINUTES),
        id=FETCH_EMAIL_SCHEDULER_JOB_ID,
        replace_existing=True,
        coalesce=True,
    )
    logging.info(f"[Email fetch scheduler] Started - every {FETCH_EMAIL_SCHEDULER_INTERVAL_MINUTES} min")


@router.post("/fetch-invoices-from-email/")
async def fetch_email_endpoint(
    from_date: Optional[str] = Query(None, description="Start date/time ('YYYY-MM-DD HH:MM:SS', or 'YYYY-MM-DD' for midnight). If omitted (along with to_date), resumes from the last fetch."),
    to_date: Optional[str] = Query(None, description="End date/time ('YYYY-MM-DD HH:MM:SS', or 'YYYY-MM-DD' for midnight). If omitted (along with from_date), defaults to now."),
    current_user: dict = Depends(get_current_user),
):
    try:
        result = run_email_fetch_cycle(from_date, to_date)
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in fetch_email_endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/invoice/{pdf_id}")
async def get_invoice(pdf_id: str):

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        # ==================================================
        # 1. Check whether the invoice exists
        # ==================================================
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
            WHERE pdf_id = ?
            """,
            (pdf_id,)
        )

        if cursor.fetchone()[0] == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Invoice with pdf_id '{pdf_id}' not found"
            )

        # ==================================================
        # 2. Get all invoice records
        # ==================================================
        cursor.execute(
            """
            SELECT
                e.user_id,
                e.pdf_id,
                e.source_file,
                e.invoice_id,
                e.InvoiceNumber,
                e.SupplierName,
                e.CustomerName,
                e.PONumber,
                e.InvoiceDate,
                e.SupplierTRN,
                e.CustomerTRN,
                e.Email,
                e.UnitPrice,
                e.Amount,
                e.Discount,
                e.VAT,
                e.VATAmount,
                e.AmountInclVAT,
                e.is_trusted,
                e.created_at,
                e.BankAccountName,
                e.BankName,
                e.BankBranch,
                e.AccountNumber,
                e.IBAN,
                e.SwiftCode,
                e.BranchCode,
                e.Tax_invoice,
                e.Stamp,
                e.Customer_address,
                e.Supplier_address,
                e.ItemCode,
                e.Unit,
                e.LineItemNumber,
                e.ItemDescription,
                e.QTY,
                e.Invoice_Total,
                e.Po_Total
            FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data] e
            WHERE e.pdf_id = ?
            ORDER BY
                e.PONumber,
                CASE
                    WHEN ISNUMERIC(e.LineItemNumber) = 1
                    THEN CAST(e.LineItemNumber AS INT)
                    ELSE 999999
                END,
                e.created_at
            """,
            (pdf_id,)
        )

        invoice_rows = cursor.fetchall()

        if not invoice_rows:
            raise HTTPException(
                status_code=404,
                detail="Invoice data not found"
            )

        invoice_columns = [
            column[0]
            for column in cursor.description
        ]

        # Convert invoice rows into dictionaries
        invoice_data_rows = [
            dict(zip(invoice_columns, row))
            for row in invoice_rows
        ]

        # ==================================================
        # 3. Get all draft records
        # ==================================================
        cursor.execute(
            """
            SELECT
                id,
                pdf_id,
                poNumber,
                Total AS Po_Total,
                Line_item,
                Item_description,
                Invoice_Number,
                QTY,
                Unit,
                created_at
            FROM [final_pulse_DBprod].[dbo].[Save_as_draft]
            WHERE pdf_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (pdf_id,)
        )

        draft_rows = cursor.fetchall()

        draft_columns = [
            column[0]
            for column in cursor.description
        ]

        # Convert draft rows into dictionaries
        draft_data_rows = [
            dict(zip(draft_columns, row))
            for row in draft_rows
        ]

        # ==================================================
        # 4. Helper function
        # ==================================================
        def safe_str(value):

            if value is None:
                return None

            return str(value)

        # ==================================================
        # 5. Get the latest invoice timestamp
        # ==================================================
        latest_invoice_created_at = max(
            (
                row.get("created_at")
                for row in invoice_data_rows
                if row.get("created_at") is not None
            ),
            default=None
        )

        # ==================================================
        # 6. Get the latest draft timestamp
        # ==================================================
        latest_draft_created_at = max(
            (
                row.get("created_at")
                for row in draft_data_rows
                if row.get("created_at") is not None
            ),
            default=None
        )

        # ==================================================
        # 7. Find the latest draft header record
        #
        # The latest draft record is used for:
        # poNumber
        # Total
        # Invoice_Number
        # ==================================================
        latest_draft_header = None

        if draft_data_rows:

            latest_draft_header = max(
                draft_data_rows,
                key=lambda row: (
                    row.get("created_at")
                    or datetime.min,
                    row.get("id")
                    or 0
                )
            )

        # ==================================================
        # 8. Decide which table has the latest data
        # ==================================================
        use_draft_header = False

        if latest_draft_header:

            if latest_invoice_created_at is None:

                use_draft_header = True

            elif (
                latest_draft_created_at is not None
                and latest_draft_created_at
                > latest_invoice_created_at
            ):

                use_draft_header = True

        # ==================================================
        # 9. Select final header values
        # ==================================================
        first_invoice_row = invoice_data_rows[0]

        if use_draft_header:

            final_po_number = (
                latest_draft_header.get("poNumber")
                if latest_draft_header.get("poNumber") is not None
                else first_invoice_row.get("PONumber")
            )

            final_po_total = (
                latest_draft_header.get("Po_Total")
                if latest_draft_header.get("Po_Total") is not None
                else first_invoice_row.get("Po_Total")
            )

            final_invoice_number = (
                latest_draft_header.get(
                    "Invoice_Number"
                )
                if latest_draft_header.get(
                    "Invoice_Number"
                ) is not None
                else first_invoice_row.get(
                    "InvoiceNumber"
                )
            )

        else:

            # Newyork_Invoice_extracted_data is newer
            final_po_number = (
                first_invoice_row.get(
                    "PONumber"
                )
            )

            final_po_total = (
                first_invoice_row.get(
                    "Po_Total"
                )
            )

            final_invoice_number = (
                first_invoice_row.get(
                    "InvoiceNumber"
                )
            )

        # ==================================================
        # 10. Create latest draft lookup by Line_item
        #
        # If duplicate draft records exist for the same
        # Line_item, only the latest record is used.
        # ==================================================
        latest_draft_by_line_item = {}

        for draft_row in draft_data_rows:

            line_item = draft_row.get(
                "Line_item"
            )

            if line_item is None:
                continue

            line_item_key = str(
                line_item
            )

            existing_draft = (
                latest_draft_by_line_item.get(
                    line_item_key
                )
            )

            if existing_draft is None:

                latest_draft_by_line_item[
                    line_item_key
                ] = draft_row

            else:

                existing_created_at = (
                    existing_draft.get(
                        "created_at"
                    )
                    or datetime.min
                )

                current_created_at = (
                    draft_row.get(
                        "created_at"
                    )
                    or datetime.min
                )

                if current_created_at > existing_created_at:

                    latest_draft_by_line_item[
                        line_item_key
                    ] = draft_row

                elif (
                    current_created_at
                    == existing_created_at
                    and (
                        draft_row.get("id")
                        or 0
                    )
                    > (
                        existing_draft.get("id")
                        or 0
                    )
                ):

                    latest_draft_by_line_item[
                        line_item_key
                    ] = draft_row

        # ==================================================
        # 11. Build line items
        # ==================================================
        items = []

        for invoice_row in invoice_data_rows:

            line_item_number = (
                invoice_row.get(
                    "LineItemNumber"
                )
            )

            line_item_key = (
                safe_str(
                    line_item_number
                )
            )

            latest_draft_item = (
                latest_draft_by_line_item.get(
                    line_item_key
                )
            )

            invoice_item_created_at = (
                invoice_row.get(
                    "created_at"
                )
            )

            draft_item_created_at = None

            if latest_draft_item:

                draft_item_created_at = (
                    latest_draft_item.get(
                        "created_at"
                    )
                )

            # Use draft line-item values only when
            # the draft record is newer
            use_draft_item = False

            if latest_draft_item:

                if invoice_item_created_at is None:

                    use_draft_item = True

                elif (
                    draft_item_created_at
                    is not None
                    and draft_item_created_at
                    > invoice_item_created_at
                ):

                    use_draft_item = True

            # ----------------------------------------------
            # Select final line-item values
            # ----------------------------------------------
            if use_draft_item:

                final_item_description = (
                    latest_draft_item.get(
                        "Item_description"
                    )
                    if latest_draft_item.get(
                        "Item_description"
                    ) is not None
                    else invoice_row.get(
                        "ItemDescription"
                    )
                )

                final_qty = (
                    latest_draft_item.get(
                        "QTY"
                    )
                    if latest_draft_item.get(
                        "QTY"
                    ) is not None
                    else invoice_row.get(
                        "QTY"
                    )
                )

                final_unit = (
                    latest_draft_item.get(
                        "Unit"
                    )
                    if latest_draft_item.get(
                        "Unit"
                    ) is not None
                    else invoice_row.get(
                        "Unit"
                    )
                )

            else:

                # Newyork_Invoice_extracted_data line item
                # is newer
                final_item_description = (
                    invoice_row.get(
                        "ItemDescription"
                    )
                )

                final_qty = (
                    invoice_row.get(
                        "QTY"
                    )
                )

                final_unit = (
                    invoice_row.get(
                        "Unit"
                    )
                )

            resolved_po_number = invoice_row.get("PONumber") or final_po_number

            # The saved draft's Po_Total only applies to the PO group it was
            # actually edited for - other PO groups on the same invoice keep
            # their own extracted Po_Total.
            if (
                use_draft_header
                and final_po_number is not None
                and resolved_po_number == final_po_number
            ):
                resolved_po_total = final_po_total
            else:
                resolved_po_total = invoice_row.get("Po_Total")

            items.append(
                {
                    "LineItemNumber": safe_str(
                        line_item_number
                    ),

                    "PONumber": safe_str(
                        resolved_po_number
                    ),

                    "Po_Total": safe_str(
                        resolved_po_total
                    ),

                    "ItemCode": safe_str(
                        invoice_row.get(
                            "ItemCode"
                        )
                    ),

                    "ItemDescription": safe_str(
                        final_item_description
                    ),

                    "Unit": safe_str(
                        final_unit
                    ),

                    "QTY": safe_str(
                        final_qty
                    ),

                    "UnitPrice": safe_str(
                        invoice_row.get(
                            "UnitPrice"
                        )
                    ),

                    "Amount": safe_str(
                        invoice_row.get(
                            "Amount"
                        )
                    ),

                    "Discount": safe_str(
                        invoice_row.get(
                            "Discount"
                        )
                    ),

                    "VAT": safe_str(
                        invoice_row.get(
                            "VAT"
                        )
                    ),

                    "VATAmount": safe_str(
                        invoice_row.get(
                            "VATAmount"
                        )
                    ),

                    "AmountInclVAT": safe_str(
                        invoice_row.get(
                            "AmountInclVAT"
                        )
                    )
                }
            )

        po_numbers_for_grn = sorted({
            item["PONumber"] for item in items if item.get("PONumber")
        })

        # S4_Purchase_Order_Data.purchase_order_item is already normalized
        # (1, 2, 3, ...) to line up 1:1 with the invoice's own LineItemNumber,
        # so this join doesn't need the /10 normalization the GRN join below does.
        # Only tax_code is sourced from here - purchase_order_item in the
        # response comes from S4_GRN_Data instead (see grn_match below), since
        # that's the raw SAP form (10, 20, 30, ...) SAP itself expects back.
        po_data_by_po_and_line: Dict[Tuple[str, str], dict] = {}
        po_data_by_po_latest: Dict[str, dict] = {}  # fallback if a line has no exact match
        if po_numbers_for_grn:
            placeholders_po = ", ".join("?" * len(po_numbers_for_grn))
            cursor.execute(
                f"""
                SELECT purchase_order, purchase_order_item, tax_code, document_currency,
                       purchase_order_quantity_unit, tax_jurisdiction, gross_amount, effective_amount, po_balance_amount,
                       product_type_code, company_code, supplier
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
                WHERE purchase_order IN ({placeholders_po})
                ORDER BY purchase_order, purchase_order_item
                """,
                po_numbers_for_grn
            )
            for po, po_item, tax_code, document_currency, po_qty_unit, tax_jurisdiction, gross_amount, effective_amount, po_balance_amount, product_type_code, company_code, supplier in cursor.fetchall():
                # S4_Purchase_Order_Data.purchase_order_item is already
                # normalized (1, 2, 3, ...), unlike the raw SAP line-number
                # form (10, 20, 30, ...) S4_GRN_Data/SAP itself expects back.
                # Service POs (product_type_code=2) never get a goods
                # receipt, so there's no GRN row to source the raw form from -
                # reverse the normalization here (x10) so a service line
                # still gets a usable purchase_order_item in the response.
                try:
                    po_item_raw_form = str(int(str(po_item).strip()) * 10)
                except (TypeError, ValueError):
                    po_item_raw_form = None
                po_str = str(po)
                entry = {
                    "tax_code": tax_code,
                    "DocumentCurrency": document_currency,
                    "purchase_order_quantity_unit": po_qty_unit,
                    "tax_jurisdiction": tax_jurisdiction,
                    "product_type_code": product_type_code,
                    "CompanyCode": company_code,
                    "Supplier": supplier,
                    "purchase_order_item_raw_form": po_item_raw_form,
                    # po_balance_amount is NULL until something first consumes
                    # against it - fall back to the line's full amount (effective_amount
                    # takes priority over gross_amount, wherever it's populated) so
                    # this always reflects the current effective balance, not
                    # just "never touched yet".
                    "po_balance_amount": po_balance_amount if po_balance_amount is not None else _first_nonzero_amount(effective_amount, gross_amount),
                }
                po_data_by_po_and_line[(po_str, str(po_item))] = entry
                # First row per PO (lowest purchase_order_item, thanks to the
                # ORDER BY) - used below when an invoice line (e.g. an OCR
                # split of a single lump-sum service PO line into several
                # sub-lines) has no PO line matching its own LineItemNumber.
                po_data_by_po_latest.setdefault(po_str, entry)
        grn_by_po_and_line: Dict[Tuple[str, str], dict] = {}
        grn_by_po_latest: Dict[str, dict] = {}  # fallback if a line has no exact match
        if po_numbers_for_grn:
            placeholders = ", ".join("?" * len(po_numbers_for_grn))
            cursor.execute(
                f"""
                SELECT purchase_order, purchase_order_item, plant, fiscal_year,
                       material_document_item, material_document, creation_date_time,
                       material_base_unit
                FROM [final_pulse_DBprod].[dbo].[S4_GRN_Data]
                WHERE purchase_order IN ({placeholders})
                ORDER BY creation_date_time DESC
                """,
                po_numbers_for_grn
            )
            for po, po_item_raw, plant, fiscal_year, md_item, material_document, created, material_base_unit in cursor.fetchall():
                po_str = str(po)
                entry = {
                    "ReferenceDocumentFiscalYear": fiscal_year,
                    "ReferenceDocumentItem": md_item,
                    "ReferenceDocument": material_document,
                    "purchase_order_item": po_item_raw,
                    "PurchaseOrderQuantityUnit": material_base_unit,
                }
                grn_by_po_latest.setdefault(po_str, entry)  # first row = latest, thanks to ORDER BY

                normalized_line = _normalize_grn_po_line(po_item_raw)
                if normalized_line is not None:
                    grn_by_po_and_line.setdefault((po_str, normalized_line), entry)

        header_company_code = None
        header_document_currency = None
        header_supplier_id = None
        for item in items:
            po_no = item.get("PONumber")
            # The invoice's own LineItemNumber isn't always already in the
            # normalized 1/2/3 form - some source documents (e.g. this Ariba
            # test invoice) print raw SAP-style line numbers (10, 20, 30) that
            # OCR carries through as-is. Normalize it the same way GRN/PO-data
            # lines already are, so the join key actually lines up instead of
            # silently missing (and, for GRN, falling back to "latest for the
            # PO" instead of this item's real line).
            normalized_item_line = _normalize_grn_po_line(item.get("LineItemNumber"))

            grn_match = grn_by_po_and_line.get((po_no, normalized_item_line))
            if grn_match is None:
                grn_match = grn_by_po_latest.get(po_no)
            item["ReferenceDocument"] = safe_str(grn_match.get("ReferenceDocument")) if grn_match else None
            item["ReferenceDocumentItem"] = safe_str(grn_match.get("ReferenceDocumentItem")) if grn_match else None
            item["ReferenceDocumentFiscalYear"] = safe_str(grn_match.get("ReferenceDocumentFiscalYear")) if grn_match else None

            po_data_match = po_data_by_po_and_line.get((po_no, normalized_item_line))
            if po_data_match is None:
                po_data_match = po_data_by_po_latest.get(po_no)

            # No GRN row (typical for a service PO, product_type_code=2, which
            # never gets a goods receipt) - fall back to the PO line's own
            # purchase_order_item x10 (see purchase_order_item_raw_form above).
            if grn_match:
                item["purchase_order_item"] = safe_str(grn_match.get("purchase_order_item"))
            elif po_data_match:
                item["purchase_order_item"] = safe_str(po_data_match.get("purchase_order_item_raw_form"))
            else:
                item["purchase_order_item"] = None
            item["PurchaseOrderQuantityUnit"] = safe_str(grn_match.get("PurchaseOrderQuantityUnit")) if grn_match else None

            item["tax_code"] = safe_str(po_data_match.get("tax_code")) if po_data_match else None
            item["DocumentCurrency"] = safe_str(po_data_match.get("DocumentCurrency")) if po_data_match else None
            item["purchase_order_quantity_unit"] = safe_str(po_data_match.get("purchase_order_quantity_unit")) if po_data_match else None
            item["tax_jurisdiction"] = safe_str(po_data_match.get("tax_jurisdiction")) if po_data_match else None
            if not item["tax_jurisdiction"]:
                item["tax_jurisdiction"] = "NY00000000"  # New York default tax jurisdiction when SAP has none on file
            item["po_balance_amount"] = safe_str(po_data_match.get("po_balance_amount")) if po_data_match else None
            item["product_type_code"] = safe_str(po_data_match.get("product_type_code")) if po_data_match else None
            if header_document_currency is None and po_data_match and po_data_match.get("DocumentCurrency"):
                header_document_currency = po_data_match.get("DocumentCurrency")
            if header_company_code is None and po_data_match and po_data_match.get("CompanyCode"):
                header_company_code = po_data_match.get("CompanyCode")
            if header_supplier_id is None and po_data_match and po_data_match.get("Supplier"):
                header_supplier_id = po_data_match.get("Supplier")

        # supplier_id is normally sourced from the invoice's own PO (via
        # S4_Purchase_Order_Data.supplier above), but plenty of invoices
        # never resolve a PO at all (e.g. a retainer/service invoice with no
        # PO printed and no fallback match) - that shouldn't also mean no
        # supplier_id, since S4_Suppliers_prod can resolve one directly from
        # SupplierName regardless of whether a PO was ever found.
        if header_supplier_id is None:
            resolved_supplier = _resolve_supplier_id_and_name(cursor, first_invoice_row.get("SupplierName"))
            if resolved_supplier:
                header_supplier_id = resolved_supplier[0]

        # ==================================================
        # 12. Build final invoice response
        # ==================================================
        invoice = {

            "CompanyCode": safe_str(
                header_company_code
            ),

            "DocumentCurrency": safe_str(
                header_document_currency
            ),

            "supplier_id": safe_str(
                header_supplier_id
            ),

            "source_file": safe_str(
                first_invoice_row.get(
                    "source_file"
                )
            ),

            "Tax_invoice": safe_str(
                first_invoice_row.get(
                    "Tax_invoice"
                )
            ),

            "Stamp": safe_str(
                first_invoice_row.get(
                    "Stamp"
                )
            ),

            "SupplierName": safe_str(
                first_invoice_row.get(
                    "SupplierName"
                )
            ),

            "CustomerName": safe_str(
                first_invoice_row.get(
                    "CustomerName"
                )
            ),

            "InvoiceNumber": safe_str(
                final_invoice_number
            ),

            # Keep multiple PO numbers on each item row instead of a single
            # invoice-level header value.
            # InvoiceDate is available only in Newyork_Invoice_extracted_data
            "InvoiceDate": safe_str(
                first_invoice_row.get(
                    "InvoiceDate"
                )
            ),

            "SupplierTRN": safe_str(
                first_invoice_row.get(
                    "SupplierTRN"
                )
            ),

            "CustomerTRN": safe_str(
                first_invoice_row.get(
                    "CustomerTRN"
                )
            ),

            "Email": safe_str(
                first_invoice_row.get(
                    "Email"
                )
            ),

            "Invoice_Total": safe_str(
                first_invoice_row.get("Invoice_Total")
            ),

            "BankAccountName": safe_str(
                first_invoice_row.get(
                    "BankAccountName"
                )
            ),

            "BankName": safe_str(
                first_invoice_row.get(
                    "BankName"
                )
            ),

            "BankBranch": safe_str(
                first_invoice_row.get(
                    "BankBranch"
                )
            ),

            "BranchCode": safe_str(
                first_invoice_row.get(
                    "BranchCode"
                )
            ),

            "AccountNumber": safe_str(
                first_invoice_row.get(
                    "AccountNumber"
                )
            ),

            "IBAN": safe_str(
                first_invoice_row.get(
                    "IBAN"
                )
            ),

            "SwiftCode": safe_str(
                first_invoice_row.get(
                    "SwiftCode"
                )
            ),

            "Customer_address": safe_str(
                first_invoice_row.get(
                    "Customer_address"
                )
            ),

            "Supplier_address": safe_str(
                first_invoice_row.get(
                    "Supplier_address"
                )
            ),

            "Items": items
        }

        # ==================================================
        # 13. Return API response
        # ==================================================
        return {
            "results": [
                {
                    "filename": safe_str(
                        first_invoice_row.get(
                            "source_file"
                        )
                    ),

                    "pdf_id": pdf_id,

                    "status": "success",

                    "invoices": [
                        invoice
                    ]
                }
            ]
        }

    except HTTPException:

        raise

    except Exception as e:

        logging.error(
            f"Error fetching invoice "
            f"{pdf_id}: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Database error: "
                f"{str(e)}"
            )
        )

    finally:

        cursor.close()
        conn.close()

# --- 27.6 Validation / comparison -------------------------------------------
@router.post("/validate-supplier")
def validate_supplier(data: PDFRequest, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT CustomerName, CustomerTRN, SupplierName, SupplierTRN FROM final_pulse_DBprod.dbo.Newyork_Invoice_extracted_data WHERE pdf_id = ?", (data.pdf_id,))
    row = cursor.fetchone()
    if not row:
        return {"status": "failed", "comment": "Invalid pdf_id"}

    customer_name, customer_trn, supplier_name, supplier_trn = row
    customer_name_clean = clean_name(customer_name)
    supplier_name_clean = clean_name(supplier_name)

    cursor.execute("SELECT supplier_name, tax_number FROM final_pulse_DBprod.dbo.Suppliers_master")
    suppliers = cursor.fetchall()
    cursor.close()
    conn.close()

    customer_name_match = customer_trn_match = supplier_name_match = supplier_trn_match = False
    for s in suppliers:
        master_name_clean = clean_name(s.supplier_name)
        if customer_name_clean == master_name_clean:
            customer_name_match = True
            if customer_trn == s.tax_number:
                customer_trn_match = True
        if supplier_name_clean == master_name_clean:
            supplier_name_match = True
            if supplier_trn == s.tax_number:
                supplier_trn_match = True

    if not customer_name_match:
        return {"status": "failed", "comment": "Invalid Customer Name"}
    if not customer_trn_match:
        return {"status": "failed", "comment": "Customer TRN does not match"}
    if not supplier_name_match:
        return {"status": "failed", "comment": "Invalid Supplier Name"}
    if not supplier_trn_match:
        return {"status": "failed", "comment": "Supplier TRN does not match"}
    return {"status": "success", "comment": "Customer and Supplier validation successful"}

@router.get("/compare-invoice-ariba-receipt_id", summary="Compare Invoice vs Receipt")
async def compare_invoice_ariba_receipt(
    po_numbers: List[str] = Query(
        ...,
        description="List of 10-digit PO Numbers starting with '45'",
    ),
    pdf_id: str = Query(...),
    tolerance: float = Query(0.99),
    current_user: dict = Depends(get_current_user)
):
    """
    Compare invoice against receipt data for a list of PO numbers and pdf_id.
    This will also store the matched RC IDs in Invoice_Receipt_Mapping table
    if all checks pass, so RPA can run later.
    """
    conn = cursor = None
    po_number = po_numbers
 
    try:
        # Check S4_Invoice_Data for a pre-existing SAP invoice up front, but
        # don't short-circuit on it - run the normal GRN/PO comparison too
        # and combine both outcomes into a single response/DB write at the
        # end, so the caller always gets the full comparison alongside the
        # SAP-duplicate check.
        existing_supplier_invoice = find_existing_s4_invoice(pdf_id, po_number)
 
        # Run the main comparison function that handles RC storage
        all_matched, full_comments, matched_items_data = perform_comparison_and_check_match(pdf_id, po_number)
       
        # Fetch data for detailed response
        invoice_data = fetch_invoice_data(pdf_id)
        if not invoice_data:
            raise HTTPException(status_code=404, detail=f"No invoice data found for pdf_id '{pdf_id}'")
 
        if not invoice_data.get("invoice_po_number"):
            no_po_message = (
                f"Invoice '{pdf_id}' has no PO number recorded against it - it was extracted "
                "without a PO number and no matching PO could be resolved in SAP"
            )
            update_fileinput_status_by_pdf_id(pdf_id, "failed", no_po_message)
            raise HTTPException(status_code=404, detail=no_po_message)
 
        ariba_data = fetch_ariba_data(po_number, invoice_data.get("line_items", []))
 
        # Service POs (product_type_code=2) never get a goods receipt, so
        # they can't go through the receipt-based comparison below at all -
        # perform_comparison_and_check_match already matched on invoice
        # total vs. PO balance instead; just surface that result here.
        if po_lines_are_service(ariba_data.get("line_items", [])):
            if existing_supplier_invoice:
                # SAP-confirmed duplicate - keep the comment a clean, fixed
                # string, no PO-match percentage reasoning carried forward.
                final_status, final_comments = "processed", "Duplicate : Invoice Already Posted in SAP"
            else:
                final_status, final_comments = ("Success" if all_matched else "failed"), full_comments
                final_comments = _carry_forward_po_match_comment(pdf_id, final_comments)
            update_fileinput_status_by_pdf_id(pdf_id, final_status, final_comments)
            if existing_supplier_invoice:
                update_customer_invoice_id(pdf_id, existing_supplier_invoice)
 
            response_data = {
                "pdf_id": pdf_id,
                "po_numbers": po_numbers,
                "po_type": "service",
                "comparison_status": "success" if (existing_supplier_invoice or all_matched) else "partial_match",
                "summary": {
                    "total_mismatches": 0 if all_matched else 1,
                    "po_match": bool(ariba_data.get("line_items")),
                    "total_match": all_matched,
                },
                "comments_stored": final_comments,
                "service_po_allocation": matched_items_data,
                "ariba_reference_data": {
                    "ariba_po_number": ariba_data.get("ariba_po_number"),
                    "ariba_total": ariba_data.get("ariba_total"),
                    "ariba_line_items": ariba_data.get("line_items", []),
                },
                "invoice_data": {
                    "invoice_po_number": invoice_data.get("invoice_po_number"),
                    "invoice_total": invoice_data.get("invoice_total"),
                    "invoice_line_items": invoice_data.get("line_items", []),
                },
                "s4_invoice_check": {
                    "exists_in_sap": bool(existing_supplier_invoice),
                    "supplier_invoice": existing_supplier_invoice,
                },
            }
            if existing_supplier_invoice:
                response_data["CUSTOMER_INVOICE_ID"] = existing_supplier_invoice
                response_data["message"] = "Invoice already exists in SAP - marked processed. Service PO comparison included for reference."
            elif all_matched:
                response_data["message"] = "All checks passed! Service PO total matched - ready for RPA."
            else:
                response_data["mismatches"] = [line for line in full_comments.split('\n') if line.strip()]
                response_data["message"] = "Comparison failed. Please fix mismatches and try again."
            return response_data
 
        receipt_data = fetch_receipt_data(
            po_number, ariba_data,
            invoice_data.get("line_items", []),
            invoice_date=invoice_data.get("invoice_date_str")
        )
        if not receipt_data:
            no_receipt_message = (
                f"No goods-receipt (GRN) data found in SAP for PO number(s) {po_number} "
                f"on invoice '{pdf_id}'"
            )
            update_fileinput_status_by_pdf_id(pdf_id, "failed", no_receipt_message)
            raise HTTPException(status_code=404, detail=no_receipt_message)
 
        # PO comparison
        po_comparison = compare_po_numbers_invoice_receipt_only(
            invoice_data.get("invoice_po_number"), receipt_data.get("receipt_po_number"))
       
        # Use the line items comparison from perform_comparison_and_check_match
        # But we need to format it in the OLD format for frontend
        new_line_items_comparison = compare_line_items_invoice_receipt_only(
            invoice_data.get("line_items", []), receipt_data.get("line_items", [])
        )
       
        # Convert to OLD format for frontend (with line_item_vs_line_item, match_score, etc.)
        old_line_items_comparison = []
        for item in new_line_items_comparison:
            old_item = {
                "line_item_comparison_number": item.get("line_item_comparison_number"),
                "po_number": item.get("po_number"),
                "invoice_item": item.get("invoice_item", {}),
                "receipt_item": item.get("receipt_item"),
                "comparison": {}
            }
           
            # Build comparison in OLD format
            comp = old_item["comparison"]
            new_comp = item.get("comparison", {})
           
            # Determine if line item matched
            is_matched = new_comp.get("match_method") is not None
           
            comp["line_item_vs_line_item"] = "matched" if is_matched else "not_matched"
            comp["ItemDescription_vs_description"] = new_comp.get("ItemDescription_vs_description", "not_matched")
            comp["match_score"] = 100.0 if is_matched else 0.0
            comp["match_reason"] = new_comp.get("match_reason", "No match found")
            comp["final_description"] = new_comp.get("final_description", "")
            comp["final_unit"] = new_comp.get("final_unit", "")
            comp["original_invoice_unit"] = new_comp.get("original_invoice_unit", "")
            comp["original_receipt_unit"] = new_comp.get("original_receipt_unit", "")
            comp["Unit_vs_unit_of_measure"] = "matched" if new_comp.get("final_unit") else "not_matched"
            comp["QTY_vs_quantity"] = new_comp.get("QTY_vs_quantity", "not_matched")
            comp["error_comments"] = new_comp.get("error_comments")
           
            old_line_items_comparison.append(old_item)
       
        # Total comparison - use matched items whenever at least one invoice
        # line actually matched a receipt line, OR if the match came from
        # Method 3 (aggregate RC-total match). Method 3 doesn't do per-line
        # matching at all - it only compares the invoice total against the
        # summed RC total - so a "matched items" total built from per-line
        # matches would (wrongly) exclude receipt lines with no invoice-line
        # counterpart (e.g. a "Delivery Charges" PO line when the invoice
        # reports freight as a header field instead of its own line item).
        #
        # This must NOT require all_matched: a PO can have several lines
        # (e.g. 4 products) while a given invoice only bills for one of
        # them - comparing that invoice's total against the OTHER PO lines'
        # totals too (the "all items" fallback) is meaningless and produces
        # a total_comparison that contradicts what Comparision_of_lineItems
        # actually shows. Any remaining gap for the items that DID match
        # (e.g. a bottle-deposit surcharge SAP doesn't track) still shows up
        # correctly via the "matched items" comparison.
        used_method3 = any(
            (item or {}).get("match_method") == "method_3_rc_total"
            for item in (matched_items_data or [])
        )
 
        matched_receipt_items = [
            lc["receipt_item"]
            for lc in new_line_items_comparison
            if lc.get("receipt_item") and lc.get("comparison", {}).get("match_method")
        ]
 
        document_currency = fetch_document_currency(po_number)
        if matched_receipt_items and not used_method3:
            total_comparison = compare_totals_invoice_receipt_only(
                invoice_data.get("invoice_total"),
                receipt_data["receipt_total"],
                tolerance,
                matched_receipt_items,
                document_currency,
            )
        else:
            total_comparison = compare_totals_invoice_receipt_only(
                invoice_data.get("invoice_total"),
                receipt_data["receipt_total"],
                tolerance,
                None,  # Use all items
                document_currency,
            )
 
        # Store status + comments in Newyork_FileInput table - if the invoice
        # was already found in S4_Invoice_Data, that takes priority over the
        # GRN/PO comparison outcome (it's a confirmed SAP fact, not a
        # heuristic match), but the GRN/PO comparison still ran and is
        # included below in the response for reference.
        if existing_supplier_invoice:
            # SAP-confirmed duplicate - keep the comment a clean, fixed
            # string, no PO-match percentage reasoning carried forward.
            final_status, final_comments = "processed", "Duplicate : Invoice Already Posted in SAP"
        else:
            final_status, final_comments = ("Success" if all_matched else "failed"), full_comments
            final_comments = _carry_forward_po_match_comment(pdf_id, final_comments)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE Newyork_FileInput SET status = ?, comments = ? WHERE pdf_id = ?",
            (final_status, final_comments, pdf_id),
        )
        conn.commit()
        if existing_supplier_invoice:
            update_customer_invoice_id(pdf_id, existing_supplier_invoice)
 
        # ariba_data/receipt_data come back sorted by their own SQL queries
        # (purchase_order, purchase_order_item) rather than in
        # Comparision_of_lineItems' order. Reorder both to match
        # invoice_data's line_items sequence - the same sequence
        # Comparision_of_lineItems is built from - so all the line-item
        # lists in the response line up row-for-row.
        invoice_line_items_ordered = invoice_data.get("line_items", [])
        ariba_line_items_ordered = _reorder_line_items_like_invoice(
            ariba_data.get("line_items", []), invoice_line_items_ordered
        )
        receipt_line_items_ordered = _reorder_line_items_like_invoice(
            receipt_data.get("line_items", []), invoice_line_items_ordered
        )
 
        # ariba_total/receipt_total from fetch_ariba_data/fetch_receipt_data
        # sum EVERY line on the PO/GRN at its FULL received/ordered quantity,
        # but a PO line can span multiple invoices (a partial shipment) - so
        # even after filtering to only the lines this invoice bills, summing
        # their raw line_total would still count quantity this invoice
        # doesn't cover. Cap each line's contribution at the quantity THIS
        # invoice actually bills, same as total_comparison's "matched items"
        # figure, so all three totals agree.
        def _sum_invoice_matched_totals(items: List[dict], invoice_line_items: List[dict]) -> float:
            invoice_qty_by_key = {}
            for inv in invoice_line_items:
                key = (str(inv.get("po_number") or ""), str(inv.get("line_item") or inv.get("LineItemNumber") or ""))
                try:
                    invoice_qty_by_key[key] = float(str(inv.get("QTY") or 0).replace(",", ""))
                except (ValueError, TypeError):
                    continue
 
            total = 0.0
            for item in items:
                key = (str(item.get("po_number") or ""), str(item.get("line_item") or ""))
                try:
                    item_qty = float(item.get("quantity") or 0)
                    line_total = float(item.get("line_total") or 0)
                except (ValueError, TypeError):
                    continue
                inv_qty = invoice_qty_by_key.get(key)
                if inv_qty is not None and 0 < inv_qty < item_qty:
                    unit_price = float(item.get("unit_price") or 0) or (line_total / item_qty if item_qty else 0)
                    total += inv_qty * unit_price
                else:
                    total += line_total
            return round(total, 2)
 
        ariba_total_matched = _sum_invoice_matched_totals(ariba_line_items_ordered, invoice_line_items_ordered)
        receipt_total_matched = _sum_invoice_matched_totals(receipt_line_items_ordered, invoice_line_items_ordered)
 
        # Build response matching your frontend's expected format
        response_data = {
            "pdf_id": pdf_id,
            "po_numbers": po_numbers,
            "comparison_status": "success" if (existing_supplier_invoice or all_matched) else "partial_match",
            "summary": {
                "total_mismatches": 0 if all_matched else 1,
                "po_match": po_comparison.get("status") == "matched",
                "total_match": total_comparison.get("status") == "matched"
            },
            "comments_stored": final_comments,
            "po_number_comparison": po_comparison,
            "total_comparison": total_comparison,
            "Comparision_of_lineItems": old_line_items_comparison,
            "ariba_reference_data": {
                "ariba_po_number": ariba_data.get("ariba_po_number"),
                "ariba_total": ariba_total_matched,
                "ariba_line_items": ariba_line_items_ordered
            },
            "receipt_data": {
                "receipt_po_number": receipt_data.get("receipt_po_number"),
                "receipt_total": receipt_total_matched,
                "receipt_line_items": receipt_line_items_ordered
            },
            "invoice_data": {
                "invoice_po_number": invoice_data.get("invoice_po_number"),
                "invoice_total": invoice_data.get("invoice_total"),
                "invoice_line_items": invoice_line_items_ordered
            },
            "s4_invoice_check": {
                "exists_in_sap": bool(existing_supplier_invoice),
                "supplier_invoice": existing_supplier_invoice,
            },
        }
 
        # Add mismatches if any (EXACTLY like your first example)
        if not all_matched:
            mismatches = [line for line in full_comments.split('\n') if line.strip()]
            response_data["mismatches"] = mismatches
 
        if existing_supplier_invoice:
            response_data["CUSTOMER_INVOICE_ID"] = existing_supplier_invoice
            response_data["message"] = "Invoice already exists in SAP - marked processed. GRN/PO comparison included for reference."
        elif all_matched:
            response_data["message"] = "All checks passed! RC IDs stored. RPA can now be executed."
        else:
            response_data["message"] = "Comparison failed. RC IDs were NOT stored. Please fix mismatches and try again."
 
        return response_data
       
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[compare_invoice_receipt] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
 


@router.get("/fetch-ariba-data/{po_number}")
async def fetch_ariba_data_endpoint(
    po_number: str = Path(..., pattern=r'^45\d{8}$'),
    current_user: dict = Depends(get_current_user)
):
    try:
        result = fetch_ariba_data_for_po(po_number)
        return JSONResponse(content={"ariba_data": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching Ariba data: {str(e)}")



@router.post("/save-pre-rpa-data/")
async def save_pre_rpa_data(records: List[PreRPAData], current_user: dict = Depends(get_current_user)):
 
    def table_for_pdf_id(pdf_id: str) -> str:
        return "Newyork_FileInput"

    def pre_rpa_table_for_pdf_id(pdf_id: str) -> str:
        return "Newyork_PRE_RPA_DATA"
 
    # Group unique pdf_ids by their source table so we can batch-fetch
    unique_pdf_ids = {(r.pdf_id or "") for r in records if r.pdf_id}
    pdf_ids_by_table: Dict[str, List[str]] = {}
    for pdf_id in unique_pdf_ids:
        pdf_ids_by_table.setdefault(table_for_pdf_id(pdf_id), []).append(pdf_id)
 
    pdf_ids_by_pre_rpa_table: Dict[str, List[str]] = {}
    for pdf_id in unique_pdf_ids:
        pdf_ids_by_pre_rpa_table.setdefault(pre_rpa_table_for_pdf_id(pdf_id), []).append(pdf_id)
 
    base64_cache: Dict[str, str] = {}
    existing_records_cache: Dict[str, bool] = {}
 
    conn = get_db_connection()
    cursor = conn.cursor()
 
    try:
        # â”€â”€ Step 1: Batch-fetch base64 per source table (one query per table) â”€â”€
        for tbl, pdf_ids in pdf_ids_by_table.items():
            if not pdf_ids:
                continue
            placeholders = ",".join("?" * len(pdf_ids))
            try:
                cursor.execute(
                    f"SELECT pdf_id, file_base64 FROM {tbl} WHERE pdf_id IN ({placeholders})",
                    pdf_ids,
                )
                for pdf_id, file_base64 in cursor.fetchall():
                    base64_cache[pdf_id] = file_base64 or ""
            except Exception as e:
                logging.warning(f"Could not batch-fetch base64 from {tbl}: {e}")
                for pdf_id in pdf_ids:
                    base64_cache.setdefault(pdf_id, "")
 
        for pdf_id in unique_pdf_ids:
            base64_cache.setdefault(pdf_id, "")
            logging.info(f"ðŸ“¦ base64 for pdf_id={pdf_id} (length={len(base64_cache[pdf_id])})")
 
        # â”€â”€ Step 2: Batch existence check per PRE_RPA table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for tbl, pdf_ids in pdf_ids_by_pre_rpa_table.items():
            if not pdf_ids:
                continue
            placeholders = ",".join("?" * len(pdf_ids))
            try:
                cursor.execute(
                    f"SELECT DISTINCT pdf_id FROM {tbl} WHERE pdf_id IN ({placeholders})",
                    pdf_ids,
                )
                existing_ids = {row[0] for row in cursor.fetchall()}
                for pdf_id in pdf_ids:
                    existing_records_cache[pdf_id] = pdf_id in existing_ids
            except Exception as e:
                logging.warning(f"Could not batch-check existence in {tbl}: {e}")
                for pdf_id in pdf_ids:
                    existing_records_cache.setdefault(pdf_id, False)
 
        # â”€â”€ Step 3: Insert or update, all parameterized â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        rows_inserted = 0
        rows_updated = 0
 
        def none_if_blank(v):
            # esc() treated None/blank the same way; preserve that behavior
            # while still using bound parameters.
            if v is None or str(v).strip() == "":
                return None
            return v
 
        for record in records:
            pdf_id = record.pdf_id or ""
            table_name = pre_rpa_table_for_pdf_id(pdf_id) if pdf_id else "Newyork_PRE_RPA_DATA"
            file_base64 = base64_cache.get(pdf_id, "")
 
            if not file_base64:
                logging.warning(
                    f"âš ï¸ base64 is empty for pdf_id={pdf_id} â€” "
                    f"check that Newyork_FileInput has this record"
                )
 
            exists = existing_records_cache.get(pdf_id, False)
 
            if exists:
                # Only rewrite base_64_string if the existing DB value is empty â€”
                # avoids re-sending multi-MB text on every update.
                update_base64 = not bool((base64_cache.get(pdf_id) or "").strip())
 
                base_sql = """
                    UPDATE {tbl} SET
                        PO_Number = ?, sup_Invoice_Number = ?, INVOICE_DATE = ?,
                        [No] = ?, [Description] = ?, Qty_Invoice = ?, Unit = ?,
                        [Price(AED)] = ?, [Amount(AED)] = ?, [Discount(AED)] = ?,
                        Charges = ?, [Taxes(AED)] = ?, [Gross_Amount(AED)] = ?,
                        Accounting = ?, Details = ?, Tax_code = ?,
                        is_process = ?, processed_date = ?, user_id = ?
                        {base64_clause}
                    WHERE pdf_id = ?
                """
                params = [
                    none_if_blank(record.PO_Number), none_if_blank(record.sup_Invoice_Number),
                    none_if_blank(record.INVOICE_DATE), none_if_blank(record.No),
                    none_if_blank(record.Description), none_if_blank(record.Qty_Invoice),
                    none_if_blank(record.Unit), none_if_blank(record.Price_AED),
                    none_if_blank(record.Amount_AED), none_if_blank(record.Discount_AED),
                    none_if_blank(record.Charges), none_if_blank(record.Taxes_AED),
                    none_if_blank(record.Gross_Amount_AED), none_if_blank(record.Accounting),
                    none_if_blank(record.Details), none_if_blank(record.Tax_code),
                    record.is_process, record.processed_date, none_if_blank(record.user_id),
                ]
                if update_base64:
                    base_sql = base_sql.format(tbl=table_name, base64_clause=", base_64_string = ?")
                    params.append(file_base64)
                else:
                    base_sql = base_sql.format(tbl=table_name, base64_clause="")
                params.append(pdf_id)
 
                cursor.execute(base_sql, params)
                rows_updated += 1
                logging.info(f"ðŸ”„ Updated No={record.No} for pdf_id={pdf_id} in {table_name}")
            else:
                sql = f"""
                    INSERT INTO {table_name} (
                        PO_Number, sup_Invoice_Number, INVOICE_DATE, [No], [Description], Qty_Invoice, Unit,
                        [Price(AED)], [Amount(AED)], [Discount(AED)], Charges, [Taxes(AED)], [Gross_Amount(AED)],
                        Accounting, Details, Tax_code, is_process, processed_date, user_id, pdf_id, base_64_string
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
                params = [
                    none_if_blank(record.PO_Number), none_if_blank(record.sup_Invoice_Number),
                    none_if_blank(record.INVOICE_DATE), none_if_blank(record.No),
                    none_if_blank(record.Description), none_if_blank(record.Qty_Invoice),
                    none_if_blank(record.Unit), none_if_blank(record.Price_AED),
                    none_if_blank(record.Amount_AED), none_if_blank(record.Discount_AED),
                    none_if_blank(record.Charges), none_if_blank(record.Taxes_AED),
                    none_if_blank(record.Gross_Amount_AED), none_if_blank(record.Accounting),
                    none_if_blank(record.Details), none_if_blank(record.Tax_code),
                    record.is_process, record.processed_date, none_if_blank(record.user_id),
                    pdf_id, file_base64,
                ]
                cursor.execute(sql, params)
                rows_inserted += 1
                logging.info(f"âœ… Inserted No={record.No} for pdf_id={pdf_id} into {table_name}")
 
        conn.commit()
        return {
            "status": "success",
            "rows_inserted": rows_inserted,
            "rows_updated": rows_updated,
            "total_processed": rows_inserted + rows_updated,
            "base64_fetched": {k: f"{len(v)} chars" for k, v in base64_cache.items()},
        }
 
    except Exception as e:
        conn.rollback()
        logging.error(f"Error in save_pre_rpa_data: {e}")
        raise HTTPException(status_code=500, detail=str(e))
 
    finally:
        cursor.close()
        conn.close()

@router.get("/distinct-suppliers/")
async def get_distinct_suppliers(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all distinct suppliers from the Newyork_FileInput table, scoped to the
    authenticated caller (only suppliers from that user's own uploads) -
    except a "Lead" user, who has full access and sees every user's
    suppliers, matching /distinct-po-numbers/ and /distinct-company-codes/.

    Different invoices from the SAME supplier are sometimes stored under
    slightly different raw spellings (e.g. "WME Entertainment LLC" vs
    "WME Entertainment, LLC" - extracted before/without SupplierName
    canonicalization, or simply printed differently across documents). This
    collapses those punctuation/case variants together via clean_name() so
    each real supplier appears only once, picking the alphabetically first
    spelling as the representative name shown.
    """
    current_user_id = current_user.get("id")
    is_lead = current_user.get("role") == "Lead"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT DISTINCT SupplierName
            FROM [final_pulse_DBprod].[dbo].[Newyork_FileInput]
            WHERE SupplierName IS NOT NULL AND SupplierName != ''
        """
        params = []
        if not is_lead:
            query += " AND user_id = ?"
            params.append(str(current_user_id))
        query += " ORDER BY SupplierName"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        raw_names = [row[0] for row in rows if row[0]]
        best_by_cleaned: Dict[str, str] = {}
        for name in raw_names:
            key = clean_name(name)
            if not key:
                continue
            if key not in best_by_cleaned or name < best_by_cleaned[key]:
                best_by_cleaned[key] = name
        suppliers = sorted(best_by_cleaned.values())

        return JSONResponse({
            "status": "success",
            "count": len(suppliers),
            "suppliers": suppliers
        })
    except Exception as e:
        logging.error(f"Error fetching distinct suppliers: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.get("/distinct-po-numbers/")
async def get_distinct_po_numbers(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all distinct PO numbers directly from Newyork_Invoice_extracted_data
    (via that table's own user_id column), scoped to the authenticated
    caller (only PO numbers from that user's own uploads) - except a "Lead"
    user, who has full access and sees every user's PO numbers, matching
    /distinct-suppliers/ and /distinct-company-codes/.

    A handful of legacy rows store PONumber as a comma-joined string (e.g.
    "4500212663, 4500224399") for a line item that had no PO of its own and
    fell back to the header's joined list - those are split apart here so
    the result is a flat list of individual PO numbers rather than joined
    strings.
    """
    current_user_id = current_user.get("id")
    is_lead = current_user.get("role") == "Lead"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT DISTINCT PONumber
            FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
            WHERE PONumber IS NOT NULL AND PONumber != ''
        """
        params = []
        if not is_lead:
            query += " AND user_id = ?"
            params.append(str(current_user_id))
        query += " ORDER BY PONumber"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        po_numbers = set()
        for row in rows:
            raw_po = row[0]
            if not raw_po:
                continue
            for p in str(raw_po).split(","):
                p = p.strip()
                if p:
                    po_numbers.add(p)
        po_numbers = sorted(po_numbers)

        return JSONResponse({
            "status": "success",
            "count": len(po_numbers),
            "po_numbers": po_numbers
        })
    except Exception as e:
        logging.error(f"Error fetching distinct PO numbers: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.get("/invoice/{pdf_id}/po-numbers")
async def get_po_numbers_for_invoice(pdf_id: str):
    """
    Get the distinct PO numbers recorded against a single invoice (pdf_id).
    A New York invoice can span multiple PO groups (one PONumber per line item);
    this collapses them to the unique set. Also handles the legacy comma-joined
    PONumber string (e.g. "4500212663, 4500224399") the same way
    fetch_invoice_data() does, in case any such rows exist for this pdf_id.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT PONumber FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data] WHERE pdf_id = ?",
            (pdf_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"No invoice found for pdf_id '{pdf_id}'")

        po_numbers = []
        for r in rows:
            raw_po = r.PONumber
            if not raw_po:
                continue
            po_numbers.extend(p.strip() for p in str(raw_po).split(",") if p.strip())
        po_numbers = list(dict.fromkeys(po_numbers))

        return JSONResponse({
            "pdf_id": pdf_id,
            "count": len(po_numbers),
            "po_numbers": po_numbers,
        })
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching PO numbers for pdf_id={pdf_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.get("/distinct-company-codes/")
async def get_distinct_company_codes(
    source: Optional[str] = Query(None, description="Filter by source:'manual'"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all distinct company codes from S4_Purchase_Order_Data,
    scoped to the authenticated caller. S4_Purchase_Order_Data is shared ERP
    master data with no user_id column of its own, so it's scoped indirectly
    by joining through the PO numbers on this user's own uploaded invoices
    (Newyork_FileInput -> Newyork_Invoice_extracted_data.PONumber -> purchase_order),
    the same join already used in /customer_graph. A "Lead" user has full
    access and sees company codes across every user's uploads.
    """
    current_user_id = current_user.get("id")
    is_lead = current_user.get("role") == "Lead"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT DISTINCT pom.company_code
            FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] pom
            INNER JOIN [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data] inv
                ON inv.PONumber = pom.purchase_order
            INNER JOIN [final_pulse_DBprod].[dbo].[Newyork_FileInput] fi
                ON fi.pdf_id = inv.pdf_id
            WHERE pom.company_code IS NOT NULL AND pom.company_code != ''
        """
        params = []
        if not is_lead:
            query += " AND fi.user_id = ?"
            params.append(str(current_user_id))
        query += " ORDER BY pom.company_code"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        company_codes = [row[0] for row in rows if row[0]]
        
        return JSONResponse({
            "status": "success",
            "count": len(company_codes),
            "company_codes": company_codes
        })
    except Exception as e:
        logging.error(f"Error fetching distinct company codes: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
@router.post("/Ariba-comments/")
async def add_ariba_comment(request: AribaCommentRequest , current_user: dict = Depends(get_current_user)):
    pdf_id = request.pdf_id
    ariba_comments = request.Ariba_Comments

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Since we set Created_at to DEFAULT GETDATE() in SQL Server,
        # we only need to pass pdf_id and Ariba_comments here!
        insert_sql = """
            INSERT INTO [dbo].[OCR_Ariba_Comments] (pdf_id, Ariba_comments)
            VALUES (?, ?)
        """
        
        cursor.execute(insert_sql, (pdf_id, ariba_comments))
        conn.commit()  # Crucial to save the data to the database

        return JSONResponse({
            "status": "success",
            "message": f"Comment successfully saved for pdf_id: {pdf_id}"
        }, status_code=201)

    except Exception as e:
        logging.error(f"Error in add_ariba_comment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        
    finally:
        cursor.close()
        conn.close()

class InvoiceItemUpdate(BaseModel):
    LineItemNumber: str


    ItemCode: Optional[str] = None
    ItemDescription: Optional[str] = None
    QTY: Optional[str] = None
    Unit: Optional[str] = None
    UnitPrice: Optional[str] = None
    Amount: Optional[str] = None
    Discount: Optional[str] = None
    VAT: Optional[str] = None
    VATAmount: Optional[str] = None
    AmountInclVAT: Optional[str] = None
    PONumber: Optional[str] = None
    Po_Total: Optional[str] = None


# Invoice header/common fields

class InvoiceUpdateRequest(BaseModel):


    # Optional invoice fields
    user_id: Optional[str] = None
    source_file: Optional[str] = None
    invoice_id: Optional[str] = None
    InvoiceNumber: Optional[str] = None
    SupplierName: Optional[str] = None
    CustomerName: Optional[str] = None
    InvoiceDate: Optional[str] = None
    SupplierTRN: Optional[str] = None
    CustomerTRN: Optional[str] = None
    Email: Optional[str] = None
    Invoice_Total: Optional[str] = None
    is_trusted: Optional[Any] = None

    BankAccountName: Optional[str] = None
    BankName: Optional[str] = None
    BankBranch: Optional[str] = None
    AccountNumber: Optional[str] = None
    IBAN: Optional[str] = None
    SwiftCode: Optional[str] = None
    BranchCode: Optional[str] = None

    Tax_invoice: Optional[Any] = None
    Stamp: Optional[Any] = None

    Customer_address: Optional[str] = None
    Supplier_address: Optional[str] = None

    # Line items are optional
    items: Optional[List[InvoiceItemUpdate]] = None

HEADER_COLUMNS_FOR_UPDATE = [
    "user_id", "source_file", "invoice_id", "InvoiceNumber", "SupplierName",
    "CustomerName", "InvoiceDate", "SupplierTRN", "CustomerTRN", "Email",
    "Invoice_Total", "is_trusted", "BankAccountName", "BankName", "BankBranch",
    "AccountNumber", "IBAN", "SwiftCode", "BranchCode", "Tax_invoice", "Stamp",
    "Customer_address", "Supplier_address",
]


@router.put("/update-invoice/{pdf_id}")
def update_invoice(
    pdf_id: str,
    data: InvoiceUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Header fields are a partial update as before: only fields present in the
    request body are changed, everything else keeps its current value.

    `items` is a FULL REPLACE, not a per-line patch: when the request
    includes `items` (even an empty list), it becomes the complete new set
    of line items for this invoice - every existing line item not present
    in `items` is deleted. Send the full list of line items you want to
    keep, not just the ones you changed. Omitting `items` entirely (leaving
    it null) leaves the existing line items untouched, e.g. for a
    header-only update.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # --------------------------------------------------
        # 1. Fetch the current header (from any one existing row) - both to
        # confirm the pdf_id exists, and as the fallback for header fields
        # this request doesn't provide when line items get replaced below
        # (a full replace re-inserts every row, so there's no existing row
        # left to leave a field un-set on).
        # --------------------------------------------------
        select_cols = ", ".join(f"[{c}]" for c in HEADER_COLUMNS_FOR_UPDATE)
        cursor.execute(
            f"""
            SELECT TOP 1 {select_cols}
            FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
            WHERE pdf_id = ?
            ORDER BY id ASC
            """,
            (pdf_id,)
        )
        existing_row = cursor.fetchone()

        if existing_row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Invoice with pdf_id '{pdf_id}' was not found"
            )

        existing_header = dict(zip(HEADER_COLUMNS_FOR_UPDATE, existing_row))

        # --------------------------------------------------
        # 2. Header/common fields - partial update as before.
        # --------------------------------------------------
        incoming_header = {
            "user_id": data.user_id,
            "source_file": data.source_file,
            "invoice_id": data.invoice_id,
            "InvoiceNumber": data.InvoiceNumber,
            "SupplierName": data.SupplierName,
            "CustomerName": data.CustomerName,
            "InvoiceDate": data.InvoiceDate,
            "SupplierTRN": data.SupplierTRN,
            "CustomerTRN": data.CustomerTRN,
            "Email": data.Email,
            "Invoice_Total": data.Invoice_Total,
            "is_trusted": data.is_trusted,
            "BankAccountName": data.BankAccountName,
            "BankName": data.BankName,
            "BankBranch": data.BankBranch,
            "AccountNumber": data.AccountNumber,
            "IBAN": data.IBAN,
            "SwiftCode": data.SwiftCode,
            "BranchCode": data.BranchCode,
            "Tax_invoice": data.Tax_invoice,
            "Stamp": data.Stamp,
            "Customer_address": data.Customer_address,
            "Supplier_address": data.Supplier_address
        }

        # Keep only fields that were provided
        fields_to_update = {
            column: value
            for column, value in incoming_header.items()
            if value is not None
        }
        updated_header_fields = dict(fields_to_update)

        # The merged header (existing value, overridden by anything this
        # request provided) is what full-replace line-item inserts carry -
        # computed regardless of whether items were sent, so it's ready
        # either way.
        merged_header = {
            col: (incoming_header[col] if incoming_header.get(col) is not None else existing_header.get(col))
            for col in HEADER_COLUMNS_FOR_UPDATE
        }

        if fields_to_update and data.items is None:
            # Only run the in-place partial UPDATE when line items are NOT
            # being replaced below - a full replace re-inserts every row
            # with merged_header already, so updating-then-deleting the same
            # rows here would just be wasted work.
            set_clause = ", ".join(
                f"[{column}] = ?"
                for column in fields_to_update.keys()
            )
            values = list(fields_to_update.values())
            values.append(pdf_id)

            update_header_sql = f"""
                UPDATE [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
                SET {set_clause},
                [created_at] = GETDATE()
                WHERE pdf_id = ?
            """
            cursor.execute(update_header_sql, values)

        # --------------------------------------------------
        # 3. Line items - full replace. `items` omitted (None) leaves
        # existing line items untouched; `items` present (including an
        # empty list) replaces the complete set.
        # --------------------------------------------------
        removed_line_item_count = None
        replaced_line_items = None

        if data.items is not None:
            cursor.execute(
                "SELECT COUNT(*) FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data] WHERE pdf_id = ?",
                (pdf_id,)
            )
            removed_line_item_count = cursor.fetchone()[0]

            cursor.execute(
                "DELETE FROM [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data] WHERE pdf_id = ?",
                (pdf_id,)
            )

            item_columns = HEADER_COLUMNS_FOR_UPDATE + [
                "pdf_id", "LineItemNumber", "ItemCode", "ItemDescription", "QTY", "Unit",
                "UnitPrice", "Amount", "Discount", "VAT", "VATAmount", "AmountInclVAT",
                "PONumber", "Po_Total",
            ]
            cols_sql = ", ".join(f"[{c}]" for c in item_columns)
            placeholders_sql = ", ".join("?" for _ in item_columns)
            insert_sql = f"""
                INSERT INTO [final_pulse_DBprod].[dbo].[Newyork_Invoice_extracted_data]
                    ({cols_sql}, [created_at])
                VALUES ({placeholders_sql}, GETDATE())
            """

            replaced_line_items = []
            for item in data.items:
                row_values = list(merged_header.values()) + [
                    pdf_id, item.LineItemNumber, item.ItemCode, item.ItemDescription,
                    item.QTY, item.Unit, item.UnitPrice, item.Amount, item.Discount,
                    item.VAT, item.VATAmount, item.AmountInclVAT, item.PONumber, item.Po_Total,
                ]
                cursor.execute(insert_sql, row_values)
                replaced_line_items.append({
                    "LineItemNumber": str(item.LineItemNumber),
                    "PONumber": item.PONumber,
                })

        # --------------------------------------------------
        # 4. Commit all updates
        # --------------------------------------------------
        conn.commit()

        response = {
            "status": "success",
            "message": "Invoice data updated successfully",
            "pdf_id": pdf_id,
            "updated_header_fields": updated_header_fields,
        }
        if data.items is not None:
            response["message"] = (
                f"Invoice header updated and line items fully replaced "
                f"({removed_line_item_count} removed, {len(replaced_line_items)} inserted)"
            )
            response["removed_line_item_count"] = removed_line_item_count
            response["line_items"] = replaced_line_items

        return response

    except HTTPException:

        conn.rollback()
        raise

    except Exception as e:

        conn.rollback()

        logging.error(
            f"Error updating invoice {pdf_id}: {str(e)}"
        )

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {str(e)}"
        )

    finally:

        cursor.close()
        conn.close()


class InvoiceStatusUpdateRequest(BaseModel):
    status: Optional[str] = None
    comments: Optional[str] = None
    customer_invoice_id: Optional[str] = None


@router.put("/update-invoice-status/{pdf_id}")
def update_invoice_status(
    pdf_id: str,
    data: InvoiceStatusUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Report back the outcome of downstream processing (e.g. after RPA submits
    the invoice to SAP via /s4-data-apis/invoice_create_in_s4) - pdf_id is
    the only identifier you need, matching Newyork_FileInput.pdf_id. Send any
    combination of status/comments/customer_invoice_id; only the fields you
    provide are updated, everything else is left as-is.

    status values already used elsewhere in this pipeline: 'processed' (RPA/
    SAP succeeded), 'failed' (RPA or SAP rejected it), 'pending', 'rpa_pending'.
    customer_invoice_id maps to Newyork_FileInput.CUSTOMER_INVOICE_ID - the
    SAP-side invoice document number once one exists.
    """
    fields_to_update = {
        column: value
        for column, value in {
            "status": data.status,
            "comments": data.comments,
            "CUSTOMER_INVOICE_ID": data.customer_invoice_id,
        }.items()
        if value is not None
    }
    if not fields_to_update:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of status, comments, customer_invoice_id",
        )

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM [final_pulse_DBprod].[dbo].[Newyork_FileInput] WHERE pdf_id = ?",
            (pdf_id,)
        )
        if cursor.fetchone()[0] == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No Newyork_FileInput record found for pdf_id '{pdf_id}'"
            )

        set_clause = ", ".join(f"[{column}] = ?" for column in fields_to_update.keys())
        values = list(fields_to_update.values())
        values.append(pdf_id)

        cursor.execute(
            f"UPDATE [final_pulse_DBprod].[dbo].[Newyork_FileInput] SET {set_clause} WHERE pdf_id = ?",
            values
        )
        conn.commit()

        return {
            "status": "success",
            "pdf_id": pdf_id,
            "updated_fields": fields_to_update,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logging.error(f"Error updating invoice status for pdf_id={pdf_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        cursor.close()
        conn.close()


class ReceiptItemDetail(BaseModel):
    line_item: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_of_measure: Optional[str] = None
    amount_accepted: Optional[float] = None
    gross_amount_accepted: Optional[float] = None

class ReceiptGroupResponse(BaseModel):
    receipt_id: str
    total_line_items: int
    total_gross_amount: float
    items: List[ReceiptItemDetail]

class POReceiptsSummaryResponse(BaseModel):
    po_number: str
    total_rc_count: int
    receipts: List[ReceiptGroupResponse]

@router.get("/po-receipts-summary/{po_number}", response_model=POReceiptsSummaryResponse)
async def get_po_receipts_summary(
    po_number: str = Path(..., description="10-digit PO Number starting with '45'", pattern=r"^45\d{8}$"),
    current_user: dict = Depends(get_current_user)
):
    """
    Groups all Ariba receipts matching a PO number into their respective Receipt IDs (RCs) 
    with their nested line items, descriptions, and quantities. Ignores negative reversal adjustments.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. FIXED: Pointing to your working server tables inside final_pulse_DBprod and dboDB
        # 2. FIXED: Filtered out negative adjustments via 'r.NumberAccepted >= 0'
        sql = """
            SELECT 
                r.ERPPOLineNumber,
                r.NumberAccepted,
                r.AmountAccepted,
                r.GrossAmountAccepted,
                r.UnitOfMeasure,
                r.LastModified,
                r.ReceiptUniqueName,
                r.LineNumberInCollection,
                r.ReceiptId,
                r.ReceiptLineDate,
                r.AmountPreviouslyAccepted,
                p_coll.description AS PO_Description,
                p_coll.unit_of_measure AS PO_UOM,
                p_coll.quantity AS PO_Quantity,
                p_coll.unit_price AS PO_UnitPrice
            FROM [final_pulse_DBprod].[dbo].[AribaReceipts] r
            LEFT JOIN [final_pulse_DBprod].[dbo].[purchase_order_master] p_coll
                ON r.ERPPONumber = p_coll.purchase_order_id
                AND r.LineNumberInCollection = p_coll.line_item
            WHERE r.ERPPONumber = ?
              AND r.NumberAccepted >= 0
            ORDER BY r.ReceiptId, r.LineNumberInCollection ASC, r.LastModified ASC
        """
        
        cursor.execute(sql, (po_number,))
        rows = cursor.fetchall()
        
        if not rows:
            return {
                "po_number": po_number,
                "total_rc_count": 0,
                "receipts": []
            }
            
        columns = [c[0] for c in cursor.description]
        
        # Dictionary to group rows by ReceiptId
        receipts_group: Dict[str, List[Dict[str, Any]]] = {}
        
        for row in rows:
            row_dict = dict(zip(columns, row))
            rc_id = str(row_dict.get("ReceiptId") or "UNKNOWN_RC")
            
            item_detail = {
                "line_item": str(row_dict.get("LineNumberInCollection") or row_dict.get("ERPPOLineNumber") or ""),
                "description": row_dict.get("PO_Description") or "",
                "quantity": float(row_dict.get("NumberAccepted") or 0.0),
                "unit_of_measure": row_dict.get("UnitOfMeasure") or row_dict.get("PO_UOM"),
                "amount_accepted": float(row_dict.get("AmountAccepted") or 0.0),
                "gross_amount_accepted": float(row_dict.get("GrossAmountAccepted") or 0.0)
            }
            
            receipts_group.setdefault(rc_id, []).append(item_detail)
            
        # Format dictionary groups into final structured list schema
        formatted_receipts = []
        for rc_id, items in receipts_group.items():
            total_gross = sum(item["gross_amount_accepted"] for item in items)
            formatted_receipts.append({
                "receipt_id": rc_id,
                "total_line_items": len(items),
                "total_gross_amount": round(total_gross, 2),
                "items": items
            })
            
        return {
            "po_number": po_number,
            "total_rc_count": len(formatted_receipts),
            "receipts": formatted_receipts
        }
        
    except Exception as e:
        logging.error(f"Error fetching PO Receipts summary for {po_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        
    finally:
        cursor.close()
        conn.close()           

class SupplierNameRequest(BaseModel):
    supplier_name: str
    invoice_total: Optional[float] = None
 
 
 
@router.post("/get_all_Po_numbers_for_supplier")
async def get_all_Po_numbers_for_supplier(
    request: SupplierNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Get every PO for a given supplier - `supplier_name` accepts either the
    supplier's NAME (fuzzy-matched against S4_Suppliers_prod, same algorithm
    used throughout this file) or a raw numeric supplier_id (e.g.
    '1021220', matching S4_Purchase_Order_Data.supplier directly) - along
    with each PO's own total (sum of its line items' effective/gross/net
    amount, same priority as everywhere else in this file) and
    purchase_order_date.

    Only OPEN POs are returned: a PO is excluded entirely when EVERY one of
    its lines has IsFinallyInvoiced = True (fully invoiced/closed) - a PO
    with even one line still False/NULL stays in the list. Same rule the
    extraction-time PO-fallback itself applies, so this never offers a PO
    that fallback would have refused to match against anyway.

    When `invoice_total` is given, every returned PO also gets a
    `"percentage"` field - a signed string showing how far that PO's own
    total is from `invoice_total`, e.g. "+58.8%" if the PO total is that
    much MORE than the invoice, "-25.0%" if that much LESS, or "0.0%" for
    an exact match - and the list is sorted by closeness (smallest
    difference first) instead of by PO number. Omitted entirely when
    `invoice_total` isn't given.

    The numeric-ID path exists because S4_Purchase_Order_Data.supplier is
    itself just that numeric id, not a name - some suppliers have PO rows
    under an id that hasn't been synced into S4_Suppliers_prod yet (or
    whose name doesn't fuzzy-match anything there), so passing the id
    directly still finds their POs even when name resolution would return
    nothing.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        input_value = (request.supplier_name or "").strip()
        supplier_id = None
        matched_name = None

        if input_value.isdigit():
            cursor.execute(
                "SELECT TOP 1 supplier_name FROM [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] WHERE supplier_id = ?",
                (input_value,),
            )
            row = cursor.fetchone()
            supplier_id = input_value
            matched_name = row[0] if row else None

        if supplier_id is None:
            resolved = _resolve_supplier_id_and_name(cursor, request.supplier_name)
            if resolved:
                supplier_id, matched_name = resolved

        if supplier_id is None:
            return JSONResponse({
                "status": "not_found",
                "supplier_name": request.supplier_name,
                "supplier_id": None,
                "supplier_name_matched": None,
                "ponumbers": [],
                "purchase_orders": [],
            })

        cursor.execute(
            """
            SELECT [purchase_order], [purchase_order_date],
                   [effective_amount], [gross_amount], [net_amount], [is_finally_invoiced]
            FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data]
            WHERE [supplier] = ? AND [purchase_order] IS NOT NULL
            """,
            (supplier_id,),
        )
        rows = cursor.fetchall()

        # Open-PO filter: a PO is only "closed" (fully invoiced) when EVERY
        # one of its lines has IsFinallyInvoiced = True - a PO with even one
        # line still False/NULL stays open. Same rule find_po_for_group_by_
        # description_and_price / find_po_by_supplier_and_total already
        # apply during extraction, so this listing only ever offers a PO
        # that could actually still be matched against.
        po_all_invoiced: Dict[str, bool] = {}
        for row in rows:
            po_id = str(row[0])
            po_all_invoiced[po_id] = po_all_invoiced.get(po_id, True) and bool(row[5])
        closed_pos = {po for po, all_invoiced in po_all_invoiced.items() if all_invoiced}
        if closed_pos:
            rows = [row for row in rows if str(row[0]) not in closed_pos]

        po_totals: Dict[str, float] = {}
        po_dates: Dict[str, Any] = {}
        for po, po_date, effective_amount, gross_amount, net_amount, _is_finally_invoiced in rows:
            po_key = str(po)
            raw_amount = _first_nonzero_amount(effective_amount, gross_amount, net_amount)
            try:
                amount_val = float(str(raw_amount).replace(",", "")) if raw_amount not in (None, "") else 0.0
            except (TypeError, ValueError):
                amount_val = 0.0
            po_totals[po_key] = po_totals.get(po_key, 0.0) + amount_val
            parsed_date = _parse_sap_date(po_date)
            if parsed_date is not None and (po_key not in po_dates or parsed_date < po_dates[po_key]):
                po_dates[po_key] = parsed_date

        target_val = request.invoice_total

        def _pct_field(po_total: float) -> dict:
            if target_val is None:
                return {}
            diff = po_total - target_val
            abs_diff = abs(diff)
            pct = (abs_diff / abs(target_val) * 100) if target_val else 0.0
            sign = "" if abs_diff <= 0.01 else ("+" if diff > 0 else "-")
            return {"percentage": f"{sign}{pct:.1f}%"}

        po_items = list(po_totals.items())
        if target_val is not None:
            # Closest-to-invoice-total first, same ordering the PO-fallback
            # itself ranks candidates by (find_po_by_supplier_and_total).
            po_items.sort(key=lambda kv: abs(round(kv[1], 2) - round(target_val, 2)))
        else:
            po_items.sort(key=lambda kv: kv[0])

        purchase_orders = [
            {
                "purchase_order": po,
                "po_total": round(total, 2),
                "purchase_order_date": po_dates[po].isoformat() if po in po_dates else None,
                **_pct_field(total),
            }
            for po, total in po_items
        ]

        return JSONResponse({
            "status": "success",
            "supplier_name": request.supplier_name,
            "supplier_id": supplier_id,
            "supplier_name_matched": matched_name,
            "ponumbers": [po["purchase_order"] for po in purchase_orders],
            "purchase_orders": purchase_orders,
        })
    except Exception as e:
        logging.error(f"Error fetching PO numbers for supplier_name={request.supplier_name!r}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/resolve-supplier-id")
async def resolve_supplier_id(
    request: SupplierNameRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Resolve a supplier_name that isn't necessarily an exact match to the
    single best-matching supplier_id in S4_Suppliers_prod - the same
    algorithm the automatic PO-fallback / SAP-duplicate-check pipeline uses
    (see _resolve_supplier_id_and_name): LIKE-prefilter on the cleaned
    name's first word, then, if more than one candidate survives that,
    score each (exact match > one name a leading-word prefix of the other >
    plain string-similarity ratio) and return only the single highest-
    scoring one.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        resolved = _resolve_supplier_id_and_name(cursor, request.supplier_name)
    finally:
        cursor.close()
        conn.close()

    if not resolved:
        return JSONResponse({
            "status": "not_found",
            "supplier_name_input": request.supplier_name,
            "supplier_id": None,
            "supplier_name_matched": None,
        })

    supplier_id, matched_name = resolved
    return JSONResponse({
        "status": "success",
        "supplier_name_input": request.supplier_name,
        "supplier_id": supplier_id,
        "supplier_name_matched": matched_name,
    })


@router.post("/customer_graph")
async def customer_graph(request: CustomerGraphRequest , current_user: dict = Depends(get_current_user)):
    # Scope every result to the authenticated caller's Newyork_Person.Id - unless
    # they're a "Lead", who has full access and sees every user's records.
    current_user_id = current_user.get("id")
    is_lead = current_user.get("role") == "Lead"

    page = request.page
    page_size = request.page_size
    field_filter = request.Field.lower() if request.Field else None

    po_number = request.po_number
    supplier = request.supplier
    company_code = request.company_code
    status_filter = request.status
    product_type_filter = request.product_type_code
    inv_start = request.invoice_start_date
    inv_end = request.invoice_end_date
    proc_start = request.process_start_date
    proc_end = request.process_end_date

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        offset = (page - 1) * page_size
        unified_query = f"""
                SELECT
                    id,
                    user_id,
                    filename,
                    pdf_id,
                    SupplierName,
                    CUSTOMER_INVOICE_ID,
                    status,
                    created_at,
                    comments,
                    folder_name,
                    brand,
                    'manual' AS source
                FROM [final_pulse_DBprod].[dbo].[Newyork_FileInput]
            """
        where_params = []
        if not is_lead:
            unified_query += " WHERE user_id = ?"
            where_params.append(str(current_user_id))

        where_conditions = []


        if po_number:
            where_conditions.append("(base_records.PONumber LIKE ? OR base_records.ERP_PO_Number LIKE ?)")
            where_params.extend([f"%{po_number}%", f"%{po_number}%"])

        if supplier:
            supplier_variants = _resolve_supplier_name_variants(cursor, supplier)
            variant_conditions = []
            for variant in supplier_variants:
                variant_conditions.append("(base_records.SupplierName LIKE ? OR base_records.PR_Supplier LIKE ?)")
                where_params.extend([f"%{variant}%", f"%{variant}%"])
            where_conditions.append("(" + " OR ".join(variant_conditions) + ")")

        if company_code:
            where_conditions.append("base_records.Company_Code LIKE ?")
            where_params.append(f"%{company_code}%")

        if status_filter:
            status_filter = status_filter.strip()
            if status_filter == "OCR-Success":
                where_conditions.append("(TRIM(base_records.status) = 'Success' OR TRIM(base_records.status) = 'success')")
            else:
                where_conditions.append("TRIM(base_records.status) = ?")
                where_params.append(status_filter)

        if product_type_filter:
            product_type_filter = product_type_filter.strip()
            ptf_lower = product_type_filter.lower()
            if ptf_lower == "service":
                where_conditions.append("TRIM(base_records.Product_Type_Code) = '2'")
            elif ptf_lower == "material":
                where_conditions.append("(base_records.Product_Type_Code IS NOT NULL AND TRIM(base_records.Product_Type_Code) <> '2')")
            else:
                where_conditions.append("TRIM(base_records.Product_Type_Code) = ?")
                where_params.append(product_type_filter)

        def clean_input_date(date_str: Optional[str]) -> Optional[str]:
            """Convert user input to YYYY-MM-DD for SQL comparison."""
            if not date_str or str(date_str).strip() == "":
                return None
            date_str = str(date_str).strip()
            
            # Try standard ISO format first
            try:
                return datetime.fromisoformat(date_str.replace("Z", "")).strftime("%Y-%m-%d")
            except ValueError:
                pass
                
            # Try YYYY-MM-DD
            try:
                if " " in date_str:
                    return datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d").strftime("%Y-%m-%d")
                return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                pass
                
            # Try DD-MM-YYYY (your database format)
            try:
                if " " in date_str:
                    return datetime.strptime(date_str.split(" ")[0], "%d-%m-%Y").strftime("%Y-%m-%d")
                return datetime.strptime(date_str, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
                
            # Try DD/MM/YYYY
            try:
                if " " in date_str:
                    return datetime.strptime(date_str.split(" ")[0], "%d/%m/%Y").strftime("%Y-%m-%d")
                return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
                
            return date_str[:10] if len(date_str) >= 10 else date_str

        clean_inv_start = clean_input_date(inv_start)
        clean_inv_end = clean_input_date(inv_end)
        clean_proc_start = clean_input_date(proc_start)
        clean_proc_end = clean_input_date(proc_end)

        # For InvoiceDate filtering - handle DD-MM-YYYY format (format 105)
        if clean_inv_start:
            where_conditions.append("""
                TRY_CONVERT(DATE, base_records.InvoiceDate, 105) >= TRY_CONVERT(DATE, ?, 120)
            """)
            where_params.append(clean_inv_start)
            
        if clean_inv_end:
            where_conditions.append("""
                TRY_CONVERT(DATE, base_records.InvoiceDate, 105) <= TRY_CONVERT(DATE, ?, 120)
            """)
            where_params.append(clean_inv_end)

        # For created_at filtering (process date)
        if clean_proc_start:
            where_conditions.append("(CAST(base_records.created_at AS DATE) >= TRY_CONVERT(DATE, ?, 120))")
            where_params.append(clean_proc_start)
            
        if clean_proc_end:
            where_conditions.append("(CAST(base_records.created_at AS DATE) <= TRY_CONVERT(DATE, ?, 120))")
            where_params.append(clean_proc_end)

        where_clause = "WHERE 1=1"
        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)

        # Main query - ORDER BY created_at DESC to show latest processed first
        sql = f"""
        SELECT * FROM (
            SELECT
                fi.id, fi.user_id, fi.filename, fi.pdf_id, fi.SupplierName, fi.CUSTOMER_INVOICE_ID,
                fi.status, fi.created_at, fi.source, fi.comments,fi.folder_name,fi.brand,
                inv.PONumber, inv.Invoice_Total AS Total, inv.InvoiceNumber, inv.InvoiceDate,
                pr.Supplier as PR_Supplier, pr.Company_Code, pr.ERP_PO_Number, pr.Product_Type_Code,
                pr.DocumentCurrency,
                ac.Ariba_comments,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, Invoice_Total, InvoiceNumber, InvoiceDate, created_at,
                    ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code,
                    MAX(p.product_type_code) AS Product_Type_Code, MAX(p.document_currency) AS DocumentCurrency
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
            LEFT JOIN (
                SELECT pdf_id, Ariba_comments,
                    ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY Created_at DESC) AS rn_ac
                FROM OCR_Ariba_Comments
            ) ac ON fi.pdf_id = ac.pdf_id AND ac.rn_ac = 1
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        ORDER BY 
            base_records.created_at DESC
        OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY
        """

        cursor.execute(sql, where_params)
        cols = [c[0] for c in cursor.description]
        records = []
        
        for row in cursor.fetchall():
            row_dict = dict(zip(cols, row))
            
            # Parse InvoiceDate - handle DD-MM-YYYY format
            invoice_date_val = row_dict.get("InvoiceDate")
            if invoice_date_val:
                # If it's already a datetime object, format it
                if isinstance(invoice_date_val, datetime):
                    invoice_date_str = invoice_date_val.strftime("%d-%m-%Y")
                else:
                    # It's a string, keep as-is (already DD-MM-YYYY)
                    invoice_date_str = str(invoice_date_val)
            else:
                invoice_date_str = None
            
            # Parse created_at
            created_at_val = row_dict.get("created_at")
            if isinstance(created_at_val, datetime):
                created_at_str = created_at_val.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            else:
                created_at_str = str(created_at_val) if created_at_val else None
            
            raw_status = row_dict.get("status")
            clean_status = raw_status.strip() if raw_status and isinstance(raw_status, str) else raw_status

            record = {
                "id": row_dict.get("id"),
                "user_id": row_dict.get("user_id"),
                "filename": row_dict.get("filename"),
                "pdf_id": row_dict.get("pdf_id"),
                "SupplierName": row_dict.get("SupplierName"),
                "CUSTOMER_INVOICE_ID": row_dict.get("CUSTOMER_INVOICE_ID"),
                "status": clean_status,
                "invoice_date": invoice_date_str,
                "processed_date": created_at_str,
                "source": row_dict.get("source"),
                "comments": row_dict.get("comments"),
                "folder_name": row_dict.get("folder_name"),
                "brand": row_dict.get("brand"),
                "PONumber": row_dict.get("PONumber"),
                "Total": row_dict.get("Total"),
                "InvoiceNumber": row_dict.get("InvoiceNumber"),
                "PR_Supplier": row_dict.get("PR_Supplier"),
                "Company_Code": row_dict.get("Company_Code"),
                "DocumentCurrency": row_dict.get("DocumentCurrency"),
                "Ariba_comments": row_dict.get("Ariba_comments"),
                "product_type_code": (
                    "Service" if str(row_dict.get("Product_Type_Code")).strip() == "2"
                    else "Material" if row_dict.get("Product_Type_Code") is not None
                    else None
                ),
            }
            records.append(record)
           

        # Count query
        count_sql = f"""
        SELECT COUNT(*), TRIM(base_records.status) FROM (
            SELECT 
                fi.status, fi.pdf_id, fi.created_at, fi.user_id,
                inv.InvoiceDate,
                pr.Company_Code, pr.Supplier as PR_Supplier, pr.Product_Type_Code,
                inv.PONumber, pr.ERP_PO_Number, fi.SupplierName,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, InvoiceDate,
                       ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code,
                    MAX(p.product_type_code) AS Product_Type_Code
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        GROUP BY TRIM(base_records.status)
        """
        
        cursor.execute(count_sql, where_params)
        status_counts = {}
        total_count = 0
        
        for count_val, status_name in cursor.fetchall():
            if status_name:
                clean_status = str(status_name).strip()
                
                if clean_status.lower() == "success":
                    clean_status = "OCR-Success"
                
                status_counts[clean_status] = status_counts.get(clean_status, 0) + count_val
            total_count += count_val

        # Ensure all status types are present in the response
        for s in ('pending', 'rpa_pending', 'OCR-Success', 'failed', 'processed'):
            status_counts.setdefault(s, 0)
        status_counts["total_invoices"] = total_count

        return JSONResponse({
            "Field": field_filter if field_filter else "all",
            "page": page, 
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 1,
            "records": records, 
            "status_counts": status_counts,
            "filters_applied": {
                "po_number": po_number, 
                "supplier": supplier, 
                "company_code": company_code,
                "status": status_filter,
                "product_type_code": product_type_filter,
                "invoice_start_date": inv_start,
                "invoice_end_date": inv_end,
                "process_start_date": proc_start,
                "process_end_date": proc_end
            }
        })
    except Exception as e:
        logging.error(f"Error in customer_graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()



class ExportExcelRequest(BaseModel):
    Field: Optional[str] = None
    po_number: Optional[str] = None
    supplier: Optional[str] = None
    company_code: Optional[str] = None
    status: Optional[str] = None
    invoice_start_date: Optional[str] = None
    invoice_end_date: Optional[str] = None
    process_start_date: Optional[str] = None
    process_end_date: Optional[str] = None


@router.post("/export_invoices_excel")
async def export_invoices_excel(
    request: ExportExcelRequest,
    current_user: dict = Depends(get_current_user)
):
    po_number = request.po_number
    supplier = request.supplier
    company_code = request.company_code
    status_filter = request.status
    inv_start = request.invoice_start_date  
    inv_end = request.invoice_end_date      
    proc_start = request.process_start_date  
    proc_end = request.process_end_date      

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        base_select = """
            SELECT id, user_id, filename, pdf_id, SupplierName, CUSTOMER_INVOICE_ID, 
                   status, created_at, comments, {source} as source
            FROM {table}
        """

        unified_query = base_select.format(source="'manual'", table="Newyork_FileInput")

        where_conditions = []
        where_params = []

        if po_number:
            where_conditions.append("(base_records.PONumber LIKE ? OR base_records.ERP_PO_Number LIKE ?)")
            where_params.extend([f"%{po_number}%", f"%{po_number}%"])

        if supplier:
            supplier_variants = _resolve_supplier_name_variants(cursor, supplier)
            variant_conditions = []
            for variant in supplier_variants:
                variant_conditions.append("(base_records.SupplierName LIKE ? OR base_records.PR_Supplier LIKE ?)")
                where_params.extend([f"%{variant}%", f"%{variant}%"])
            where_conditions.append("(" + " OR ".join(variant_conditions) + ")")

        if company_code:
            where_conditions.append("base_records.Company_Code LIKE ?")
            where_params.append(f"%{company_code}%")

        if status_filter:
            if status_filter == "OCR-Success":
                where_conditions.append("(base_records.status = 'Success' OR base_records.status = 'success')")
            else:
                where_conditions.append("base_records.status = ?")
                where_params.append(status_filter)

        # Date cleaner helper
        def clean_input_date(date_str: Optional[str]) -> Optional[str]:
            if not date_str or str(date_str).strip() == "":
                return None
            date_str = str(date_str).strip()
            try:
                return datetime.fromisoformat(date_str.replace("Z", "")).strftime("%Y-%m-%d")
            except ValueError:
                pass
            try:
                if " " in date_str:
                    return datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d").strftime("%Y-%m-%d")
                return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                pass
            try:
                if " " in date_str:
                    return datetime.strptime(date_str.split(" ")[0], "%d-%m-%Y").strftime("%Y-%m-%d")
                return datetime.strptime(date_str, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
            return date_str[:10] if len(date_str) >= 10 else date_str

        clean_inv_start = clean_input_date(inv_start)
        clean_inv_end = clean_input_date(inv_end)
        clean_proc_start = clean_input_date(proc_start)
        clean_proc_end = clean_input_date(proc_end)

        # Invoice date filtering - handle multiple formats
        if clean_inv_start:
            where_conditions.append("""
                (COALESCE(
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 120),
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 105),
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 103)
                ) >= TRY_CONVERT(DATE, ?, 120))
            """)
            where_params.append(clean_inv_start)
            
        if clean_inv_end:
            where_conditions.append("""
                (COALESCE(
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 120),
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 105),
                    TRY_CONVERT(DATE, base_records.InvoiceDate, 103)
                ) <= TRY_CONVERT(DATE, ?, 120))
            """)
            where_params.append(clean_inv_end)

        # Process date filtering
        if clean_proc_start:
            where_conditions.append("(CAST(base_records.created_at AS DATE) >= TRY_CONVERT(DATE, ?, 120))")
            where_params.append(clean_proc_start)
            
        if clean_proc_end:
            where_conditions.append("(CAST(base_records.created_at AS DATE) <= TRY_CONVERT(DATE, ?, 120))")
            where_params.append(clean_proc_end)

        where_clause = "WHERE 1=1"
        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)

        # Main query
        sql = f"""
        SELECT * FROM (
            SELECT
                fi.id, 
                fi.user_id, 
                fi.filename, 
                fi.pdf_id, 
                fi.SupplierName, 
                fi.CUSTOMER_INVOICE_ID,
                fi.status, 
                fi.created_at, 
                fi.source, 
                fi.comments,
                inv.PONumber,
                inv.Invoice_Total AS Total,
                inv.InvoiceNumber,
                inv.InvoiceDate,
                pr.Supplier as PR_Supplier, 
                pr.Company_Code, 
                pr.ERP_PO_Number,
                ac.Ariba_comments,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, Invoice_Total, InvoiceNumber, InvoiceDate, created_at,
                       ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
            LEFT JOIN (
                SELECT pdf_id, Ariba_comments,
                       ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY Created_at DESC) AS rn_ac
                FROM OCR_Ariba_Comments
            ) ac ON fi.pdf_id = ac.pdf_id AND ac.rn_ac = 1
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        ORDER BY base_records.created_at DESC
        """

        cursor.execute(sql, where_params)
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        
        # Convert to list of dicts
        data_list = []
        for row in rows:
            row_dict = dict(zip(cols, row))
            
            # Strip status whitespace
            if "status" in row_dict and row_dict["status"]:
                row_dict["status"] = str(row_dict["status"]).strip()
            
            # Format datetime fields
            created_at_val = row_dict.get("created_at")
            if isinstance(created_at_val, datetime):
                row_dict["created_at"] = created_at_val.strftime("%Y-%m-%d %H:%M:%S")
            elif created_at_val:
                row_dict["created_at"] = str(created_at_val)
            
            # Format InvoiceDate - handle DD-MM-YYYY
            invoice_date_val = row_dict.get("InvoiceDate")
            if isinstance(invoice_date_val, datetime):
                row_dict["InvoiceDate"] = invoice_date_val.strftime("%d-%m-%Y")
            elif invoice_date_val:
                invoice_date_str = str(invoice_date_val)
                if re.match(r'^\d{2}-\d{2}-\d{4}$', invoice_date_str):
                    row_dict["InvoiceDate"] = invoice_date_str
                elif re.match(r'^\d{4}-\d{2}-\d{2}$', invoice_date_str):
                    try:
                        dt = datetime.strptime(invoice_date_str, "%Y-%m-%d")
                        row_dict["InvoiceDate"] = dt.strftime("%d-%m-%Y")
                    except:
                        row_dict["InvoiceDate"] = invoice_date_str
                else:
                    row_dict["InvoiceDate"] = invoice_date_str
            
            data_list.append(row_dict)
        # Create DataFrame
        if not data_list:
            # Return empty Excel with headers
            df = pd.DataFrame(columns=[
                "id", "user_id", "filename", "pdf_id", "SupplierName", "CUSTOMER_INVOICE_ID",
                "status", "created_at", "source", "comments", "PONumber", 
                "Total", "InvoiceNumber", "InvoiceDate", "PR_Supplier", "Company_Code", 
                "ERP_PO_Number", "Ariba_comments"
            ])
        else:
            df = pd.DataFrame(data_list)
            
            # Reorder columns for better readability
            column_order = [
                "id", 
                "pdf_id", 
                "filename", 
                "SupplierName", 
                "PONumber", 
                "InvoiceNumber", 
                "InvoiceDate", 
                "Total", 
                "CUSTOMER_INVOICE_ID",
                "status", 
                "source", 
                "created_at", 
                "PR_Supplier", 
                "Company_Code",
                "ERP_PO_Number", 
                "Ariba_comments", 
                "comments", 
                "user_id"
            ]
            # Only keep columns that exist
            existing_columns = [col for col in column_order if col in df.columns]
            # Add any remaining columns not in the order list
            extra_columns = [col for col in df.columns if col not in existing_columns]
            df = df[existing_columns + extra_columns]

        # Write to Excel buffer
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Invoices")
            
            # Auto-adjust column widths
            worksheet = writer.sheets["Invoices"]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # Highlight header row
            from openpyxl.styles import Font, PatternFill, Alignment
            header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True, size=11)
            header_align = Alignment(horizontal="center", vertical="center")
            
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = header_align
            
            # Freeze header row
            worksheet.freeze_panes = "A2"
        
        buffer.seek(0)
        
        # Return the file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Invoice_Report_{timestamp}.xlsx"
        
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )

    except Exception as e:
        logging.error(f"Error in export_invoices_excel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
    finally:
        cursor.close()
        conn.close()


# Add these models and endpoints after your existing CustomerGraphRequest class

class DateRangeRequest(BaseModel):
    from_date: Optional[str] = None  # Format: "YYYY-MM-DD HH:MM:SS.mmm"
    to_date: Optional[str] = None    # Format: "YYYY-MM-DD HH:MM:SS.mmm"
    supplier: Optional[str] = None
    po_number: Optional[str] = None
    company_code: Optional[str] = None
    Field: Optional[str] = "manual"
    status: Optional[str] = None

class PaginatedDateRangeRequest(DateRangeRequest):
    page: int = 1
    page_size: int = 10


@router.post("/date-range-count")
async def get_date_range_count(
    request: DateRangeRequest
):
    """
    Get counts of invoices filtered by created_at date range and other optional filters.
    Returns total count and status-wise breakdown.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Build base select for each source
        base_select = """
            SELECT
                id,
                user_id,
                filename,
                pdf_id,
                SupplierName,
                CUSTOMER_INVOICE_ID,
                status,
                created_at,
                comments,
                folder_name,
                brand,
                {source} AS source
            FROM {table}
        """

        unified_query = base_select.format(source="'manual'", table="Newyork_FileInput")

        where_conditions = []
        where_params = []

        # Date range filter on created_at
        if request.from_date:
            where_conditions.append("base_records.created_at >= ?")
            where_params.append(request.from_date)
        if request.to_date:
            where_conditions.append("base_records.created_at <= ?")
            where_params.append(request.to_date)

        # Other filters
        if request.po_number:
            where_conditions.append("(base_records.PONumber LIKE ? OR base_records.ERP_PO_Number LIKE ?)")
            where_params.extend([f"%{request.po_number}%", f"%{request.po_number}%"])

        if request.supplier:
            supplier_variants = _resolve_supplier_name_variants(cursor, request.supplier)
            variant_conditions = []
            for variant in supplier_variants:
                variant_conditions.append("(base_records.SupplierName LIKE ? OR base_records.PR_Supplier LIKE ?)")
                where_params.extend([f"%{variant}%", f"%{variant}%"])
            where_conditions.append("(" + " OR ".join(variant_conditions) + ")")

        if request.company_code:
            where_conditions.append("base_records.Company_Code LIKE ?")
            where_params.append(f"%{request.company_code}%")

        if request.status:
            status_filter = request.status.strip()
            if status_filter == "OCR-Success":
                where_conditions.append("(TRIM(base_records.status) = 'Success' OR TRIM(base_records.status) = 'success')")
            else:
                where_conditions.append("TRIM(base_records.status) = ?")
                where_params.append(status_filter)

        where_clause = "WHERE 1=1"
        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)

        # Count query with status breakdown
        count_sql = f"""
        SELECT 
            TRIM(base_records.status) as status,
            COUNT(*) as count
        FROM (
            SELECT 
                fi.status, 
                fi.pdf_id, 
                fi.created_at,
                inv.PONumber,
                pr.Supplier as PR_Supplier,
                pr.Company_Code,
                pr.ERP_PO_Number,
                fi.SupplierName,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, 
                       ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        GROUP BY TRIM(base_records.status)
        """

        cursor.execute(count_sql, where_params)
        status_counts = {}
        total_count = 0

        for status_name, count_val in cursor.fetchall():
            if status_name:
                clean_status = str(status_name).strip()
                if clean_status.lower() == "success":
                    clean_status = "OCR-Success"
                status_counts[clean_status] = status_counts.get(clean_status, 0) + count_val
            total_count += count_val

        # Ensure all status types are present
        for s in ('pending', 'rpa_pending', 'OCR-Success', 'failed', 'processed'):
            status_counts.setdefault(s, 0)

        return JSONResponse({
            "status": "success",
            "total_count": total_count,
            "status_counts": status_counts,
            "filters_applied": {
                "from_date": request.from_date,
                "to_date": request.to_date,
                "po_number": request.po_number,
                "supplier": request.supplier,
                "company_code": request.company_code,
                "Field": request.Field,
                "status": request.status
            }
        })

    except Exception as e:
        logging.error(f"Error in get_date_range_count: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/date-range-records")
async def get_date_range_records(
    request: PaginatedDateRangeRequest
):
    """
    Get paginated invoice records filtered by created_at date range and other optional filters.
    Returns the same fields as customer_graph endpoint.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        page = request.page
        page_size = request.page_size
        offset = (page - 1) * page_size

        # Build base select for each source
        base_select = """
            SELECT
                id,
                user_id,
                filename,
                pdf_id,
                SupplierName,
                CUSTOMER_INVOICE_ID,
                status,
                created_at,
                comments,
                folder_name,
                brand,
                {source} AS source
            FROM {table}
        """

        unified_query = base_select.format(source="'manual'", table="Newyork_FileInput")


        where_conditions = []
        where_params = []

        # Date range filter on created_at
        if request.from_date:
            where_conditions.append("base_records.created_at >= ?")
            where_params.append(request.from_date)
        if request.to_date:
            where_conditions.append("base_records.created_at <= ?")
            where_params.append(request.to_date)

        # Other filters
        if request.po_number:
            where_conditions.append("(base_records.PONumber LIKE ? OR base_records.ERP_PO_Number LIKE ?)")
            where_params.extend([f"%{request.po_number}%", f"%{request.po_number}%"])

        if request.supplier:
            supplier_variants = _resolve_supplier_name_variants(cursor, request.supplier)
            variant_conditions = []
            for variant in supplier_variants:
                variant_conditions.append("(base_records.SupplierName LIKE ? OR base_records.PR_Supplier LIKE ?)")
                where_params.extend([f"%{variant}%", f"%{variant}%"])
            where_conditions.append("(" + " OR ".join(variant_conditions) + ")")

        if request.company_code:
            where_conditions.append("base_records.Company_Code LIKE ?")
            where_params.append(f"%{request.company_code}%")

        if request.status:
            status_filter = request.status.strip()
            if status_filter == "OCR-Success":
                where_conditions.append("(TRIM(base_records.status) = 'Success' OR TRIM(base_records.status) = 'success')")
            else:
                where_conditions.append("TRIM(base_records.status) = ?")
                where_params.append(status_filter)

        where_clause = "WHERE 1=1"
        if where_conditions:
            where_clause += " AND " + " AND ".join(where_conditions)

        # Main query - same as customer_graph
        sql = f"""
        SELECT * FROM (
            SELECT
                fi.id,
                fi.user_id,
                fi.filename,
                fi.pdf_id,
                fi.SupplierName,
                fi.CUSTOMER_INVOICE_ID,
                fi.status,
                fi.created_at,
                fi.source,
                fi.comments,
                fi.folder_name,
                fi.brand,
                inv.PONumber,
                inv.Invoice_Total AS Total,
                inv.InvoiceNumber,
                inv.InvoiceDate,
                pr.Supplier as PR_Supplier,
                pr.Company_Code,
                pr.ERP_PO_Number,
                ac.Ariba_comments,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, Invoice_Total, InvoiceNumber, InvoiceDate, created_at,
                    ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
            LEFT JOIN (
                SELECT pdf_id, Ariba_comments,
                    ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY Created_at DESC) AS rn_ac
                FROM OCR_Ariba_Comments
            ) ac ON fi.pdf_id = ac.pdf_id AND ac.rn_ac = 1
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        ORDER BY 
            base_records.created_at DESC
        OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY
        """

        cursor.execute(sql, where_params)
        cols = [c[0] for c in cursor.description]
        records = []

        for row in cursor.fetchall():
            row_dict = dict(zip(cols, row))

            # Parse InvoiceDate - handle DD-MM-YYYY format
            invoice_date_val = row_dict.get("InvoiceDate")
            if invoice_date_val:
                if isinstance(invoice_date_val, datetime):
                    invoice_date_str = invoice_date_val.strftime("%d-%m-%Y")
                else:
                    invoice_date_str = str(invoice_date_val)
            else:
                invoice_date_str = None

            # Parse created_at
            created_at_val = row_dict.get("created_at")
            if isinstance(created_at_val, datetime):
                created_at_str = created_at_val.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            else:
                created_at_str = str(created_at_val) if created_at_val else None

            raw_status = row_dict.get("status")
            clean_status = raw_status.strip() if raw_status and isinstance(raw_status, str) else raw_status

            record = {
                "id": row_dict.get("id"),
                "user_id": row_dict.get("user_id"),
                "filename": row_dict.get("filename"),
                "pdf_id": row_dict.get("pdf_id"),
                "SupplierName": row_dict.get("SupplierName"),
                "CUSTOMER_INVOICE_ID": row_dict.get("CUSTOMER_INVOICE_ID"),
                "status": clean_status,
                "invoice_date": invoice_date_str,
                "processed_date": created_at_str,
                "source": row_dict.get("source"),
                "comments": row_dict.get("comments"),
                "folder_name": row_dict.get("folder_name"),
                "brand": row_dict.get("brand"),
                "PONumber": row_dict.get("PONumber"),
                "Total": row_dict.get("Total"),
                "InvoiceNumber": row_dict.get("InvoiceNumber"),
                "PR_Supplier": row_dict.get("PR_Supplier"),
                "Company_Code": row_dict.get("Company_Code"),
                "Ariba_comments": row_dict.get("Ariba_comments"),
            }
            records.append(record)

        # Count query for total
        count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT 
                fi.status, 
                fi.pdf_id, 
                fi.created_at,
                ROW_NUMBER() OVER (PARTITION BY fi.pdf_id ORDER BY fi.created_at DESC) as rn_fi
            FROM ({unified_query}) fi
            LEFT JOIN (
                SELECT pdf_id, PONumber, 
                       ROW_NUMBER() OVER (PARTITION BY pdf_id ORDER BY created_at DESC) AS rn
                FROM Newyork_Invoice_extracted_data
            ) inv ON fi.pdf_id = inv.pdf_id AND inv.rn = 1
            LEFT JOIN (
                SELECT p.purchase_order AS ERP_PO_Number, MAX(s.supplier_name) AS Supplier, MAX(p.company_code) AS Company_Code
                FROM [final_pulse_DBprod].[dbo].[S4_Purchase_Order_Data] p
                LEFT JOIN [final_pulse_DBprod].[dbo].[S4_Suppliers_prod] s ON s.supplier_id = p.supplier
                WHERE p.purchase_order IS NOT NULL
                GROUP BY p.purchase_order
            ) pr ON inv.PONumber = pr.ERP_PO_Number
        ) base_records
        {where_clause} AND base_records.rn_fi = 1
        """

        cursor.execute(count_sql, where_params)
        total_count = cursor.fetchone()[0] if cursor.description else 0

        return JSONResponse({
            "status": "success",
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 1,
            "records": records,
            "filters_applied": {
                "from_date": request.from_date,
                "to_date": request.to_date,
                "po_number": request.po_number,
                "supplier": request.supplier,
                "company_code": request.company_code,
                "Field": request.Field,
                "status": request.status
            }
        })

    except Exception as e:
        logging.error(f"Error in get_date_range_records: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()