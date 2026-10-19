ERP v2 — SHAREABLE DEMO v0.8.8
================================

This package contains ONLY synthetic test data.
It is intentionally isolated from the normal ERP_v2 data folder.

DEMO LOGIN
  Username: demo
  Password: Demo2026!

Optional production/operator test account
  Username: pack1
  PIN: 2468

WHAT IS FAKE
  - customer names and customer numbers
  - addresses, emails and phone numbers
  - order numbers, references and invoices
  - prices, volumes and purchasing history
  - suppliers, stock levels and scanner/device names

The product/produce terminology is realistic only so testers can understand the workflow.
There is no connection to real customer or accounting data.

LOCAL TEST
  Double-click START_DEMO.bat (or run: python run.py)

HOSTING
  This folder is Docker-ready. Deploy the folder/repository on a host that can run
  a Dockerfile, or run `python demo_server.py` on a Python web host.
  The hosted demo resets to the bundled synthetic seed whenever the server process
  restarts by default (DEMO_RESET_ON_START=1).

IMPORTANT
  This DEMO uses a separate ERP_v2_DEMO data directory and never migrates or searches
  for the normal ERP_v2 production database.
