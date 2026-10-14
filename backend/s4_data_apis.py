import io
import json
import os
import re
import asyncio
import uuid
import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import quote_plus, urljoin
import pandas as pd
import httpx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from fastapi import (
    APIRouter, UploadFile, File, HTTPException, Depends, Request,
    Query, Path, FastAPI
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from auth import get_person_details


load_dotenv()

# =================================================================
# Configuration
# =================================================================
SAP_PO_BASE_URL = os.environ.get(
    "SAP_BASE_URL",
    "https://my425838-api.s4hana.cloud.sap/sap/opu/odata4/sap/"
    "api_purchaseorder_2/srvd_a2x/sap/purchaseorder/0001/PurchaseOrder",
)
SAP_GRN_BASE_URL = os.environ.get(
    "SAP_GRN_BASE_URL",
    "https://my425838-api.s4hana.cloud.sap/sap/opu/odata/sap/"
    "API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader",
)

SAP_SUPPLIER_BASE_URL = os.environ.get(
    "SAP_SUPPLIER_BASE_URL",
    "https://my425838-api.s4hana.cloud.sap/sap/opu/odata/sap/"
    "API_BUSINESS_PARTNER/A_Supplier",
)

SAP_SUPPLIER_INVOICE_BASE_URL = os.environ.get(
    "SAP_SUPPLIER_INVOICE_BASE_URL",
    "https://my425838-api.s4hana.cloud.sap/sap/opu/odata/sap/"
    "API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice",
)
SAP_SUPPLIER_INVOICE_LIST_URL = os.environ.get(
    "SAP_SUPPLIER_INVOICE_LIST_URL",
    "https://my425838-api.s4hana.cloud.sap/sap/opu/odata/sap/"
    "API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice",
)

SAP_AUTH_HEADER = os.environ.get(
    "SAP_AUTH_HEADER",
    "Basic SVNVSVRFX1BST0RJUy1PRUdHTFFZRzpHUjRNLyVpRntdaX5jSHduUlxcWSN1ZFBBRm8mS302KEd4ei9pQGNj",
)

DOMAIN_ID = os.environ.get("DOMAIN_ID", "0CEB10E7-3000-4C70-96A8-E4B4709F7BAF")
SYSTEM_USER_ID = os.environ.get("SYSTEM_USER_ID", "638E4F54-FCA6-4A71-AFA9-E4B4709F7BAF")

PY_ODBC_CONNECTION_STRING = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={os.getenv('SERVER_NAME')};"
    f"DATABASE={os.getenv('Spain_DB_NAME')};"
    f"UID={os.getenv('DB_USER')};"
    f"PWD={os.getenv('DB_PASSWORD')};"
    f"TrustServerCertificate=yes;"
    # Without MARS, a single physical connection can only have one command's
    # result set open at a time - if anything on this connection (e.g. a
    # concurrent scheduler run reusing a not-yet-fully-drained pooled
    # connection) overlaps with another statement, pyodbc raises "Connection
    # is busy with results for another command". MARS lets one connection
    # hold multiple active result sets so this can't happen.
    f"MARS_Connection=yes;"
)

engine = create_async_engine(
    "mssql+aioodbc:///?odbc_connect=" + quote_plus(PY_ODBC_CONNECTION_STRING),
)

PO_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"          # e.g. 2026-08-31T00:00:00Z
ODATA_V2_DT_FMT = "datetime'%Y-%m-%dT%H:%M:%S'"  # e.g. datetime'2026-08-31T00:00:00'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sap_sync")

# --- turn on APScheduler's own internal debug logging too ---
logging.getLogger("apscheduler").setLevel(logging.DEBUG)

router = APIRouter(tags=["S4 Data apis"], prefix="/s4-data-apis")
# router=FastAPI()

scheduler = AsyncIOScheduler()


async def get_current_user(request: Request) -> dict:
    result = get_person_details(request)
    return result["data"]

# =================================================================
# Scheduler debug listeners
# =================================================================
def _job_listener(event):
    """Fires whenever a scheduled job finishes, errors, or is missed."""
    if event.exception:
        print(f"[SCHEDULER]  Job '{event.job_id}' RAISED an exception: {event.exception}")
        logger.error("[SCHEDULER] Job %s raised exception: %s", event.job_id, event.exception)
    elif event.code == EVENT_JOB_MISSED:
        print(f"[SCHEDULER]   Job '{event.job_id}' was MISSED (scheduler too busy / down at run time)")
        logger.warning("[SCHEDULER] Job %s was missed", event.job_id)
    else:
        print(f"[SCHEDULER]  Job '{event.job_id}' executed successfully at {datetime.now(timezone.utc).isoformat()}")
        logger.info("[SCHEDULER] Job %s executed successfully", event.job_id)


scheduler.add_listener(_job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)


def print_scheduler_status(context: str = ""):
    """Prints a snapshot of scheduler state + registered jobs. Call anywhere to debug."""
    print(f"\n[SCHEDULER STATUS] {context}")
    print(f"  running   = {scheduler.running}")
    print(f"  state     = {scheduler.state}  (0=stopped, 1=running, 2=paused)")
    jobs = scheduler.get_jobs()
    if not jobs:
        print("  jobs      = (none registered)")
    else:
        for job in jobs:
            print(f"  job id='{job.id}' next_run_time={job.next_run_time} trigger={job.trigger}")
    print("")


# =================================================================
# Shared helper
# =================================================================
def one_month_back(dt: datetime) -> datetime:
    """Subtract one calendar month from `dt`, handling rollover/day-clamping."""
    year = dt.year
    month = dt.month - 1
    if month == 0:
        month = 12
        year -= 1

    if month == 2:
        last_day = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    elif month in (4, 6, 9, 11):
        last_day = 30
    else:
        last_day = 31

    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)

# =================================================================
# Invoice: DB helpers
# =================================================================
async def invoice_get_from_to_dates(conn) -> tuple[datetime, datetime]:
    row = (await conn.execute(
        text(
            """
            SELECT TOP (1) to_date
            FROM dbo.S4_Invoice_Data_schedular_logs
            WHERE flag = 1
            ORDER BY created_at DESC
            """
        )
    )).fetchone()

    to_date = datetime.now(timezone.utc).replace(microsecond=0)

    if row is not None:
        from_date = row[0]
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
    else:
        # today = datetime.now(timezone.utc).date()
        # today_midnight = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        # from_date = one_month_back(today_midnight)
        from_date = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)

    print(f"[INVOICE] date window -> from={from_date.isoformat()} to={to_date.isoformat()}")
    return from_date, to_date

# =================================================================
# PO: DB helpers
# =================================================================
async def po_get_from_to_dates(conn) -> tuple[datetime, datetime]:
    row = (await conn.execute(
        text(
            """
            SELECT TOP (1) to_date
            FROM [dbo].[S4_Purchase_Order_Data_schedular_logs]
            WHERE flag = 1
            ORDER BY created_at DESC
            """
        )
    )).fetchone()

    to_date = datetime.now(timezone.utc).replace(microsecond=0)

    if row is not None:
        from_date = row[0]
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
    else:
        # today = datetime.now(timezone.utc).date()
        # today_midnight = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        # from_date = one_month_back(today_midnight)
        from_date = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)

    print(f"[PO] date window -> from={from_date.isoformat()} to={to_date.isoformat()}")
    return from_date, to_date


async def po_insert_scheduler_log(conn, from_date, to_date, total_records, inserted, updated, flag):
    await conn.execute(
        text(
            """
            INSERT INTO [dbo].[S4_Purchase_Order_Data_schedular_logs]
                (from_date, to_date, total_records, inserted_records, updated_records, created_at, flag)
            VALUES (:from_date, :to_date, :total_records, :inserted_records, :updated_records, :created_at, :flag)
            """
        ),
        {
            "from_date": from_date,
            "to_date": to_date,
            "total_records": total_records,
            "inserted_records": inserted,
            "updated_records": updated,
            "created_at": datetime.now(timezone.utc),
            "flag": flag,
        },
    )
    await conn.commit()
    print(f"[PO] scheduler log written -> total={total_records} inserted={inserted} updated={updated} flag={flag}")

async def grn_insert_scheduler_log(conn, from_date, to_date, total_records, inserted, updated, flag):
    await conn.execute(
        text(
            """
            INSERT INTO [dbo].[S4_GRN_Data_schedular_logs]
                (from_date, to_date, total_records, inserted_records, updated_records, created_at, flag)
            VALUES (:from_date, :to_date, :total_records, :inserted_records, :updated_records, :created_at, :flag)
            """
        ),
        {
            "from_date": from_date,
            "to_date": to_date,
            "total_records": total_records,
            "inserted_records": inserted,
            "updated_records": updated,
            "created_at": datetime.now(timezone.utc),
            "flag": flag,
        },
    )
    await conn.commit()
    print(f"[GRN] scheduler log written -> total={total_records} inserted={inserted} updated={updated} flag={flag}")


PO_KEY_COLUMNS = ("purchase_order", "purchase_order_item")
PO_ALL_COLUMNS = [
    "purchase_order", "purchase_order_type", "created_by_user", "creation_date",
    "purchase_order_date", "last_change_date_time", "validity_start_date",
    "validity_end_date", "purchasing_processing_status", "company_code",
    "purchasing_organization", "purchasing_group", "supplier", "pricing_document",
    "pricing_procedure", "exchange_rate", "tax_return_country", "vat_registration_country",
    "purchase_order_item", "purchase_order_item_text", "document_currency",
    "material_group", "material", "material_type", "plant",
    "reference_delivery_address_id", "base_unit", "purchasing_info_record",
    "net_amount", "gross_amount", "quantity", "unit_price", "tax_code",
    "tax_determination_date", "purg_doc_price_date","product_type_code",
    "purchase_order_quantity_unit", "tax_jurisdiction","effective_amount",
    "is_finally_invoiced",
]


async def upsert_rows_via_merge(
    conn,
    table: str,
    key_columns: tuple[str, ...],
    all_columns: list[str],
    rows: list[dict],
) -> tuple[int, int, int]:
    """Bulk-upsert `rows` into `table` in one round trip using a staging temp
    table + a single MERGE, instead of one SELECT+UPDATE/INSERT per row.

    `key_columns` may be a single column or a composite key.

    Each row dict may omit columns that had no value (falsy values are
    stripped upstream). A missing column leaves the existing value alone on
    UPDATE (via COALESCE) and is inserted as NULL on INSERT - matching the
    previous per-row behaviour.
    """
    total_records = 0
    rows_by_key: dict[tuple, dict] = {}
    duplicate_count = 0
    for row in rows:
        if any(row.get(k) is None for k in key_columns):
            continue
        total_records += 1
        key = tuple(row.get(k) for k in key_columns)
        filtered = {c: row.get(c) for c in all_columns}
        existing = rows_by_key.get(key)
        if existing is not None:
            duplicate_count += 1
            for c in all_columns:
                if filtered.get(c) is not None:
                    existing[c] = filtered[c]
        else:
            rows_by_key[key] = filtered

    filtered_rows = list(rows_by_key.values())

    if duplicate_count:
        print(
            f"[UPSERT] target table = dbo.{table} -> collapsed {duplicate_count} "
            f"duplicate-key row(s) from the fetched data before MERGE"
        )
    print(f"[UPSERT] target table = dbo.{table} -> {len(filtered_rows)} row(s) to insert/update")

    if not filtered_rows:
        return total_records, 0, 0

    staging_table = "#staging_" + uuid.uuid4().hex[:8]
    cols_sql = ", ".join(f"[{c}]" for c in all_columns)

    await conn.execute(text(
        f"SELECT TOP 0 {cols_sql} INTO {staging_table} FROM dbo.{table} WHERE 1=0"
    ))

    # Load the staging table via OPENJSON rather than a parameterized
    # multi-row INSERT. Without fast_executemany support under the async
    # aioodbc driver (SQLAlchemy's mssql+pyodbc do_executemany optimization
    # doesn't work on aioodbc's async cursor - see the engine definition
    # above), a plain executemany-style call falls back to one network round
    # trip PER ROW, which is minutes of dead time for a 30k+ row batch.
    # Every OPENJSON column is typed nvarchar(max) and relies on SQL
    # Server's implicit conversion into the staging table's real column
    # types on INSERT, so this doesn't need to know each column's actual
    # SQL type.
    #
    # Sent in CHUNKS rather than one giant parameter: a 45k-row batch across
    # ~40 columns serializes to 50+ MB of JSON in a single query parameter,
    # which one real sync run blew up on - ODBC's SQLPutData streams a
    # large parameter in pieces, and on a 50+ MB payload the TCP connection
    # to Azure SQL died mid-stream ("Communication link failure (10060)")
    # rather than the query itself being slow. Splitting into ~5-8 MB
    # chunks keeps each individual parameter small enough to send reliably;
    # the staging table just accumulates rows across chunks before the one
    # MERGE below runs against all of them together.
    json_with_clause = ", ".join(f"[{c}] nvarchar(max) '$.\"{c}\"'" for c in all_columns)
    CHUNK_SIZE = 3000
    num_chunks = (len(filtered_rows) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for chunk_idx in range(0, len(filtered_rows), CHUNK_SIZE):
        chunk = filtered_rows[chunk_idx:chunk_idx + CHUNK_SIZE]
        json_payload = json.dumps(chunk, default=str)
        await conn.execute(
            text(f"""
                INSERT INTO {staging_table} ({cols_sql})
                SELECT {cols_sql}
                FROM OPENJSON(:json_payload)
                WITH ({json_with_clause})
            """),
            {"json_payload": json_payload},
        )
        if num_chunks > 1:
            print(
                f"[UPSERT] target table = dbo.{table} -> staged chunk "
                f"{chunk_idx // CHUNK_SIZE + 1}/{num_chunks} ({len(chunk)} row(s))"
            )

    update_columns = [c for c in all_columns if c not in key_columns]
    set_clause = ", ".join(
        f"target.[{c}] = COALESCE(source.[{c}], target.[{c}])" for c in update_columns
    )
    insert_vals = ", ".join(f"source.[{c}]" for c in all_columns)
    on_clause = " AND ".join(f"target.[{k}] = source.[{k}]" for k in key_columns)

    merge_sql = text(f"""
        MERGE dbo.{table} AS target
        USING {staging_table} AS source
        ON {on_clause}
        WHEN MATCHED THEN
            UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN
            INSERT ({cols_sql})
            VALUES ({insert_vals})
        OUTPUT $action AS merge_action;
    """)
    result = await conn.execute(merge_sql)
    actions = [r[0] for r in result.fetchall()]
    inserted_records = actions.count("INSERT")
    updated_records = actions.count("UPDATE")

    print(
        f"[UPSERT] target table = dbo.{table} -> "
        f"inserted={inserted_records} updated={updated_records}"
    )

    await conn.execute(text(f"DROP TABLE {staging_table}"))
    await conn.commit()

    return total_records, inserted_records, updated_records


async def po_upsert_rows(conn, rows: list[dict]) -> tuple[int, int, int]:
    print(f"[PO] starting upsert for {len(rows)} row(s)")
    total_records, inserted_records, updated_records = await upsert_rows_via_merge(
        conn, "S4_Purchase_Order_Data", PO_KEY_COLUMNS, PO_ALL_COLUMNS, rows
    )
    print(f"[PO] upsert complete -> total={total_records} inserted={inserted_records} updated={updated_records}")
    logger.info(
        "PO upsert complete. total=%s inserted=%s updated=%s",
        total_records, inserted_records, updated_records,
    )
    return total_records, inserted_records, updated_records


# =================================================================
# PO: SAP OData client
# =================================================================
async def po_fetch_csrf_token(client: httpx.AsyncClient) -> str:
    print("[PO] fetching CSRF token...")
    resp = await client.get(
        SAP_PO_BASE_URL,
        headers={"x-csrf-token": "fetch", "Authorization": SAP_AUTH_HEADER},
        params={"$top": 1},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.headers.get("x-csrf-token")

    if not token:
        raise RuntimeError(
            "SAP did not return an x-csrf-token header on the fetch call. "
            "Check that the 'x-csrf-token: fetch' header and Authorization "
            "header were both sent correctly."
        )

    print(f"[PO] got CSRF token: {token}")
    logger.info("Fetched PO CSRF token: %s", token)
    return token


async def po_fetch_purchase_orders(from_date: datetime, to_date: datetime) -> list[dict]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        token = await po_fetch_csrf_token(client)

        filter_str = (
            f"LastChangeDateTime ge {from_date.strftime(PO_DATE_FMT)} "
            f"and LastChangeDateTime le {to_date.strftime(PO_DATE_FMT)}"
        )
        print(f"[PO] fetching purchase orders with filter: {filter_str}")

        headers = {
            "x-csrf-token": token,
            "Authorization": SAP_AUTH_HEADER,
        }
        page_size = 5000
        base_params = {
            "$filter": filter_str,
            "$expand": "_PurchaseOrderItem",
            # Without $top, SAP falls back to its own default page size (100
            # here) and this loop pays a full network round trip per page -
            # ~130 sequential requests for a 13k-order sync. Ask for a much
            # bigger page.
            "$top": page_size,
        }

        orders: list[dict] = []
        skip = 0
        page = 0
        while True:
            page += 1
            # A large date range / big $expand page can take SAP longer than
            # httpx's default 5s read timeout to respond even though it's
            # still working, not actually down - retry with backoff instead
            # of failing the whole sync on one slow page (same pattern as
            # grn_fetch_material_documents below).
            MAX_ATTEMPTS = 3
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    resp = await client.get(
                        SAP_PO_BASE_URL,
                        headers=headers,
                        params={**base_params, "$skip": skip},
                        timeout=180,
                    )
                    resp.raise_for_status()
                    break
                except httpx.ReadTimeout:
                    if attempt == MAX_ATTEMPTS:
                        raise
                    wait = 10 * attempt
                    print(f"[PO] page {page}: read timeout on attempt {attempt}/{MAX_ATTEMPTS} - retrying in {wait}s")
                    await asyncio.sleep(wait)
            data = resp.json()
            batch = data.get("value", [])
            orders.extend(batch)
            print(f"[PO] page {page}: fetched {len(batch)} (running total {len(orders)})")

            # SAP's OData V4 PurchaseOrder service doesn't return
            # @odata.nextLink once a client-specified $top is satisfied - it
            # just returns exactly $top rows and stops, even when more rows
            # match the filter. Drive pagination ourselves via $skip instead:
            # a batch that fills the whole page size means there may be
            # more, so keep going; a partial batch means this was the last
            # page.
            if len(batch) < page_size:
                break
            skip += page_size

    print(f"[PO] fetched {len(orders)} purchase order(s) from SAP across {page} page(s)")
    return orders


# =================================================================
# PO: JSON -> row mapping
# =================================================================
def po_normalize_line_item(raw) -> str:
    if raw in (None, ""):
        return raw
    try:
        num = int(raw)
    except (TypeError, ValueError):
        return raw
    return str(num // 10) if num % 10 == 0 else str(num)


def po_map_orders_to_rows(orders: list[dict]) -> list[dict]:
    rows = []

    for po in orders:
        items = po.get("_PurchaseOrderItem") or []
        for item in items:
            row = {
                "purchase_order": po.get("PurchaseOrder") or None,
                "purchase_order_type": po.get("PurchaseOrderType") or None,
                "created_by_user": po.get("CreatedByUser") or None,
                "creation_date": po.get("CreationDate") or None,
                "purchase_order_date": po.get("PurchaseOrderDate") or None,
                "last_change_date_time": po.get("LastChangeDateTime") or None,
                "validity_start_date": po.get("ValidityStartDate") or None,
                "validity_end_date": po.get("ValidityEndDate") or None,
                "purchasing_processing_status": po.get("PurchasingProcessingStatus") or None,
                "company_code": po.get("CompanyCode") or None,
                "purchasing_organization": po.get("PurchasingOrganization") or None,
                "purchasing_group": po.get("PurchasingGroup") or None,
                "supplier": po.get("Supplier") or None,
                "pricing_document": po.get("PricingDocument") or None,
                "pricing_procedure": po.get("PricingProcedure") or None,
                "exchange_rate": po.get("ExchangeRate") or None,
                "tax_return_country": po.get("TaxReturnCountry") or None,
                "vat_registration_country": po.get("VATRegistrationCountry") or None,
                "purchase_order_item": po_normalize_line_item(item.get("PurchaseOrderItem")) or None,
                "purchase_order_item_text": item.get("PurchaseOrderItemText") or None,   # <-- added
                "document_currency": item.get("DocumentCurrency") or None,
                "material_group": item.get("MaterialGroup") or None,
                "material": item.get("Material") or None,
                "material_type": item.get("MaterialType") or None,
                "plant": item.get("Plant") or None,
                "reference_delivery_address_id": item.get("ReferenceDeliveryAddressID") or None,
                "base_unit": item.get("BaseUnit") or None,
                "purchasing_info_record": item.get("PurchasingInfoRecord") or None,
                "net_amount": item.get("NetAmount") or None,
                "gross_amount": item.get("GrossAmount") or None,
                "quantity": item.get("OrderQuantity") or None,
                "unit_price": item.get("NetPriceAmount") or None,
                "tax_code": item.get("TaxCode") or None,
                "tax_determination_date": item.get("TaxDeterminationDate") or None,
                "purg_doc_price_date": item.get("PurgDocPriceDate") or None,
                "product_type_code" : item.get("ProductTypeCode") or None,
                "purchase_order_quantity_unit" : item.get("PurchaseOrderQuantityUnit") or None,
                "tax_jurisdiction" : item.get("TaxJurisdiction") or None,
                "effective_amount": item.get("EffectiveAmount") or None,
                "is_finally_invoiced": item.get("IsFinallyInvoiced"),
            }

            row = {
                column: value
                for column, value in row.items()
                if value is not None and value != ""
            }

            rows.append(row)

    print(f"[PO] mapped {len(rows)} row(s) from {len(orders)} order(s)")
    return rows


# =================================================================
# GRN: DB helpers
# =================================================================
async def grn_get_from_to_dates(conn) -> tuple[datetime, datetime]:
    row = (await conn.execute(
        text(
            """
            SELECT TOP (1) to_date
            FROM dbo.S4_GRN_Data_schedular_logs
            WHERE flag = 1
            ORDER BY created_at DESC
            """
        )
    )).fetchone()

    to_date = datetime.now(timezone.utc).replace(microsecond=0)

    if row is not None:
        from_date = row[0]
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
    else:
        # today = datetime.now(timezone.utc).date()
        # today_midnight = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        # from_date = one_month_back(today_midnight)
        from_date = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)

    print(f"[GRN] date window -> from={from_date.isoformat()} to={to_date.isoformat()}")
    return from_date, to_date


GRN_KEY_COLUMNS = ("material_document", "material_document_item")
GRN_ALL_COLUMNS = [
    "material_document", "purchase_order", "purchase_order_item",
    "inventory_transaction_type", "document_date", "posting_date",
    "creation_date_time", "created_by_user", "goods_movement_code",
    "material_document_item", "material", "plant", "storage_location", "batch",
    "goods_movement_type", "inventory_stock_type", "supplier",
    "goods_movement_ref_doc_type", "controlling_area", "profit_center",
    "material_base_unit", "quantity_in_base_unit", "entry_unit",
    "quantity_in_entry_unit", "company_code_currency", "fiscal_year",
    "fiscal_year_period", "fiscal_year_variant", "debit_credit_code",
    "material_document_line",
]


async def grn_upsert_rows(conn, rows: list[dict]) -> tuple[int, int, int]:
    print(f"[GRN] starting upsert for {len(rows)} row(s)")
    total_records, inserted_records, updated_records = await upsert_rows_via_merge(
        conn, "S4_GRN_Data", GRN_KEY_COLUMNS, GRN_ALL_COLUMNS, rows
    )
    print(f"[GRN] upsert complete -> total={total_records} inserted={inserted_records} updated={updated_records}")
    logger.info(
        "GRN upsert complete. total=%s inserted=%s updated=%s",
        total_records, inserted_records, updated_records,
    )
    return total_records, inserted_records, updated_records


# =================================================================
# GRN: SAP OData V2 client
# =================================================================
async def grn_fetch_material_documents(from_date: datetime, to_date: datetime) -> list[dict]:
    filter_str = (
        f"CreationDate ge {from_date.strftime(ODATA_V2_DT_FMT)} "
        f"and CreationDate le {to_date.strftime(ODATA_V2_DT_FMT)}"
    )
    print(f"[GRN] fetching material documents with filter: {filter_str}")

    headers = {
        "Accept": "application/json",
        "Authorization": SAP_AUTH_HEADER,
    }
    page_size = 5000
    base_params = {
        "$filter": filter_str,
        "$expand": "to_MaterialDocumentItem",
        # Same reasoning as po_fetch_purchase_orders' $top: avoid paying a
        # full round trip per SAP-default-sized page.
        "$top": page_size,
    }

    documents: list[dict] = []
    skip = 0
    page = 0
    async with httpx.AsyncClient() as client:
        while True:
            page += 1
            # A large date range (all POs/GRNs since 2026-08-25 here) can make a
            # single page slow enough to blow past a 60s read timeout even
            # though SAP is still working, not actually down - retry with
            # backoff instead of failing the whole sync on one slow page.
            MAX_ATTEMPTS = 3
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    resp = await client.get(
                        SAP_GRN_BASE_URL,
                        headers=headers,
                        params={**base_params, "$skip": skip},
                        timeout=180,
                    )
                    resp.raise_for_status()
                    break
                except httpx.ReadTimeout:
                    if attempt == MAX_ATTEMPTS:
                        raise
                    wait = 10 * attempt
                    print(f"[GRN] page {page}: read timeout on attempt {attempt}/{MAX_ATTEMPTS} - retrying in {wait}s")
                    await asyncio.sleep(wait)
            data = resp.json().get("d", {})
            batch = data.get("results", [])
            documents.extend(batch)
            print(f"[GRN] page {page}: fetched {len(batch)} (running total {len(documents)})")

            # Same as po_fetch_purchase_orders: don't rely on "__next" once a
            # client-specified $top is satisfied - drive pagination
            # ourselves via $skip, stopping only on a partial (less than
            # page_size) batch.
            if len(batch) < page_size:
                break
            skip += page_size

    print(f"[GRN] fetched {len(documents)} material document(s) from SAP across {page} page(s)")
    return documents


# =================================================================
# GRN: OData V2 date/time parsing helpers
# =================================================================
_ODATA_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")
_ODATA_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


_ODATA_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def grn_parse_odata_date(value) -> datetime | None:
    if not value:
        return None
    match = _ODATA_DATE_RE.match(value)
    if not match:
        return None
    millis = int(match.group(1))
    # SAP OData sentinel dates (e.g. "9999-12-31" for an open-ended validity
    # end date) produce a millis value far outside what datetime.fromtimestamp
    # can convert on Windows (OSError: [Errno 22] Invalid argument - its
    # libc-backed epoch conversion has a much narrower range there than on
    # Linux). Computing directly off the epoch avoids that OS-level
    # conversion entirely and works for any value datetime itself can hold
    # (years 1-9999).
    try:
        return _ODATA_EPOCH + timedelta(milliseconds=millis)
    except OverflowError:
        return None


def grn_parse_odata_date_str(value) -> str | None:
    dt = grn_parse_odata_date(value)
    return dt.isoformat() if dt else None


def grn_parse_odata_duration(value) -> timedelta:
    if not value:
        return timedelta()
    match = _ODATA_DURATION_RE.match(value)
    if not match:
        return timedelta()
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def grn_combine_date_and_duration(date_value, duration_value) -> str | None:
    header_date = grn_parse_odata_date(date_value)
    if header_date is None:
        return None
    header_time = grn_parse_odata_duration(duration_value)
    return (header_date + header_time).isoformat()


# =================================================================
# GRN: JSON -> row mapping
# =================================================================
def grn_map_documents_to_rows(documents: list[dict]) -> list[dict]:
    rows = []

    for doc in documents:
        creation_date_time = grn_combine_date_and_duration(
            doc.get("CreationDate"), doc.get("CreationTime")
        )
        document_date = grn_parse_odata_date_str(doc.get("DocumentDate"))
        posting_date = grn_parse_odata_date_str(doc.get("PostingDate"))

        items = (doc.get("to_MaterialDocumentItem") or {}).get("results", [])
        for item in items:
            row = {
                "material_document": doc.get("MaterialDocument") or None,
                "purchase_order": item.get("PurchaseOrder") or None,
                "purchase_order_item": item.get("PurchaseOrderItem") or None,
                "inventory_transaction_type": doc.get("InventoryTransactionType") or None,
                "document_date": document_date,
                "posting_date": posting_date,
                "creation_date_time": creation_date_time,
                "created_by_user": doc.get("CreatedByUser") or None,
                "goods_movement_code": doc.get("GoodsMovementCode") or None,
                "material_document_item": item.get("MaterialDocumentItem") or None,
                "material": item.get("Material") or None,
                "plant": item.get("Plant") or None,
                "storage_location": item.get("StorageLocation") or None,
                "batch": item.get("Batch") or None,
                "goods_movement_type": item.get("GoodsMovementType") or None,
                "inventory_stock_type": item.get("InventoryStockType") or None,
                "supplier": item.get("Supplier") or None,
                "goods_movement_ref_doc_type": item.get("GoodsMovementRefDocType") or None,
                "controlling_area": item.get("ControllingArea") or None,
                "profit_center": item.get("ProfitCenter") or None,
                "material_base_unit": item.get("MaterialBaseUnit") or None,
                "quantity_in_base_unit": item.get("QuantityInBaseUnit") or None,
                "entry_unit": item.get("EntryUnit") or None,
                "quantity_in_entry_unit": item.get("QuantityInEntryUnit") or None,
                "company_code_currency": item.get("CompanyCodeCurrency") or None,
                "fiscal_year": item.get("FiscalYear") or None,
                "fiscal_year_period": item.get("FiscalYearPeriod") or None,
                "fiscal_year_variant": item.get("FiscalYearVariant") or None,
                "debit_credit_code": item.get("DebitCreditCode") or None,
                "material_document_line": item.get("MaterialDocumentLine") or None,
            }

            row = {
                column: value
                for column, value in row.items()
                if value is not None and value != ""
            }

            rows.append(row)

    print(f"[GRN] mapped {len(rows)} row(s) from {len(documents)} document(s)")
    return rows

async def invoice_fetch_supplier_invoices(from_date: datetime, to_date: datetime) -> list[dict]:
    filter_str = (
        f"PostingDate ge {from_date.strftime(ODATA_V2_DT_FMT)} "
        f"and PostingDate le {to_date.strftime(ODATA_V2_DT_FMT)}"
    )
    print(f"[INVOICE] fetching supplier invoices with filter: {filter_str}")

    headers = {
        "Accept": "application/json",
        "Authorization": SAP_AUTH_HEADER,
    }
    # to_SuplrInvcItemPurOrdRef carries the PO reference(s) each invoice item
    # was posted against (PurchaseOrder, PurchaseOrderItem, TaxCode, etc.) -
    # expanded here so invoice_map_items_to_rows below has it without a
    # second round trip per invoice.
    params = {"$filter": filter_str, "$expand": "to_SuplrInvcItemPurOrdRef"}

    invoices: list[dict] = []
    url = SAP_SUPPLIER_INVOICE_LIST_URL
    page = 0
    async with httpx.AsyncClient() as client:
        while url:
            page += 1
            # A large date range / the to_SuplrInvcItemPurOrdRef $expand can
            # make a page slow enough to blow past httpx's default 5s read
            # timeout even though SAP is still working, not actually down -
            # retry with backoff instead of failing the whole sync on one
            # slow page (same pattern as po_fetch_purchase_orders/
            # grn_fetch_material_documents above).
            MAX_ATTEMPTS = 3
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    resp = await client.get(
                        url,
                        headers=headers,
                        params=params if page == 1 else None,
                        timeout=180,
                    )
                    resp.raise_for_status()
                    break
                except httpx.ReadTimeout:
                    if attempt == MAX_ATTEMPTS:
                        raise
                    wait = 10 * attempt
                    print(f"[INVOICE] page {page}: read timeout on attempt {attempt}/{MAX_ATTEMPTS} - retrying in {wait}s")
                    await asyncio.sleep(wait)
            data = resp.json().get("d", {})
            batch = data.get("results", [])
            invoices.extend(batch)
            print(f"[INVOICE] page {page}: fetched {len(batch)} (running total {len(invoices)})")

            next_link = data.get("__next")
            if next_link and not next_link.startswith("http"):
                next_link = urljoin(url, next_link)
            url = next_link

    print(f"[INVOICE] fetched {len(invoices)} supplier invoice(s) from SAP across {page} page(s)")
    return invoices

# =================================================================
# Invoice: JSON -> row mapping
# =================================================================
def invoice_map_invoices_to_rows(invoices: list[dict]) -> list[dict]:
    rows = []
    for inv in invoices:
        row = {
            "supplier_invoice": inv.get("SupplierInvoice") or None,
            "fiscal_year": inv.get("FiscalYear") or None,
            "company_code": inv.get("CompanyCode") or None,
            "document_date": grn_parse_odata_date_str(inv.get("DocumentDate")),
            "posting_date": grn_parse_odata_date_str(inv.get("PostingDate")),
            "creation_date": grn_parse_odata_date_str(inv.get("CreationDate")),
            "supplier_invoice_id_by_invcg_party": inv.get("SupplierInvoiceIDByInvcgParty") or None,
            "invoicing_party": inv.get("InvoicingParty") or None,
            "document_currency": inv.get("DocumentCurrency") or None,
            "invoice_gross_amount": inv.get("InvoiceGrossAmount") or None,
            "unplanned_delivery_cost": inv.get("UnplannedDeliveryCost") or None,
            "document_header_text": inv.get("DocumentHeaderText") or None,
            "reconciliation_account": inv.get("ReconciliationAccount") or None,
            "manual_cash_discount": inv.get("ManualCashDiscount") or None,
            "payment_terms": inv.get("PaymentTerms") or None,
            "due_calculation_base_date": grn_parse_odata_date_str(inv.get("DueCalculationBaseDate")),
            "cash_discount1_percent": inv.get("CashDiscount1Percent") or None,
            "cash_discount1_days": inv.get("CashDiscount1Days") or None,
            "cash_discount2_percent": inv.get("CashDiscount2Percent") or None,
            "cash_discount2_days": inv.get("CashDiscount2Days") or None,
            "net_payment_days": inv.get("NetPaymentDays") or None,
            "payment_blocking_reason": inv.get("PaymentBlockingReason") or None,
            "accounting_document_type": inv.get("AccountingDocumentType") or None,
            "supplier_invoice_status": inv.get("SupplierInvoiceStatus") or None,
            "payment_method": inv.get("PaymentMethod") or None,
            "invoice_reference": inv.get("InvoiceReference") or None,
            "invoice_reference_fiscal_year": inv.get("InvoiceReferenceFiscalYear") or None,
            "tax_is_calculated_automatically": inv.get("TaxIsCalculatedAutomatically"),
            "suplr_invc_is_capital_goods_related": inv.get("SuplrInvcIsCapitalGoodsRelated"),
            "supplier_invoice_is_credit_memo": inv.get("SupplierInvoiceIsCreditMemo") or None,
            "tax_determination_date": grn_parse_odata_date_str(inv.get("TaxDeterminationDate")),
            "tax_reporting_date": grn_parse_odata_date_str(inv.get("TaxReportingDate")),
            "tax_fulfillment_date": grn_parse_odata_date_str(inv.get("TaxFulfillmentDate")),
            "invoice_receipt_date": grn_parse_odata_date_str(inv.get("InvoiceReceiptDate")),
            "supplier_invoice_origin": inv.get("SupplierInvoiceOrigin") or None,
            "reverse_document": inv.get("ReverseDocument") or None,
            "reverse_document_fiscal_year": inv.get("ReverseDocumentFiscalYear") or None,
            "is_reversal": inv.get("IsReversal"),
            "is_reversed": inv.get("IsReversed"),
        }

        row = {
            column: value
            for column, value in row.items()
            if value is not None and value != ""
        }

        rows.append(row)

    print(f"[INVOICE] mapped {len(rows)} row(s) from {len(invoices)} invoice(s)")
    return rows
INVOICE_KEY_COLUMNS = ("supplier_invoice", "fiscal_year")
INVOICE_ALL_COLUMNS = [
    "supplier_invoice", "fiscal_year", "company_code", "document_date", "posting_date",
    "creation_date", "supplier_invoice_id_by_invcg_party", "invoicing_party",
    "document_currency", "invoice_gross_amount", "unplanned_delivery_cost",
    "document_header_text", "reconciliation_account", "manual_cash_discount",
    "payment_terms", "due_calculation_base_date", "cash_discount1_percent",
    "cash_discount1_days", "cash_discount2_percent", "cash_discount2_days",
    "net_payment_days", "payment_blocking_reason", "accounting_document_type",
    "supplier_invoice_status", "payment_method", "invoice_reference",
    "invoice_reference_fiscal_year", "tax_is_calculated_automatically",
    "suplr_invc_is_capital_goods_related", "supplier_invoice_is_credit_memo",
    "tax_determination_date", "tax_reporting_date", "tax_fulfillment_date",
    "invoice_receipt_date", "supplier_invoice_origin", "reverse_document",
    "reverse_document_fiscal_year", "is_reversal", "is_reversed",
]
async def invoice_upsert_rows(conn, rows: list[dict]) -> tuple[int, int, int]:
    print(f"[INVOICE] starting upsert for {len(rows)} row(s)")
    total_records, inserted_records, updated_records = await upsert_rows_via_merge(
        conn, "S4_Invoice_Data", INVOICE_KEY_COLUMNS, INVOICE_ALL_COLUMNS, rows
    )
    print(f"[INVOICE] upsert complete -> total={total_records} inserted={inserted_records} updated={updated_records}")
    logger.info(
        "Invoice upsert complete. total=%s inserted=%s updated=%s",
        total_records, inserted_records, updated_records,
    )
    return total_records, inserted_records, updated_records

# =================================================================
# Invoice PO reference items (to_SuplrInvcItemPurOrdRef): JSON -> row mapping
# =================================================================
# One supplier invoice can post against several PO lines (e.g. a single
# invoice billing two different PurchaseOrder/PurchaseOrderItem pairs), so -
# unlike S4_Invoice_Data, which is one row per invoice - this is one row per
# (supplier_invoice, fiscal_year, supplier_invoice_item), mirroring how
# grn_map_documents_to_rows flattens a GRN document's to_MaterialDocumentItem
# array.
def invoice_map_items_to_rows(invoices: list[dict]) -> list[dict]:
    rows = []
    for inv in invoices:
        supplier_invoice = inv.get("SupplierInvoice") or None
        fiscal_year = inv.get("FiscalYear") or None
        items = (inv.get("to_SuplrInvcItemPurOrdRef") or {}).get("results", [])
        for item in items:
            row = {
                "supplier_invoice": supplier_invoice,
                "fiscal_year": fiscal_year,
                "supplier_invoice_item": item.get("SupplierInvoiceItem") or None,
                "purchase_order": item.get("PurchaseOrder") or None,
                "purchase_order_item": item.get("PurchaseOrderItem") or None,
                "plant": item.get("Plant") or None,
                "tax_code": item.get("TaxCode") or None,
                "document_currency": item.get("DocumentCurrency") or None,
                "supplier_invoice_item_amount": item.get("SupplierInvoiceItemAmount") or None,
                "purchase_order_quantity_unit": item.get("PurchaseOrderQuantityUnit") or None,
                "quantity_in_purchase_order_unit": item.get("QuantityInPurchaseOrderUnit") or None,
                "product_type": item.get("ProductType") or None,
                "is_finally_invoiced": item.get("IsFinallyInvoiced"),
            }

            row = {
                column: value
                for column, value in row.items()
                if value is not None and value != ""
            }

            rows.append(row)

    print(f"[INVOICE ITEM] mapped {len(rows)} PO-reference item row(s) from {len(invoices)} invoice(s)")
    return rows

INVOICE_ITEM_KEY_COLUMNS = ("supplier_invoice", "fiscal_year", "supplier_invoice_item")
INVOICE_ITEM_ALL_COLUMNS = [
    "supplier_invoice", "fiscal_year", "supplier_invoice_item",
    "purchase_order", "purchase_order_item", "plant", "tax_code",
    "document_currency", "supplier_invoice_item_amount",
    "purchase_order_quantity_unit", "quantity_in_purchase_order_unit",
    "product_type", "is_finally_invoiced",
]

async def invoice_item_upsert_rows(conn, rows: list[dict]) -> tuple[int, int, int]:
    print(f"[INVOICE ITEM] starting upsert for {len(rows)} row(s)")
    total_records, inserted_records, updated_records = await upsert_rows_via_merge(
        conn, "S4_Invoice_PurOrdRef_Data", INVOICE_ITEM_KEY_COLUMNS, INVOICE_ITEM_ALL_COLUMNS, rows
    )
    print(f"[INVOICE ITEM] upsert complete -> total={total_records} inserted={inserted_records} updated={updated_records}")
    logger.info(
        "Invoice PO-reference item upsert complete. total=%s inserted=%s updated=%s",
        total_records, inserted_records, updated_records,
    )
    return total_records, inserted_records, updated_records

async def invoice_insert_scheduler_log(conn, from_date, to_date, total_records, inserted, updated, flag):
    await conn.execute(
        text(
            """
            INSERT INTO [dbo].[S4_Invoice_Data_schedular_logs]
                (from_date, to_date, total_records, inserted_records, updated_records, created_at, flag)
            VALUES (:from_date, :to_date, :total_records, :inserted_records, :updated_records, :created_at, :flag)
            """
        ),
        {
            "from_date": from_date,
            "to_date": to_date,
            "total_records": total_records,
            "inserted_records": inserted,
            "updated_records": updated,
            "created_at": datetime.now(timezone.utc),
            "flag": flag,
        },
    )
    await conn.commit()
    print(f"[INVOICE] scheduler log written -> total={total_records} inserted={inserted} updated={updated} flag={flag}")


# =================================================================
# Retry helper: run DB work against a FRESH connection each attempt.
#
# A "Communication link failure" (pyodbc 08S01 / TCP error 10060) mid-MERGE
# leaves the connection that hit it unusable for anything further - the
# underlying socket is gone. Retrying on the SAME connection just fails
# again immediately. Since none of upsert_rows_via_merge's work commits
# until it's fully done, re-running `work_fn` whole against a brand-new
# connection is safe on retry: no partial/duplicate rows, just re-spent
# time. Used to wrap "upsert + write scheduler log" for PO/GRN/Invoice sync,
# each of which has been observed to intermittently drop the connection
# partway through a large batch.
# =================================================================
async def _db_retry(work_fn, max_attempts: int = 3, label: str = ""):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        conn = await engine.connect()
        try:
            return await work_fn(conn)
        except OperationalError as e:
            last_exc = e
            try:
                await conn.rollback()
            except Exception:
                pass
            if attempt == max_attempts:
                raise
            wait = 15 * attempt
            print(
                f"[DB RETRY{' ' + label if label else ''}] connection failure on attempt "
                f"{attempt}/{max_attempts}: {e} - retrying in {wait}s with a fresh connection"
            )
            await asyncio.sleep(wait)
        finally:
            await conn.close()
    raise last_exc


# =================================================================
# PO-only sync, usable standalone (via /sync-po_by_date_range) or as
# sync_all's Step 1/4.
# =================================================================
async def sync_po(start_date=None, end_date=None, run_id: str | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    print(f"[PO_SYNC:{run_id}] PO sync starting...")

    if start_date is not None and end_date is not None:
        po_from_date = start_date
        po_to_date = end_date
    else:
        date_conn = await engine.connect()
        try:
            po_from_date, po_to_date = await po_get_from_to_dates(date_conn)
        finally:
            await date_conn.close()

    try:
        orders = await po_fetch_purchase_orders(po_from_date, po_to_date)
        po_rows = po_map_orders_to_rows(orders)

        async def _upsert_and_log(conn):
            po_total, po_inserted, po_updated = await po_upsert_rows(conn, po_rows)
            await po_insert_scheduler_log(conn, po_from_date, po_to_date, po_total, po_inserted, po_updated, flag=1)
            return po_total, po_inserted, po_updated

        po_total, po_inserted, po_updated = await _db_retry(_upsert_and_log, label=f"PO_SYNC:{run_id}")

        result = {
            "status": "success",
            "from_date": po_from_date.strftime(PO_DATE_FMT),
            "to_date": po_to_date.strftime(PO_DATE_FMT),
            "purchase_orders_fetched": len(orders),
            "line_items_total": po_total,
            "line_items_inserted": po_inserted,
            "line_items_updated": po_updated,
        }
        print(f"[PO_SYNC:{run_id}] PO sync  SUCCESS "
              f"(fetched={len(orders)}, total={po_total}, inserted={po_inserted}, updated={po_updated})")
        return result
    except Exception as exc:
        print(f"[PO_SYNC:{run_id}] PO sync  FAILED: {exc}")
        logger.exception("PO sync failed")
        try:
            log_conn = await engine.connect()
            try:
                await po_insert_scheduler_log(log_conn, po_from_date, po_to_date, 0, 0, 0, flag=0)
            finally:
                await log_conn.close()
        except Exception:
            logger.exception("PO sync failure-log write also failed")
        return {
            "status": "error",
            "from_date": po_from_date.strftime(PO_DATE_FMT),
            "to_date": po_to_date.strftime(PO_DATE_FMT),
            "message": str(exc),
        }


# =================================================================
# Invoice-only sync - the header + PO-reference-item fetch/upsert, usable
# standalone (via /sync-invoice_by_date_range) or as sync_all's Step 3/4.
# =================================================================
async def sync_invoice(start_date=None, end_date=None, run_id: str | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    print(f"[INVOICE_SYNC:{run_id}] Invoice sync starting...")

    if start_date is not None and end_date is not None:
        invoice_from_date = start_date
        invoice_to_date = end_date
    else:
        date_conn = await engine.connect()
        try:
            invoice_from_date, invoice_to_date = await invoice_get_from_to_dates(date_conn)
        finally:
            await date_conn.close()

    try:
        invoices = await invoice_fetch_supplier_invoices(invoice_from_date, invoice_to_date)
        invoice_rows = invoice_map_invoices_to_rows(invoices)
        invoice_item_rows = invoice_map_items_to_rows(invoices)

        async def _upsert_and_log(conn):
            invoice_total, invoice_inserted, invoice_updated = await invoice_upsert_rows(conn, invoice_rows)
            invoice_item_total, invoice_item_inserted, invoice_item_updated = await invoice_item_upsert_rows(
                conn, invoice_item_rows
            )
            await invoice_insert_scheduler_log(
                conn, invoice_from_date, invoice_to_date,
                invoice_total, invoice_inserted, invoice_updated, flag=1
            )
            return (
                invoice_total, invoice_inserted, invoice_updated,
                invoice_item_total, invoice_item_inserted, invoice_item_updated,
            )

        (
            invoice_total, invoice_inserted, invoice_updated,
            invoice_item_total, invoice_item_inserted, invoice_item_updated,
        ) = await _db_retry(_upsert_and_log, label=f"INVOICE_SYNC:{run_id}")

        result = {
            "status": "success",
            "from_date": invoice_from_date.isoformat(),
            "to_date": invoice_to_date.isoformat(),
            "supplier_invoices_fetched": len(invoices),
            "line_items_total": invoice_total,
            "line_items_inserted": invoice_inserted,
            "line_items_updated": invoice_updated,
            "po_ref_items_total": invoice_item_total,
            "po_ref_items_inserted": invoice_item_inserted,
            "po_ref_items_updated": invoice_item_updated,
        }
        print(f"[INVOICE_SYNC:{run_id}] Invoice sync  SUCCESS "
              f"(fetched={len(invoices)}, total={invoice_total}, inserted={invoice_inserted}, updated={invoice_updated}, "
              f"po_ref_items_total={invoice_item_total}, po_ref_items_inserted={invoice_item_inserted}, "
              f"po_ref_items_updated={invoice_item_updated})")
        return result
    except Exception as exc:
        print(f"[INVOICE_SYNC:{run_id}] Invoice sync  FAILED: {exc}")
        logger.exception("Invoice sync failed")
        try:
            log_conn = await engine.connect()
            try:
                await invoice_insert_scheduler_log(log_conn, invoice_from_date, invoice_to_date, 0, 0, 0, flag=0)
            finally:
                await log_conn.close()
        except Exception:
            logger.exception("Invoice sync failure-log write also failed")
        return {
            "status": "error",
            "from_date": invoice_from_date.isoformat(),
            "to_date": invoice_to_date.isoformat(),
            "message": str(exc),
        }


# =================================================================
# GRN-only sync, usable as sync_all's Step 2/4.
# =================================================================
async def sync_grn(start_date=None, end_date=None, run_id: str | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    print(f"[GRN_SYNC:{run_id}] GRN sync starting...")

    if start_date is not None and end_date is not None:
        grn_from_date = start_date
        grn_to_date = end_date
    else:
        date_conn = await engine.connect()
        try:
            grn_from_date, grn_to_date = await grn_get_from_to_dates(date_conn)
        finally:
            await date_conn.close()

    try:
        documents = await grn_fetch_material_documents(grn_from_date, grn_to_date)
        grn_rows = grn_map_documents_to_rows(documents)

        async def _upsert_and_log(conn):
            grn_total, grn_inserted, grn_updated = await grn_upsert_rows(conn, grn_rows)
            await grn_insert_scheduler_log(conn, grn_from_date, grn_to_date, grn_total, grn_inserted, grn_updated, flag=1)
            return grn_total, grn_inserted, grn_updated

        grn_total, grn_inserted, grn_updated = await _db_retry(_upsert_and_log, label=f"GRN_SYNC:{run_id}")

        result = {
            "status": "success",
            "from_date": grn_from_date.isoformat(),
            "to_date": grn_to_date.isoformat(),
            "material_documents_fetched": len(documents),
            "line_items_total": grn_total,
            "line_items_inserted": grn_inserted,
            "line_items_updated": grn_updated,
        }
        print(f"[GRN_SYNC:{run_id}] GRN sync  SUCCESS "
              f"(fetched={len(documents)}, total={grn_total}, inserted={grn_inserted}, updated={grn_updated})")
        return result
    except Exception as exc:
        print(f"[GRN_SYNC:{run_id}] GRN sync  FAILED: {exc}")
        logger.exception("GRN sync failed")
        try:
            log_conn = await engine.connect()
            try:
                await grn_insert_scheduler_log(log_conn, grn_from_date, grn_to_date, 0, 0, 0, flag=0)
            finally:
                await log_conn.close()
        except Exception:
            logger.exception("GRN sync failure-log write also failed")
        return {
            "status": "error",
            "from_date": grn_from_date.isoformat(),
            "to_date": grn_to_date.isoformat(),
            "message": str(exc),
        }


# =================================================================
# Combined job: PO first, then GRN
# =================================================================
async def sync_all(start_date=None, end_date=None):
    run_id = uuid.uuid4().hex[:8]
    started_at = datetime.now(timezone.utc)
    print(f"\n{'='*70}")
    print(f"[SYNC_ALL] ▶ run_id={run_id} STARTED at {started_at.isoformat()}")
    print(f"{'='*70}")

    result = {
        "purchase_order": None,
        "material_document": None,
    }

    # ---------------- PO sync ----------------
    print(f"[SYNC_ALL:{run_id}] Step 1/4 -> PO sync starting...")
    result["purchase_order"] = await sync_po(start_date, end_date, run_id=run_id)
    print(f"[SYNC_ALL:{run_id}] Step 1/4 -> PO sync  {result['purchase_order']['status'].upper()}")

    # ---------------- GRN sync (runs after PO) ----------------
    print(f"[SYNC_ALL:{run_id}] Step 2/4 -> GRN sync starting...")
    result["material_document"] = await sync_grn(start_date, end_date, run_id=run_id)
    print(f"[SYNC_ALL:{run_id}] Step 2/4 -> GRN sync  {result['material_document']['status'].upper()}")

    # ---------------- Invoice sync (runs after GRN) ----------------
    print(f"[SYNC_ALL:{run_id}] Step 3/4 -> Invoice sync starting...")
    result["supplier_invoice"] = await sync_invoice(start_date, end_date, run_id=run_id)
    print(f"[SYNC_ALL:{run_id}] Step 3/4 -> Invoice sync  {result['supplier_invoice']['status'].upper()}")

    # ---------------- Supplier master-data sync (runs after Invoice) ----------------
    # No date range - always a full fetch/upsert of every supplier, same as
    # the standalone /sync-suppliers endpoint always did.
    print(f"[SYNC_ALL:{run_id}] Step 4/4 -> Supplier sync starting...")
    result["supplier"] = await sync_suppliers(run_id=run_id)
    print(f"[SYNC_ALL:{run_id}] Step 4/4 -> Supplier sync  {result['supplier']['status'].upper()}")

    overall_status = (
        "success"
        if result["purchase_order"]["status"] == "success"
        and result["material_document"]["status"] == "success"
        and result["supplier_invoice"]["status"] == "success"
        and result["supplier"]["status"] == "success"
        else "partial_failure"
    )

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()
    print(f"{'='*70}")
    print(f"[SYNC_ALL]  run_id={run_id} FINISHED at {finished_at.isoformat()} "
          f"(duration={duration:.2f}s) overall_status={overall_status}")
    print(f"{'='*70}\n")

    return overall_status, result



# =================================================================
# Endpoint wrapper for manual/HTTP-triggered sync
# =================================================================
@router.post("/sync-all")
async def sync_all_endpoint():
    overall_status, result = await sync_all()
    status_code = 200 if overall_status == "success" else 207  # 207 = Multi-Status
    return JSONResponse(status_code=status_code, content={"status": overall_status, **result})

@router.post("/sync-all_by_date_range")
async def sync_all_endpoint(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...)
):
    overall_status, result = await sync_all(start_date, end_date)

    status_code = 200 if overall_status == "success" else 207

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            **result
        }
    )


@router.post("/sync-po_by_date_range")
async def sync_po_endpoint(
    from_date: date = Query(..., description="e.g. 2026-09-13"),
    to_date: date = Query(..., description="e.g. 2026-09-15"),
):
    """
    PO-only sync (PurchaseOrder + _PurchaseOrderItem) for a caller-given date
    range - doesn't touch GRN/invoice like /sync-all_by_date_range does.

    from_date is sent to SAP as that day's start (00:00:00 UTC). to_date is
    normally sent as that day's end (23:59:59 UTC) - EXCEPT when to_date is
    today's UTC date, in which case the actual current UTC time is used
    instead of 23:59:59, since 23:59:59 hasn't happened yet and SAP's
    LastChangeDateTime filter would otherwise silently include changes made
    later today, right up to midnight, that don't exist yet.
    """
    from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if to_date == now_utc.date():
        to_dt = now_utc.replace(microsecond=0)
    else:
        to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)

    result = await sync_po(from_dt, to_dt)

    status_code = 200 if result["status"] == "success" else 207
    return JSONResponse(status_code=status_code, content=result)


@router.post("/sync-invoice_by_date_range")
async def sync_invoice_endpoint(
    from_date: date = Query(..., description="e.g. 2026-09-13"),
    to_date: date = Query(..., description="e.g. 2026-09-15"),
):
    """
    Invoice-only sync (A_SupplierInvoice + to_SuplrInvcItemPurOrdRef) for a
    caller-given date range - doesn't touch PO/GRN like /sync-all_by_date_range
    does.

    from_date is sent to SAP as that day's start (00:00:00 UTC). to_date is
    normally sent as that day's end (23:59:59 UTC) - EXCEPT when to_date is
    today's UTC date, in which case the actual current UTC time is used
    instead of 23:59:59, since 23:59:59 hasn't happened yet and SAP's
    PostingDate filter would otherwise silently include invoices posted
    later today, right up to midnight, that don't exist yet.
    """
    from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if to_date == now_utc.date():
        to_dt = now_utc.replace(microsecond=0)
    else:
        to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)

    result = await sync_invoice(from_dt, to_dt)

    status_code = 200 if result["status"] == "success" else 207
    return JSONResponse(status_code=status_code, content=result)


# =================================================================
# Scheduler endpoints
# =================================================================
@router.post("/scheduler/start")
async def start_scheduler(current_user: dict = Depends(get_current_user)):
    print(f"[SCHEDULER] /scheduler/start called. current running={scheduler.running} state={scheduler.state}")

    if scheduler.state == 0:  # STATE_STOPPED - never started before
        scheduler.add_job(
            sync_all,
            trigger=IntervalTrigger(minutes=10),
            id="my_scheduler_job",
            replace_existing=True,
        )
        scheduler.start()

        scheduler.add_job(
            sync_all,
            trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
            id="my_scheduler_job_immediate",
            replace_existing=True,
        )
        print("[SCHEDULER] ▶ Scheduler STARTED fresh (job added, interval=30min)")
        print_scheduler_status("after start()")
        return {"status": "started", "message": "Scheduler will run every 30 minutes"}

    if scheduler.state == 2:  # STATE_PAUSED
        scheduler.resume()
        print("[SCHEDULER] ▶ Scheduler RESUMED from paused state")
        print_scheduler_status("after resume()")
        return {"status": "resumed", "message": "Scheduler resumed"}

    print("[SCHEDULER] Scheduler already running, no action taken")
    print_scheduler_status("already running - no action")
    return {"status": "already_running", "message": "Scheduler is already running"}

def start_s4_scheduler():
    print(f"[SCHEDULER] start check. current running={scheduler.running} state={scheduler.state}")

    if scheduler.state == 0:  # STATE_STOPPED - never started before
        scheduler.add_job(
            sync_all,
            trigger=IntervalTrigger(minutes=10),
            id="my_scheduler_job",
            replace_existing=True,
        )
        scheduler.start()

        scheduler.add_job(
            sync_all,
            trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
            id="my_scheduler_job_immediate",
            replace_existing=True,
        )
        print("[SCHEDULER] ▶ Scheduler STARTED (interval=10min)")
        print_scheduler_status("after start")
        return {"status": "started", "message": "Scheduler started"}

    if scheduler.state == 2:  # STATE_PAUSED
        scheduler.resume()
        print("[SCHEDULER] ▶ Scheduler RESUMED")
        print_scheduler_status("after resume")
        return {"status": "resumed", "message": "Scheduler resumed"}

    print("[SCHEDULER] already running, no action taken")
    return {"status": "already_running", "message": "Scheduler is already running"}


@router.post("/scheduler/stop")
async def stop_scheduler(current_user: dict = Depends(get_current_user)):
    print(f"[SCHEDULER] /scheduler/stop called. current running={scheduler.running} state={scheduler.state}")

    if scheduler.state == 1:  # STATE_RUNNING
        scheduler.pause()
        print("[SCHEDULER] ⏸ Scheduler PAUSED (jobs will not fire until resumed)")
        print_scheduler_status("after pause()")
        return {"status": "stopped", "message": "Scheduler paused"}

    print("[SCHEDULER] Scheduler already stopped/paused, no action taken")
    print_scheduler_status("already stopped - no action")
    return {"status": "already_stopped", "message": "Scheduler is not running"}


@router.get("/scheduler/status")
async def scheduler_status(current_user: dict = Depends(get_current_user)):
    """Debug endpoint: check scheduler state and next run time at any point."""
    jobs = scheduler.get_jobs()
    job_info = [
        {"id": job.id, "next_run_time": str(job.next_run_time), "trigger": str(job.trigger)}
        for job in jobs
    ]
    print_scheduler_status("manual status check via /scheduler/status")
    return {
        "running": scheduler.running,
        "state": scheduler.state,
        "jobs": job_info,
    }


# =================================================================
# Supplier Invoice creation (S4 SupplierInvoice OData V2 service)
# =================================================================
class SupplierInvoiceItemPurOrdRef(BaseModel):
    SupplierInvoiceItem: str
    PurchaseOrder: str
    PurchaseOrderItem: str
    DocumentCurrency: str
    TaxCode: str
    QuantityInPurchaseOrderUnit: str
    SupplierInvoiceItemAmount: str
    PurchaseOrderQuantityUnit: str
    TaxJurisdiction: Optional[str] = "NY00000000"
    ReferenceDocument: Optional[str] = None
    ReferenceDocumentItem: Optional[str] = None
    ReferenceDocumentFiscalYear: Optional[str] = None


class SupplierInvoiceItemPurOrdRefWrapper(BaseModel):
    results: List[SupplierInvoiceItemPurOrdRef]


class SupplierInvoiceTaxItem(BaseModel):
    TaxCode: str
    TaxJurisdiction: Optional[str] = "NY00000000"
    DocumentCurrency: str
    TaxAmount: str


class SupplierInvoiceTaxWrapper(BaseModel):
    results: List[SupplierInvoiceTaxItem]


class SupplierInvoiceCreateRequest(BaseModel):
    pdf_id: Optional[str] = None
    CompanyCode: str
    DocumentDate: str                      # "YYYY-MM-DD"
    DueCalculationBaseDate: str            # "YYYY-MM-DD"
    InvoiceGrossAmount: str
    SupplierInvoiceIDByInvcgParty: str
    DocumentHeaderText: Optional[str] = None
    TaxIsCalculatedAutomatically: bool = False
    DocumentCurrency: str
    to_SuplrInvcItemPurOrdRef: SupplierInvoiceItemPurOrdRefWrapper
    to_SupplierInvoiceTax: Optional[SupplierInvoiceTaxWrapper] = None



def si_date_to_odata_ms(date_str: str) -> str:
    """Convert a plain 'YYYY-MM-DD' date into SAP OData V2's '/Date(<epoch_ms>)/' format,
    anchored at UTC midnight."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return f"/Date({int(dt.timestamp() * 1000)})/"


def si_current_utc_odata_ms() -> str:
    """Current UTC timestamp in SAP OData V2's '/Date(<epoch_ms>)/' format, used for PostingDate."""
    return f"/Date({int(datetime.now(timezone.utc).timestamp() * 1000)})/"


async def si_fetch_csrf_token(client: httpx.AsyncClient) -> str:
    print("[SupplierInvoice] fetching CSRF token...")
    resp = await client.get(
        SAP_SUPPLIER_INVOICE_BASE_URL,
        headers={
            "x-csrf-token": "fetch",
            "Authorization": SAP_AUTH_HEADER,
            "Accept": "application/json",
        },
        params={"$top": 1},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.headers.get("x-csrf-token")

    if not token:
        raise RuntimeError(
            "SAP did not return an x-csrf-token header on the fetch call. "
            "Check that the 'x-csrf-token: fetch' header and Authorization "
            "header were both sent correctly."
        )

    print(f"[SupplierInvoice] got CSRF token: {token}")
    logger.info("Fetched SupplierInvoice CSRF token: %s", token)
    return token


async def si_create_supplier_invoice(payload: dict) -> httpx.Response:
    """GET (fetch CSRF token, keep session cookies) then POST the invoice payload,
    exactly mirroring the manual curl flow (GET x-csrf-token: fetch -> POST with token)."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        token = await si_fetch_csrf_token(client)

        print("[SupplierInvoice] posting invoice to SAP...")
        resp = await client.post(
            SAP_SUPPLIER_INVOICE_BASE_URL,
            headers={
                "x-csrf-token": token,
                "Authorization": SAP_AUTH_HEADER,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
        )
    print(f"[SupplierInvoice] SAP responded with status {resp.status_code}")
    return resp


def si_extract_created_invoice_id(body: dict) -> Optional[str]:
    """SAP's OData V2 response wraps the created entity under 'd'. Try the
    field names the SupplierInvoice service is known to use for the
    generated document number; return None if the shape doesn't match
    rather than guessing."""
    if not isinstance(body, dict):
        return None
    entity = body.get("d", body)
    if not isinstance(entity, dict):
        return None
    for key in ("SupplierInvoice", "SupplierInvoiceID", "AccountingDocument", "FiscalYear"):
        value = entity.get(key)
        if value:
            return str(value)
    return None


async def si_update_fileinput_after_submission(
    pdf_id: str, status: str, comment: str, customer_invoice_id: Optional[str] = None
) -> dict:
    """Write the SAP submission outcome back to Newyork_FileInput for this
    pdf_id. Mirrors newyork_ocr.py's update_fileinput_status_by_pdf_id /
    update_customer_invoice_id helpers, kept local to this module instead of
    importing newyork_ocr.py (which would pull in its much heavier
    dependencies - paramiko, google-genai - just for two column writes).
    Best-effort: a failure here is logged, not raised, since the SAP call
    itself has already succeeded or failed by this point. Returns a dict
    the caller can surface in the API response so a bad pdf_id (0 rows
    matched) or a DB error isn't silently invisible."""
    fields = {"status": status, "comments": comment}
    if customer_invoice_id:
        fields["CUSTOMER_INVOICE_ID"] = customer_invoice_id
    set_clause = ", ".join(f"[{c}] = :{c}" for c in fields)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(f"UPDATE dbo.Newyork_FileInput SET {set_clause} WHERE pdf_id = :pdf_id"),
                {**fields, "pdf_id": pdf_id},
            )
            rowcount = result.rowcount
        if rowcount:
            print(f"[SupplierInvoice] Newyork_FileInput updated for pdf_id={pdf_id} -> status={status}")
            return {"updated": True, "rowcount": rowcount}
        else:
            logger.warning(f"[SupplierInvoice] No Newyork_FileInput row matched pdf_id={pdf_id} - status/CUSTOMER_INVOICE_ID were NOT updated")
            return {"updated": False, "rowcount": 0, "error": f"No Newyork_FileInput row found for pdf_id={pdf_id}"}
    except Exception as exc:
        logger.exception(f"Failed to update Newyork_FileInput for pdf_id={pdf_id} after SAP submission")
        return {"updated": False, "rowcount": 0, "error": str(exc)}


@router.post("/invoice_create_in_s4")
async def invoice_create_in_s4(
    data: SupplierInvoiceCreateRequest
):
    try:
        payload = {
            "CompanyCode": data.CompanyCode,
            "DocumentDate": si_date_to_odata_ms(data.DocumentDate),
            "PostingDate": si_current_utc_odata_ms(),
            "DueCalculationBaseDate": si_date_to_odata_ms(data.DueCalculationBaseDate),
            "InvoiceGrossAmount": data.InvoiceGrossAmount,
            "SupplierInvoiceIDByInvcgParty": data.SupplierInvoiceIDByInvcgParty,
            "TaxIsCalculatedAutomatically": data.TaxIsCalculatedAutomatically,
            "DocumentCurrency": data.DocumentCurrency,
            # SAP's DocumentHeaderText (BKTXT) field is limited to 25 characters -
            # truncate rather than let SAP reject the whole request over it.
            **({"DocumentHeaderText": data.DocumentHeaderText[:25]} if data.DocumentHeaderText else {}),
            "to_SuplrInvcItemPurOrdRef": {
                "results": [
                    {**item.model_dump(exclude_none=True), "TaxJurisdiction": item.TaxJurisdiction or "NY00000000"}
                    for item in data.to_SuplrInvcItemPurOrdRef.results
                ]
            },
        }
        if data.to_SupplierInvoiceTax:
            payload["to_SupplierInvoiceTax"] = {
                "results": [
                    {**item.model_dump(exclude_none=True), "TaxJurisdiction": item.TaxJurisdiction or "NY00000000"}
                    for item in data.to_SupplierInvoiceTax.results
                ]
            }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date format (expected YYYY-MM-DD): {exc}")

    try:
        resp = await si_create_supplier_invoice(payload)
    except httpx.HTTPError as exc:
        logger.exception("SupplierInvoice creation failed to reach SAP")
        if data.pdf_id:
            await si_update_fileinput_after_submission(data.pdf_id, "failed", f"Could not reach SAP: {exc}")
        raise HTTPException(status_code=502, detail=f"Could not reach SAP: {exc}")

    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if not resp.is_success:
        logger.error("SAP rejected SupplierInvoice creation (%s): %s", resp.status_code, body)
        if data.pdf_id:
            await si_update_fileinput_after_submission(
                data.pdf_id, "failed", f"SAP rejected invoice creation ({resp.status_code}): {body}"
            )
        raise HTTPException(status_code=resp.status_code, detail=body)

    fileinput_update = None
    if data.pdf_id:
        fileinput_update = await si_update_fileinput_after_submission(
            data.pdf_id, "processed", "Invoice created in SAP successfully",
            customer_invoice_id=si_extract_created_invoice_id(body),
        )
    else:
        logger.warning("[SupplierInvoice] No pdf_id supplied - Newyork_FileInput was not updated")
        fileinput_update = {"updated": False, "rowcount": 0, "error": "No pdf_id supplied in the request"}

    if isinstance(body, dict):
        body["fileinput_update"] = fileinput_update
    return body


# =================================================================
# Supplier master data sync (S4 API_BUSINESS_PARTNER OData V4 service)
# =================================================================
SUPPLIER_KEY_COLUMNS = ("supplier_id",)
SUPPLIER_ALL_COLUMNS = ["supplier_id", "supplier_name"]


async def supplier_fetch_all() -> list[dict]:
    """Fetches every supplier (Supplier, SupplierName) from SAP (OData V2),
    following d.__next until exhausted - no date filter, this is master data."""
    headers = {
        "Accept": "application/json",
        "Authorization": SAP_AUTH_HEADER,
    }
    params = {"$select": "Supplier,SupplierName"}

    suppliers: list[dict] = []
    url = SAP_SUPPLIER_BASE_URL
    page = 0
    async with httpx.AsyncClient() as client:
        while url:
            page += 1
            resp = await client.get(
                url,
                headers=headers,
                params=params if page == 1 else None
            )
            resp.raise_for_status()
            data = resp.json().get("d", {})
            batch = data.get("results", [])
            suppliers.extend(batch)
            print(f"[SUPPLIER] page {page}: fetched {len(batch)} (running total {len(suppliers)})")

            next_link = data.get("__next")
            if next_link and not next_link.startswith("http"):
                next_link = urljoin(url, next_link)
            url = next_link

    print(f"[SUPPLIER] fetched {len(suppliers)} supplier(s) from SAP across {page} page(s)")
    return suppliers


def supplier_map_to_rows(suppliers: list[dict]) -> list[dict]:
    rows = []
    for sup in suppliers:
        row = {
            "supplier_id": sup.get("Supplier") or None,
            "supplier_name": sup.get("SupplierName") or None,
        }
        row = {k: v for k, v in row.items() if v is not None and v != ""}
        rows.append(row)

    print(f"[SUPPLIER] mapped {len(rows)} row(s)")
    return rows


async def supplier_upsert_rows(conn, rows: list[dict]) -> tuple[int, int, int]:
    print(f"[SUPPLIER] starting upsert for {len(rows)} row(s)")
    total_records, inserted_records, updated_records = await upsert_rows_via_merge(
        conn, "S4_Suppliers_prod", SUPPLIER_KEY_COLUMNS, SUPPLIER_ALL_COLUMNS, rows
    )
    print(f"[SUPPLIER] upsert complete -> total={total_records} inserted={inserted_records} updated={updated_records}")
    logger.info(
        "Supplier upsert complete. total=%s inserted=%s updated=%s",
        total_records, inserted_records, updated_records,
    )
    return total_records, inserted_records, updated_records


# =================================================================
# Supplier-only sync, usable standalone (via /sync-suppliers) or as
# sync_all's Step 4/4. No date range needed - this is a full master-data
# fetch/upsert every time, same as the standalone endpoint always did.
# =================================================================
async def sync_suppliers(run_id: str | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:8]
    print(f"[SUPPLIER_SYNC:{run_id}] Supplier sync starting...")
    try:
        suppliers = await supplier_fetch_all()
        rows = supplier_map_to_rows(suppliers)

        async def _upsert(conn):
            return await supplier_upsert_rows(conn, rows)

        total, inserted, updated = await _db_retry(_upsert, label=f"SUPPLIER_SYNC:{run_id}")

        result = {
            "status": "success",
            "suppliers_fetched": len(suppliers),
            "total_records": total,
            "inserted_records": inserted,
            "updated_records": updated,
        }
        print(f"[SUPPLIER_SYNC:{run_id}] Supplier sync  SUCCESS "
              f"(fetched={len(suppliers)}, total={total}, inserted={inserted}, updated={updated})")
        return result
    except Exception as exc:
        print(f"[SUPPLIER_SYNC:{run_id}] Supplier sync  FAILED: {exc}")
        logger.exception("Supplier sync failed")
        return {"status": "error", "message": str(exc)}


@router.post("/sync-suppliers")
async def sync_suppliers_endpoint():
    result = await sync_suppliers()
    status_code = 200 if result["status"] == "success" else 502
    return JSONResponse(status_code=status_code, content=result)


# =================================================================
# Supplier master data: bulk-load from an uploaded Excel file
# =================================================================
_BUSINESS_PARTNER_RE = re.compile(r"^(.*)\((\d+)\)\s*$")


def parse_business_partner(value: str) -> tuple[str, str] | None:
    """Splits 'FIVE PACHA UNIVERSE HOLDING (DIFC) LTD (1000)' into
    ('FIVE PACHA UNIVERSE HOLDING (DIFC) LTD', '1000'). Only the trailing
    (<digits>) is treated as the id, so other parenthesised text in the
    name (like '(DIFC)') is left alone. Returns None if the value doesn't
    end in (<digits>)."""
    if not value:
        return None
    match = _BUSINESS_PARTNER_RE.match(value.strip())
    if not match:
        return None
    supplier_name = match.group(1).strip()
    supplier_id = match.group(2).strip()
    if not supplier_name or not supplier_id:
        return None
    return supplier_name, supplier_id


@router.post("/upload-suppliers-excel")
async def upload_suppliers_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=422, detail="Only .xlsx/.xls files are supported")

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read Excel file: {exc}")

    if "Business Partner" not in df.columns:
        raise HTTPException(
            status_code=422,
            detail=f"Excel file must contain a 'Business Partner' column. Found: {list(df.columns)}",
        )

    rows_by_id: dict[str, dict] = {}
    unparsed: list[str] = []

    for raw_value in df["Business Partner"]:
        if pd.isna(raw_value):
            continue
        parsed = parse_business_partner(str(raw_value))
        if parsed is None:
            unparsed.append(str(raw_value))
            continue
        supplier_name, supplier_id = parsed
        rows_by_id[supplier_id] = {"supplier_id": supplier_id, "supplier_name": supplier_name}

    rows = list(rows_by_id.values())
    print(f"[SUPPLIER_EXCEL] file={file.filename} excel_rows={len(df)} "
          f"parsed={len(rows)} unparsed={len(unparsed)}")

    conn = await engine.connect()
    try:
        total, inserted, updated = await supplier_upsert_rows(conn, rows)
    finally:
        await conn.close()

    return {
        "status": "success",
        "excel_rows": len(df),
        "parsed_rows": len(rows),
        "unparsed_rows": len(unparsed),
        "unparsed_values": unparsed,
        "total_records": total,
        "inserted_records": inserted,
        "updated_records": updated,
    }