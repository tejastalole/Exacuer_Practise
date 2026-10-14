# Asset Management

Complete **Company Asset Management** module built as a custom Frappe app (`asset_management`).

Track company assets by category, assign them to employees, return them, and keep status consistent with server-side (Python) and client-side (JS) validations.

> **Note:** DocTypes are prefixed with **`AM`** (e.g. `AM Asset`) so they do **not** conflict with ERPNext’s built-in `Asset` / `Asset Category` DocTypes. ERPNext / Frappe core code is not modified.

---

## Table of contents

1. [Overview](#overview)
2. [Features](#features)
3. [Tech stack & dependencies](#tech-stack--dependencies)
4. [Installation](#installation)
5. [Module structure](#module-structure)
6. [DocTypes](#doctypes)
7. [Asset status lifecycle](#asset-status-lifecycle)
8. [End-to-end business flow](#end-to-end-business-flow)
9. [Validations](#validations)
10. [Permissions](#permissions)
11. [Workspace](#workspace)
12. [File map](#file-map)
13. [How to use (Desk)](#how-to-use-desk)
14. [Practice / test scenarios](#practice--test-scenarios)
15. [Design decisions](#design-decisions)
16. [Development tips](#development-tips)
17. [License](#license)

---

## Overview

Companies need a simple loop:

**Category → Asset → Assign to Employee → Return → Asset free again**

This app implements that loop with hard rules:

| Rule | Behaviour |
|------|-----------|
| One asset, one employee | An asset that is already **Assigned** cannot be assigned again until it is returned |
| Status is the source of truth | Assignment / Return submit events update `AM Asset.status` automatically |
| Valid dates & values | Future dates and negative purchase value are blocked (JS + Python) |
| Safe cancel | Cancel Assignment / Return only when it does not break history |

---

## Features

- **AM Asset Category** — master for grouping assets (Laptop, Monitor, etc.)
- **AM Asset** — asset register with status, serial number, company, purchase info, current assignee
- **AM Asset Assignment** — submittable document to assign an available asset to an Active Employee
- **AM Asset Return** — submittable document linked to an Assignment; auto-updates asset status on submit
- **Status set:** `Available`, `Assigned`, `Under Maintenance`, `Returned`, `Retired`
- **Asset Register** script report with filters, status chart, and summary cards
- **Desk Workspace** named **Asset Management** with report and DocType shortcuts
- **Client + server validations** for a consistent UX and data integrity
- **AM Stock Entry** — barcode-based stock movements; see [Stock Entry barcode scan](#stock-entry-barcode-scan)

---

## Tech stack & dependencies

| Item | Value |
|------|--------|
| Framework | [Frappe](https://docs.frappe.io/framework/user/en/introduction) |
| App name | `asset_management` |
| App title | Asset Management |
| License | MIT |
| Typical companions | ERPNext (for `Company`, `Employee`) — optional but expected on this bench |

Required linked DocTypes (from ERPNext / HRMS):

- `Company`
- `Employee`

---

## Installation

### On an existing bench (this project)

```bash
cd /path/to/exacuer_assessment

# App already created under apps/asset_management — install on site:
bench --site site1.local install-app asset_management

# After DocType / code changes:
bench --site site1.local migrate
bench --site site1.local clear-cache
```

### Fresh install from a git remote

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site your.site install-app asset_management
bench --site your.site migrate
```

### Services

`bench migrate` needs Redis (and usually the rest of `bench start`):

```bash
bench start
# then in another terminal
bench --site site1.local migrate
```

---

## Module structure

```
asset_management/                 # app root
└── asset_management/
    ├── hooks.py                  # app metadata & hooks
    ├── modules.txt               # "Asset Management"
    └── asset_management/         # module package
        ├── doctype/
        │   ├── am_asset_category/
        │   ├── am_asset/
        │   ├── am_asset_assignment/
        │   └── am_asset_return/
        ├── report/
        │   └── asset_register/
        └── workspace/
            └── asset_management/
```

---

## DocTypes

### 1. AM Asset Category

Master for asset types.

| Field | Type | Notes |
|-------|------|--------|
| `category_name` | Data (unique, naming) | Required |
| `description` | Small Text | Optional |
| `is_active` | Check | Inactive categories cannot be used on new assets |

**Naming:** by field `category_name`.

---

### 2. AM Asset

Central asset record.

| Field | Type | Notes |
|-------|------|--------|
| `naming_series` | Select | `AM-AST-.YYYY.-.#####` |
| `asset_name` | Data | Required |
| `asset_category` | Link → AM Asset Category | Required; active only |
| `company` | Link → Company | Required |
| `status` | Select | See [status lifecycle](#asset-status-lifecycle) |
| `serial_number` | Data (unique) | Optional but recommended |
| `purchase_date` | Date | Cannot be in the future |
| `purchase_value` | Currency | Cannot be negative |
| `assigned_to` | Link → Employee | **Read only** — set by Assignment |
| `assigned_on` | Date | **Read only** |
| `current_assignment` | Link → AM Asset Assignment | **Read only** |
| `description` | Small Text | Optional |

**Naming:** naming series `AM-AST-.YYYY.-.#####`.

**Links (form Connections):** Assignments and Returns for this asset.

---

### 3. AM Asset Assignment *(submittable)*

Assigns one asset to one employee.

| Field | Type | Notes |
|-------|------|--------|
| `naming_series` | Select | `AM-ASG-.YYYY.-.#####` |
| `asset` | Link → AM Asset | Only Available / Returned (query filter) |
| `asset_name` | Data | Fetch from asset |
| `asset_status` | Data | Fetch from asset (help for UI) |
| `employee` | Link → Employee | Active employees only |
| `employee_name` | Data | Fetch from employee |
| `assignment_date` | Date | Default Today; not in future |
| `company` | Link → Company | Fetch from asset |
| `remarks` | Small Text | Optional |

**On Submit**

- Sets asset `status = Assigned`
- Sets `assigned_to`, `assigned_on`, `current_assignment`

**On Cancel**

- Blocked if a submitted Return already exists for this Assignment
- Otherwise restores asset to `Available` and clears assignment fields

---

### 4. AM Asset Return *(submittable)*

Returns an asset against a submitted Assignment.

| Field | Type | Notes |
|-------|------|--------|
| `naming_series` | Select | `AM-RET-.YYYY.-.#####` |
| `assignment` | Link → AM Asset Assignment | Submitted assignments only |
| `asset` | Link → AM Asset | Fetch / read only |
| `employee` | Link → Employee | Fetch / read only |
| `return_date` | Date | ≥ assignment date; not in future |
| `condition_on_return` | Select | `Good` / `Damaged` / `Needs Maintenance` |
| `company` | Link → Company | Fetch |
| `remarks` | Small Text | Optional |

**On Submit — automatic status update**

| Condition on return | New asset status |
|---------------------|------------------|
| Good | **Available** |
| Damaged | **Returned** |
| Needs Maintenance | **Under Maintenance** |

Also clears `assigned_to`, `assigned_on`, `current_assignment`.

**On Cancel**

- Restores asset to **Assigned** with the original employee/assignment (if not already assigned again)

---

## Asset status lifecycle

```text
                 ┌──────────────────┐
                 │     Retired      │  (manual / end of life)
                 └────────▲─────────┘
                          │
   ┌──────────────┐  Assign   ┌──────────────┐  Return (Good)
   │   Available  │──────────▶│   Assigned   │──────────────────┐
   └──────▲───────┘           └──────────────┘                  │
          │                          │                          │
          │                          │ Return                   │
          │                          │ (Damaged / Maint.)       │
          │                          ▼                          │
          │               ┌────────────────────┐                │
          │               │ Under Maintenance  │                │
          │               │ or Returned        │                │
          │               └─────────┬──────────┘                │
          │                         │ (manual → Available)      │
          └─────────────────────────┴───────────────────────────┘
```

| Status | Meaning |
|--------|---------|
| **Available** | Free to assign |
| **Assigned** | Currently with one employee |
| **Under Maintenance** | Not assignable until fixed / set Available |
| **Returned** | Returned (e.g. damaged); can be reassigned after check, or set Available |
| **Retired** | End of life; cannot assign |

Assignment is allowed only when status is **Available** or **Returned**.

---

## End-to-end business flow

```text
1. Create AM Asset Category          (e.g. Laptop)
2. Create AM Asset                   status = Available
3. Create AM Asset Assignment        select Asset + Employee → Save → Submit
      └── Asset becomes Assigned, assigned_to = Employee
4. Create AM Asset Return            select Assignment → Save → Submit
      └── Asset status auto-updates from condition_on_return
5. Asset can be assigned again       (if Available or Returned)
```

**Hard rule:** while status is **Assigned**, a second Assignment for the same asset is rejected (Python + JS).

---

## Validations

### Python (server — source of truth)

| DocType | Checks |
|---------|--------|
| **AM Asset Category** | Category name required / trimmed |
| **AM Asset** | Valid status; purchase date ≤ today; purchase value ≥ 0; category active; cannot delete if Assigned; clearing assignee when freeing status (unless assignment flow flag) |
| **AM Asset Assignment** | Assignment date ≤ today; employee Active; asset not Retired / Under Maintenance / already Assigned; only one open assignment per asset |
| **AM Asset Return** | Return date ≤ today; return date ≥ assignment date; assignment submitted; no duplicate return; asset currently Assigned; employee matches assignee |

Flags:

- `asset.flags.from_assignment_flow = True` — used when Assignment/Return updates the asset so manual status-clear logic does not fight the workflow.

### JavaScript (client — early feedback)

| DocType | Behaviour |
|---------|-----------|
| **AM Asset Category** | Category name required on validate |
| **AM Asset** | Required fields; date/value checks; query active categories; clear assignee fields when status set to Available / Returned / Retired |
| **AM Asset Assignment** | Filter assets to Available/Returned; filter Active employees; block future dates; warn if asset already Assigned |
| **AM Asset Return** | Require assignment & return date; block future dates; warn if assignment already returned |

---

## Permissions

Default roles with full access (create / read / write / delete / submit where applicable):

- **System Manager**
- **HR Manager**

Adjust in DocType → Permission Manager as needed for your org.

---

## Workspace

Desk sidebar / Awesome Bar:

- **Asset Management** workspace
  - Shortcuts: Categories, Assets, Assignments, Returns, Asset Register
  - Cards: Masters, Transactions, and Reports links
- **Asset Register** report at `/app/query-report/Asset Register`
  - Filters: Company, Category, Status, Assigned To, purchase date range
  - Current assignee, days assigned, total assignments, and last return condition per asset
  - Status donut chart and summary cards (totals by status, total purchase value)

---

## File map

```text
doctype/am_asset_category/
  am_asset_category.json    # schema
  am_asset_category.py      # Python validations
  am_asset_category.js      # JS validations

doctype/am_asset/
  am_asset.json
  am_asset.py
  am_asset.js

doctype/am_asset_assignment/
  am_asset_assignment.json  # is_submittable: 1
  am_asset_assignment.py    # validate + on_submit / on_cancel
  am_asset_assignment.js

doctype/am_asset_return/
  am_asset_return.json      # is_submittable: 1
  am_asset_return.py        # auto status update on submit
  am_asset_return.js

workspace/asset_management/
  asset_management.json

report/asset_register/
  asset_register.json       # Script Report definition
  asset_register.py         # columns, data, chart, summary
  asset_register.js         # filters and status formatting

doctype/am_stock_entry/
  am_stock_entry.json       # is_submittable: 1
  am_stock_entry.py         # barcode API, validations, posts ERPNext Stock Entry
  am_stock_entry.js         # scan UX, warehouse defaults, totals, client validations

doctype/am_stock_entry_item/
  am_stock_entry_item.json  # child table (istable: 1)

doctype/am_google_sheet_settings/  # single DocType: Web App URL, token, auto submit, last sync

google_sheet_sync.py        # scheduler job + Sync Now: pull rows, create entries, write back
google_apps_script/
  stock_import.gs           # Apps Script Web App for the Google Sheet (app root, not deployed)
```

---

## Stock Entry barcode scan

**AM Stock Entry** is a separate submittable DocType for barcode-based stock movements. ERPNext's core **Stock Entry** is not customized; on submit, AM Stock Entry creates and submits a regular ERPNext Stock Entry (linked in **ERPNext Stock Entry**) so stock ledger and accounting stay standard. Cancelling the AM Stock Entry cancels that Stock Entry.

Supported types: **Material Issue**, **Material Receipt**, **Material Transfer**.

| Step | Behaviour |
|------|-----------|
| Scan | Barcode is looked up in **Item → Barcodes**; an exact Item Code also works |
| First scan | Item is added with qty `1`, plus item name, UOM, rate (warehouse valuation rate → item valuation rate → standard rate), and available qty |
| Repeat scan | The existing row's qty increases by `1` |
| Warehouses | Rows take the **Default Source / Target Warehouse**, falling back to the Item Default warehouse for the company |
| Save (server) | Duplicate rows (same item, warehouses, rate) are merged; missing, disabled, template, non-stock, or end-of-life items, qty ≤ 0, and receipts without a rate are rejected |
| Submit (server) | Source/target warehouse required per type, not the same, not group/disabled, same company; source stock must cover the qty unless negative stock is allowed |

Whitelisted methods (`asset_management.asset_management.doctype.am_stock_entry.am_stock_entry`): `resolve_barcode(barcode, company, warehouse)` and `get_item_details(item_code, company, warehouse)`.

---

## Google Sheet stock import

Rows typed into a Google Sheet automatically become **AM Stock Entries** (one per row). With **Auto Sync** on, ERPNext **pulls** from the sheet every **Sync Interval** (30, 60, 90 or 120 seconds; scheduler job `asset_management.google_sheet_sync.sync_stock_entries` runs every minute and performs each sync due within it), so it works with a site running only on `http://127.0.0.1:8004` — no public URL or tunnel needed. The sheet's Apps Script (`google_apps_script/stock_import.gs`) is deployed as a Web App that returns pending rows and writes results back.

**Sheet columns** (row 1 headers, any order): `Entry Type`, `Item Code`, `Qty`, `Source Warehouse`, `Target Warehouse`, `Status`, `ERPNext ID`, `Error`. Optional: `Company`, `Posting Date`, `Rate`, `Remarks`.

- `Entry Type`: `Material Issue`, `Material Receipt`, or `Material Transfer`
- `Item Code`: an Item Code or an Item barcode
- **Status is set by you** and decides what happens; the sync never overwrites it:

  | Status | No entry yet | Entry is Draft | Entry is Submitted | Entry is Cancelled |
  |--------|--------------|----------------|--------------------|--------------------|
  | `Pending` | wait | — | — | — |
  | (empty) | create draft | — | — | — |
  | `Draft` | create draft | — | error | error |
  | `Submitted` | create + submit | submit | — | error |
  | `Cancelled` | error | error | cancel (also cancels the ERPNext Stock Entry) | — |

- The sync writes the AM Stock Entry name to **ERPNext ID** and problems to **Error**. A row with an Error is skipped until you edit it. An unknown Status (anything other than the values above) is reported in **Error**.
- Editing a row (item, qty, warehouses, …) updates its entry while it is a **Draft**; ERPNext compares the row with the entry on every sync of that row. A Submitted entry can't be changed: set Status to `Cancelled` and add a new row.
- To make a new entry from a row whose entry was cancelled, clear **ERPNext ID** and set Status again
- The Status cell's note holds the row's sync key, the state last applied and a hash of the row data last applied; each AM Stock Entry stores the key (**Google Sheet Sync Key**), so a row is never imported twice, even if writing the result back fails and is retried

**Setup**

1. In the sheet: **Extensions → Apps Script**, replace the code with `google_apps_script/stock_import.gs`, and save.
2. **Project Settings → Script Properties** → add `SYNC_TOKEN` = a long random secret (e.g. output of `openssl rand -hex 24`).
3. **Deploy → New deployment → Web app**: Execute as **Me**, Who has access **Anyone**. Authorize, then copy the Web app URL (ends in `/exec`). After code changes, use **Deploy → Manage deployments → Edit → New version** so the URL stays the same. ERPNext checks the script's `SCRIPT_VERSION`; if an older script is still deployed, **Last Sync Result** says "The deployed Apps Script is outdated".
4. In ERPNext: **AM Google Sheet Settings** → check **Enabled**, paste the **Web App URL**, enter the same **Sync Token**, check **Auto Sync**, pick a **Sync Interval (sec)**, Save.
5. Optional **Daily Sync**: check it and set **Start Date** and **Sync Time** (e.g. `00:00` for 12 midnight) to sync once a day at that time instead. Auto Sync and Daily Sync are exclusive: checking one unchecks the other. A new or changed schedule counts from the moment it is saved; if ERPNext is off at Sync Time, the sync runs as soon as it starts again. **Last Daily Sync On** shows the latest run.
6. Keep `bench start` running (the scheduler and worker run the sync). **Sync Now** on the settings page syncs immediately; **Last Sync On / Last Sync Result** show the outcome, and failures go to the Error Log. Auto Sync and Sync Now never run at the same time.

---

## How to use (Desk)

1. Open the **Asset Management** workspace.
2. Create categories under **AM Asset Category**.
3. Create assets under **AM Asset** (keep status **Available** for new stock).
4. Open **AM Asset Assignment** → pick asset + employee → **Save** → **Submit**.
5. When the employee returns the item, open **AM Asset Return** → pick the Assignment → set condition → **Save** → **Submit**.
6. Confirm on **AM Asset** that status and `Assigned To` updated automatically.
7. Open the **Asset Register** report for an overview of all assets.

---

## Practice / test scenarios

Use these to verify the module:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Assign Available asset | Status → Assigned; `assigned_to` set |
| 2 | Assign same asset again without return | Error: already assigned |
| 3 | Return with condition **Good** | Status → Available; assignee cleared |
| 4 | Return with **Needs Maintenance** | Status → Under Maintenance |
| 5 | Return with **Damaged** | Status → Returned |
| 6 | Assign Retired asset | Error |
| 7 | Return date before assignment date | Error |
| 8 | Cancel Assignment after Return submitted | Error |
| 9 | Purchase date in future | Error |
| 10 | Negative purchase value | Error |

---

## Design decisions

1. **`AM` prefix** — avoids clash with ERPNext Fixed Asset (`Asset`, `Asset Category`).
2. **Submittable Assignment & Return** — audit trail (draft → submitted → cancelled) instead of editing the asset only.
3. **Return drives status** — condition on return maps to Available / Returned / Under Maintenance so ops can route damaged vs good items.
4. **Read-only assignee fields on Asset** — prevents bypassing Assignment; updates only via Assignment/Return controllers.
5. **No ERPNext / Frappe core edits** — all logic lives in this custom app ([Frappe Framework docs](https://docs.frappe.io/framework/user/en/introduction)).

---

## Development tips

```bash
# Reload DocType metadata after JSON edits
bench --site site1.local migrate
bench --site site1.local clear-cache

# Tail web errors while testing forms
tail -f logs/web.log

# Confirm DocTypes exist
bench --site site1.local mariadb -e \
  "SELECT name FROM \`tabDocType\` WHERE module='Asset Management';"
```

**Contributing / lint**

```bash
cd apps/asset_management
pre-commit install
```

Configured tools: `ruff`, `eslint`, `prettier`, `pyupgrade`.

---

## License

MIT — see `license.txt`.

---

## Publisher

- **Publisher:** Tejas Talole  
- **Email:** tejastalole7@gmail.com  
- **App:** `asset_management` — Asset Management  
