from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import date, datetime, timedelta
import sqlite3
import json
import math
import secrets
import hashlib
import hmac
import base64
import re as pyre
import qrcode
import qrcode.image.svg
from typing import Optional
import sys
import io
import zipfile
from xml.sax.saxutils import escape as xml_escape

# Some regression tests load app.py directly via importlib rather than as a normal script.
# Ensure the build directory is importable so the shared storage helper is available.
_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))
from storage import DB_PATH

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="ERP v2 Demo", version="0.8.8-demo")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

# Prototype builds reuse localhost and the same /static filenames across versions.
# Disable browser caching here so a freshly downloaded build can never execute
# JavaScript from an older ERP prototype. Production will use fingerprinted assets.
@app.middleware("http")
async def prototype_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def ensure_v05_schema():
    """Small forward-only migration for the v0.5 prototype."""
    con = db()
    try:
        customer_cols = {r[1] for r in con.execute("PRAGMA table_info(customers)")}
        for name, ddl in [
            ("source", "TEXT NOT NULL DEFAULT 'excel'"),
            ("user_modified", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ]:
            if name not in customer_cols:
                con.execute(f"ALTER TABLE customers ADD COLUMN {name} {ddl}")

        article_cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
        for name, ddl in [
            ("source", "TEXT NOT NULL DEFAULT 'excel'"),
            ("user_modified", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ]:
            if name not in article_cols:
                con.execute(f"ALTER TABLE articles ADD COLUMN {name} {ddl}")

        con.execute(
            """CREATE TABLE IF NOT EXISTS master_data_events(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 entity_type TEXT NOT NULL,
                 entity_key TEXT NOT NULL,
                 action TEXT NOT NULL,
                 message TEXT NOT NULL,
                 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_master_events_entity ON master_data_events(entity_type,entity_key,created_at)")

        # Groundwork for the future unique-label scanning workflow.  These tables
        # are intentionally dormant in v0.5; no production scan UI writes to them yet.
        con.execute(
            """CREATE TABLE IF NOT EXISTS label_instances(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 label_uid TEXT NOT NULL UNIQUE,
                 order_id INTEGER NOT NULL,
                 order_line_id INTEGER,
                 article_no INTEGER NOT NULL,
                 sequence_no INTEGER NOT NULL,
                 lifecycle_state TEXT NOT NULL DEFAULT 'CREATED',
                 active INTEGER NOT NULL DEFAULT 1,
                 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                 printed_at TEXT,
                 produced_at TEXT,
                 unloading_at TEXT,
                 FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
               )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS label_scan_events(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 label_instance_id INTEGER NOT NULL,
                 event_type TEXT NOT NULL,
                 location TEXT,
                 device_code TEXT,
                 operator TEXT,
                 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY(label_instance_id) REFERENCES label_instances(id) ON DELETE CASCADE
               )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS scan_devices(
                 device_code TEXT PRIMARY KEY,
                 name TEXT NOT NULL,
                 location_type TEXT NOT NULL,
                 station_id INTEGER,
                 active INTEGER NOT NULL DEFAULT 1
               )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_label_instances_order ON label_instances(order_id,article_no)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_label_scan_events_label ON label_scan_events(label_instance_id,created_at)")
        con.execute("UPDATE customers SET created_at=COALESCE(created_at,CURRENT_TIMESTAMP),updated_at=COALESCE(updated_at,CURRENT_TIMESTAMP)")
        con.execute("UPDATE articles SET created_at=COALESCE(created_at,CURRENT_TIMESTAMP),updated_at=COALESCE(updated_at,CURRENT_TIMESTAMP)")
        con.commit()
    finally:
        con.close()


ensure_v05_schema()


# ============================================================
# v0.7 — USERS / OPERATORS / DEVICES / AUDIT
# ============================================================

SESSION_COOKIE = "erp_v2_session"
SESSION_DAYS = 14
PBKDF2_ITERATIONS = 240000

ROLE_PERMISSIONS = {
    "admin": ["*"],
    "office": [
        "dashboard", "order_read", "order_edit", "history", "customer_read", "master_edit",
        "article_read", "pricing_read", "pricing_edit", "purchasing_read", "purchasing_edit",
        "production_read", "production_edit", "transport_read", "transport_edit",
        "labels", "fortnox_read", "statistics_read", "audit_read"
    ],
    "production": [
        "dashboard", "history", "production_read", "production_edit", "labels", "statistics_read"
    ],
    "loading": [
        "dashboard", "history", "production_read", "transport_read", "labels", "statistics_read"
    ],
    "viewer": ["dashboard", "history", "statistics_read"],
}

ROLE_LABELS = {
    "admin": "Admin",
    "office": "Kontor",
    "production": "Produktion",
    "loading": "Utlastning",
    "viewer": "Läsbehörighet",
}


def ensure_v07_schema():
    con = db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL,
          email TEXT,
          account_type TEXT NOT NULL DEFAULT 'office',
          role TEXT NOT NULL DEFAULT 'viewer',
          password_hash TEXT,
          pin_hash TEXT,
          badge_code TEXT UNIQUE,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_sessions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          expires_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id,expires_at);
        CREATE TABLE IF NOT EXISTS audit_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          username TEXT,
          display_name TEXT,
          action TEXT NOT NULL,
          entity_type TEXT,
          entity_key TEXT,
          message TEXT NOT NULL,
          details_json TEXT,
          ip_address TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id,created_at DESC);
        """)
        # scan_devices already exists from the label groundwork. Keep it as the
        # physical device registry for production/unloading.
        con.commit()
    finally:
        con.close()


ensure_v07_schema()


# ============================================================
# v0.7.6 — PURCHASING MASTER DATA, PART 1: SUPPLIERS
# ============================================================

def ensure_v076_supplier_schema():
    con = db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS suppliers(
          supplier_no INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          org_no TEXT,
          contact_name TEXT,
          email TEXT,
          phone TEXT,
          address TEXT,
          postal_address TEXT,
          payment_terms TEXT,
          delivery_terms TEXT,
          default_lead_time_days REAL,
          min_order_value REAL,
          currency TEXT NOT NULL DEFAULT 'SEK',
          notes TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          source TEXT NOT NULL DEFAULT 'erp_v2',
          user_modified INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers(name);
        CREATE INDEX IF NOT EXISTS idx_suppliers_active ON suppliers(active,supplier_no);
        """)
        con.commit()
    finally:
        con.close()


ensure_v076_supplier_schema()


# ============================================================
# v0.7.7 — PURCHASING MASTER DATA, PART 2: SUPPLIER/MATERIAL INFO RECORDS
# ============================================================

def ensure_v077_info_record_schema():
    con = db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS supplier_info_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          supplier_no INTEGER NOT NULL,
          component_code TEXT NOT NULL,
          supplier_material_no TEXT,
          purchase_price REAL,
          currency TEXT NOT NULL DEFAULT 'SEK',
          price_unit_qty REAL NOT NULL DEFAULT 1,
          purchase_unit TEXT,
          min_order_qty REAL,
          order_multiple_qty REAL,
          lead_time_days REAL,
          source_status TEXT NOT NULL DEFAULT 'APPROVED',
          valid_from TEXT,
          valid_to TEXT,
          notes TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(supplier_no, component_code),
          FOREIGN KEY(supplier_no) REFERENCES suppliers(supplier_no),
          FOREIGN KEY(component_code) REFERENCES components(code)
        );
        CREATE INDEX IF NOT EXISTS idx_info_records_supplier ON supplier_info_records(supplier_no,active);
        CREATE INDEX IF NOT EXISTS idx_info_records_material ON supplier_info_records(component_code,active);
        CREATE INDEX IF NOT EXISTS idx_info_records_status ON supplier_info_records(source_status,active);

        CREATE TABLE IF NOT EXISTS supplier_info_record_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          info_record_id INTEGER NOT NULL,
          action TEXT NOT NULL,
          supplier_no INTEGER NOT NULL,
          component_code TEXT NOT NULL,
          snapshot_json TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(info_record_id) REFERENCES supplier_info_records(id)
        );
        CREATE INDEX IF NOT EXISTS idx_info_record_events_record
          ON supplier_info_record_events(info_record_id,created_at DESC);
        """)
        con.commit()
    finally:
        con.close()


ensure_v077_info_record_schema()


# ============================================================
# v0.7.8 — PURCHASING HISTORY (ACTUAL PURCHASE SNAPSHOTS)
# ============================================================

def ensure_v078_purchase_history_schema():
    con = db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS purchase_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          supplier_no INTEGER NOT NULL,
          component_code TEXT NOT NULL,
          purchase_date TEXT NOT NULL,
          quantity REAL,
          purchase_unit TEXT,
          purchase_price REAL,
          currency TEXT NOT NULL DEFAULT 'SEK',
          price_unit_qty REAL NOT NULL DEFAULT 1,
          supplier_material_no TEXT,
          external_reference TEXT,
          source TEXT NOT NULL DEFAULT 'MANUAL',
          notes TEXT,
          created_by TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(supplier_no) REFERENCES suppliers(supplier_no),
          FOREIGN KEY(component_code) REFERENCES components(code)
        );
        CREATE INDEX IF NOT EXISTS idx_purchase_history_material
          ON purchase_history(component_code,purchase_date DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_purchase_history_supplier
          ON purchase_history(supplier_no,purchase_date DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_purchase_history_date
          ON purchase_history(purchase_date DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_purchase_history_reference
          ON purchase_history(external_reference);
        """)
        con.commit()
    finally:
        con.close()


ensure_v078_purchase_history_schema()


# ============================================================
# v0.7.9 — PURCHASING MATERIAL MASTER
# ============================================================

MATERIAL_TYPES = {
    "RETURN_PACKAGING": "Returemballage",
    "PACKAGING": "Wellpapp / emballage",
    "FILM": "Plastfilm",
    "LABEL": "Etikett",
    "PALLET": "Pall",
    "CONSUMABLE": "Förbrukningsmaterial",
    "RAW_MATERIAL": "Råvara",
    "SERVICE": "Tjänst / kostnad",
    "OTHER": "Övrigt",
}

# Known legacy purchase-number -> goods-receipt-number pairs from the current Excel ERP.
# These are migration aids only. The future ERP should use the canonical material number
# for both PO and goods receipt.
LEGACY_PURCHASE_ALIASES = {
    "980": "800",  # Inköp RHP -> RHP Svartpall
    "981": "801",  # Inköp Stor Svart RL
    "982": "803",  # Inköp Liten Svart
    "983": "805",  # Inköp Liten Röd
    "984": "806",  # Inköp Stor Grön
    "985": "804",  # Inköp Gråpall
}

# High-confidence mappings from the old Excel BOM-column identities to proper
# purchasing material numbers. Uncertain links are deliberately left unmapped.
LEGACY_BOM_CANONICAL = {
    "W":"800","X":"801","Y":"803","Z":"805","AA":"806","AB":"802","AC":"807","AD":"880",
    "AE":"810","AF":"813","AG":"811","AH":"820","AI":"821","AJ":"823",
    "AS":"800","AT":"801","AW":"805","AX":"803","AZ":"806","BA":"804",
    "CY":"890","CZ":"891","DA":"892","DB":"893","DC":"894","DD":"895",
}


def _material_type_from_name(name: str, article_no: int | None = None):
    n = (name or "").casefold()
    if any(x in n for x in ["frakt", "transport"]): return "SERVICE"
    if "etikett" in n: return "LABEL"
    if "film" in n or "kuddpåse" in n or "påse" in n: return "FILM"
    if any(x in n for x in ["kartong", "expo", "lock", "papper", "låda", "embalage", "emballage"]): return "PACKAGING"
    if any(x in n for x in ["pall", "rhp", " rl", "hörnstöd"]): return "RETURN_PACKAGING"
    if article_no is not None and 900 <= article_no <= 939: return "RAW_MATERIAL"
    return "OTHER"


def _material_review_defaults(article_no: int, name: str):
    n=(name or "").casefold()
    helper_words=["fel datum","order ","backning","rätt vikt","makulerad","bonus","kampanj","ändring av order",
                  "invägd vikt","framsorterad vikt","frånsortering","isac test"]
    if any(x in n for x in helper_words) or (article_no >= 1070 and "pris" in n):
        return 0,0,"REVIEW"
    if article_no in range(960,968):
        return 0,0,"REVIEW"
    if str(article_no) in LEGACY_PURCHASE_ALIASES:
        return 1,1,"LEGACY_ALIAS"
    if 800 <= article_no <= 895:
        return 1,1,"OK"
    # Keep other 751+ records visible. They are migration candidates, not trusted
    # purchasing master data until reviewed.
    return 1, 0 if _material_type_from_name(name, article_no)=="SERVICE" else 1, "REVIEW"


def ensure_v079_material_master_schema():
    con=db()
    try:
        cols={r[1] for r in con.execute("PRAGMA table_info(components)")}
        additions=[
            ("material_type", "TEXT NOT NULL DEFAULT 'OTHER'"),
            ("purchase_unit", "TEXT"),
            ("purchase_to_base_factor", "REAL NOT NULL DEFAULT 1"),
            ("purchasable", "INTEGER NOT NULL DEFAULT 1"),
            ("stock_managed", "INTEGER NOT NULL DEFAULT 0"),
            ("bom_usable", "INTEGER NOT NULL DEFAULT 1"),
            ("active", "INTEGER NOT NULL DEFAULT 1"),
            ("source", "TEXT NOT NULL DEFAULT 'legacy_bom'"),
            ("user_modified", "INTEGER NOT NULL DEFAULT 0"),
            ("legacy_article_no", "INTEGER"),
            ("legacy_purchase_article_no", "INTEGER"),
            ("supplier_article_no", "TEXT"),
            ("canonical_material_code", "TEXT"),
            ("review_status", "TEXT NOT NULL DEFAULT 'LEGACY_BOM'"),
            ("notes", "TEXT"),
            ("legacy_units_per_pallet", "REAL"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
        ]
        for name,ddl in additions:
            if name not in cols:
                con.execute(f"ALTER TABLE components ADD COLUMN {name} {ddl}")

        # Existing BOM columns remain valid legacy aliases. Where we know the
        # canonical modern purchasing number with high confidence, store the link.
        con.execute("UPDATE components SET source=COALESCE(NULLIF(source,''),'legacy_bom'), active=COALESCE(active,1), purchasable=COALESCE(purchasable,1), bom_usable=1, created_at=COALESCE(created_at,CURRENT_TIMESTAMP), updated_at=COALESCE(updated_at,CURRENT_TIMESTAMP)")
        for legacy, canonical in LEGACY_BOM_CANONICAL.items():
            con.execute("UPDATE components SET canonical_material_code=?,review_status='LEGACY_BOM' WHERE code=? AND user_modified=0", (canonical,legacy))

        # Seed a proper material master from the current Excel article master.
        # Everything >= 751 is kept visible as migration data; trusted packaging
        # ranges are marked OK while ambiguous helper records are marked REVIEW.
        rows=con.execute("""SELECT article_no,name,unit,supplier_article_no,units_per_pallet
                            FROM articles WHERE article_no>=751 ORDER BY article_no""").fetchall()
        for r in rows:
            no=int(r["article_no"]); code=str(no); name=r["name"] or f"Material {no}"
            purch,stock,review=_material_review_defaults(no,name)
            mtype=_material_type_from_name(name,no)
            purchase_unit=(r["unit"] or "").strip()
            factor=1.0
            low=name.casefold()
            if "st/enhet" in low and r["units_per_pallet"]:
                purchase_unit="ENHET"; factor=float(r["units_per_pallet"])
            elif "st/pall" in low and r["units_per_pallet"]:
                purchase_unit="PALL"; factor=float(r["units_per_pallet"])
            canonical=LEGACY_PURCHASE_ALIASES.get(code)
            existing=con.execute("SELECT user_modified FROM components WHERE code=?",(code,)).fetchone()
            if existing and existing["user_modified"]:
                continue
            con.execute("""INSERT INTO components(
                    code,name,category,unit,material_type,purchase_unit,purchase_to_base_factor,
                    purchasable,stock_managed,bom_usable,active,source,user_modified,legacy_article_no,
                    supplier_article_no,canonical_material_code,review_status,legacy_units_per_pallet,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,unit=excluded.unit,material_type=excluded.material_type,
                    purchase_unit=excluded.purchase_unit,purchase_to_base_factor=excluded.purchase_to_base_factor,
                    purchasable=excluded.purchasable,stock_managed=excluded.stock_managed,bom_usable=excluded.bom_usable,
                    active=1,source='excel_article',legacy_article_no=excluded.legacy_article_no,
                    supplier_article_no=excluded.supplier_article_no,canonical_material_code=excluded.canonical_material_code,
                    review_status=excluded.review_status,legacy_units_per_pallet=excluded.legacy_units_per_pallet,
                    updated_at=CURRENT_TIMESTAMP
                WHERE components.user_modified=0""",
                (code,name,"Inköpsmaterial",r["unit"] or "",mtype,purchase_unit,factor,purch,stock,1,1,
                 "excel_article",0,no,r["supplier_article_no"] or "",canonical,review,r["units_per_pallet"]))

        for alias, canonical in LEGACY_PURCHASE_ALIASES.items():
            con.execute("UPDATE components SET legacy_purchase_article_no=CAST(code AS INTEGER),canonical_material_code=?,review_status='LEGACY_ALIAS' WHERE code=? AND user_modified=0",(canonical,alias))
            # Store the old purchasing number on the canonical material for reference.
            con.execute("UPDATE components SET legacy_purchase_article_no=? WHERE code=? AND (legacy_purchase_article_no IS NULL OR user_modified=0)",(int(alias),canonical))

        con.execute("CREATE INDEX IF NOT EXISTS idx_components_purchasing ON components(purchasable,active,material_type,code)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_components_canonical ON components(canonical_material_code)")
        con.commit()
    finally:
        con.close()


ensure_v079_material_master_schema()


# ============================================================
# v0.7.10 — CANONICAL MATERIAL + INVENTORY FOUNDATION
# ============================================================

def _resolve_canonical_material(con, code: str):
    """Return (canonical_row, requested_alias_row_or_none)."""
    key=(code or "").strip().upper()
    row=con.execute("SELECT * FROM components WHERE code=?",(key,)).fetchone()
    if not row:
        return None, None
    canonical=(row["canonical_material_code"] or "").strip().upper()
    if canonical:
        target=con.execute("SELECT * FROM components WHERE code=?",(canonical,)).fetchone()
        if target:
            return target, row
    return row, None


def _inventory_on_hand(con, code: str):
    row=con.execute("SELECT COALESCE(SUM(quantity_base),0) qty, COUNT(*) cnt FROM inventory_movements WHERE component_code=?",(code,)).fetchone()
    return float(row["qty"] or 0), int(row["cnt"] or 0)


def ensure_v0710_inventory_schema():
    con=db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS inventory_movements(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          component_code TEXT NOT NULL,
          movement_type TEXT NOT NULL,
          quantity_base REAL NOT NULL,
          reference_type TEXT,
          reference_no TEXT,
          note TEXT,
          created_by TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(component_code) REFERENCES components(code)
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_material
          ON inventory_movements(component_code,created_at DESC,id DESC);
        """)

        # Legacy purchasing numbers and Excel BOM-column identities are lookup aliases,
        # not separate stock objects. Keep them for migration/search only.
        con.execute("""UPDATE components
                       SET purchasable=0,stock_managed=0,bom_usable=0
                       WHERE canonical_material_code IS NOT NULL
                         AND canonical_material_code<>''
                         AND user_modified=0""")

        # Any prototype purchasing history that was entered against a legacy alias is
        # moved to its canonical physical material. History itself is preserved.
        aliases=con.execute("SELECT code,canonical_material_code FROM components WHERE canonical_material_code IS NOT NULL AND canonical_material_code<>''").fetchall()
        for a in aliases:
            old=a["code"]; new=a["canonical_material_code"]
            con.execute("UPDATE purchase_history SET component_code=? WHERE component_code=?",(new,old))
            info_rows=con.execute("SELECT id,supplier_no FROM supplier_info_records WHERE component_code=?",(old,)).fetchall()
            for ir in info_rows:
                duplicate=con.execute("SELECT id FROM supplier_info_records WHERE supplier_no=? AND component_code=? AND id<>?",(ir["supplier_no"],new,ir["id"])).fetchone()
                if duplicate:
                    con.execute("UPDATE supplier_info_records SET active=0,notes=TRIM(COALESCE(notes,'') || ' Legacy alias ' || ? || ' ersatt av ' || ?),updated_at=CURRENT_TIMESTAMP WHERE id=?",(old,new,ir["id"]))
                else:
                    con.execute("UPDATE supplier_info_records SET component_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new,ir["id"]))
        con.commit()
    finally:
        con.close()


ensure_v0710_inventory_schema()


# ============================================================
# v0.8.0 — PRODUCTION FOUNDATION + EXCEL MAIN REFERENCE STOCK
# ============================================================

def ensure_v080_production_schema():
    con=db()
    try:
        cols={r[1] for r in con.execute("PRAGMA table_info(components)")}
        for name,ddl in [
            ("excel_main_stock","REAL"),
            ("excel_main_reorder_point","REAL"),
            ("excel_main_moq","REAL"),
            ("excel_main_unit_price","REAL"),
            ("excel_main_on_order","TEXT"),
            ("excel_main_row","INTEGER"),
            ("excel_main_synced_at","TEXT"),
        ]:
            if name not in cols:
                con.execute(f"ALTER TABLE components ADD COLUMN {name} {ddl}")

        sr_cols={r[1] for r in con.execute("PRAGMA table_info(station_requirements)")}
        for name,ddl in [
            ("source_basis","TEXT NOT NULL DEFAULT 'ORDER_LINES'"),
            ("raw_pallets","REAL"),
        ]:
            if name not in sr_cols:
                con.execute(f"ALTER TABLE station_requirements ADD COLUMN {name} {ddl}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_station_req_station ON station_requirements(station_id,ready,order_id)")

        # v0.8.1 pallet label lifecycle metadata. Existing dormant label_instances
        # table is extended rather than replaced so scanner history remains stable.
        li_cols={r[1] for r in con.execute("PRAGMA table_info(label_instances)")}
        for name,ddl in [
            ("total_sequences","INTEGER NOT NULL DEFAULT 1"),
            ("qty_on_label","REAL"),
            ("total_qty","REAL"),
            ("station_id","INTEGER"),
            ("template_name","TEXT NOT NULL DEFAULT 'STANDARD'"),
            ("printer_name","TEXT NOT NULL DEFAULT 'Intermec PX4i (300 dpi)'"),
            ("print_count","INTEGER NOT NULL DEFAULT 0"),
        ]:
            if name not in li_cols:
                con.execute(f"ALTER TABLE label_instances ADD COLUMN {name} {ddl}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_label_instances_line ON label_instances(order_line_id,sequence_no)")
        con.commit()
    finally:
        con.close()


def _operational_order_predicate(alias='o'):
    return f"EXISTS(SELECT 1 FROM order_lines opl WHERE opl.order_id={alias}.id AND opl.ordered_qty>0)"


def _ensure_station_requirements(con, start_date: str | None = None, end_date: str | None = None):
    """Create/update S1-S4 requirements from order lines without changing READY state.

    Mirrors Excel order!AS42:AS45: ROUNDUP(SUMIF(station,pallets),0).
    """
    where=[_operational_order_predicate('o')]
    args=[]
    if start_date:
        where.append("o.delivery_date>=?"); args.append(start_date)
    if end_date:
        where.append("o.delivery_date<=?"); args.append(end_date)
    orders=con.execute(f"SELECT o.id,o.source FROM orders o WHERE {' AND '.join(where)}",args).fetchall()
    for o in orders:
        for station_id in range(1,5):
            raw=con.execute("""SELECT COALESCE(SUM(CASE WHEN l.pallets>0 THEN l.pallets ELSE 0 END),0) p
                               FROM order_lines l WHERE l.order_id=? AND l.station_id=? AND l.ordered_qty>0""",
                            (o['id'],station_id)).fetchone()['p']
            raw=float(raw or 0)
            if raw <= 1e-12:
                continue
            required=float(math.ceil(raw-1e-12))
            source_basis='EXCEL_IMPORT' if o['source']=='legacy' else 'ERP_ORDER'
            existing=con.execute("SELECT 1 FROM station_requirements WHERE order_id=? AND station_id=?",(o['id'],station_id)).fetchone()
            if existing:
                con.execute("""UPDATE station_requirements SET required_pallets=?,raw_pallets=?,source_basis=?,updated_at=CURRENT_TIMESTAMP
                               WHERE order_id=? AND station_id=?""",(required,raw,source_basis,o['id'],station_id))
            else:
                con.execute("""INSERT INTO station_requirements(order_id,station_id,required_pallets,ready,source_basis,raw_pallets)
                               VALUES(?,?,?,0,?,?)""",(o['id'],station_id,required,source_basis,raw))
    con.commit()


ensure_v080_production_schema()


# ============================================================
# v0.7.4 — PRICING CONDITIONS
# ============================================================

def ensure_v074_pricing_schema():
    con = db()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS pricing_conditions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          condition_type TEXT NOT NULL,
          customer_no INTEGER NOT NULL,
          article_no INTEGER NOT NULL,
          price REAL NOT NULL,
          valid_from TEXT,
          valid_to TEXT,
          name TEXT,
          notes TEXT,
          priority INTEGER NOT NULL DEFAULT 100,
          active INTEGER NOT NULL DEFAULT 1,
          source TEXT NOT NULL DEFAULT 'erp_v2',
          source_key TEXT,
          created_by TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(customer_no) REFERENCES customers(customer_no),
          FOREIGN KEY(article_no) REFERENCES articles(article_no)
        );
        CREATE INDEX IF NOT EXISTS idx_pricing_lookup
          ON pricing_conditions(customer_no,article_no,condition_type,active,valid_from,valid_to);
        CREATE INDEX IF NOT EXISTS idx_pricing_source ON pricing_conditions(source,condition_type);
        CREATE TABLE IF NOT EXISTS pricing_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          condition_id INTEGER,
          action TEXT NOT NULL,
          condition_type TEXT NOT NULL,
          customer_no INTEGER NOT NULL,
          article_no INTEGER NOT NULL,
          old_price REAL,
          new_price REAL,
          old_valid_from TEXT,
          old_valid_to TEXT,
          new_valid_from TEXT,
          new_valid_to TEXT,
          actor TEXT,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_pricing_events_created ON pricing_events(created_at DESC,id DESC);
        """)

        line_cols = {r[1] for r in con.execute("PRAGMA table_info(order_lines)")}
        for name, ddl in [
            ("price_condition_id", "INTEGER"),
            ("price_date", "TEXT"),
            ("price_override_reason", "TEXT"),
        ]:
            if name not in line_cols:
                con.execute(f"ALTER TABLE order_lines ADD COLUMN {name} {ddl}")

        # First v0.7.4 run: migrate the current Excel price masters into real
        # condition records. Zero prices in prislista are placeholders and are
        # intentionally not treated as valid sales prices.
        n = con.execute("SELECT COUNT(*) n FROM pricing_conditions").fetchone()["n"]
        if n == 0:
            con.execute("""
              INSERT INTO pricing_conditions(
                condition_type,customer_no,article_no,price,valid_from,valid_to,
                name,priority,active,source,source_key,created_by
              )
              SELECT 'STANDARD',cp.customer_no,cp.article_no,cp.price,NULL,NULL,
                     'Kundpris',100,1,'excel',
                     'prislista:'||cp.customer_no||':'||cp.article_no,'Excel-import'
              FROM customer_prices cp JOIN customers c ON c.customer_no=cp.customer_no JOIN articles a ON a.article_no=cp.article_no WHERE cp.price>0
            """)
            con.execute("""
              INSERT INTO pricing_conditions(
                condition_type,customer_no,article_no,price,valid_from,valid_to,
                name,priority,active,source,source_key,created_by
              )
              SELECT 'CAMPAIGN',cp.customer_no,cp.article_no,cp.price,cp.valid_from,cp.valid_to,
                     'Importerad kampanj',200,1,'excel','kampanjpris:'||cp.id,'Excel-import'
              FROM campaign_prices cp JOIN customers c ON c.customer_no=cp.customer_no JOIN articles a ON a.article_no=cp.article_no WHERE cp.price>0
            """)
        con.commit()
    finally:
        con.close()


ensure_v074_pricing_schema()


def _hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_secret(secret: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_payload(row):
    if row is None:
        return None
    perms = ROLE_PERMISSIONS.get(row["role"], [])
    return {
        "id": row["id"], "username": row["username"], "display_name": row["display_name"],
        "email": row["email"] or "", "account_type": row["account_type"], "role": row["role"],
        "role_label": ROLE_LABELS.get(row["role"], row["role"]), "active": bool(row["active"]),
        "permissions": perms,
    }


def _has_permission(user, permission: str) -> bool:
    if not user:
        return False
    perms = ROLE_PERMISSIONS.get(user["role"], [])
    return "*" in perms or permission in perms


def _current_user_from_request(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    con = db()
    row = con.execute(
        """SELECT u.* FROM auth_sessions s
           JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at>CURRENT_TIMESTAMP AND u.active=1""",
        (_token_hash(token),),
    ).fetchone()
    if row:
        con.execute("UPDATE auth_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE token_hash=?", (_token_hash(token),))
        con.commit()
    con.close()
    return row


def _create_session(con, user_id: int):
    token = secrets.token_urlsafe(36)
    expires = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT INTO auth_sessions(user_id,token_hash,expires_at) VALUES(?,?,?)",
        (user_id, _token_hash(token), expires),
    )
    return token



def _user_display_name(user):
    if not user:
        return ""
    try:
        return user["display_name"] or ""
    except Exception:
        try:
            return user.get("display_name", "") or ""
        except Exception:
            return ""


def _write_audit(con, user, action: str, entity_type: str, entity_key: str, message: str, details=None, ip=""):
    con.execute(
        """INSERT INTO audit_events(user_id,username,display_name,action,entity_type,entity_key,message,details_json,ip_address)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            user["id"] if user else None,
            user["username"] if user else "system",
            user["display_name"] if user else "System",
            action, entity_type, str(entity_key or ""), message,
            json.dumps(details or {}, ensure_ascii=False), ip or "",
        ),
    )


def _permission_for_request(method: str, path: str):
    # Admin-only configuration.
    if path.startswith("/api/users") or path.startswith("/api/devices"):
        return "admin"
    if path.startswith("/api/audit"):
        return "audit_read"
    if path.startswith("/api/statistics"):
        return "statistics_read"
    if path.startswith("/api/pricing"):
        return "pricing_edit" if method in {"POST", "PUT", "DELETE", "PATCH"} else "pricing_read"
    if path.startswith("/api/suppliers") or path.startswith("/api/purchasing"):
        return "purchasing_edit" if method in {"POST", "PUT", "DELETE", "PATCH"} else "purchasing_read"
    # Mutations.
    if method in {"POST", "PUT", "DELETE"} and (path.startswith("/api/customers") or path.startswith("/api/articles")):
        return "master_edit"
    if method == "POST" and path == "/api/orders":
        return "order_edit"
    if method == "PATCH" and path.startswith("/api/orders/"):
        return "order_edit"
    if method == "POST" and path.startswith("/api/production/"):
        return "production_edit"
    if method == "POST" and path.startswith("/api/labels/"):
        return "labels"
    if method == "PATCH" and path.startswith("/api/transport/"):
        return "transport_edit"
    return None


@app.middleware("http")
async def erp_auth_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    exempt = (
        path == "/" or path.startswith("/static/") or
        path in {"/api/auth/me", "/api/auth/setup", "/api/auth/login", "/api/auth/operator-login", "/api/auth/logout",
                 "/api/auth/local-recovery", "/api/auth/local-reset"}
    )
    user = None
    if path.startswith("/api/") and not exempt:
        user = _current_user_from_request(request)
        if not user:
            return JSONResponse({"detail": "Du måste logga in."}, status_code=401)
        request.state.user = user
        permission = _permission_for_request(method, path)
        if permission == "admin" and user["role"] != "admin":
            return JSONResponse({"detail": "Endast admin har behörighet till detta."}, status_code=403)
        if permission and permission != "admin" and not _has_permission(user, permission):
            return JSONResponse({"detail": "Du saknar behörighet för den här åtgärden."}, status_code=403)

    response = await call_next(request)

    # Central audit safety net for successful mutations. This deliberately records
    # actions even before every individual module has richer event descriptions.
    if user is not None and method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
        if not path.startswith("/api/auth/"):
            con = db()
            try:
                _write_audit(
                    con, user, method, "API", path,
                    f"{user['display_name']} utförde {method} {path}",
                    ip=request.client.host if request.client else "",
                )
                con.commit()
            finally:
                con.close()
    return response


class FirstAdminIn(BaseModel):
    username: str
    display_name: str
    password: str
    email: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class OperatorLoginIn(BaseModel):
    username: str
    pin: str


class LocalResetIn(BaseModel):
    user_id: Optional[int] = None
    password: str
    username: str = "admin"
    display_name: str = "Admin"


class UserIn(BaseModel):
    username: str
    display_name: str
    email: str = ""
    account_type: str = "office"
    role: str = "viewer"
    secret: str = ""
    badge_code: str = ""
    active: bool = True


class DeviceIn(BaseModel):
    device_code: str
    name: str
    location_type: str
    station_id: Optional[int] = None
    active: bool = True


@app.get("/api/auth/me")
def auth_me(request: Request):
    con = db()
    user_count = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    con.close()
    if user_count == 0:
        return {"authenticated": False, "setup_required": True}
    user = _current_user_from_request(request)
    return {"authenticated": bool(user), "setup_required": False, "user": _user_payload(user) if user else None}


def _require_local_request(request: Request):
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Lokal kontoåterställning är endast tillgänglig på ERP-datorn.")


@app.get("/api/auth/local-recovery")
def auth_local_recovery(request: Request):
    _require_local_request(request)
    con = db()
    try:
        rows = con.execute(
            """SELECT id,username,display_name,account_type,role,active,created_at,last_login_at
               FROM users ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END, active DESC, display_name COLLATE NOCASE"""
        ).fetchall()
        return {
            "ok": True,
            "database": str(DB_PATH),
            "users": [dict(r) for r in rows],
            "office_users": [dict(r) for r in rows if r["account_type"] == "office"],
        }
    finally:
        con.close()


@app.post("/api/auth/local-reset")
def auth_local_reset(payload: LocalResetIn, response: Response, request: Request):
    _require_local_request(request)
    if len(payload.password) < 8:
        raise HTTPException(400, "Det nya lösenordet måste vara minst 8 tecken.")
    con = db()
    try:
        user = None
        if payload.user_id is not None:
            user = con.execute("SELECT * FROM users WHERE id=?", (payload.user_id,)).fetchone()
            if not user:
                raise HTTPException(404, "Användaren finns inte i den aktiva databasen.")
            if user["account_type"] != "office":
                raise HTTPException(400, "Det kontot är ett produktions/PIN-konto och kan inte återställas som kontorsinloggning här.")
            con.execute(
                "UPDATE users SET password_hash=?,active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (_hash_secret(payload.password), user["id"]),
            )
            action = "LOCAL_PASSWORD_RESET"
            message = f"Lokalt lösenord återställdes för {user['username']}"
            user_id = user["id"]
        else:
            username = (payload.username or "admin").strip()
            display_name = (payload.display_name or "Admin").strip()
            if len(username) < 2 or len(display_name) < 2:
                raise HTTPException(400, "Användarnamn och namn krävs.")
            existing = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
            if existing:
                raise HTTPException(409, "Användarnamnet finns redan. Välj kontot i listan i stället.")
            cur = con.execute(
                """INSERT INTO users(username,display_name,account_type,role,password_hash,active)
                   VALUES(?,?,'office','admin',?,1)""",
                (username, display_name, _hash_secret(payload.password)),
            )
            user_id = cur.lastrowid
            action = "LOCAL_ADMIN_CREATED"
            message = f"Nytt lokalt admin-konto skapades: {username}"
        user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        token = _create_session(con, user_id)
        _write_audit(con, user, action, "USER", user_id, message, ip=request.client.host if request.client else "")
        con.commit()
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS*86400)
        return {"ok": True, "user": _user_payload(user)}
    finally:
        con.close()


@app.post("/api/auth/setup")
def auth_setup(payload: FirstAdminIn, response: Response, request: Request):
    con = db()
    try:
        if con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] > 0:
            raise HTTPException(409, "Första admin-kontot är redan skapat.")
        username = payload.username.strip()
        name = payload.display_name.strip()
        if len(username) < 2 or len(name) < 2:
            raise HTTPException(400, "Användarnamn och namn krävs.")
        if len(payload.password) < 8:
            raise HTTPException(400, "Lösenordet måste vara minst 8 tecken.")
        cur = con.execute(
            """INSERT INTO users(username,display_name,email,account_type,role,password_hash,active)
               VALUES(?,?,?,'office','admin',?,1)""",
            (username, name, payload.email.strip(), _hash_secret(payload.password)),
        )
        user_id = cur.lastrowid
        user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        token = _create_session(con, user_id)
        _write_audit(con, user, "SETUP", "USER", user_id, f"Första admin-kontot skapades: {name}", ip=request.client.host if request.client else "")
        con.commit()
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS*86400)
        return {"ok": True, "user": _user_payload(user)}
    except sqlite3.IntegrityError:
        con.rollback(); raise HTTPException(409, "Användarnamnet används redan.")
    finally:
        con.close()


@app.post("/api/auth/login")
def auth_login(payload: LoginIn, response: Response, request: Request):
    con = db()
    try:
        user = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (payload.username.strip(),)).fetchone()
        if not user or user["account_type"] != "office" or not _verify_secret(payload.password, user["password_hash"]):
            raise HTTPException(401, "Fel användarnamn eller lösenord.")
        token = _create_session(con, user["id"])
        con.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
        _write_audit(con, user, "LOGIN", "USER", user["id"], "Inloggning — kontor", ip=request.client.host if request.client else "")
        con.commit()
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS*86400)
        return {"ok": True, "user": _user_payload(user)}
    finally:
        con.close()


@app.post("/api/auth/operator-login")
def auth_operator_login(payload: OperatorLoginIn, response: Response, request: Request):
    con = db()
    try:
        user = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (payload.username.strip(),)).fetchone()
        if not user or user["account_type"] != "operator" or not _verify_secret(payload.pin, user["pin_hash"]):
            raise HTTPException(401, "Fel operatör eller PIN.")
        token = _create_session(con, user["id"])
        con.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (user["id"],))
        _write_audit(con, user, "LOGIN", "USER", user["id"], "Inloggning — produktion", ip=request.client.host if request.client else "")
        con.commit()
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_DAYS*86400)
        return {"ok": True, "user": _user_payload(user)}
    finally:
        con.close()


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        con = db()
        con.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_token_hash(token),))
        con.commit(); con.close()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/users")
def users_list():
    con = db()
    rows = con.execute(
        """SELECT id,username,display_name,email,account_type,role,badge_code,active,created_at,last_login_at
           FROM users ORDER BY active DESC,display_name COLLATE NOCASE"""
    ).fetchall()
    con.close()
    return [{**dict(r), "role_label": ROLE_LABELS.get(r["role"], r["role"])} for r in rows]


@app.post("/api/users")
def create_user(payload: UserIn, request: Request):
    if payload.role not in ROLE_PERMISSIONS:
        raise HTTPException(400, "Ogiltig roll.")
    if payload.account_type not in {"office", "operator"}:
        raise HTTPException(400, "Kontotyp måste vara office eller operator.")
    username = payload.username.strip()
    name = payload.display_name.strip()
    if len(username) < 2 or len(name) < 2:
        raise HTTPException(400, "Användarnamn och namn krävs.")
    if payload.account_type == "office" and len(payload.secret) < 8:
        raise HTTPException(400, "Kontorslösenord måste vara minst 8 tecken.")
    if payload.account_type == "operator" and not pyre.fullmatch(r"\d{4,8}", payload.secret or ""):
        raise HTTPException(400, "Operatörs-PIN måste vara 4–8 siffror.")
    con = db()
    try:
        password_hash = _hash_secret(payload.secret) if payload.account_type == "office" else None
        pin_hash = _hash_secret(payload.secret) if payload.account_type == "operator" else None
        cur = con.execute(
            """INSERT INTO users(username,display_name,email,account_type,role,password_hash,pin_hash,badge_code,active)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (username,name,payload.email.strip(),payload.account_type,payload.role,password_hash,pin_hash,payload.badge_code.strip() or None,1 if payload.active else 0),
        )
        actor = getattr(request.state, "user", None)
        _write_audit(con, actor, "CREATE", "USER", cur.lastrowid, f"Användare skapad: {name} ({payload.role})")
        con.commit(); return {"ok":True,"id":cur.lastrowid}
    except sqlite3.IntegrityError as e:
        con.rollback(); raise HTTPException(409,"Användarnamn eller badge-kod används redan.")
    finally: con.close()


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserIn, request: Request):
    if payload.role not in ROLE_PERMISSIONS: raise HTTPException(400,"Ogiltig roll.")
    if payload.account_type not in {"office","operator"}: raise HTTPException(400,"Ogiltig kontotyp.")
    con=db()
    try:
        existing=con.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        if not existing: raise HTTPException(404,"Användaren finns inte.")
        password_hash=existing["password_hash"]
        pin_hash=existing["pin_hash"]
        if payload.secret:
            if payload.account_type=="office":
                if len(payload.secret)<8: raise HTTPException(400,"Lösenordet måste vara minst 8 tecken.")
                password_hash=_hash_secret(payload.secret); pin_hash=None
            else:
                if not pyre.fullmatch(r"\d{4,8}",payload.secret): raise HTTPException(400,"PIN måste vara 4–8 siffror.")
                pin_hash=_hash_secret(payload.secret); password_hash=None
        con.execute(
            """UPDATE users SET username=?,display_name=?,email=?,account_type=?,role=?,password_hash=?,pin_hash=?,badge_code=?,active=?,updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (payload.username.strip(),payload.display_name.strip(),payload.email.strip(),payload.account_type,payload.role,password_hash,pin_hash,payload.badge_code.strip() or None,1 if payload.active else 0,user_id),
        )
        actor=getattr(request.state,"user",None)
        _write_audit(con,actor,"UPDATE","USER",user_id,f"Användare uppdaterad: {payload.display_name}")
        con.commit(); return {"ok":True}
    except sqlite3.IntegrityError:
        con.rollback(); raise HTTPException(409,"Användarnamn eller badge-kod används redan.")
    finally: con.close()


@app.get("/api/devices")
def device_list():
    con=db(); rows=con.execute("SELECT * FROM scan_devices ORDER BY active DESC,device_code").fetchall(); con.close()
    return [dict(r) for r in rows]


@app.post("/api/devices")
def create_device(payload: DeviceIn, request: Request):
    con=db()
    try:
        con.execute(
            "INSERT INTO scan_devices(device_code,name,location_type,station_id,active) VALUES(?,?,?,?,?)",
            (payload.device_code.strip().upper(),payload.name.strip(),payload.location_type.strip().upper(),payload.station_id,1 if payload.active else 0),
        )
        actor=getattr(request.state,"user",None)
        _write_audit(con,actor,"CREATE","DEVICE",payload.device_code,f"Enhet skapad: {payload.name}")
        con.commit(); return {"ok":True}
    except sqlite3.IntegrityError:
        con.rollback(); raise HTTPException(409,"Enhetskoden används redan.")
    finally: con.close()


@app.put("/api/devices/{device_code}")
def update_device(device_code: str, payload: DeviceIn, request: Request):
    if device_code.upper()!=payload.device_code.strip().upper():
        raise HTTPException(400,"Enhetskoden kan inte ändras.")
    con=db()
    if not con.execute("SELECT 1 FROM scan_devices WHERE device_code=?",(device_code.upper(),)).fetchone():
        con.close(); raise HTTPException(404,"Enheten finns inte.")
    con.execute("UPDATE scan_devices SET name=?,location_type=?,station_id=?,active=? WHERE device_code=?",
                (payload.name.strip(),payload.location_type.strip().upper(),payload.station_id,1 if payload.active else 0,device_code.upper()))
    actor=getattr(request.state,"user",None)
    _write_audit(con,actor,"UPDATE","DEVICE",device_code,f"Enhet uppdaterad: {payload.name}")
    con.commit(); con.close(); return {"ok":True}


@app.get("/api/audit")
def audit_list(limit: int = Query(100, ge=1, le=500)):
    con=db(); rows=con.execute("SELECT * FROM audit_events ORDER BY created_at DESC,id DESC LIMIT ?",(limit,)).fetchall(); con.close()
    return [dict(r) for r in rows]


# ============================================================
# v0.7 — STATISTICS
# ============================================================

def _stats_filters(date_from: str, date_to: str, customer_no: Optional[int], article_no: Optional[int], weekday: Optional[int]):
    where=["COALESCE(o.delivery_date,o.order_date) BETWEEN ? AND ?"]
    args=[date_from,date_to]
    if customer_no is not None:
        where.append("o.customer_no=?"); args.append(customer_no)
    if article_no is not None:
        where.append("EXISTS(SELECT 1 FROM order_lines fx WHERE fx.order_id=o.id AND fx.article_no=?)"); args.append(article_no)
    if weekday is not None:
        where.append("CAST(strftime('%w',COALESCE(o.delivery_date,o.order_date)) AS INTEGER)=?"); args.append(weekday)
    return " AND ".join(where), args


@app.get("/api/statistics/options")
def statistics_options():
    con=db()
    span=con.execute("SELECT MIN(COALESCE(delivery_date,order_date)) min_date,MAX(COALESCE(delivery_date,order_date)) max_date FROM orders").fetchone()
    customers=con.execute("SELECT customer_no,name FROM customers WHERE active=1 ORDER BY customer_no").fetchall()
    articles=con.execute("SELECT article_no,name FROM articles WHERE active=1 ORDER BY article_no").fetchall()
    con.close()
    return {"min_date":span["min_date"],"max_date":span["max_date"],"customers":[dict(r) for r in customers],"articles":[dict(r) for r in articles]}


@app.get("/api/statistics/overview")
def statistics_overview(
    date_from: str,
    date_to: str,
    customer_no: Optional[int] = None,
    article_no: Optional[int] = None,
    weekday: Optional[int] = Query(None, ge=0, le=6),
):
    try:
        d1=datetime.strptime(date_from,"%Y-%m-%d").date(); d2=datetime.strptime(date_to,"%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400,"Datum måste vara YYYY-MM-DD.")
    if d2<d1: raise HTTPException(400,"Till-datum måste vara efter från-datum.")
    if (d2-d1).days>1200: raise HTTPException(400,"Välj högst cirka 3 år åt gången.")

    con=db()
    where,args=_stats_filters(date_from,date_to,customer_no,article_no,weekday)

    filtered=con.execute(f"""SELECT o.id,o.order_no,o.customer_no,COALESCE(o.delivery_date,o.order_date) d
                             FROM orders o WHERE {where}""",args).fetchall()
    order_ids=[r["id"] for r in filtered]
    order_nos=[r["order_no"] for r in filtered if r["order_no"] is not None]

    if not filtered:
        con.close()
        return {"filters":{"date_from":date_from,"date_to":date_to,"customer_no":customer_no,"article_no":article_no,"weekday":weekday},
                "kpis":{"orders":0,"kg":0,"avg_kg_per_order":0,"customers":0,"products":0,"native_sales_value":0,"native_sales_orders":0},
                "trend":[],"weekdays":[],"top_customers":[],"top_products":[],"top_articles":[],"stations":[],"notes":["Ingen data för vald period."]}

    id_marks=','.join('?' for _ in order_ids)
    no_marks=','.join('?' for _ in order_nos) if order_nos else 'NULL'

    unique_customers=len({r["customer_no"] for r in filtered})
    # Historical physical volume is taken from dailyLoad, keyed to the filtered order numbers.
    dl_where=f"dl.order_no IN ({no_marks}) AND dl.loading_date BETWEEN ? AND ?" if order_nos else "1=0"
    dl_args=order_nos+[date_from,date_to]
    dl_kpi=con.execute(f"SELECT COALESCE(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.product) products FROM daily_load dl WHERE {dl_where}",dl_args).fetchone()
    total_kg=float(dl_kpi["kg"] or 0)

    native=con.execute(f"""SELECT COALESCE(SUM(l.goods_value+l.deposit_amount),0) value,COUNT(DISTINCT o.id) orders
                            FROM orders o JOIN order_lines l ON l.order_id=o.id
                            WHERE o.id IN ({id_marks}) AND o.source='erp_v2'""",order_ids).fetchone()

    # Trend: daily up to 120 days, otherwise weekly.
    days=(d2-d1).days+1
    if days<=120:
        trend_sql=f"""SELECT dl.loading_date period,ROUND(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.order_no) orders
                      FROM daily_load dl WHERE {dl_where} GROUP BY dl.loading_date ORDER BY dl.loading_date"""
    else:
        trend_sql=f"""SELECT strftime('%Y-W%W',dl.loading_date) period,ROUND(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.order_no) orders
                      FROM daily_load dl WHERE {dl_where} GROUP BY strftime('%Y-W%W',dl.loading_date) ORDER BY MIN(dl.loading_date)"""
    trend=[dict(r) for r in con.execute(trend_sql,dl_args).fetchall()]

    weekday_rows=con.execute(f"""SELECT CAST(strftime('%w',dl.loading_date) AS INTEGER) weekday,ROUND(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.order_no) orders
                                 FROM daily_load dl WHERE {dl_where} GROUP BY weekday ORDER BY weekday""",dl_args).fetchall()
    weekdays=[dict(r) for r in weekday_rows]

    top_customers=con.execute(f"""SELECT o.customer_no,c.name,ROUND(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.order_no) orders
                                    FROM daily_load dl JOIN orders o ON o.order_no=dl.order_no JOIN customers c ON c.customer_no=o.customer_no
                                    WHERE {dl_where} GROUP BY o.customer_no,c.name ORDER BY kg DESC LIMIT 12""",dl_args).fetchall()
    top_products=con.execute(f"""SELECT dl.product,ROUND(SUM(dl.weight_kg),0) kg,COUNT(DISTINCT dl.order_no) orders
                                   FROM daily_load dl WHERE {dl_where} AND COALESCE(dl.product,'')<>''
                                   GROUP BY dl.product ORDER BY kg DESC LIMIT 12""",dl_args).fetchall()

    top_articles=con.execute(f"""SELECT l.article_no,a.name,COUNT(DISTINCT l.order_id) orders,ROUND(SUM(l.ordered_qty),2) qty
                                   FROM order_lines l JOIN articles a ON a.article_no=l.article_no
                                   WHERE l.order_id IN ({id_marks})
                                   GROUP BY l.article_no,a.name ORDER BY orders DESC,qty DESC LIMIT 12""",order_ids).fetchall()
    stations=con.execute(f"""SELECT a.station_id,COUNT(DISTINCT l.order_id) orders,ROUND(SUM(l.ordered_qty),2) qty
                              FROM order_lines l JOIN articles a ON a.article_no=l.article_no
                              WHERE l.order_id IN ({id_marks}) AND a.station_id IS NOT NULL
                              GROUP BY a.station_id ORDER BY a.station_id""",order_ids).fetchall()

    result={
      "filters":{"date_from":date_from,"date_to":date_to,"customer_no":customer_no,"article_no":article_no,"weekday":weekday},
      "kpis":{
        "orders":len(filtered),"kg":total_kg,"avg_kg_per_order":total_kg/len(filtered) if filtered else 0,
        "customers":unique_customers,"products":int(dl_kpi["products"] or 0),
        "native_sales_value":float(native["value"] or 0),"native_sales_orders":int(native["orders"] or 0),
      },
      "trend":trend,"weekdays":weekdays,
      "top_customers":[dict(r) for r in top_customers],"top_products":[dict(r) for r in top_products],
      "top_articles":[dict(r) for r in top_articles],"stations":[dict(r) for r in stations],
      "notes":[
        "Historisk vikt/volym hämtas från dailyLoad och kopplas via ordernummer.",
        "Artikel- och stationsstatistik hämtas från sparade orderrader + aktuell artikelmaster.",
        "Försäljningsvärde visas endast för order skapade nativt i ERP v2 tills Fortnox-värdena är fullt verifierade."
      ]
    }
    con.close(); return result


def rowdict(row):
    return dict(row) if row is not None else None


def parse_json(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def resolve_price(con, customer_no: int, article_no: int, pricing_date: str):
    pricing_date = pricing_date or date.today().isoformat()
    row = con.execute(
        """SELECT pc.*,c.name customer_name,a.name article_name
           FROM pricing_conditions pc
           JOIN customers c ON c.customer_no=pc.customer_no
           JOIN articles a ON a.article_no=pc.article_no
           WHERE pc.customer_no=? AND pc.article_no=? AND pc.active=1 AND pc.price>0
             AND (pc.valid_from IS NULL OR pc.valid_from='' OR pc.valid_from<=?)
             AND (pc.valid_to IS NULL OR pc.valid_to='' OR pc.valid_to>=?)
           ORDER BY
             CASE pc.condition_type WHEN 'CAMPAIGN' THEN 2 WHEN 'STANDARD' THEN 1 ELSE 0 END DESC,
             pc.priority DESC,
             CASE pc.source WHEN 'erp_v2' THEN 1 ELSE 0 END DESC,
             COALESCE(pc.valid_from,'') DESC,
             pc.id DESC
           LIMIT 1""",
        (customer_no, article_no, pricing_date, pricing_date),
    ).fetchone()
    if not row:
        return {
            "price": None, "price_source": "Pris saknas", "condition_id": None,
            "condition_type": None, "condition_name": None,
            "valid_from": None, "valid_to": None, "pricing_date": pricing_date,
        }
    kind = "Kampanj" if row["condition_type"] == "CAMPAIGN" else "Kundpris"
    return {
        "price": float(row["price"]), "price_source": kind, "condition_id": row["id"],
        "condition_type": row["condition_type"], "condition_name": row["name"] or kind,
        "valid_from": row["valid_from"], "valid_to": row["valid_to"],
        "pricing_date": pricing_date, "source": row["source"],
    }


def price_for(con, customer_no: int, article_no: int, pricing_date: str):
    resolved = resolve_price(con, customer_no, article_no, pricing_date)
    return resolved["price"], resolved["price_source"]


def calculate_line(con, customer_no: int, article_no: int, qty: float, pricing_date: str, manual_price: Optional[float] = None, manual_reason: str = ""):
    article = con.execute("SELECT * FROM articles WHERE article_no=? AND active=1", (article_no,)).fetchone()
    if not article:
        raise HTTPException(404, f"Artikel {article_no} finns inte.")

    resolved = resolve_price(con, customer_no, article_no, pricing_date)
    if manual_price is not None:
        if manual_price <= 0:
            raise HTTPException(400, "Manuellt pris måste vara större än 0.")
        price = float(manual_price)
        price_source = "Manuell"
        price_condition_id = None
        condition_type = "MANUAL"
        condition_name = manual_reason.strip() or "Manuellt orderpris"
    else:
        price = resolved["price"]
        price_source = resolved["price_source"]
        price_condition_id = resolved["condition_id"]
        condition_type = resolved["condition_type"]
        condition_name = resolved["condition_name"]

    order_unit_qty = float(article["order_unit_qty"] or 0)
    kg_per_order_unit = float(article["kg_per_order_unit"] or 0)
    units_per_pallet = float(article["units_per_pallet"] or 0)
    units_per_ppl = float(article["units_per_ppl"] or 0)
    time_factor = float(article["production_time_factor"] or 0)
    deposit_per_unit = float(article["deposit_per_unit"] or 0)
    units_per_label = float(article["units_per_label"] or 0)

    sale_qty = qty * order_unit_qty
    kg = qty * kg_per_order_unit
    pallets = qty / units_per_pallet if units_per_pallet else None
    ppl = qty / units_per_ppl if units_per_ppl else None
    production_time = qty * time_factor

    # Mirrors the current Excel formula:
    # =IF(AO15>1,SUM(J15*AP15),"")
    deposit_amount = qty * deposit_per_unit if deposit_per_unit > 1 else 0
    goods_value = (price * sale_qty) if price is not None else None

    comps = con.execute(
        """SELECT ac.component_code,c.name,c.category,ac.qty_per_order_unit
           FROM article_components ac
           JOIN components c ON c.code=ac.component_code
           WHERE ac.article_no=?
           ORDER BY c.category,c.name""",
        (article_no,),
    ).fetchall()

    material_usage = []
    for comp in comps:
        amount = float(comp["qty_per_order_unit"] or 0) * qty
        if abs(amount) > 1e-12:
            material_usage.append({
                "code": comp["component_code"],
                "name": comp["name"],
                "category": comp["category"],
                "quantity": amount,
            })

    suggested_labels = None
    if units_per_label > 0 and sale_qty > 0:
        suggested_labels = math.ceil(sale_qty / units_per_label)

    return {
        "article_no": article_no,
        "name": article["name"],
        "ordered_qty": qty,
        "sale_qty": sale_qty,
        "unit": article["unit"] or "",
        "unit_price": price,
        "price_source": price_source,
        "price_condition_id": price_condition_id,
        "price_condition_type": condition_type,
        "price_condition_name": condition_name,
        "price_date": pricing_date,
        "price_valid_from": resolved.get("valid_from") if manual_price is None else None,
        "price_valid_to": resolved.get("valid_to") if manual_price is None else None,
        "price_override_reason": manual_reason.strip() if manual_price is not None else "",
        "deposit_amount": deposit_amount,
        "goods_value": goods_value,
        "kg": kg,
        "pallets": pallets,
        "ppl": ppl,
        "station_id": article["station_id"],
        "production_time": production_time,
        "units_per_label": units_per_label,
        "suggested_labels": suggested_labels,
        "material_usage": material_usage,
    }


def order_totals(con, order_id: int, source: str):
    if source == "legacy":
        row = con.execute(
            """SELECT legacy_goods_total,legacy_deposit_total,legacy_invoice_total,
                      legacy_total_kg,legacy_eur_pallets,legacy_srs_pallets,legacy_total_ppl,
                      legacy_rounding
               FROM orders WHERE id=?""",
            (order_id,),
        ).fetchone()
        return {
            "goods": float(row["legacy_goods_total"] or 0),
            "deposit": float(row["legacy_deposit_total"] or 0),
            "invoice_total": float(row["legacy_invoice_total"] or 0),
            "kg": float(row["legacy_total_kg"] or 0),
            "eur_pallets": float(row["legacy_eur_pallets"] or 0),
            "srs_pallets": float(row["legacy_srs_pallets"] or 0),
            "pallets": float(row["legacy_eur_pallets"] or 0) + float(row["legacy_srs_pallets"] or 0),
            "ppl": float(row["legacy_total_ppl"] or 0),
            "rounding": float(row["legacy_rounding"] or 0),
        }

    row = con.execute(
        """SELECT COALESCE(SUM(goods_value),0) goods,
                  COALESCE(SUM(deposit_amount),0) deposit,
                  COALESCE(SUM(kg),0) kg,
                  COALESCE(SUM(pallets),0) pallets,
                  COALESCE(SUM(ppl),0) ppl
           FROM order_lines WHERE order_id=?""",
        (order_id,),
    ).fetchone()
    goods = float(row["goods"] or 0)
    deposit = float(row["deposit"] or 0)
    return {
        "goods": goods,
        "deposit": deposit,
        "invoice_total": goods + deposit,
        "kg": float(row["kg"] or 0),
        "pallets": float(row["pallets"] or 0),
        "ppl": float(row["ppl"] or 0),
        "eur_pallets": 0,
        "srs_pallets": 0,
        "rounding": 0,
    }


@app.get("/")
def home():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/meta")
def meta():
    con = db()
    settings = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}
    max_db = con.execute("SELECT COALESCE(MAX(order_no),0) n FROM orders").fetchone()["n"]
    excel_current = int(settings.get("current_excel_order_no") or 0)
    next_order = max(int(max_db or 0), excel_current) + 1

    result = {
        "app": "ERP v2",
        "version": "0.8.8 DEMO · syntetisk testdata",
        "demo": True,
        "demo_notice": "All data i denna build är syntetisk testdata.",
        "workbook_snapshot": settings.get("workbook_snapshot"),
        "source_workbook": settings.get("source_workbook"),
        "next_order_no": next_order,
        "next_customer_no": con.execute("SELECT COALESCE(MAX(customer_no),100)+1 n FROM customers WHERE customer_no<10000").fetchone()["n"],
        "next_article_no": con.execute("SELECT COALESCE(MAX(article_no),99)+1 n FROM articles WHERE article_no<10000").fetchone()["n"],
        "counts": {
            "customers": con.execute("SELECT COUNT(*) n FROM customers WHERE active=1").fetchone()["n"],
            "archived_customers": con.execute("SELECT COUNT(*) n FROM customers WHERE active=0").fetchone()["n"],
            "articles": con.execute("SELECT COUNT(*) n FROM articles WHERE active=1").fetchone()["n"],
            "archived_articles": con.execute("SELECT COUNT(*) n FROM articles WHERE active=0").fetchone()["n"],
            "prices": con.execute("SELECT COUNT(*) n FROM customer_prices").fetchone()["n"],
            "historical_orders": con.execute("SELECT COUNT(*) n FROM orders WHERE source='legacy'").fetchone()["n"],
        },
    }
    con.close()
    return result


@app.get("/api/dashboard")
def dashboard():
    con = db()
    today = date.today().isoformat()
    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    week_end = (date.today() + timedelta(days=6-date.today().weekday())).isoformat()
    op=_operational_order_predicate('o')

    today_stats = con.execute(
        f"""SELECT COUNT(*) orders,
                  COALESCE(SUM(CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                         ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END),0) kg
           FROM orders o WHERE o.delivery_date=? AND {op}""", (today,)
    ).fetchone()

    week_stats = con.execute(
        f"""SELECT COUNT(*) orders,
                  COALESCE(SUM(CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                         ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END),0) kg
           FROM orders o WHERE o.delivery_date BETWEEN ? AND ? AND {op}""", (week_start,week_end)
    ).fetchone()

    latest_legacy_date = con.execute("SELECT MAX(delivery_date) d FROM orders WHERE source='legacy'").fetchone()["d"]
    recent = con.execute(
        """SELECT o.id,o.order_no,o.source,o.order_date,o.delivery_date,o.status,
                  o.customer_no,c.name customer_name,
                  CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                       ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END total_kg,
                  CASE WHEN o.source='legacy' THEN o.legacy_invoice_total
                       ELSE COALESCE((SELECT SUM(goods_value+deposit_amount) FROM order_lines l WHERE l.order_id=o.id),0) END total_value
           FROM orders o JOIN customers c ON c.customer_no=o.customer_no
           ORDER BY o.order_no DESC LIMIT 10"""
    ).fetchall()
    activity = con.execute(
        """SELECT a.id,a.created_at,a.event_type,a.message,a.order_id,o.order_no,c.name customer_name
           FROM activity_events a LEFT JOIN orders o ON o.id=a.order_id
           LEFT JOIN customers c ON c.customer_no=o.customer_no
           ORDER BY a.created_at DESC,a.id DESC LIMIT 12"""
    ).fetchall()
    volume = con.execute(
        """SELECT type,ROUND(SUM(weight_kg),0) weight FROM daily_load
           WHERE loading_date >= date((SELECT MAX(loading_date) FROM daily_load),'-30 day')
             AND type IS NOT NULL AND type<>'' GROUP BY type ORDER BY weight DESC LIMIT 8"""
    ).fetchall()
    counts = {
        "customers": con.execute("SELECT COUNT(*) n FROM customers WHERE active=1").fetchone()["n"],
        "articles": con.execute("SELECT COUNT(*) n FROM articles WHERE active=1").fetchone()["n"],
        "history": con.execute("SELECT COUNT(*) n FROM orders WHERE source='legacy'").fetchone()["n"],
        "new_orders": con.execute("SELECT COUNT(*) n FROM orders WHERE source='erp_v2'").fetchone()["n"],
    }
    con.close()
    return {
        "today":{"date":today,"orders":today_stats["orders"],"kg":today_stats["kg"] or 0},
        "week":{"orders":week_stats["orders"],"kg":week_stats["kg"] or 0,"start":week_start,"end":week_end},
        "latest_legacy_date":latest_legacy_date,"counts":counts,
        "recent_orders":[dict(r) for r in recent],"activity":[dict(r) for r in activity],"volume":[dict(r) for r in volume],
        "operational_sources":["legacy","excel_current","erp_v2"],
    }


class PricingConditionIn(BaseModel):
    condition_type: str
    customer_no: int
    article_no: int
    price: float = Field(gt=0)
    valid_from: str = ""
    valid_to: str = ""
    name: str = ""
    notes: str = ""
    active: bool = True


def _condition_status(row, on_date: str):
    if not row["active"]:
        return "inactive"
    vf = row["valid_from"] or ""
    vt = row["valid_to"] or ""
    if vf and vf > on_date:
        return "future"
    if vt and vt < on_date:
        return "expired"
    return "current"


def _validate_condition(con, payload: PricingConditionIn, exclude_id: Optional[int] = None):
    ctype = payload.condition_type.strip().upper()
    if ctype not in {"STANDARD", "CAMPAIGN"}:
        raise HTTPException(400, "Villkorstyp måste vara STANDARD eller CAMPAIGN.")
    if not con.execute("SELECT 1 FROM customers WHERE customer_no=? AND active=1", (payload.customer_no,)).fetchone():
        raise HTTPException(400, "Okänd eller arkiverad kund.")
    if not con.execute("SELECT 1 FROM articles WHERE article_no=? AND active=1", (payload.article_no,)).fetchone():
        raise HTTPException(400, "Okänd eller arkiverad artikel.")
    vf = payload.valid_from.strip() or None
    vt = payload.valid_to.strip() or None
    if vf and vt and vf > vt:
        raise HTTPException(400, "Gäller från kan inte vara efter gäller till.")
    if ctype == "CAMPAIGN" and (not vf or not vt):
        raise HTTPException(400, "Kampanjpris måste ha både start- och slutdatum.")

    # Avoid ambiguous price determination. Date ranges are inclusive.
    q = """SELECT id,name,valid_from,valid_to FROM pricing_conditions
           WHERE active=1 AND condition_type=? AND customer_no=? AND article_no=?"""
    args = [ctype, payload.customer_no, payload.article_no]
    if exclude_id is not None:
        q += " AND id<>?"; args.append(exclude_id)
    for row in con.execute(q, args).fetchall():
        evf = row["valid_from"] or "0001-01-01"
        evt = row["valid_to"] or "9999-12-31"
        nvf = vf or "0001-01-01"
        nvt = vt or "9999-12-31"
        if nvf <= evt and nvt >= evf:
            label = row["name"] or ("Kampanj" if ctype == "CAMPAIGN" else "Kundpris")
            raise HTTPException(409, f"Överlappande {ctype.lower()} finns redan: {label} (ID {row['id']}).")
    return ctype, vf, vt


def _pricing_event(con, actor, action, condition_id, ctype, customer_no, article_no,
                   old_price, new_price, old_from, old_to, new_from, new_to, message):
    con.execute(
        """INSERT INTO pricing_events(condition_id,action,condition_type,customer_no,article_no,
             old_price,new_price,old_valid_from,old_valid_to,new_valid_from,new_valid_to,actor,message)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (condition_id,action,ctype,customer_no,article_no,old_price,new_price,old_from,old_to,
         new_from,new_to,actor["display_name"] if actor else "System",message),
    )


@app.get("/api/pricing/summary")
def pricing_summary():
    con = db(); today = date.today().isoformat()
    standard = con.execute("SELECT COUNT(*) n FROM pricing_conditions WHERE condition_type='STANDARD' AND active=1 AND price>0").fetchone()["n"]
    active_campaigns = con.execute("""SELECT COUNT(*) n FROM pricing_conditions WHERE condition_type='CAMPAIGN' AND active=1 AND price>0
        AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>=?)""", (today,today)).fetchone()["n"]
    future_campaigns = con.execute("SELECT COUNT(*) n FROM pricing_conditions WHERE condition_type='CAMPAIGN' AND active=1 AND valid_from>?", (today,)).fetchone()["n"]
    customers_priced = con.execute("SELECT COUNT(DISTINCT customer_no) n FROM pricing_conditions WHERE condition_type='STANDARD' AND active=1 AND price>0").fetchone()["n"]
    con.close()
    return {"standard_prices":standard,"active_campaigns":active_campaigns,"future_campaigns":future_campaigns,"customers_priced":customers_priced,"today":today}


@app.get("/api/pricing/conditions")
def pricing_conditions(condition_type: str = "STANDARD", search: str = "", customer_no: Optional[int] = None,
                       article_no: Optional[int] = None, status: str = "all", limit: int = Query(300, ge=1, le=2000)):
    con = db(); today = date.today().isoformat()
    where=["pc.condition_type=?"]; args=[condition_type.strip().upper()]
    if customer_no is not None: where.append("pc.customer_no=?"); args.append(customer_no)
    if article_no is not None: where.append("pc.article_no=?"); args.append(article_no)
    if search.strip():
        q=f"%{search.strip()}%"; where.append("(CAST(pc.customer_no AS TEXT) LIKE ? OR c.name LIKE ? OR CAST(pc.article_no AS TEXT) LIKE ? OR a.name LIKE ? OR pc.name LIKE ?)"); args += [q,q,q,q,q]
    rows=con.execute(f"""SELECT pc.*,c.name customer_name,a.name article_name
                           FROM pricing_conditions pc JOIN customers c ON c.customer_no=pc.customer_no
                           JOIN articles a ON a.article_no=pc.article_no
                           WHERE {' AND '.join(where)}
                           ORDER BY pc.active DESC,COALESCE(pc.valid_from,'9999-12-31') DESC,pc.customer_no,pc.article_no,pc.id DESC LIMIT ?""", args+[limit]).fetchall()
    out=[]
    for r in rows:
        d=dict(r); d["status"]=_condition_status(r,today)
        if status != "all" and d["status"] != status: continue
        out.append(d)
    con.close(); return out


@app.get("/api/pricing/conditions/{condition_id}")
def pricing_condition_detail(condition_id: int):
    con=db(); row=con.execute("""SELECT pc.*,c.name customer_name,a.name article_name FROM pricing_conditions pc
                                  JOIN customers c ON c.customer_no=pc.customer_no JOIN articles a ON a.article_no=pc.article_no
                                  WHERE pc.id=?""",(condition_id,)).fetchone(); con.close()
    if not row: raise HTTPException(404,"Prisvillkoret finns inte.")
    return dict(row)


@app.get("/api/pricing/article-overview")
def pricing_article_overview(search: str = "", pricing_date: str = "", limit: int = Query(1500, ge=1, le=5000)):
    """Show the effective/current customer prices for one or more matching articles.

    This is deliberately article-centric: entering e.g. "120" means article 120,
    not customer 120. Each customer is returned once per article with the effective
    price for the requested pricing date, plus the underlying standard price.
    """
    term = search.strip()
    if not term:
        return []

    when = (pricing_date or date.today().isoformat()).strip()
    con = db()
    try:
        # An all-numeric search is treated as an exact article number first.
        # If no such article exists, fall back to article-number/name contains.
        article_rows = []
        if pyre.fullmatch(r"\d+", term):
            article_rows = con.execute(
                "SELECT article_no,name FROM articles WHERE active=1 AND article_no=? ORDER BY article_no LIMIT 25",
                (int(term),),
            ).fetchall()

        if not article_rows:
            q = f"%{term}%"
            article_rows = con.execute(
                """SELECT article_no,name FROM articles
                   WHERE active=1 AND (CAST(article_no AS TEXT) LIKE ? OR name LIKE ?)
                   ORDER BY article_no LIMIT 25""",
                (q, q),
            ).fetchall()

        if not article_rows:
            return []

        article_nos = [int(r["article_no"]) for r in article_rows]
        placeholders = ",".join("?" for _ in article_nos)
        pairs = con.execute(
            f"""SELECT DISTINCT pc.article_no,pc.customer_no,c.name customer_name,a.name article_name
                FROM pricing_conditions pc
                JOIN customers c ON c.customer_no=pc.customer_no
                JOIN articles a ON a.article_no=pc.article_no
                WHERE pc.active=1 AND pc.price>0
                  AND c.active=1 AND a.active=1
                  AND pc.article_no IN ({placeholders})
                ORDER BY pc.article_no,pc.customer_no
                LIMIT ?""",
            article_nos + [limit],
        ).fetchall()

        out = []
        for pair in pairs:
            customer_no = int(pair["customer_no"])
            article_no = int(pair["article_no"])
            resolved = resolve_price(con, customer_no, article_no, when)
            if resolved["price"] is None:
                continue

            std = con.execute(
                """SELECT pc.*
                   FROM pricing_conditions pc
                   WHERE pc.customer_no=? AND pc.article_no=? AND pc.condition_type='STANDARD'
                     AND pc.active=1 AND pc.price>0
                     AND (pc.valid_from IS NULL OR pc.valid_from='' OR pc.valid_from<=?)
                     AND (pc.valid_to IS NULL OR pc.valid_to='' OR pc.valid_to>=?)
                   ORDER BY pc.priority DESC,
                            CASE pc.source WHEN 'erp_v2' THEN 1 ELSE 0 END DESC,
                            COALESCE(pc.valid_from,'') DESC, pc.id DESC
                   LIMIT 1""",
                (customer_no, article_no, when, when),
            ).fetchone()

            out.append({
                "article_no": article_no,
                "article_name": pair["article_name"],
                "customer_no": customer_no,
                "customer_name": pair["customer_name"],
                "standard_price": float(std["price"]) if std else None,
                "current_price": resolved["price"],
                "price_source": resolved["price_source"],
                "condition_id": resolved["condition_id"],
                "condition_name": resolved["condition_name"],
                "valid_from": resolved["valid_from"],
                "valid_to": resolved["valid_to"],
                "pricing_date": when,
                "source": resolved.get("source"),
            })
        return out
    finally:
        con.close()


@app.get("/api/pricing/resolve")
def pricing_resolve(customer_no: int, article_no: int, pricing_date: str):
    con=db(); result=resolve_price(con,customer_no,article_no,pricing_date); con.close(); return result


@app.get("/api/pricing/events")
def pricing_events(limit: int = Query(200, ge=1, le=1000)):
    con=db(); rows=con.execute("""SELECT pe.*,c.name customer_name,a.name article_name FROM pricing_events pe
                                  LEFT JOIN customers c ON c.customer_no=pe.customer_no LEFT JOIN articles a ON a.article_no=pe.article_no
                                  ORDER BY pe.created_at DESC,pe.id DESC LIMIT ?""",(limit,)).fetchall(); con.close()
    return [dict(r) for r in rows]


@app.post("/api/pricing/conditions")
def create_pricing_condition(payload: PricingConditionIn, request: Request):
    con=db()
    try:
        ctype,vf,vt=_validate_condition(con,payload)
        actor=getattr(request.state,"user",None)
        name=payload.name.strip() or ("Kampanj" if ctype=="CAMPAIGN" else "Kundpris")
        cur=con.execute("""INSERT INTO pricing_conditions(condition_type,customer_no,article_no,price,valid_from,valid_to,name,notes,priority,active,source,created_by)
                           VALUES(?,?,?,?,?,?,?,?,?,?, 'erp_v2',?)""",
                        (ctype,payload.customer_no,payload.article_no,payload.price,vf,vt,name,payload.notes.strip(),200 if ctype=="CAMPAIGN" else 100,1 if payload.active else 0,actor["display_name"] if actor else "System"))
        cid=cur.lastrowid
        msg=f"{name}: {payload.customer_no}/{payload.article_no} → {payload.price:.2f} kr"
        _pricing_event(con,actor,"CREATE",cid,ctype,payload.customer_no,payload.article_no,None,payload.price,None,None,vf,vt,msg)
        _write_audit(con,actor,"CREATE","PRICE_CONDITION",cid,msg)
        con.commit(); return {"ok":True,"id":cid}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()


@app.put("/api/pricing/conditions/{condition_id}")
def update_pricing_condition(condition_id: int, payload: PricingConditionIn, request: Request):
    con=db()
    try:
        old=con.execute("SELECT * FROM pricing_conditions WHERE id=?",(condition_id,)).fetchone()
        if not old: raise HTTPException(404,"Prisvillkoret finns inte.")
        ctype,vf,vt=_validate_condition(con,payload,condition_id)
        actor=getattr(request.state,"user",None)
        name=payload.name.strip() or ("Kampanj" if ctype=="CAMPAIGN" else "Kundpris")
        con.execute("""UPDATE pricing_conditions SET condition_type=?,customer_no=?,article_no=?,price=?,valid_from=?,valid_to=?,name=?,notes=?,priority=?,active=?,source='erp_v2',source_key=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (ctype,payload.customer_no,payload.article_no,payload.price,vf,vt,name,payload.notes.strip(),200 if ctype=="CAMPAIGN" else 100,1 if payload.active else 0,condition_id))
        msg=f"{name}: {payload.customer_no}/{payload.article_no} {float(old['price']):.2f} → {payload.price:.2f} kr"
        _pricing_event(con,actor,"UPDATE",condition_id,ctype,payload.customer_no,payload.article_no,old["price"],payload.price,old["valid_from"],old["valid_to"],vf,vt,msg)
        _write_audit(con,actor,"UPDATE","PRICE_CONDITION",condition_id,msg,{"old_price":old["price"],"new_price":payload.price})
        con.commit(); return {"ok":True,"id":condition_id}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()


@app.delete("/api/pricing/conditions/{condition_id}")
def archive_pricing_condition(condition_id: int, request: Request):
    con=db()
    try:
        old=con.execute("SELECT * FROM pricing_conditions WHERE id=?",(condition_id,)).fetchone()
        if not old: raise HTTPException(404,"Prisvillkoret finns inte.")
        actor=getattr(request.state,"user",None)
        con.execute("UPDATE pricing_conditions SET active=0,source='erp_v2',source_key=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(condition_id,))
        msg=f"Prisvillkor arkiverat: {old['customer_no']}/{old['article_no']} · {float(old['price']):.2f} kr"
        _pricing_event(con,actor,"ARCHIVE",condition_id,old["condition_type"],old["customer_no"],old["article_no"],old["price"],None,old["valid_from"],old["valid_to"],None,None,msg)
        _write_audit(con,actor,"ARCHIVE","PRICE_CONDITION",condition_id,msg)
        con.commit(); return {"ok":True}
    finally: con.close()


@app.get("/api/customers")
def customers(search: str = "", include_inactive: bool = False, limit: int = Query(200, ge=1, le=2000)):
    con = db()
    where = ["1=1"]
    args = []
    if not include_inactive:
        where.append("active=1")
    if search.strip():
        q = f"%{search.strip()}%"
        where.append("(CAST(customer_no AS TEXT) LIKE ? OR name LIKE ? OR address LIKE ? OR postal_address LIKE ?)")
        args += [q,q,q,q]
    args.append(limit)
    rows = con.execute(
        f"SELECT * FROM customers WHERE {' AND '.join(where)} ORDER BY active DESC,customer_no LIMIT ?",
        args,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/customers/{customer_no}")
def customer_detail(customer_no: int):
    con = db()
    row = con.execute("SELECT * FROM customers WHERE customer_no=?", (customer_no,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Kunden finns inte.")
    stats = con.execute(
        """SELECT COUNT(*) orders,MAX(delivery_date) last_delivery
           FROM orders WHERE customer_no=?""",
        (customer_no,),
    ).fetchone()
    result = dict(row)
    result["stats"] = dict(stats)
    con.close()
    return result


class CustomerMasterIn(BaseModel):
    customer_no: int
    name: str
    address: str = ""
    postal_address: str = ""
    carrier: str = ""
    pallet_system: Optional[int] = None
    srs_no: str = ""
    bonus_pct: Optional[float] = None
    default_reference: str = ""
    order_email: str = ""
    invoice_email: str = ""
    label_name: str = ""
    notes: str = ""
    transport_email: str = ""
    fortnox_flag: Optional[int] = None
    phone: str = ""
    loading_time: str = ""
    pallet_material: str = ""
    smak_profile: str = ""
    active: bool = True


def write_master_event(con, entity_type: str, entity_key, action: str, message: str):
    con.execute(
        "INSERT INTO master_data_events(entity_type,entity_key,action,message) VALUES(?,?,?,?)",
        (entity_type, str(entity_key), action, message),
    )


@app.post("/api/customers")
def create_customer(payload: CustomerMasterIn):
    if not payload.name.strip():
        raise HTTPException(400, "Kundnamn krävs.")
    con = db()
    try:
        existing = con.execute("SELECT name,active FROM customers WHERE customer_no=?", (payload.customer_no,)).fetchone()
        if existing:
            status = "arkiverad" if not existing["active"] else "aktiv"
            raise HTTPException(
                409,
                f"Kundnummer {payload.customer_no} används redan av {existing['name']} ({status})."
            )
        con.execute(
            """INSERT INTO customers(
                 customer_no,name,address,postal_address,carrier,pallet_system,srs_no,bonus_pct,
                 default_reference,order_email,invoice_email,label_name,notes,transport_email,
                 fortnox_flag,phone,loading_time,pallet_material,smak_profile,active,
                 source,user_modified,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'erp_v2',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (
                payload.customer_no,payload.name.strip(),payload.address,payload.postal_address,payload.carrier,
                payload.pallet_system,payload.srs_no,payload.bonus_pct,payload.default_reference,payload.order_email,
                payload.invoice_email,payload.label_name,payload.notes,payload.transport_email,payload.fortnox_flag,
                payload.phone,payload.loading_time,payload.pallet_material,payload.smak_profile,1 if payload.active else 0,
            ),
        )
        write_master_event(con,"customer",payload.customer_no,"CREATE",f"Kund {payload.customer_no} skapad i ERP v2")
        con.commit()
        return {"ok":True,"customer_no":payload.customer_no}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


@app.put("/api/customers/{customer_no}")
def update_customer(customer_no: int, payload: CustomerMasterIn):
    if customer_no != payload.customer_no:
        raise HTTPException(400, "Kundnummer kan inte ändras efter att kunden skapats.")
    if not payload.name.strip():
        raise HTTPException(400, "Kundnamn krävs.")
    con = db()
    try:
        if not con.execute("SELECT 1 FROM customers WHERE customer_no=?", (customer_no,)).fetchone():
            raise HTTPException(404, "Kunden finns inte.")
        con.execute(
            """UPDATE customers SET
                 name=?,address=?,postal_address=?,carrier=?,pallet_system=?,srs_no=?,bonus_pct=?,
                 default_reference=?,order_email=?,invoice_email=?,label_name=?,notes=?,transport_email=?,
                 fortnox_flag=?,phone=?,loading_time=?,pallet_material=?,smak_profile=?,active=?,
                 user_modified=1,updated_at=CURRENT_TIMESTAMP
               WHERE customer_no=?""",
            (
                payload.name.strip(),payload.address,payload.postal_address,payload.carrier,payload.pallet_system,
                payload.srs_no,payload.bonus_pct,payload.default_reference,payload.order_email,payload.invoice_email,
                payload.label_name,payload.notes,payload.transport_email,payload.fortnox_flag,payload.phone,
                payload.loading_time,payload.pallet_material,payload.smak_profile,1 if payload.active else 0,customer_no,
            ),
        )
        write_master_event(con,"customer",customer_no,"UPDATE",f"Kund {customer_no} uppdaterad")
        con.commit()
        return {"ok":True,"customer_no":customer_no}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


@app.delete("/api/customers/{customer_no}")
def archive_customer(customer_no: int):
    con = db()
    try:
        row=con.execute("SELECT name,active FROM customers WHERE customer_no=?",(customer_no,)).fetchone()
        if not row: raise HTTPException(404,"Kunden finns inte.")
        con.execute("UPDATE customers SET active=0,user_modified=1,updated_at=CURRENT_TIMESTAMP WHERE customer_no=?",(customer_no,))
        write_master_event(con,"customer",customer_no,"ARCHIVE",f"Kund {customer_no} — {row['name']} arkiverad")
        con.commit(); return {"ok":True}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()



# ============================================================
# v0.7.9 — PURCHASING MATERIAL MASTER API
# ============================================================

class PurchasingMaterialIn(BaseModel):
    code: str
    name: str
    material_type: str = "OTHER"
    unit: str = ""
    purchase_unit: str = ""
    purchase_to_base_factor: float = 1
    purchasable: bool = True
    stock_managed: bool = True
    bom_usable: bool = True
    canonical_material_code: str = ""
    supplier_article_no: str = ""
    review_status: str = "OK"
    notes: str = ""
    active: bool = True


def _validate_material_payload(con, payload: PurchasingMaterialIn, current_code: str | None = None):
    code=(payload.code or "").strip().upper()
    if not code: raise HTTPException(400,"Materialnummer krävs.")
    if not payload.name.strip(): raise HTTPException(400,"Benämning krävs.")
    mtype=(payload.material_type or "OTHER").strip().upper()
    if mtype not in MATERIAL_TYPES: raise HTTPException(400,"Ogiltig materialtyp.")
    if payload.purchase_to_base_factor is None or payload.purchase_to_base_factor <= 0:
        raise HTTPException(400,"Omräkningsfaktor måste vara större än 0.")
    canonical=(payload.canonical_material_code or "").strip().upper()
    if canonical:
        if canonical==code: raise HTTPException(400,"Ett material kan inte länka till sig självt som kanoniskt material.")
        if not con.execute("SELECT 1 FROM components WHERE code=?",(canonical,)).fetchone():
            raise HTTPException(400,f"Kanoniskt material {canonical} finns inte.")
    if current_code is None and con.execute("SELECT 1 FROM components WHERE code=?",(code,)).fetchone():
        raise HTTPException(409,f"Material {code} finns redan.")
    return code,mtype,canonical


@app.get("/api/purchasing/material-master")
def material_master_list(search: str = "", material_type: str = "all", include_inactive: bool = False,
                         purchasable_only: bool = False, limit: int = Query(3000,ge=1,le=5000)):
    con=db(); where=["(c.canonical_material_code IS NULL OR c.canonical_material_code='')"]; args=[]
    if not include_inactive: where.append("c.active=1")
    if purchasable_only: where.append("c.purchasable=1")
    if material_type and material_type.lower()!="all":
        mt=material_type.strip().upper()
        if mt not in MATERIAL_TYPES: con.close(); raise HTTPException(400,"Ogiltig materialtyp.")
        where.append("c.material_type=?"); args.append(mt)
    term=search.strip().upper()
    if term:
        q=f"%{term}%"
        where.append("""(
          UPPER(c.code)=? OR UPPER(c.name) LIKE ? OR UPPER(COALESCE(c.category,'')) LIKE ? OR UPPER(COALESCE(c.supplier_article_no,'')) LIKE ?
          OR EXISTS(
              SELECT 1 FROM components a
               WHERE a.canonical_material_code=c.code
                 AND (UPPER(a.code)=? OR UPPER(a.name) LIKE ? OR UPPER(COALESCE(a.supplier_article_no,'')) LIKE ?)
          )
        )""")
        args += [term,q,q,q,term,q,q]
    args.append(limit)
    rows=con.execute(f"""SELECT c.*,
             (SELECT COUNT(*) FROM supplier_info_records ir WHERE ir.component_code=c.code) info_record_count,
             (SELECT COUNT(*) FROM purchase_history ph WHERE ph.component_code=c.code) purchase_history_count,
             (SELECT COUNT(*) FROM article_components ac LEFT JOIN components lc ON lc.code=ac.component_code
                WHERE ac.component_code=c.code OR lc.canonical_material_code=c.code) bom_usage_count,
             (SELECT COUNT(*) FROM components a WHERE a.canonical_material_code=c.code) alias_count,
             (SELECT COALESCE(SUM(im.quantity_base),0) FROM inventory_movements im WHERE im.component_code=c.code) on_hand,
             (SELECT COUNT(*) FROM inventory_movements im WHERE im.component_code=c.code) inventory_movement_count
         FROM components c WHERE {' AND '.join(where)}
         ORDER BY c.active DESC,
           CASE WHEN c.code GLOB '[0-9]*' THEN 0 ELSE 1 END,
           CASE WHEN c.code GLOB '[0-9]*' THEN CAST(c.code AS INTEGER) ELSE 999999999 END,
           c.code LIMIT ?""",args).fetchall()
    con.close(); return [dict(r) for r in rows]


@app.get("/api/purchasing/material-master/{code}")
def material_master_detail(code: str):
    requested=code.strip().upper(); con=db()
    row, alias=_resolve_canonical_material(con,requested)
    if not row: con.close(); raise HTTPException(404,"Materialet finns inte.")
    key=row["code"]
    result=dict(row)
    if alias:
        result["requested_alias"]={"code":alias["code"],"name":alias["name"],"review_status":alias["review_status"]}
    result["aliases"]=[dict(r) for r in con.execute("SELECT code,name,review_status,source FROM components WHERE canonical_material_code=? ORDER BY review_status,code",(key,)).fetchall()]
    result["info_records"]=[dict(r) for r in con.execute("""SELECT ir.*,s.name supplier_name,COALESCE(ir.lead_time_days,s.default_lead_time_days) effective_lead_time_days
         FROM supplier_info_records ir JOIN suppliers s ON s.supplier_no=ir.supplier_no
         WHERE ir.component_code=? ORDER BY ir.active DESC,ir.supplier_no""",(key,)).fetchall()]
    result["purchase_history"]=[dict(r) for r in con.execute("""SELECT ph.*,s.name supplier_name FROM purchase_history ph
         JOIN suppliers s ON s.supplier_no=ph.supplier_no WHERE ph.component_code=? ORDER BY ph.purchase_date DESC,ph.id DESC LIMIT 20""",(key,)).fetchall()]
    result["bom_usage_count"]=con.execute("""SELECT COUNT(*) FROM article_components ac LEFT JOIN components lc ON lc.code=ac.component_code
         WHERE ac.component_code=? OR lc.canonical_material_code=?""",(key,key)).fetchone()[0]
    on_hand,movement_count=_inventory_on_hand(con,key)
    result["on_hand"]=on_hand
    result["inventory_initialized"]=movement_count>0
    result["inventory_movements"]=[dict(r) for r in con.execute("SELECT * FROM inventory_movements WHERE component_code=? ORDER BY created_at DESC,id DESC LIMIT 50",(key,)).fetchall()]
    result["transition_stock"] = result.get("excel_main_stock") if not result["inventory_initialized"] else on_hand
    result["transition_stock_source"] = "EXCEL_MAIN" if (not result["inventory_initialized"] and result.get("excel_main_stock") is not None) else ("ERP_LEDGER" if result["inventory_initialized"] else "NONE")
    con.close(); return result


@app.post("/api/purchasing/material-master")
def create_purchasing_material(payload: PurchasingMaterialIn):
    con=db()
    try:
        code,mtype,canonical=_validate_material_payload(con,payload)
        con.execute("""INSERT INTO components(code,name,category,unit,material_type,purchase_unit,purchase_to_base_factor,
          purchasable,stock_managed,bom_usable,active,source,user_modified,supplier_article_no,canonical_material_code,
          review_status,notes,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
          (code,payload.name.strip(),"Inköpsmaterial",payload.unit.strip(),mtype,payload.purchase_unit.strip(),payload.purchase_to_base_factor,
           1 if payload.purchasable else 0,1 if payload.stock_managed else 0,1 if payload.bom_usable else 0,1 if payload.active else 0,
           "erp_v2",payload.supplier_article_no.strip(),canonical,payload.review_status.strip().upper() or "OK",payload.notes.strip()))
        con.commit(); write_master_event(con,"purchasing_material",code,"CREATE",f"Inköpsmaterial {code} skapat")
        con.commit(); return {"ok":True,"code":code}
    finally: con.close()


@app.put("/api/purchasing/material-master/{code}")
def update_purchasing_material(code: str, payload: PurchasingMaterialIn):
    key=code.strip().upper(); con=db()
    try:
        if not con.execute("SELECT 1 FROM components WHERE code=?",(key,)).fetchone(): raise HTTPException(404,"Materialet finns inte.")
        if payload.code.strip().upper()!=key: raise HTTPException(400,"Materialnummer kan inte ändras. Skapa ett nytt material i stället.")
        _code,mtype,canonical=_validate_material_payload(con,payload,key)
        con.execute("""UPDATE components SET name=?,unit=?,material_type=?,purchase_unit=?,purchase_to_base_factor=?,
          purchasable=?,stock_managed=?,bom_usable=?,active=?,source='erp_v2',user_modified=1,supplier_article_no=?,
          canonical_material_code=?,review_status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE code=?""",
          (payload.name.strip(),payload.unit.strip(),mtype,payload.purchase_unit.strip(),payload.purchase_to_base_factor,
           1 if payload.purchasable else 0,1 if payload.stock_managed else 0,1 if payload.bom_usable else 0,1 if payload.active else 0,
           payload.supplier_article_no.strip(),canonical,payload.review_status.strip().upper() or "OK",payload.notes.strip(),key))
        write_master_event(con,"purchasing_material",key,"UPDATE",f"Inköpsmaterial {key} uppdaterat")
        con.commit(); return {"ok":True,"code":key}
    finally: con.close()


@app.delete("/api/purchasing/material-master/{code}")
def archive_purchasing_material(code: str):
    key=code.strip().upper(); con=db()
    try:
        if not con.execute("SELECT 1 FROM components WHERE code=?",(key,)).fetchone(): raise HTTPException(404,"Materialet finns inte.")
        con.execute("UPDATE components SET active=0,user_modified=1,updated_at=CURRENT_TIMESTAMP WHERE code=?",(key,))
        write_master_event(con,"purchasing_material",key,"ARCHIVE",f"Inköpsmaterial {key} arkiverat")
        con.commit(); return {"ok":True}
    finally: con.close()


class InventoryMovementIn(BaseModel):
    mode: str = "COUNT"
    quantity: float
    note: str = ""
    reference_no: str = ""


@app.post("/api/purchasing/material-master/{code}/inventory-adjustment")
def inventory_adjustment(code: str, payload: InventoryMovementIn, request: Request):
    con=db()
    try:
        material, alias=_resolve_canonical_material(con,code)
        if not material: raise HTTPException(404,"Materialet finns inte.")
        key=material["code"]
        if not material["stock_managed"]:
            raise HTTPException(400,f"Material {key} är inte markerat som lagerfört.")
        mode=(payload.mode or "COUNT").strip().upper()
        if mode not in {"COUNT","ADJUSTMENT"}:
            raise HTTPException(400,"Lagerhändelsen måste vara COUNT eller ADJUSTMENT.")
        current,_=_inventory_on_hand(con,key)
        if mode=="COUNT":
            if payload.quantity < 0: raise HTTPException(400,"Inventerat lagersaldo kan inte vara negativt.")
            delta=float(payload.quantity)-current
            movement_type="INVENTORY_COUNT"
            message=f"Inventering {key}: {current:g} → {float(payload.quantity):g} {material['unit'] or ''}".strip()
        else:
            if abs(float(payload.quantity)) < 1e-12: raise HTTPException(400,"Justeringen kan inte vara 0.")
            delta=float(payload.quantity)
            movement_type="MANUAL_ADJUSTMENT"
            message=f"Lagerjustering {key}: {delta:+g} {material['unit'] or ''}".strip()
        user=getattr(request.state,"user",None)
        created_by=user["display_name"] if user is not None else "System"
        con.execute("""INSERT INTO inventory_movements(component_code,movement_type,quantity_base,reference_type,reference_no,note,created_by)
                       VALUES(?,?,?,'MANUAL',?,?,?)""",(key,movement_type,delta,payload.reference_no.strip(),payload.note.strip(),created_by))
        write_master_event(con,"inventory",key,movement_type,message)
        con.commit()
        new_qty,_=_inventory_on_hand(con,key)
        return {"ok":True,"component_code":key,"on_hand":new_qty,"delta":delta}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


# ============================================================
# v0.7.6 — SUPPLIER MASTER
# ============================================================

class SupplierMasterIn(BaseModel):
    supplier_no: int
    name: str
    org_no: str = ""
    contact_name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    postal_address: str = ""
    payment_terms: str = ""
    delivery_terms: str = ""
    default_lead_time_days: Optional[float] = None
    min_order_value: Optional[float] = None
    currency: str = "SEK"
    notes: str = ""
    active: bool = True


@app.get("/api/suppliers")
def supplier_list(search: str = "", include_inactive: bool = False,
                  limit: int = Query(1000, ge=1, le=3000)):
    con = db()
    where = ["1=1"]
    args = []
    if not include_inactive:
        where.append("active=1")
    term = search.strip()
    if term:
        q = f"%{term}%"
        if pyre.fullmatch(r"\d+", term):
            # Numeric supplier search means exact supplier number, mirroring the
            # exact-number behaviour we use in Orderhistorik.
            where.append("supplier_no=?")
            args.append(int(term))
        else:
            where.append("(name LIKE ? OR org_no LIKE ? OR contact_name LIKE ? OR email LIKE ? OR phone LIKE ?)")
            args += [q, q, q, q, q]
    args.append(limit)
    rows = con.execute(
        f"SELECT * FROM suppliers WHERE {' AND '.join(where)} ORDER BY active DESC, supplier_no LIMIT ?",
        args,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/suppliers/{supplier_no}")
def supplier_detail(supplier_no: int):
    con = db()
    row = con.execute("SELECT * FROM suppliers WHERE supplier_no=?", (supplier_no,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Leverantören finns inte.")
    result = dict(row)
    records = con.execute(
        """SELECT ir.*, c.name AS component_name,c.category AS component_category,c.unit AS component_unit,
                  COALESCE(ir.lead_time_days,s.default_lead_time_days) AS effective_lead_time_days
           FROM supplier_info_records ir
           JOIN components c ON c.code=ir.component_code
           JOIN suppliers s ON s.supplier_no=ir.supplier_no
           WHERE ir.supplier_no=?
           ORDER BY ir.active DESC,
                    CASE ir.source_status WHEN 'PREFERRED' THEN 1 WHEN 'APPROVED' THEN 2 WHEN 'BACKUP' THEN 3 WHEN 'BLOCKED' THEN 4 ELSE 5 END,
                    c.category,c.name""",
        (supplier_no,),
    ).fetchall()
    result["info_records"] = [dict(r) for r in records]
    purchases = con.execute(
        """SELECT ph.*,c.name AS component_name,c.category AS component_category,c.unit AS component_unit
             FROM purchase_history ph
             JOIN components c ON c.code=ph.component_code
             WHERE ph.supplier_no=?
             ORDER BY ph.purchase_date DESC,ph.id DESC
             LIMIT 25""",
        (supplier_no,),
    ).fetchall()
    result["purchase_history"] = [dict(r) for r in purchases]
    con.close()
    return result


@app.post("/api/suppliers")
def create_supplier(payload: SupplierMasterIn):
    if payload.supplier_no <= 0:
        raise HTTPException(400, "Leverantörsnummer måste vara större än 0.")
    if not payload.name.strip():
        raise HTTPException(400, "Leverantörsnamn krävs.")
    con = db()
    try:
        existing = con.execute(
            "SELECT name,active FROM suppliers WHERE supplier_no=?", (payload.supplier_no,)
        ).fetchone()
        if existing:
            status = "arkiverad" if not existing["active"] else "aktiv"
            raise HTTPException(
                409,
                f"Leverantörsnummer {payload.supplier_no} används redan av {existing['name']} ({status}).",
            )
        con.execute(
            """INSERT INTO suppliers(
                 supplier_no,name,org_no,contact_name,email,phone,address,postal_address,
                 payment_terms,delivery_terms,default_lead_time_days,min_order_value,currency,
                 notes,active,source,user_modified,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'erp_v2',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (
                payload.supplier_no,payload.name.strip(),payload.org_no.strip(),payload.contact_name.strip(),
                payload.email.strip(),payload.phone.strip(),payload.address.strip(),payload.postal_address.strip(),
                payload.payment_terms.strip(),payload.delivery_terms.strip(),payload.default_lead_time_days,
                payload.min_order_value,(payload.currency or "SEK").strip().upper(),payload.notes.strip(),
                1 if payload.active else 0,
            ),
        )
        write_master_event(con,"supplier",payload.supplier_no,"CREATE",f"Leverantör {payload.supplier_no} skapad i ERP v2")
        con.commit()
        return {"ok": True, "supplier_no": payload.supplier_no}
    except HTTPException:
        con.rollback()
        raise
    finally:
        con.close()


@app.put("/api/suppliers/{supplier_no}")
def update_supplier(supplier_no: int, payload: SupplierMasterIn):
    if supplier_no != payload.supplier_no:
        raise HTTPException(400, "Leverantörsnummer kan inte ändras efter att leverantören skapats.")
    if not payload.name.strip():
        raise HTTPException(400, "Leverantörsnamn krävs.")
    con = db()
    try:
        if not con.execute("SELECT 1 FROM suppliers WHERE supplier_no=?", (supplier_no,)).fetchone():
            raise HTTPException(404, "Leverantören finns inte.")
        con.execute(
            """UPDATE suppliers SET
                 name=?,org_no=?,contact_name=?,email=?,phone=?,address=?,postal_address=?,
                 payment_terms=?,delivery_terms=?,default_lead_time_days=?,min_order_value=?,currency=?,
                 notes=?,active=?,user_modified=1,updated_at=CURRENT_TIMESTAMP
               WHERE supplier_no=?""",
            (
                payload.name.strip(),payload.org_no.strip(),payload.contact_name.strip(),payload.email.strip(),
                payload.phone.strip(),payload.address.strip(),payload.postal_address.strip(),payload.payment_terms.strip(),
                payload.delivery_terms.strip(),payload.default_lead_time_days,payload.min_order_value,
                (payload.currency or "SEK").strip().upper(),payload.notes.strip(),1 if payload.active else 0,supplier_no,
            ),
        )
        write_master_event(con,"supplier",supplier_no,"UPDATE",f"Leverantör {supplier_no} uppdaterad")
        con.commit()
        return {"ok": True, "supplier_no": supplier_no}
    except HTTPException:
        con.rollback()
        raise
    finally:
        con.close()


@app.delete("/api/suppliers/{supplier_no}")
def archive_supplier(supplier_no: int):
    con = db()
    try:
        row = con.execute("SELECT name FROM suppliers WHERE supplier_no=?", (supplier_no,)).fetchone()
        if not row:
            raise HTTPException(404, "Leverantören finns inte.")
        con.execute(
            "UPDATE suppliers SET active=0,user_modified=1,updated_at=CURRENT_TIMESTAMP WHERE supplier_no=?",
            (supplier_no,),
        )
        write_master_event(con,"supplier",supplier_no,"ARCHIVE",f"Leverantör {supplier_no} — {row['name']} arkiverad")
        con.commit()
        return {"ok": True}
    except HTTPException:
        con.rollback()
        raise
    finally:
        con.close()



# ============================================================
# v0.7.7 — SUPPLIER/MATERIAL INFO RECORDS
# ============================================================

INFO_SOURCE_STATUSES = {"PREFERRED", "APPROVED", "BACKUP", "BLOCKED"}


class SupplierInfoRecordIn(BaseModel):
    supplier_no: int
    component_code: str
    supplier_material_no: str = ""
    purchase_price: Optional[float] = None
    currency: str = "SEK"
    price_unit_qty: float = 1
    purchase_unit: str = ""
    min_order_qty: Optional[float] = None
    order_multiple_qty: Optional[float] = None
    lead_time_days: Optional[float] = None
    source_status: str = "APPROVED"
    valid_from: str = ""
    valid_to: str = ""
    notes: str = ""
    active: bool = True


def _validate_info_record_payload(con, payload: SupplierInfoRecordIn, existing_id: Optional[int] = None):
    if payload.supplier_no <= 0:
        raise HTTPException(400, "Leverantörsnummer måste vara större än 0.")
    requested_code = (payload.component_code or "").strip().upper()
    if not requested_code:
        raise HTTPException(400, "Material krävs.")
    supplier = con.execute("SELECT supplier_no,name,active,currency,default_lead_time_days FROM suppliers WHERE supplier_no=?", (payload.supplier_no,)).fetchone()
    if not supplier:
        raise HTTPException(404, "Leverantören finns inte.")
    if not supplier["active"] and existing_id is None:
        raise HTTPException(400, "Nya info records kan inte skapas för en arkiverad leverantör.")
    material, alias = _resolve_canonical_material(con, requested_code)
    if not material:
        raise HTTPException(404, f"Material {requested_code} finns inte i materialmastern.")
    code = material["code"]
    status = (payload.source_status or "APPROVED").strip().upper()
    if status not in INFO_SOURCE_STATUSES:
        raise HTTPException(400, "Ogiltig källstatus.")
    if payload.purchase_price is not None and payload.purchase_price < 0:
        raise HTTPException(400, "Inköpspris kan inte vara negativt.")
    if payload.price_unit_qty is None or payload.price_unit_qty <= 0:
        raise HTTPException(400, "Prisenhet måste vara större än 0.")
    for value, label in [
        (payload.min_order_qty, "MOQ"),
        (payload.order_multiple_qty, "Ordermultipel"),
        (payload.lead_time_days, "Ledtid"),
    ]:
        if value is not None and value < 0:
            raise HTTPException(400, f"{label} kan inte vara negativt.")
    if payload.valid_from and payload.valid_to and payload.valid_from > payload.valid_to:
        raise HTTPException(400, "Giltig-från kan inte ligga efter giltig-till.")
    duplicate = con.execute(
        "SELECT id,active FROM supplier_info_records WHERE supplier_no=? AND component_code=? AND id<>COALESCE(?, -1)",
        (payload.supplier_no, code, existing_id),
    ).fetchone()
    if duplicate:
        state = "aktivt" if duplicate["active"] else "arkiverat"
        raise HTTPException(409, f"Info record finns redan för leverantör {payload.supplier_no} och material {code} ({state}). Redigera det befintliga recordet i stället.")
    return supplier, material, status, code


def _info_record_snapshot(row):
    return {k: row[k] for k in row.keys()}


def _write_info_record_event(con, info_record_id: int, action: str, message: str):
    row = con.execute("SELECT * FROM supplier_info_records WHERE id=?", (info_record_id,)).fetchone()
    if not row:
        return
    con.execute(
        """INSERT INTO supplier_info_record_events(info_record_id,action,supplier_no,component_code,snapshot_json,message)
           VALUES(?,?,?,?,?,?)""",
        (info_record_id, action, row["supplier_no"], row["component_code"], json.dumps(_info_record_snapshot(row), ensure_ascii=False), message),
    )


@app.get("/api/purchasing/materials")
def purchasing_materials(search: str = "", limit: int = Query(500, ge=1, le=2000)):
    con = db()
    term = search.strip().upper()
    args = []
    where = ["c.active=1", "(c.canonical_material_code IS NULL OR c.canonical_material_code='')",
             "(c.purchasable=1 OR EXISTS(SELECT 1 FROM supplier_info_records x WHERE x.component_code=c.code))"]
    if term:
        q = f"%{term}%"
        where.append("""(UPPER(c.code)=? OR UPPER(c.code) LIKE ? OR UPPER(c.name) LIKE ? OR UPPER(COALESCE(c.category,'')) LIKE ? OR UPPER(COALESCE(c.material_type,'')) LIKE ?
                       OR EXISTS(SELECT 1 FROM components a WHERE a.canonical_material_code=c.code AND (UPPER(a.code)=? OR UPPER(a.name) LIKE ?)))""")
        args += [term, q, q, q, q, term, q]
    args.append(limit)
    rows = con.execute(
        f"""SELECT c.code,c.name,c.category,c.unit,c.material_type,c.purchase_unit,c.purchase_to_base_factor,c.review_status,
                   COUNT(ir.id) AS supplier_count,
                   SUM(CASE WHEN ir.active=1 AND ir.source_status!='BLOCKED' THEN 1 ELSE 0 END) AS active_source_count
            FROM components c
            LEFT JOIN supplier_info_records ir ON ir.component_code=c.code
            WHERE {' AND '.join(where)}
            GROUP BY c.code,c.name,c.category,c.unit,c.material_type,c.purchase_unit,c.purchase_to_base_factor,c.review_status
            ORDER BY CASE WHEN c.code GLOB '[0-9]*' THEN 0 ELSE 1 END,
                     CASE WHEN c.code GLOB '[0-9]*' THEN CAST(c.code AS INTEGER) ELSE 999999999 END,c.code
            LIMIT ?""",
        args,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/purchasing/info-records")
def info_record_list(search: str = "", supplier_no: Optional[int] = None, component_code: str = "",
                     source_status: str = "all", include_inactive: bool = False,
                     limit: int = Query(2000, ge=1, le=5000)):
    con = db()
    where = ["1=1"]
    args = []
    if not include_inactive:
        where.append("ir.active=1")
    if supplier_no is not None:
        where.append("ir.supplier_no=?"); args.append(supplier_no)
    if component_code.strip():
        where.append("ir.component_code=?"); args.append(component_code.strip())
    if source_status and source_status.lower() != "all":
        status = source_status.strip().upper()
        if status not in INFO_SOURCE_STATUSES:
            con.close(); raise HTTPException(400, "Ogiltig källstatus.")
        where.append("ir.source_status=?"); args.append(status)
    term = search.strip()
    if term:
        q = f"%{term}%"
        if pyre.fullmatch(r"\d+", term):
            where.append("(ir.supplier_no=? OR ir.component_code=? OR ir.supplier_material_no LIKE ?)")
            args += [int(term), term, q]
        else:
            where.append("(ir.component_code=? OR ir.component_code LIKE ? OR c.name LIKE ? OR c.category LIKE ? OR s.name LIKE ? OR ir.supplier_material_no LIKE ?)")
            args += [term, q, q, q, q, q]
    args.append(limit)
    rows = con.execute(
        f"""SELECT ir.*,s.name AS supplier_name,s.active AS supplier_active,
                   c.name AS component_name,c.category AS component_category,c.unit AS component_unit,
                   COALESCE(ir.lead_time_days,s.default_lead_time_days) AS effective_lead_time_days,
                   CASE WHEN ir.lead_time_days IS NULL THEN 'SUPPLIER_DEFAULT' ELSE 'INFO_RECORD' END AS lead_time_source
            FROM supplier_info_records ir
            JOIN suppliers s ON s.supplier_no=ir.supplier_no
            JOIN components c ON c.code=ir.component_code
            WHERE {' AND '.join(where)}
            ORDER BY ir.active DESC,c.category,c.name,
                     CASE ir.source_status WHEN 'PREFERRED' THEN 1 WHEN 'APPROVED' THEN 2 WHEN 'BACKUP' THEN 3 WHEN 'BLOCKED' THEN 4 ELSE 5 END,
                     ir.purchase_price IS NULL, ir.purchase_price, ir.supplier_no
            LIMIT ?""",
        args,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/purchasing/info-records/{info_record_id}")
def info_record_detail(info_record_id: int):
    con = db()
    row = con.execute(
        """SELECT ir.*,s.name AS supplier_name,s.active AS supplier_active,s.default_lead_time_days,s.currency AS supplier_currency,
                  c.name AS component_name,c.category AS component_category,c.unit AS component_unit,
                  COALESCE(ir.lead_time_days,s.default_lead_time_days) AS effective_lead_time_days
           FROM supplier_info_records ir
           JOIN suppliers s ON s.supplier_no=ir.supplier_no
           JOIN components c ON c.code=ir.component_code
           WHERE ir.id=?""",
        (info_record_id,),
    ).fetchone()
    if not row:
        con.close(); raise HTTPException(404, "Info record finns inte.")
    result = dict(row)
    events = con.execute(
        "SELECT action,message,created_at FROM supplier_info_record_events WHERE info_record_id=? ORDER BY created_at DESC,id DESC LIMIT 50",
        (info_record_id,),
    ).fetchall()
    result["events"] = [dict(e) for e in events]
    purchases = con.execute(
        """SELECT ph.*
             FROM purchase_history ph
             WHERE ph.supplier_no=? AND ph.component_code=?
             ORDER BY ph.purchase_date DESC,ph.id DESC
             LIMIT 25""",
        (row["supplier_no"], row["component_code"]),
    ).fetchall()
    result["purchase_history"] = [dict(r) for r in purchases]
    con.close()
    return result


@app.post("/api/purchasing/info-records")
def create_info_record(payload: SupplierInfoRecordIn):
    con = db()
    try:
        supplier, material, status, code = _validate_info_record_payload(con, payload)
        cur = con.execute(
            """INSERT INTO supplier_info_records(
                 supplier_no,component_code,supplier_material_no,purchase_price,currency,price_unit_qty,purchase_unit,
                 min_order_qty,order_multiple_qty,lead_time_days,source_status,valid_from,valid_to,notes,active,
                 created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (
                payload.supplier_no,code,payload.supplier_material_no.strip(),payload.purchase_price,
                (payload.currency or supplier["currency"] or "SEK").strip().upper(),payload.price_unit_qty,
                payload.purchase_unit.strip(),payload.min_order_qty,payload.order_multiple_qty,payload.lead_time_days,
                status,payload.valid_from.strip(),payload.valid_to.strip(),payload.notes.strip(),1 if payload.active else 0,
            ),
        )
        rid = cur.lastrowid
        _write_info_record_event(con,rid,"CREATE",f"Info record skapat: leverantör {payload.supplier_no} / material {code}")
        write_master_event(con,"supplier_info_record",rid,"CREATE",f"Leverantör {payload.supplier_no} kopplad till material {code}")
        con.commit()
        return {"ok": True, "id": rid}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


@app.put("/api/purchasing/info-records/{info_record_id}")
def update_info_record(info_record_id: int, payload: SupplierInfoRecordIn):
    con = db()
    try:
        old = con.execute("SELECT * FROM supplier_info_records WHERE id=?", (info_record_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Info record finns inte.")
        supplier, material, status, code = _validate_info_record_payload(con, payload, info_record_id)
        con.execute(
            """UPDATE supplier_info_records SET
                 supplier_no=?,component_code=?,supplier_material_no=?,purchase_price=?,currency=?,price_unit_qty=?,purchase_unit=?,
                 min_order_qty=?,order_multiple_qty=?,lead_time_days=?,source_status=?,valid_from=?,valid_to=?,notes=?,active=?,updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                payload.supplier_no,code,payload.supplier_material_no.strip(),payload.purchase_price,
                (payload.currency or supplier["currency"] or "SEK").strip().upper(),payload.price_unit_qty,
                payload.purchase_unit.strip(),payload.min_order_qty,payload.order_multiple_qty,payload.lead_time_days,
                status,payload.valid_from.strip(),payload.valid_to.strip(),payload.notes.strip(),1 if payload.active else 0,info_record_id,
            ),
        )
        _write_info_record_event(con,info_record_id,"UPDATE",f"Info record uppdaterat: leverantör {payload.supplier_no} / material {code}")
        write_master_event(con,"supplier_info_record",info_record_id,"UPDATE",f"Info record {info_record_id} uppdaterat")
        con.commit()
        return {"ok": True, "id": info_record_id}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


@app.delete("/api/purchasing/info-records/{info_record_id}")
def archive_info_record(info_record_id: int):
    con = db()
    try:
        row = con.execute("SELECT * FROM supplier_info_records WHERE id=?", (info_record_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Info record finns inte.")
        con.execute("UPDATE supplier_info_records SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (info_record_id,))
        _write_info_record_event(con,info_record_id,"ARCHIVE",f"Info record arkiverat: leverantör {row['supplier_no']} / material {row['component_code']}")
        write_master_event(con,"supplier_info_record",info_record_id,"ARCHIVE",f"Info record {info_record_id} arkiverat")
        con.commit()
        return {"ok": True}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


# ============================================================
# v0.7.8 — PURCHASE HISTORY / "WHERE DID WE BUY THIS LAST TIME?"
# ============================================================

class PurchaseHistoryIn(BaseModel):
    supplier_no: int
    component_code: str
    purchase_date: str
    quantity: Optional[float] = None
    purchase_unit: str = ""
    purchase_price: Optional[float] = None
    currency: str = "SEK"
    price_unit_qty: float = 1
    supplier_material_no: str = ""
    external_reference: str = ""
    notes: str = ""


def _purchase_history_where(material_search: str = "", supplier_search: str = "", date_from: str = "", date_to: str = "", reference_search: str = ""):
    where = ["1=1"]
    args = []
    mt = material_search.strip()
    if mt:
        q = f"%{mt}%"
        where.append("""(ph.component_code=? OR ph.component_code LIKE ? OR c.name LIKE ? OR c.category LIKE ?
                         OR EXISTS(SELECT 1 FROM components a WHERE a.canonical_material_code=ph.component_code AND (a.code=? OR a.name LIKE ?)))""")
        args += [mt, q, q, q, mt.upper(), q]
    st = supplier_search.strip()
    if st:
        q = f"%{st}%"
        if pyre.fullmatch(r"\d+", st):
            where.append("ph.supplier_no=?")
            args.append(int(st))
        else:
            where.append("s.name LIKE ?")
            args.append(q)
    if date_from.strip():
        where.append("ph.purchase_date>=?"); args.append(date_from.strip())
    if date_to.strip():
        where.append("ph.purchase_date<=?"); args.append(date_to.strip())
    rt = reference_search.strip()
    if rt:
        where.append("ph.external_reference LIKE ?")
        args.append(f"%{rt}%")
    return where, args


@app.get("/api/purchasing/history")
def purchase_history_list(material_search: str = "", supplier_search: str = "", date_from: str = "", date_to: str = "", reference_search: str = "", limit: int = Query(1000, ge=1, le=5000)):
    con = db()
    where, args = _purchase_history_where(material_search, supplier_search, date_from, date_to, reference_search)
    args.append(limit)
    rows = con.execute(
        f"""SELECT ph.*,s.name AS supplier_name,c.name AS component_name,c.category AS component_category,c.unit AS component_unit
              FROM purchase_history ph
              JOIN suppliers s ON s.supplier_no=ph.supplier_no
              JOIN components c ON c.code=ph.component_code
             WHERE {' AND '.join(where)}
             ORDER BY ph.purchase_date DESC,ph.id DESC
             LIMIT ?""",
        args,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/purchasing/history-summary")
def purchase_history_summary(material_search: str = "", supplier_search: str = "", date_from: str = "", date_to: str = "", reference_search: str = ""):
    con = db()
    where, args = _purchase_history_where(material_search, supplier_search, date_from, date_to, reference_search)
    base = f"""FROM purchase_history ph
                JOIN suppliers s ON s.supplier_no=ph.supplier_no
                JOIN components c ON c.code=ph.component_code
                WHERE {' AND '.join(where)}"""
    agg = con.execute(
        f"""SELECT COUNT(*) AS purchase_count,
                    COUNT(DISTINCT ph.supplier_no) AS supplier_count,
                    COUNT(DISTINCT ph.component_code) AS material_count,
                    SUM(COALESCE(ph.quantity,0)) AS total_quantity
             {base}""",
        args,
    ).fetchone()
    last = con.execute(
        f"""SELECT ph.*,s.name AS supplier_name,c.name AS component_name,c.category AS component_category,c.unit AS component_unit
             {base}
             ORDER BY ph.purchase_date DESC,ph.id DESC LIMIT 1""",
        args,
    ).fetchone()
    con.close()
    return {
        "purchase_count": int(agg["purchase_count"] or 0),
        "supplier_count": int(agg["supplier_count"] or 0),
        "material_count": int(agg["material_count"] or 0),
        "total_quantity": float(agg["total_quantity"] or 0),
        "last_purchase": dict(last) if last else None,
    }


@app.get("/api/purchasing/history/{history_id}")
def purchase_history_detail(history_id: int):
    con = db()
    row = con.execute(
        """SELECT ph.*,s.name AS supplier_name,s.active AS supplier_active,
                  c.name AS component_name,c.category AS component_category,c.unit AS component_unit
             FROM purchase_history ph
             JOIN suppliers s ON s.supplier_no=ph.supplier_no
             JOIN components c ON c.code=ph.component_code
             WHERE ph.id=?""",
        (history_id,),
    ).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "Inköpshistorikposten finns inte.")
    return dict(row)


@app.post("/api/purchasing/history")
def create_purchase_history(payload: PurchaseHistoryIn, request: Request):
    requested_code = payload.component_code.strip().upper()
    if not requested_code:
        raise HTTPException(400, "Material krävs.")
    try:
        date.fromisoformat(payload.purchase_date.strip())
    except Exception:
        raise HTTPException(400, "Inköpsdatum måste anges som ett giltigt datum.")
    if payload.quantity is not None and payload.quantity <= 0:
        raise HTTPException(400, "Kvantitet måste vara större än 0.")
    if payload.purchase_price is not None and payload.purchase_price < 0:
        raise HTTPException(400, "Inköpspris kan inte vara negativt.")
    if payload.price_unit_qty <= 0:
        raise HTTPException(400, "Prisenheten måste vara större än 0.")
    con = db()
    try:
        supplier = con.execute("SELECT * FROM suppliers WHERE supplier_no=?", (payload.supplier_no,)).fetchone()
        if not supplier:
            raise HTTPException(400, f"Leverantör {payload.supplier_no} finns inte.")
        material, alias = _resolve_canonical_material(con, requested_code)
        if not material:
            raise HTTPException(400, f"Material {requested_code} finns inte.")
        code = material["code"]
        user = getattr(request.state, "user", None)
        created_by = user["display_name"] if user is not None else "System"
        cur = con.execute(
            """INSERT INTO purchase_history(
                 supplier_no,component_code,purchase_date,quantity,purchase_unit,purchase_price,currency,
                 price_unit_qty,supplier_material_no,external_reference,source,notes,created_by
               ) VALUES(?,?,?,?,?,?,?,?,?,?, 'MANUAL',?,?)""",
            (
                payload.supplier_no,code,payload.purchase_date.strip(),payload.quantity,payload.purchase_unit.strip(),
                payload.purchase_price,(payload.currency or supplier["currency"] or "SEK").strip().upper(),
                payload.price_unit_qty,payload.supplier_material_no.strip(),payload.external_reference.strip(),
                payload.notes.strip(),created_by,
            ),
        )
        hid = cur.lastrowid
        write_master_event(con,"purchase_history",hid,"CREATE",f"Historiskt inköp registrerat: leverantör {payload.supplier_no} / material {code} / {payload.purchase_date.strip()}")
        con.commit()
        return {"ok": True, "id": hid}
    except HTTPException:
        con.rollback(); raise
    finally:
        con.close()


@app.get("/api/customer-recommendations/{customer_no}")
def customer_recommendations(customer_no: int, limit: int = Query(8, ge=1, le=20), order_date: str = "", pricing_date: str = ""):
    con = db()
    if not con.execute("SELECT 1 FROM customers WHERE customer_no=?",(customer_no,)).fetchone():
        con.close(); raise HTTPException(404,"Kunden finns inte.")
    rows=con.execute(
        """SELECT l.article_no,a.name,
                  COUNT(DISTINCT o.id) order_count,
                  SUM(l.ordered_qty) total_qty,
                  MAX(COALESCE(o.delivery_date,o.order_date)) last_date,
                  SUM(CASE WHEN COALESCE(o.delivery_date,o.order_date)>=date('now','-180 day') THEN 1 ELSE 0 END) recent_hits
           FROM orders o
           JOIN order_lines l ON l.order_id=o.id
           JOIN articles a ON a.article_no=l.article_no AND a.active=1
           WHERE o.customer_no=?
           GROUP BY l.article_no,a.name
           ORDER BY (COUNT(DISTINCT o.id)*10 +
                     SUM(CASE WHEN COALESCE(o.delivery_date,o.order_date)>=date('now','-180 day') THEN 1 ELSE 0 END)*4) DESC,
                    MAX(COALESCE(o.delivery_date,o.order_date)) DESC
           LIMIT ?""",
        (customer_no,limit),
    ).fetchall()
    when=pricing_date or order_date or date.today().isoformat()
    result=[]
    for r in rows:
        price,source=price_for(con,customer_no,r["article_no"],when)
        d=dict(r); d["current_price"]=price; d["price_source"]=source; result.append(d)
    con.close()
    return result


@app.get("/api/articles")
def article_list(search: str = "", include_inactive: bool = False, limit: int = Query(800, ge=1, le=3000)):
    con = db()
    where=["1=1"]
    args=[]
    if not include_inactive:
        where.append("active=1")
    if search.strip():
        q=f"%{search.strip()}%"
        where.append("(CAST(article_no AS TEXT) LIKE ? OR name LIKE ? OR supplier_article_no LIKE ?)")
        args += [q,q,q]
    args.append(limit)
    rows=con.execute(
        f"SELECT * FROM articles WHERE {' AND '.join(where)} ORDER BY active DESC,article_no LIMIT ?",args
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/articles/{article_no}")
def article_detail(article_no: int):
    con = db()
    article = con.execute("SELECT * FROM articles WHERE article_no=?", (article_no,)).fetchone()
    if not article:
        con.close()
        raise HTTPException(404, "Artikeln finns inte.")
    comps = con.execute(
        """SELECT c.code,c.name,c.category,ac.qty_per_order_unit
           FROM article_components ac JOIN components c ON c.code=ac.component_code
           WHERE ac.article_no=? ORDER BY c.category,c.name""",
        (article_no,),
    ).fetchall()
    result = dict(article)
    result["components"] = [dict(r) for r in comps]
    con.close()
    return result


class ArticleMasterIn(BaseModel):
    article_no: int
    supplier_article_no: str = ""
    station_id: Optional[int] = None
    pallets_per_hour: Optional[float] = None
    production_time_factor: float = 0
    name: str
    order_unit_qty: float = 0
    unit: str = ""
    kg_per_order_unit: float = 0
    units_per_label: float = 0
    deposit_per_unit: float = 0
    units_per_pallet: float = 0
    units_per_ppl: float = 0
    active: bool = True


@app.post("/api/articles")
def create_article(payload: ArticleMasterIn):
    if not payload.name.strip(): raise HTTPException(400,"Benämning krävs.")
    con=db()
    try:
        existing = con.execute("SELECT name,active FROM articles WHERE article_no=?",(payload.article_no,)).fetchone()
        if existing:
            status = "arkiverad" if not existing["active"] else "aktiv"
            raise HTTPException(
                409,
                f"Artikelnummer {payload.article_no} används redan av {existing['name']} ({status})."
            )
        con.execute(
            """INSERT INTO articles(
                 article_no,supplier_article_no,station_id,pallets_per_hour,production_time_factor,
                 name,order_unit_qty,unit,kg_per_order_unit,units_per_label,deposit_per_unit,
                 units_per_pallet,units_per_ppl,active,source,user_modified,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'erp_v2',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (payload.article_no,payload.supplier_article_no,payload.station_id,payload.pallets_per_hour,
             payload.production_time_factor,payload.name.strip(),payload.order_unit_qty,payload.unit,
             payload.kg_per_order_unit,payload.units_per_label,payload.deposit_per_unit,
             payload.units_per_pallet,payload.units_per_ppl,1 if payload.active else 0),
        )
        write_master_event(con,"article",payload.article_no,"CREATE",f"Artikel {payload.article_no} skapad i ERP v2")
        con.commit(); return {"ok":True,"article_no":payload.article_no}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()


@app.put("/api/articles/{article_no}")
def update_article(article_no: int, payload: ArticleMasterIn):
    if article_no != payload.article_no: raise HTTPException(400,"Artikelnummer kan inte ändras efter att artikeln skapats.")
    if not payload.name.strip(): raise HTTPException(400,"Benämning krävs.")
    con=db()
    try:
        if not con.execute("SELECT 1 FROM articles WHERE article_no=?",(article_no,)).fetchone():
            raise HTTPException(404,"Artikeln finns inte.")
        con.execute(
            """UPDATE articles SET supplier_article_no=?,station_id=?,pallets_per_hour=?,production_time_factor=?,
                 name=?,order_unit_qty=?,unit=?,kg_per_order_unit=?,units_per_label=?,deposit_per_unit=?,
                 units_per_pallet=?,units_per_ppl=?,active=?,user_modified=1,updated_at=CURRENT_TIMESTAMP
               WHERE article_no=?""",
            (payload.supplier_article_no,payload.station_id,payload.pallets_per_hour,payload.production_time_factor,
             payload.name.strip(),payload.order_unit_qty,payload.unit,payload.kg_per_order_unit,payload.units_per_label,
             payload.deposit_per_unit,payload.units_per_pallet,payload.units_per_ppl,1 if payload.active else 0,article_no),
        )
        write_master_event(con,"article",article_no,"UPDATE",f"Artikel {article_no} uppdaterad")
        con.commit(); return {"ok":True,"article_no":article_no}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()


@app.delete("/api/articles/{article_no}")
def archive_article(article_no: int):
    con=db()
    try:
        row=con.execute("SELECT name FROM articles WHERE article_no=?",(article_no,)).fetchone()
        if not row: raise HTTPException(404,"Artikeln finns inte.")
        con.execute("UPDATE articles SET active=0,user_modified=1,updated_at=CURRENT_TIMESTAMP WHERE article_no=?",(article_no,))
        write_master_event(con,"article",article_no,"ARCHIVE",f"Artikel {article_no} — {row['name']} arkiverad")
        con.commit(); return {"ok":True}
    except HTTPException:
        con.rollback(); raise
    finally: con.close()


@app.get("/api/calculate-line")
def calculate_line_api(customer_no: int, article_no: int, qty: float, order_date: str = "", pricing_date: str = "", manual_price: Optional[float] = None, manual_reason: str = ""):
    if qty <= 0:
        raise HTTPException(400, "Antal måste vara större än 0.")
    con = db()
    customer = con.execute("SELECT 1 FROM customers WHERE customer_no=?", (customer_no,)).fetchone()
    if not customer:
        con.close()
        raise HTTPException(404, "Kunden finns inte.")
    when = pricing_date or order_date or date.today().isoformat()
    result = calculate_line(con, customer_no, article_no, qty, when, manual_price, manual_reason)
    con.close()
    return result


class LineIn(BaseModel):
    article_no: int
    qty: float = Field(gt=0)
    manual_price: Optional[float] = None
    manual_reason: str = ""


class OrderIn(BaseModel):
    order_no: Optional[int] = None
    customer_no: int
    order_date: str
    delivery_date: str
    internal_reference: str = ""
    customer_reference: str = ""
    info: str = ""
    status: str = "Ny"
    lines: list[LineIn]


@app.post("/api/orders")
def create_order(payload: OrderIn):
    con = db()
    try:
        customer = con.execute("SELECT * FROM customers WHERE customer_no=? AND active=1", (payload.customer_no,)).fetchone()
        if not customer:
            raise HTTPException(400, "Okänd kund.")

        if not payload.lines:
            raise HTTPException(400, "Ordern måste ha minst en artikel.")

        if payload.order_no is None:
            excel_current = int(con.execute("SELECT value FROM settings WHERE key='current_excel_order_no'").fetchone()["value"])
            max_db = int(con.execute("SELECT COALESCE(MAX(order_no),0) n FROM orders").fetchone()["n"])
            order_no = max(max_db, excel_current) + 1
        else:
            order_no = payload.order_no

        exists = con.execute("SELECT 1 FROM orders WHERE order_no=?", (order_no,)).fetchone()
        if exists:
            raise HTTPException(409, f"Ordernummer {order_no} finns redan.")

        cur = con.execute(
            """INSERT INTO orders(
                 order_no,source,customer_no,order_date,delivery_date,internal_reference,
                 customer_reference,info,status,invoice_email
               ) VALUES(?,'erp_v2',?,?,?,?,?,?,?,?)""",
            (
                order_no,payload.customer_no,payload.order_date,payload.delivery_date,
                payload.internal_reference,payload.customer_reference,payload.info,
                payload.status,customer["invoice_email"] or ""
            ),
        )
        order_id = cur.lastrowid

        station_totals = {}
        total_goods = total_deposit = total_kg = 0.0

        for idx, line in enumerate(payload.lines, start=1):
            calc = calculate_line(con, payload.customer_no, line.article_no, line.qty, payload.delivery_date, line.manual_price, line.manual_reason)
            if calc["unit_price"] is None:
                raise HTTPException(400, f"Pris saknas för kund {payload.customer_no} / artikel {line.article_no} på leveransdatum {payload.delivery_date}.")

            line_cur = con.execute(
                """INSERT INTO order_lines(
                     order_id,line_no,article_no,ordered_qty,sale_qty,description_snapshot,
                     unit_price,price_source,price_condition_id,price_date,price_override_reason,
                     deposit_amount,goods_value,kg,pallets,ppl,station_id,production_time,units_per_label
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id,idx,line.article_no,line.qty,calc["sale_qty"],calc["name"],
                    calc["unit_price"],calc["price_source"],calc["price_condition_id"],calc["price_date"],calc["price_override_reason"],
                    calc["deposit_amount"],calc["goods_value"] or 0,calc["kg"],calc["pallets"],calc["ppl"],
                    calc["station_id"],calc["production_time"],calc["units_per_label"]
                ),
            )
            line_id = line_cur.lastrowid

            total_goods += float(calc["goods_value"] or 0)
            total_deposit += float(calc["deposit_amount"] or 0)
            total_kg += float(calc["kg"] or 0)

            if calc["station_id"] and int(calc["station_id"]) in {1,2,3,4}:
                station_totals.setdefault(int(calc["station_id"]), 0.0)
                station_totals[int(calc["station_id"])] += max(0.0,float(calc["pallets"] or 0))

            for comp in calc["material_usage"]:
                con.execute(
                    """INSERT INTO order_material_usage(
                         order_id,order_line_id,component_code,component_name,category,quantity
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        order_id,line_id,comp["code"],comp["name"],comp["category"],comp["quantity"]
                    ),
                )

            if calc["suggested_labels"]:
                con.execute(
                    """INSERT INTO label_jobs(order_id,order_line_id,article_no,template_name,copies,status)
                       VALUES(?,?,?,?,?,'Förslag')""",
                    (
                        order_id,line_id,line.article_no,
                        customer["label_name"] or "Standard",int(calc["suggested_labels"])
                    ),
                )

        for station_id, raw_pallets in station_totals.items():
            required_pallets=float(math.ceil(raw_pallets-1e-12)) if raw_pallets>0 else 0
            if required_pallets<=0:
                continue
            con.execute(
                """INSERT INTO station_requirements(order_id,station_id,required_pallets,ready,source_basis,raw_pallets)
                   VALUES(?,?,?,0,'ERP_ORDER',?)""",
                (order_id,station_id,required_pallets,raw_pallets),
            )

        con.execute(
            """INSERT INTO transport_bookings(order_id,carrier,status,payload_json)
               VALUES(?,?, 'Ej bokad', ?)""",
            (
                order_id, customer["carrier"] or "",
                json.dumps({
                    "order_no": order_no,
                    "customer_no": payload.customer_no,
                    "delivery_date": payload.delivery_date,
                    "weight_kg": total_kg,
                    "prototype": True
                }, ensure_ascii=False)
            ),
        )

        con.execute(
            "INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
            (order_id,"ORDER_CREATED",f"Order {order_no} skapad"),
        )
        con.commit()

        return {
            "ok": True,
            "order_id": order_id,
            "order_no": order_no,
            "totals": {
                "goods": total_goods,
                "deposit": total_deposit,
                "invoice_total": total_goods + total_deposit,
                "kg": total_kg,
            },
        }
    except HTTPException:
        con.rollback()
        raise
    except sqlite3.IntegrityError as e:
        con.rollback()
        raise HTTPException(409, str(e))
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _orders_filter(search: str = "", source: str = "all", status: str = "all", weekday: str = "all", from_date: str = "", to_date: str = ""):
    """Build the shared Orderhistorik filter used by both screen and Excel export."""
    where = ["1=1"]
    args = []

    if search.strip():
        term = search.strip()
        if pyre.fullmatch(r"\d{3}", term):
            where.append("o.customer_no=?")
            args.append(int(term))
        elif pyre.fullmatch(r"\d{5}", term):
            where.append("o.order_no=?")
            args.append(int(term))
        elif term.casefold().startswith("ref:"):
            ref_term = term[4:].strip()
            if ref_term:
                where.append("o.customer_reference LIKE ?")
                args.append(f"%{ref_term}%")
        else:
            q = f"%{term}%"
            where.append(
                "(CAST(o.order_no AS TEXT) LIKE ? "
                "OR CAST(o.customer_no AS TEXT) = ? "
                "OR c.name LIKE ? "
                "OR o.customer_reference LIKE ?)"
            )
            args += [q, term, q, q]
    if source != "all":
        where.append("o.source=?")
        args.append(source)
    if status != "all":
        where.append("o.status=?")
        args.append(status)
    if from_date:
        where.append("o.delivery_date>=?")
        args.append(from_date)
    if to_date:
        where.append("o.delivery_date<=?")
        args.append(to_date)
    if weekday != "all":
        where.append("strftime('%w',o.delivery_date)=?")
        args.append(str(weekday))
    return where, args


def _excel_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_inline_cell(ref: str, value, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr} t="n"><v>{value}</v></c>'
    safe = xml_escape(str(value), {'"': '&quot;'})
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{safe}</t></is></c>'


def _build_order_history_xlsx(rows) -> bytes:
    """Dependency-free XLSX writer for the order-history export."""
    headers = [
        "Ordernr", "Kundnr", "Kund", "Orderdatum", "Leveransdatum", "Veckodag",
        "Status", "Kg", "Värde (SEK)", "Källa", "Kundreferens", "Fakturanr"
    ]
    weekday_names = {0:"Måndag", 1:"Tisdag", 2:"Onsdag", 3:"Torsdag", 4:"Fredag", 5:"Lördag", 6:"Söndag"}
    data = []
    for r in rows:
        delivery = r["delivery_date"] or ""
        weekday_name = ""
        if delivery:
            try:
                weekday_name = weekday_names[date.fromisoformat(str(delivery)[:10]).weekday()]
            except Exception:
                pass
        source_label = "Excel historik" if r["source"] == "legacy" else ("Excel aktuell" if r["source"] == "excel_current" else "ERP v2")
        data.append([
            r["order_no"], r["customer_no"], r["customer_name"], r["order_date"] or "",
            delivery, weekday_name, r["status"] or "", float(r["total_kg"] or 0),
            float(r["total_value"] or 0), source_label, r["customer_reference"] or "", r["invoice_no"] or ""
        ])

    rows_xml = []
    cells = [_xlsx_inline_cell(f"{_excel_col(i+1)}1", h, 1) for i, h in enumerate(headers)]
    rows_xml.append(f'<row r="1" ht="22" customHeight="1">{"".join(cells)}</row>')
    for ri, values in enumerate(data, start=2):
        cells = []
        for ci, val in enumerate(values, start=1):
            style = 2 if ci == 8 else (3 if ci == 9 else 0)
            cells.append(_xlsx_inline_cell(f"{_excel_col(ci)}{ri}", val, style))
        rows_xml.append(f'<row r="{ri}">{"".join(cells)}</row>')

    last_row = max(1, len(data) + 1)
    last_col = _excel_col(len(headers))
    widths = [12,10,30,13,15,13,18,13,16,16,22,14]
    cols_xml = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths, start=1))
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>{cols_xml}</cols>
  <sheetData>{''.join(rows_xml)}</sheetData>
  <autoFilter ref="A1:{last_col}{last_row}"/>
</worksheet>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Orderhistorik" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="# ##0.00 [$kr-sv-SE]"/></numFmts>
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left/><right/><top/><bottom style="thin"><color rgb="FFD9E2F3"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out.getvalue()


@app.get("/api/orders")
def list_orders(
    search: str = "",
    source: str = "all",
    status: str = "all",
    weekday: str = "all",
    from_date: str = "",
    to_date: str = "",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    con = db()
    where, args = _orders_filter(search, source, status, weekday, from_date, to_date)

    sql = f"""
      SELECT o.id,o.order_no,o.source,o.customer_no,c.name customer_name,
             o.order_date,o.delivery_date,o.status,o.customer_reference,o.invoice_no,
             CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                  ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END total_kg,
             CASE WHEN o.source='legacy' THEN o.legacy_invoice_total
                  ELSE COALESCE((SELECT SUM(goods_value+deposit_amount) FROM order_lines l WHERE l.order_id=o.id),0) END total_value,
             (SELECT COUNT(*) FROM order_lines l WHERE l.order_id=o.id) line_count
      FROM orders o JOIN customers c ON c.customer_no=o.customer_no
      WHERE {' AND '.join(where)}
      ORDER BY o.order_no DESC LIMIT ? OFFSET ?
    """
    args += [limit, offset]
    rows = con.execute(sql, args).fetchall()

    count_sql = f"""SELECT COUNT(*) n FROM orders o JOIN customers c ON c.customer_no=o.customer_no
                    WHERE {' AND '.join(where)}"""
    count_args = args[:-2]
    total = con.execute(count_sql, count_args).fetchone()["n"]

    statuses = [r["status"] for r in con.execute("SELECT DISTINCT status FROM orders ORDER BY status")]
    con.close()
    return {"rows":[dict(r) for r in rows],"total":total,"statuses":statuses}


@app.get("/api/orders/export.xlsx")
def export_orders_excel(
    search: str = "",
    source: str = "all",
    status: str = "all",
    weekday: str = "all",
    from_date: str = "",
    to_date: str = "",
):
    con = db()
    where, args = _orders_filter(search, source, status, weekday, from_date, to_date)
    rows = con.execute(
        f"""
        SELECT o.id,o.order_no,o.source,o.customer_no,c.name customer_name,
               o.order_date,o.delivery_date,o.status,o.customer_reference,o.invoice_no,
               CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                    ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END total_kg,
               CASE WHEN o.source='legacy' THEN o.legacy_invoice_total
                    ELSE COALESCE((SELECT SUM(goods_value+deposit_amount) FROM order_lines l WHERE l.order_id=o.id),0) END total_value
        FROM orders o JOIN customers c ON c.customer_no=o.customer_no
        WHERE {' AND '.join(where)}
        ORDER BY o.order_no DESC
        """,
        args,
    ).fetchall()
    con.close()
    payload = _build_order_history_xlsx(rows)
    filename = f"Orderhistorik_{date.today().isoformat()}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@app.get("/api/orders/{order_id}")
def order_detail(order_id: int):
    con = db()
    order = con.execute(
        """SELECT o.*,c.name customer_name,c.address,c.postal_address,c.carrier,c.pallet_system,
                  c.srs_no,c.label_name,c.phone,c.loading_time,c.pallet_material,c.smak_profile,c.bonus_pct
           FROM orders o JOIN customers c ON c.customer_no=o.customer_no
           WHERE o.id=?""",
        (order_id,),
    ).fetchone()
    if not order:
        con.close()
        raise HTTPException(404, "Ordern finns inte.")

    lines = con.execute(
        """SELECT l.*,a.name article_name,a.unit
           FROM order_lines l
           LEFT JOIN articles a ON a.article_no=l.article_no
           WHERE l.order_id=? ORDER BY l.line_no""",
        (order_id,),
    ).fetchall()
    materials = con.execute(
        """SELECT component_code,component_name,category,SUM(quantity) quantity
           FROM order_material_usage WHERE order_id=?
           GROUP BY component_code,component_name,category
           ORDER BY category,component_name""",
        (order_id,),
    ).fetchall()
    stations = con.execute(
        "SELECT * FROM station_requirements WHERE order_id=? ORDER BY station_id",
        (order_id,),
    ).fetchall()
    activity = con.execute(
        "SELECT * FROM activity_events WHERE order_id=? ORDER BY created_at DESC,id DESC LIMIT 50",
        (order_id,),
    ).fetchall()
    labels = con.execute(
        """SELECT lj.*,a.name article_name
           FROM label_jobs lj LEFT JOIN articles a ON a.article_no=lj.article_no
           WHERE lj.order_id=? ORDER BY lj.id""",
        (order_id,),
    ).fetchall()
    transport = con.execute(
        "SELECT * FROM transport_bookings WHERE order_id=? ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()

    result = dict(order)
    result["lines"] = [dict(r) for r in lines]
    result["materials"] = [dict(r) for r in materials]
    result["stations"] = [dict(r) for r in stations]
    result["activity"] = [dict(r) for r in activity]
    result["labels"] = [dict(r) for r in labels]
    result["transport"] = dict(transport) if transport else None
    result["totals"] = order_totals(con, order_id, order["source"])
    result["fortnox_legacy_payload"] = parse_json(order["fortnox_payload_json"], {})
    result["legacy_components"] = parse_json(order["legacy_components_json"], {})

    # Legacy Excel did not store a permanent row-price snapshot in `sparade`.
    # v0.7.3 reconstructs row values from the article/customer price masters.
    # For transparency, validate the reconstructed rows against the immutable
    # saved order totals. Recent orders normally validate exactly; older orders
    # can differ if their historical price has since changed.
    if order["source"] == "legacy":
        line_gross = sum(float(r["goods_value"] or 0) for r in lines)
        bonus_pct = float(order["bonus_pct"] or 0)
        reconstructed_net = line_gross * (1 - bonus_pct)
        reconstructed_kg = sum(float(r["kg"] or 0) for r in lines)
        saved_goods = float(order["legacy_goods_total"] or 0)
        saved_kg = float(order["legacy_total_kg"] or 0)
        price_rows_present = any(r["unit_price"] is not None for r in lines)
        result["legacy_line_data_status"] = {
            "price_verified": bool(price_rows_present and abs(reconstructed_net - saved_goods) <= 0.05),
            "kg_verified": bool(abs(reconstructed_kg - saved_kg) <= 0.05),
            "saved_goods": saved_goods,
            "reconstructed_goods_after_bonus": reconstructed_net,
            "saved_kg": saved_kg,
            "reconstructed_kg": reconstructed_kg,
            "bonus_pct": bonus_pct,
        }

    con.close()
    return result


class StatusIn(BaseModel):
    status: str


@app.patch("/api/orders/{order_id}/status")
def update_status(order_id: int, payload: StatusIn):
    con = db()
    order = con.execute("SELECT order_no,source FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404, "Ordern finns inte.")
    if order["source"] in {"legacy","excel_current"}:
        con.close(); raise HTTPException(400, "Importerade Excel-order är skrivskyddade i prototypen.")
    con.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (payload.status,order_id))
    con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
                (order_id,"STATUS",f"Status ändrad till {payload.status}"))
    con.commit(); con.close()
    return {"ok":True}


@app.get("/api/production")
def production(days_back: int = 1, days_forward: int = 14):
    con = db()
    today_d=date.today()
    start=(today_d-timedelta(days=days_back)).isoformat()
    end=(today_d+timedelta(days=days_forward)).isoformat()
    _ensure_station_requirements(con,start,end)

    rows=con.execute(
        """SELECT o.id,o.order_no,o.source,o.delivery_date,o.status,o.customer_no,c.name customer_name,
                  CASE WHEN o.source IN ('legacy','excel_current') THEN o.legacy_total_kg
                       ELSE COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) END total_kg,
                  sr.station_id,sr.required_pallets,sr.raw_pallets,sr.ready,sr.ready_at,sr.source_basis
           FROM orders o JOIN customers c ON c.customer_no=o.customer_no
           JOIN station_requirements sr ON sr.order_id=o.id
           WHERE (o.delivery_date BETWEEN ? AND ? OR o.source='excel_current') AND sr.station_id BETWEEN 1 AND 4
           ORDER BY o.delivery_date,o.order_no,sr.station_id""",(start,end)).fetchall()

    grouped={}
    for r in rows:
        oid=r["id"]
        if oid not in grouped:
            d=date.fromisoformat(r["delivery_date"]) if r["delivery_date"] else today_d
            bucket="OVERDUE" if d<today_d else ("TODAY" if d==today_d else ("TOMORROW" if d==today_d+timedelta(days=1) else "LATER"))
            grouped[oid]={"id":oid,"order_no":r["order_no"],"source":r["source"],"delivery_date":r["delivery_date"],
                          "status":r["status"],"customer_no":r["customer_no"],"customer_name":r["customer_name"],
                          "total_kg":float(r["total_kg"] or 0),"bucket":bucket,"stations":[]}
        grouped[oid]["stations"].append({"station_id":r["station_id"],"required_pallets":r["required_pallets"],
                                            "raw_pallets":r["raw_pallets"],"ready":bool(r["ready"]),"ready_at":r["ready_at"],
                                            "source_basis":r["source_basis"]})
    station_summary={str(x):{"orders":0,"pallets":0.0,"ready_orders":0} for x in range(1,5)}
    bucket_counts={"OVERDUE":0,"TODAY":0,"TOMORROW":0,"LATER":0}
    for o in grouped.values():
        o["all_ready"]=bool(o["stations"]) and all(x["ready"] for x in o["stations"])
        bucket_counts[o["bucket"]]+=1
        for st in o["stations"]:
            k=str(st["station_id"]); station_summary[k]["orders"]+=1; station_summary[k]["pallets"]+=float(st["required_pallets"] or 0)
            if st["ready"]: station_summary[k]["ready_orders"]+=1

    events=con.execute(
        """SELECT a.created_at,a.event_type,a.message,o.order_no,o.source,c.name customer_name
           FROM activity_events a LEFT JOIN orders o ON o.id=a.order_id LEFT JOIN customers c ON c.customer_no=o.customer_no
           WHERE a.event_type IN ('STATION_READY','STATION_REOPENED','ORDER_READY','ORDER_CREATED')
           ORDER BY a.created_at DESC,a.id DESC LIMIT 20""").fetchall()
    result={"orders":list(grouped.values()),"events":[dict(r) for r in events],"start":start,"end":end,
            "today":today_d.isoformat(),"bucket_counts":bucket_counts,"station_summary":station_summary}
    con.close(); return result


@app.post("/api/production/{order_id}/station/{station_id}/toggle")
def toggle_station(order_id: int, station_id: int):
    con = db()
    row = con.execute(
        """SELECT sr.*,o.order_no,c.name customer_name
           FROM station_requirements sr
           JOIN orders o ON o.id=sr.order_id
           JOIN customers c ON c.customer_no=o.customer_no
           WHERE sr.order_id=? AND sr.station_id=?""",
        (order_id,station_id),
    ).fetchone()
    if not row:
        con.close(); raise HTTPException(404, "Stationen finns inte på ordern.")

    new_ready = 0 if row["ready"] else 1
    ready_at = datetime.now().isoformat(timespec="seconds") if new_ready else None
    con.execute(
        """UPDATE station_requirements SET ready=?,ready_at=?,updated_at=CURRENT_TIMESTAMP
           WHERE order_id=? AND station_id=?""",
        (new_ready,ready_at,order_id,station_id),
    )
    event_type = "STATION_READY" if new_ready else "STATION_REOPENED"
    message = f"S{station_id} markerad READY" if new_ready else f"S{station_id} öppnad igen"
    con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",(order_id,event_type,message))

    statuses = con.execute("SELECT ready FROM station_requirements WHERE order_id=?",(order_id,)).fetchall()
    all_ready = bool(statuses) and all(r["ready"] for r in statuses)
    if all_ready and new_ready:
        con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
                    (order_id,"ORDER_READY","Hela ordern är READY"))
    con.commit(); con.close()
    return {"ok":True,"ready":bool(new_ready),"all_ready":all_ready}


@app.get("/api/transport")
def transport():
    con = db()
    rows = con.execute(
        """SELECT o.id,o.order_no,o.delivery_date,o.customer_no,c.name customer_name,c.carrier,
                  tb.status,tb.external_id,
                  COALESCE((SELECT SUM(kg) FROM order_lines l WHERE l.order_id=o.id),0) total_kg,
                  COALESCE((SELECT SUM(pallets) FROM order_lines l WHERE l.order_id=o.id),0) pallets,
                  COALESCE((SELECT SUM(ppl) FROM order_lines l WHERE l.order_id=o.id),0) ppl
           FROM orders o
           JOIN customers c ON c.customer_no=o.customer_no
           LEFT JOIN transport_bookings tb ON tb.order_id=o.id
           WHERE o.source='erp_v2'
           ORDER BY o.delivery_date DESC,o.order_no DESC LIMIT 100"""
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


class TransportStatusIn(BaseModel):
    status: str


@app.patch("/api/transport/{order_id}")
def transport_status(order_id: int, payload: TransportStatusIn):
    allowed={"Ej bokad","Planerad","Bokad manuellt"}
    if payload.status not in allowed:
        raise HTTPException(400,"Ogiltig status.")
    con=db()
    row=con.execute("SELECT id FROM transport_bookings WHERE order_id=? ORDER BY id DESC LIMIT 1",(order_id,)).fetchone()
    if not row:
        con.close(); raise HTTPException(404,"Transportpost saknas.")
    con.execute("UPDATE transport_bookings SET status=? WHERE id=?",(payload.status,row["id"]))
    con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
                (order_id,"TRANSPORT",f"Transportstatus: {payload.status}"))
    con.commit(); con.close()
    return {"ok":True}


@app.get("/api/labels/{order_id}")
def labels(order_id: int):
    con=db()
    order=con.execute("""SELECT o.id,o.order_no,o.source,o.customer_no,o.delivery_date,
                                c.name customer_name,c.label_name,c.loading_time
                         FROM orders o JOIN customers c ON c.customer_no=o.customer_no WHERE o.id=?""",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Ordern finns inte.")
    rows=con.execute("""SELECT li.*,a.name article_name,l.line_no,l.ordered_qty,l.pallets,
                               a.units_per_pallet,a.order_unit_qty
                        FROM label_instances li
                        LEFT JOIN articles a ON a.article_no=li.article_no
                        LEFT JOIN order_lines l ON l.id=li.order_line_id
                        WHERE li.order_id=? AND li.active=1
                        ORDER BY l.line_no,li.sequence_no""",(order_id,)).fetchall()
    line_count=con.execute("SELECT COUNT(*) n FROM order_lines WHERE order_id=? AND ordered_qty>0",(order_id,)).fetchone()["n"]
    con.close()
    return {"order":dict(order),"labels":[dict(r) for r in rows],"line_count":line_count,
            "printer":"Intermec PX4i (300 dpi)",
            "note":"Palletiketter baseras på orderkvantitet / enheter per pall. Varje fysisk pall får ett stabilt unikt ID."}


def _label_template(customer_name: str, article_name: str) -> str:
    customer=(customer_name or "").casefold()
    article=(article_name or "").casefold()
    if "lidl" in customer:
        return "LIDL"
    if "ica" in customer and ("dröm" in article or "drom" in article):
        return "ICA_DRÖM"
    return "STANDARD"


@app.post("/api/labels/{order_id}/generate")
def generate_labels(order_id: int, request: Request):
    con=db()
    order=con.execute("""SELECT o.id,o.order_no,o.customer_no,c.name customer_name
                         FROM orders o JOIN customers c ON c.customer_no=o.customer_no WHERE o.id=?""",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Ordern finns inte.")
    lines=con.execute("""SELECT l.id,l.line_no,l.article_no,l.ordered_qty,l.station_id,l.pallets,
                                a.name article_name,a.units_per_pallet
                         FROM order_lines l LEFT JOIN articles a ON a.article_no=l.article_no
                         WHERE l.order_id=? AND l.ordered_qty>0 ORDER BY l.line_no""",(order_id,)).fetchall()
    created=0; updated=0; skipped=[]
    for line in lines:
        qty=float(line["ordered_qty"] or 0)
        upp=float(line["units_per_pallet"] or 0)
        if qty<=0 or upp<=0:
            skipped.append({"line_no":line["line_no"],"article_no":line["article_no"],"reason":"Enheter per pall saknas"})
            continue
        count=max(1,int(math.ceil(qty/upp-1e-12)))
        template=_label_template(order["customer_name"],line["article_name"] or "")
        remaining=qty
        for seq in range(1,count+1):
            label_qty=min(upp,max(0,remaining)); remaining-=label_qty
            uid=f"PLT-{order['order_no']}-A{line['article_no']}-{seq:03d}"
            existing=con.execute("SELECT id,lifecycle_state FROM label_instances WHERE label_uid=?",(uid,)).fetchone()
            if existing:
                con.execute("""UPDATE label_instances SET order_id=?,order_line_id=?,article_no=?,sequence_no=?,total_sequences=?,
                               qty_on_label=?,total_qty=?,station_id=?,template_name=?,printer_name=?,active=1
                               WHERE id=?""",
                            (order_id,line["id"],line["article_no"],seq,count,label_qty,qty,line["station_id"],template,
                             "Intermec PX4i (300 dpi)",existing["id"]))
                updated+=1
            else:
                con.execute("""INSERT INTO label_instances(label_uid,order_id,order_line_id,article_no,sequence_no,total_sequences,
                               qty_on_label,total_qty,station_id,template_name,printer_name,lifecycle_state,active)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,'CREATED',1)""",
                            (uid,order_id,line["id"],line["article_no"],seq,count,label_qty,qty,line["station_id"],template,
                             "Intermec PX4i (300 dpi)"))
                created+=1
        # deactivate surplus instances if the order quantity was reduced before production
        con.execute("""UPDATE label_instances SET active=0
                       WHERE order_line_id=? AND sequence_no>? AND lifecycle_state IN ('CREATED','PRINTED')""",(line["id"],count))
    user=getattr(request.state,"user",None)
    _write_audit(con,user,"LABEL_GENERATE","order",order["order_no"],
                 f"Palletiketter genererade: {created} nya, {updated} uppdaterade",{"skipped":skipped})
    con.commit(); con.close()
    return {"ok":True,"created":created,"updated":updated,"skipped":skipped}


@app.post("/api/labels/{order_id}/mark-printed")
def mark_labels_printed(order_id: int, request: Request):
    con=db()
    order=con.execute("SELECT order_no FROM orders WHERE id=?",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Ordern finns inte.")
    now=datetime.now().isoformat(timespec="seconds")
    rows=con.execute("SELECT id,label_uid,lifecycle_state FROM label_instances WHERE order_id=? AND active=1",(order_id,)).fetchall()
    if not rows:
        con.close(); raise HTTPException(400,"Generera etiketterna först.")
    for r in rows:
        new_state="PRINTED" if r["lifecycle_state"]=="CREATED" else r["lifecycle_state"]
        con.execute("UPDATE label_instances SET lifecycle_state=?,printed_at=?,print_count=print_count+1 WHERE id=?",(new_state,now,r["id"]))
        con.execute("INSERT INTO label_scan_events(label_instance_id,event_type,location,operator) VALUES(?,?,?,?)",
                    (r["id"],"PRINTED","LABEL_PRINTER",_user_display_name(getattr(request.state,"user",None))))
    con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
                (order_id,"LABELS_PRINTED",f"{len(rows)} palletiketter skickade till utskrift"))
    con.commit(); con.close()
    return {"ok":True,"count":len(rows),"printer":"Intermec PX4i (300 dpi)"}


class LabelScanIn(BaseModel):
    label_uid: str
    device_code: str = "PROD-TEST"


@app.post("/api/labels/scan/produced")
def scan_label_produced(payload: LabelScanIn, request: Request):
    uid=(payload.label_uid or "").strip().upper()
    if not pyre.fullmatch(r"PLT-[A-Z0-9_-]+",uid):
        raise HTTPException(400,"Ogiltigt etikett-ID.")
    con=db()
    row=con.execute("""SELECT li.*,o.order_no,l.line_no,c.name customer_name,a.name article_name
                       FROM label_instances li
                       JOIN orders o ON o.id=li.order_id
                       JOIN customers c ON c.customer_no=o.customer_no
                       LEFT JOIN order_lines l ON l.id=li.order_line_id
                       LEFT JOIN articles a ON a.article_no=li.article_no
                       WHERE UPPER(li.label_uid)=?""",(uid,)).fetchone()
    if not row:
        con.close(); raise HTTPException(404,"Etiketten finns inte i ERP.")
    if not row["active"]:
        con.close(); raise HTTPException(409,"Etiketten är inte längre aktiv på ordern.")
    state=row["lifecycle_state"] or "CREATED"
    if state=="PRODUCED":
        con.close(); raise HTTPException(409,f"{uid} är redan produktionsskannad.")
    if state in ("UNLOADED","READY"):
        con.close(); raise HTTPException(409,f"{uid} har redan passerat produktionen ({state}).")
    if state=="CREATED":
        con.close(); raise HTTPException(409,"Etiketten måste vara utskriven innan den kan produktionsskannas.")
    if state!="PRINTED":
        con.close(); raise HTTPException(409,f"Etiketten har status {state} och kan inte produktionsskannas.")
    now=datetime.now().isoformat(timespec="seconds")
    device=(payload.device_code or "PROD-TEST").strip().upper()[:80]
    operator=_user_display_name(getattr(request.state,"user",None))
    con.execute("UPDATE label_instances SET lifecycle_state='PRODUCED',produced_at=? WHERE id=?",(now,row["id"]))
    con.execute("INSERT INTO label_scan_events(label_instance_id,event_type,location,device_code,operator) VALUES(?,?,?,?,?)",
                (row["id"],"PRODUCED",f"S{row['station_id']}" if row["station_id"] else "PRODUCTION",device,operator))
    con.execute("INSERT INTO activity_events(order_id,event_type,message) VALUES(?,?,?)",
                (row["order_id"],"LABEL_PRODUCED",f"{uid} produktionsskannad"))
    _write_audit(con,getattr(request.state,"user",None),"LABEL_SCAN_PRODUCED","label",uid,
                 f"Pall produktionsskannad för order {row['order_no']}",{"device_code":device})
    con.commit(); con.close()
    return {"ok":True,"label_uid":uid,"state":"PRODUCED","order_no":row["order_no"],
            "customer_name":row["customer_name"],"article_no":row["article_no"],
            "article_name":row["article_name"],"sequence_no":row["sequence_no"],
            "total_sequences":row["total_sequences"],"station_id":row["station_id"],"produced_at":now}


@app.get("/api/labels/qr/{label_uid}")
def label_qr(label_uid: str):
    # SVG keeps the code razor-sharp on the 300 dpi Intermec without requiring PIL.
    if not pyre.fullmatch(r"PLT-[A-Za-z0-9_-]+",label_uid):
        raise HTTPException(400,"Ogiltigt etikett-ID.")
    img=qrcode.make(label_uid,image_factory=qrcode.image.svg.SvgPathImage,box_size=8,border=2)
    import io
    buf=io.BytesIO(); img.save(buf)
    return Response(content=buf.getvalue(),media_type="image/svg+xml",headers={"Cache-Control":"public, max-age=31536000"})


@app.get("/api/fortnox/{order_id}/preview")
def fortnox_preview(order_id: int):
    con=db()
    order=con.execute("""SELECT o.*,c.name customer_name,c.address,c.postal_address,c.fortnox_flag
                         FROM orders o JOIN customers c ON c.customer_no=o.customer_no WHERE o.id=?""",(order_id,)).fetchone()
    if not order:
        con.close(); raise HTTPException(404,"Ordern finns inte.")

    if order["source"]=="legacy":
        payload=parse_json(order["fortnox_payload_json"],{})
        con.close()
        return {"verified_source":"Excel sparade","send_enabled":False,"payload":payload,
                "note":"Detta är den importerade Fortnox-projektionen från Excel. Sändning är avsiktligt avstängd i prototypen."}

    totals=order_totals(con,order_id,"erp_v2")
    lines=con.execute("""SELECT l.line_no,l.article_no,l.description_snapshot,l.ordered_qty,l.sale_qty,l.unit_price,
                                l.deposit_amount,l.goods_value
                         FROM order_lines l WHERE l.order_id=? ORDER BY l.line_no""",(order_id,)).fetchall()
    payload={
        "order_no":order["order_no"],
        "delivery_date":order["delivery_date"],
        "customer_no":order["customer_no"],
        "customer_name":order["customer_name"],
        "address":order["address"],
        "postal_address":order["postal_address"],
        "goods_total":totals["goods"],
        "deposit_total":totals["deposit"],
        "prototype_total_before_verified_vat":totals["invoice_total"],
        "lines":[dict(r) for r in lines],
        "mapping_verified":False,
        "vat":"NOT VERIFIED",
        "rounding":"NOT VERIFIED"
    }
    con.close()
    return {"verified_source":"ERP v2 prototype","send_enabled":False,"payload":payload,
            "note":"Ingen Fortnox-sändning sker. Moms, avrundning och de produkt-specifika Fortnox-fälten måste verifieras mot nuvarande VBA innan aktivering."}


@app.get("/api/activity")
def activity(limit: int = Query(50,ge=1,le=200)):
    con=db()
    rows=con.execute("""SELECT a.*,o.order_no,c.name customer_name
                        FROM activity_events a
                        LEFT JOIN orders o ON o.id=a.order_id
                        LEFT JOIN customers c ON c.customer_no=o.customer_no
                        ORDER BY a.created_at DESC,a.id DESC LIMIT ?""",(limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/reporting/volume")
def volume_report(days: int = Query(30,ge=1,le=3650)):
    con=db()
    max_date=con.execute("SELECT MAX(loading_date) d FROM daily_load").fetchone()["d"]
    if not max_date:
        con.close(); return {"max_date":None,"rows":[]}
    start=(date.fromisoformat(max_date)-timedelta(days=days-1)).isoformat()
    rows=con.execute("""SELECT type,ROUND(SUM(weight_kg),0) weight_kg,COUNT(DISTINCT order_no) orders
                        FROM daily_load
                        WHERE loading_date BETWEEN ? AND ? AND type IS NOT NULL AND type<>''
                        GROUP BY type ORDER BY weight_kg DESC""",(start,max_date)).fetchall()
    con.close()
    return {"start":start,"max_date":max_date,"rows":[dict(r) for r in rows]}
