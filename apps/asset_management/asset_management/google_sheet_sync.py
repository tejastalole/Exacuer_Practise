# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

"""Sync a Google Sheet (via its Apps Script web app) with AM Stock Entries.

ERPNext calls out to Google, so this works when the site only runs on localhost.
The sheet's Status column is the state the user wants (Draft / Submitted / Cancelled);
each sync brings the linked AM Stock Entry to that state, and edits to a row update
its Draft entry. Rows carry a sync key that the AM Stock Entry stores, so a row is
never imported twice even if writing the result back to the sheet fails and is retried.
"""

import re
import time
from datetime import datetime, timedelta

import frappe
import requests
from frappe import _
from frappe.utils import (
	add_to_date,
	cint,
	cstr,
	flt,
	get_datetime,
	get_time,
	getdate,
	now_datetime,
	strip_html,
	time_diff_in_seconds,
)
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from asset_management.asset_management.doctype.am_stock_entry.am_stock_entry import (
	MATERIAL_ISSUE,
	MATERIAL_RECEIPT,
	MATERIAL_TRANSFER,
	find_item_code,
)

SETTINGS_DOCTYPE = "AM Google Sheet Settings"
STOCK_ENTRY_TYPES = (MATERIAL_ISSUE, MATERIAL_RECEIPT, MATERIAL_TRANSFER)
REQUEST_TIMEOUT = 60
# Must match SCRIPT_VERSION in google_apps_script/stock_import.gs.
SCRIPT_VERSION = 2
SYNC_LOCK = "am_google_sheet_sync"
LOCK_TIMEOUT = 30
DEFAULT_SYNC_INTERVAL = 60
# The scheduler calls sync_stock_entries every minute; syncs due in the last few
# seconds of that minute are left to the next call instead of holding a worker.
SCHEDULER_TICK = 60
TICK_MARGIN = 5

DRAFT = "Draft"
SUBMITTED = "Submitted"
CANCELLED = "Cancelled"
STATE_BY_DOCSTATUS = {0: DRAFT, 1: SUBMITTED, 2: CANCELLED}


def sync_stock_entries():
	"""Scheduler entry point, called every minute."""
	if not cint(get_setting("enabled")):
		return
	run_daily_sync_if_due()
	run_interval_syncs()


def run_daily_sync_if_due():
	if not cint(get_setting("daily_sync")):
		return

	due = get_last_daily_sync_time()
	last_daily_sync_on = get_setting("last_daily_sync_on")
	if not due or (last_daily_sync_on and get_datetime(last_daily_sync_on) >= due):
		return

	run_sync()
	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_daily_sync_on", now_datetime())
	frappe.db.commit()


def get_last_daily_sync_time():
	"""Most recent scheduled daily sync time up to now, or None before the Start Date."""
	sync_time = get_setting("daily_sync_time")
	start_date = get_setting("daily_sync_from")
	if sync_time is None or not start_date:
		return None

	now = now_datetime()
	due = datetime.combine(now.date(), get_time(sync_time))
	if due > now:
		due -= timedelta(days=1)
	return due if due.date() >= getdate(start_date) else None


def run_interval_syncs():
	"""Runs each Auto Sync that falls due before the next scheduler call, so intervals
	shorter than a minute or not a multiple of one are kept."""
	window_end = add_to_date(now_datetime(), seconds=SCHEDULER_TICK - TICK_MARGIN)

	while is_auto_sync_enabled():
		due = get_next_sync_time()
		if due >= window_end or now_datetime() >= window_end:
			return

		wait = time_diff_in_seconds(due, now_datetime())
		if wait > 0:
			time.sleep(wait)
			# Start a new transaction so settings changed while waiting are seen.
			frappe.db.commit()
			continue

		run_sync()


def is_auto_sync_enabled():
	return cint(get_setting("enabled")) and cint(get_setting("auto_sync"))


def get_next_sync_time():
	last_sync_on = get_setting("last_sync_on")
	if not last_sync_on:
		return now_datetime()
	interval = cint(get_setting("sync_interval")) or DEFAULT_SYNC_INTERVAL
	return add_to_date(get_datetime(last_sync_on), seconds=interval)


def get_setting(fieldname):
	return frappe.db.get_single_value(SETTINGS_DOCTYPE, fieldname, cache=False)


@frappe.whitelist(methods=["POST"])
def sync_now():
	frappe.only_for(("System Manager", "Stock Manager"))
	if not cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, "enabled")):
		frappe.throw(_("Enable Google Sheet sync first"))
	return run_sync()


def run_sync():
	started_on = now_datetime()
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	try:
		# Auto sync and Sync Now must not process the same rows at the same time.
		with filelock(SYNC_LOCK, timeout=LOCK_TIMEOUT):
			summary = sync_with_sheet(settings)
	except LockTimeoutError:
		return _("Another sync is running; try again in a moment")
	except Exception as e:
		frappe.db.rollback()
		# Expected problems (bad token, outdated script, ...) are shown in Last Sync Result;
		# only unexpected ones are logged, so a 30-second sync doesn't flood the Error Log.
		if not isinstance(e, frappe.ValidationError):
			frappe.log_error(title=_("Google Sheet sync failed"), reference_doctype=SETTINGS_DOCTYPE)
		summary = _("Failed: {0}").format(get_error_message(e))

	frappe.db.set_single_value(
		SETTINGS_DOCTYPE, {"last_sync_on": started_on, "last_sync_result": summary}
	)
	frappe.db.commit()
	return summary


def sync_with_sheet(settings):
	url = settings.web_app_url
	token = settings.get_password("sync_token")

	data = call_web_app(requests.get(url, params={"action": "pending", "token": token}, timeout=REQUEST_TIMEOUT))
	if cint(data.get("version")) < SCRIPT_VERSION:
		frappe.throw(
			_(
				"The deployed Apps Script is outdated. Paste google_apps_script/stock_import.gs into the "
				"Apps Script editor, then Deploy → Manage deployments → Edit → New version → Deploy"
			)
		)
	rows = data.get("rows") or []
	if not rows:
		return _("No changes")

	source = _("Google Sheet '{0}' / {1}").format(data.get("spreadsheet"), data.get("sheet"))
	results = process_rows(rows, source)

	# Entries must be saved before the sheet is told about them.
	frappe.db.commit()

	call_web_app(
		requests.post(url, json={"token": token, "results": results}, timeout=REQUEST_TIMEOUT)
	)

	failed = sum(1 for result in results if result.get("error"))
	return _("{0} row(s) synced, {1} with errors").format(len(results) - failed, failed)


def call_web_app(response):
	response.raise_for_status()
	try:
		data = response.json()
	except ValueError:
		frappe.throw(
			_("Web App did not return JSON. Check that it is deployed with access 'Anyone'. Response: {0}").format(
				strip_html(response.text)[:200]
			)
		)

	if data.get("error"):
		frappe.throw(_("Web App error: {0}").format(data["error"]))
	return data


def process_rows(rows, source=None):
	results = []
	for row in rows:
		row = frappe._dict(row if isinstance(row, dict) else {})
		sync_key = cstr(row.sync_key).strip()
		if not sync_key:
			continue

		frappe.db.savepoint("am_sheet_row")
		try:
			doc = apply_row_status(row, sync_key, source)
			result = get_result(sync_key, doc)
			# Only a successful sync marks the row data as applied.
			result["row_hash"] = cstr(row.row_hash)
			results.append(result)
		except Exception as e:
			frappe.db.rollback(save_point="am_sheet_row")
			if isinstance(e, frappe.ValidationError):
				error = get_error_message(e)
			else:
				frappe.log_error(title=_("Google Sheet row sync failed"), reference_doctype="AM Stock Entry")
				error = _("Unexpected error; check the Error Log in ERPNext")

			results.append(get_result(sync_key, get_linked_entry_or_none(row, sync_key), error))
		finally:
			frappe.clear_messages()

	return results


def apply_row_status(row, sync_key, source=None):
	target = cstr(row.target_status).strip()
	if target not in (DRAFT, SUBMITTED, CANCELLED):
		frappe.throw(_("Status must be one of: Pending, Draft, Submitted, Cancelled"))

	doc = get_linked_entry(row, sync_key)
	if not doc:
		if target == CANCELLED:
			frappe.throw(_("There is no AM Stock Entry to cancel"))
		doc = create_stock_entry_from_row(row, sync_key, source)
		if target == SUBMITTED:
			doc.submit()
		return doc

	current = STATE_BY_DOCSTATUS[doc.docstatus]
	if target != CANCELLED and current != CANCELLED and row_differs(doc, row, source):
		if current == SUBMITTED:
			frappe.throw(
				_("{0} is Submitted and can't be changed; set Status to Cancelled and add a new row instead").format(
					doc.name
				)
			)
		update_stock_entry_from_row(doc, row, source)

	if current == target:
		return doc

	if current == DRAFT and target == SUBMITTED:
		doc.submit()
	elif current == SUBMITTED and target == CANCELLED:
		doc.cancel()
	elif current == DRAFT and target == CANCELLED:
		frappe.throw(_("{0} is a Draft; only Submitted entries can be cancelled").format(doc.name))
	elif current == SUBMITTED:
		frappe.throw(_("{0} is already Submitted; it can only be Cancelled").format(doc.name))
	else:
		frappe.throw(
			_("{0} is Cancelled; clear ERPNext ID in the sheet to create a new entry").format(doc.name)
		)

	return doc


def get_linked_entry(row, sync_key):
	name = cstr(row.erpnext_id).strip()
	if name:
		if not frappe.db.exists("AM Stock Entry", name):
			frappe.throw(_("AM Stock Entry {0} does not exist").format(name))
		return frappe.get_doc("AM Stock Entry", name)

	name = frappe.db.get_value(
		"AM Stock Entry",
		{"sheet_sync_key": sync_key, "docstatus": ["<", 2]},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc("AM Stock Entry", name) if name else None


def get_linked_entry_or_none(row, sync_key):
	try:
		return get_linked_entry(row, sync_key)
	except frappe.ValidationError:
		return None


def create_stock_entry_from_row(row, sync_key, source=None):
	doc = frappe.new_doc("AM Stock Entry")
	doc.sheet_sync_key = sync_key
	set_values_from_row(doc, row, source)
	doc.insert(ignore_permissions=True)
	return doc


def update_stock_entry_from_row(doc, row, source=None):
	set_values_from_row(doc, row, source)
	doc.save(ignore_permissions=True)


def row_differs(doc, row, source=None):
	"""Whether the sheet row describes something other than what the entry holds."""
	if not cstr(row.item_code).strip():
		# A row with only Status and ERPNext ID just changes the entry's state.
		return False

	expected = frappe.new_doc("AM Stock Entry")
	set_values_from_row(expected, row, source)
	return get_comparable_values(expected, row) != get_comparable_values(doc, row)


def get_comparable_values(doc, row):
	# Rate and Posting Date are filled in by ERPNext when the sheet leaves them empty.
	compare_rate = bool(flt(row.rate))
	return (
		doc.stock_entry_type,
		doc.company,
		doc.from_warehouse or None,
		doc.to_warehouse or None,
		cstr(doc.remarks),
		getdate(doc.posting_date) if row.posting_date else None,
		tuple(
			(
				item.item_code,
				flt(item.qty),
				item.s_warehouse or None,
				item.t_warehouse or None,
				flt(item.basic_rate) if compare_rate else None,
			)
			for item in doc.items
		),
	)


def set_values_from_row(doc, row, source=None):
	entry_type = cstr(row.entry_type).strip()
	if entry_type not in STOCK_ENTRY_TYPES:
		frappe.throw(_("Entry Type must be one of: {0}").format(", ".join(STOCK_ENTRY_TYPES)))

	item_value = cstr(row.item_code).strip()
	if not item_value:
		frappe.throw(_("Item Code is required"))

	item_code = find_item_code(item_value)
	if not item_code:
		frappe.throw(_("No Item found for {0}").format(item_value))

	qty = flt(row.qty)
	if qty <= 0:
		frappe.throw(_("Qty must be greater than zero"))

	source_warehouse = cstr(row.source_warehouse).strip() or None
	target_warehouse = cstr(row.target_warehouse).strip() or None
	if entry_type in (MATERIAL_ISSUE, MATERIAL_TRANSFER) and not source_warehouse:
		frappe.throw(_("Source Warehouse is required for {0}").format(entry_type))
	if entry_type in (MATERIAL_RECEIPT, MATERIAL_TRANSFER) and not target_warehouse:
		frappe.throw(_("Target Warehouse is required for {0}").format(entry_type))

	doc.update(
		{
			"stock_entry_type": entry_type,
			"company": cstr(row.company).strip() or get_company(source_warehouse or target_warehouse),
			"from_warehouse": source_warehouse,
			"to_warehouse": target_warehouse,
			"remarks": "\n".join(filter(None, [cstr(source).strip(), cstr(row.remarks).strip()])),
		}
	)
	if row.posting_date:
		doc.posting_date = getdate(row.posting_date)

	doc.set(
		"items",
		[
			{
				"item_code": item_code,
				"qty": qty,
				"s_warehouse": source_warehouse,
				"t_warehouse": target_warehouse,
				"basic_rate": flt(row.rate),
			}
		],
	)
	doc.set_row_warehouses()
	doc.validate_warehouses()


def get_result(sync_key, doc=None, error=None):
	return {
		"sync_key": sync_key,
		"name": doc.name if doc else "",
		"state": STATE_BY_DOCSTATUS[doc.docstatus] if doc else "",
		"error": error or "",
	}


def get_company(warehouse):
	return (
		(warehouse and frappe.db.get_value("Warehouse", warehouse, "company"))
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)


def get_error_message(error):
	message = re.sub(r"<br\s*/?>", "; ", cstr(error))
	return strip_html(message).strip() or error.__class__.__name__
