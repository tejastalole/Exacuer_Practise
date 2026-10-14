# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

MATERIAL_ISSUE = "Material Issue"
MATERIAL_RECEIPT = "Material Receipt"
MATERIAL_TRANSFER = "Material Transfer"


class AMStockEntry(Document):
	def validate(self):
		self.set_row_warehouses()
		self.set_missing_rates()
		if self.docstatus == 0:
			self.merge_duplicate_rows()
		self.validate_items()
		self.set_amounts_and_totals()

	def before_submit(self):
		self.validate_warehouses()
		self.validate_stock_availability()

	def on_submit(self):
		self.make_stock_entry()

	def on_cancel(self):
		self.cancel_stock_entry()

	def needs_source_warehouse(self):
		return self.stock_entry_type in (MATERIAL_ISSUE, MATERIAL_TRANSFER)

	def needs_target_warehouse(self):
		return self.stock_entry_type in (MATERIAL_RECEIPT, MATERIAL_TRANSFER)

	def set_row_warehouses(self):
		if not self.needs_source_warehouse():
			self.from_warehouse = None
		if not self.needs_target_warehouse():
			self.to_warehouse = None

		for row in self.items:
			row.s_warehouse = (row.s_warehouse or self.from_warehouse) if self.needs_source_warehouse() else None
			row.t_warehouse = (row.t_warehouse or self.to_warehouse) if self.needs_target_warehouse() else None

	def set_missing_rates(self):
		for row in self.items:
			if row.item_code and not flt(row.basic_rate):
				row.basic_rate = get_basic_rate(row.item_code, row.s_warehouse or row.t_warehouse)

	def merge_duplicate_rows(self):
		rows_by_key = {}
		items = []
		merged_items = set()

		for row in self.items:
			key = (row.item_code, row.s_warehouse or "", row.t_warehouse or "", flt(row.basic_rate))
			if row.item_code and key in rows_by_key:
				kept_row = rows_by_key[key]
				kept_row.qty = flt(kept_row.qty) + flt(row.qty)
				kept_row.barcode = kept_row.barcode or row.barcode
				merged_items.add(row.item_code)
				continue

			rows_by_key[key] = row
			items.append(row)

		if not merged_items:
			return

		self.set("items", items)
		for idx, row in enumerate(self.items, start=1):
			row.idx = idx

		frappe.msgprint(
			_("Duplicate rows merged for: {0}").format(", ".join(sorted(merged_items))),
			indicator="blue",
			alert=True,
		)

	def validate_items(self):
		errors = []
		items = {}

		for row in self.items:
			if not row.item_code:
				errors.append(_("Row #{0}: Item Code is required").format(row.idx))
				continue

			if row.item_code not in items:
				items[row.item_code] = get_item(row.item_code)

			error = get_item_error(items[row.item_code], row.item_code, self.posting_date)
			if error:
				errors.append(_("Row #{0}: {1}").format(row.idx, error))
			elif flt(row.qty) <= 0:
				errors.append(
					_("Row #{0}: Quantity for {1} must be greater than zero").format(
						row.idx, frappe.bold(row.item_code)
					)
				)
			elif self.stock_entry_type == MATERIAL_RECEIPT and flt(row.basic_rate) <= 0:
				errors.append(
					_("Row #{0}: Rate for {1} is required for a Material Receipt").format(
						row.idx, frappe.bold(row.item_code)
					)
				)

		throw_errors(errors, _("Invalid Items"))

	def set_amounts_and_totals(self):
		for row in self.items:
			row.amount = flt(row.qty) * flt(row.basic_rate)
			row.actual_qty = get_actual_qty(row.item_code, row.s_warehouse)

		self.total_qty = sum(flt(row.qty) for row in self.items)
		self.total_amount = sum(flt(row.amount) for row in self.items)

	def validate_warehouses(self):
		errors = []
		warehouses = {}

		for row in self.items:
			if self.needs_source_warehouse() and not row.s_warehouse:
				errors.append(
					_("Row #{0}: Source Warehouse is required for {1}").format(row.idx, self.stock_entry_type)
				)
			if self.needs_target_warehouse() and not row.t_warehouse:
				errors.append(
					_("Row #{0}: Target Warehouse is required for {1}").format(row.idx, self.stock_entry_type)
				)
			if row.s_warehouse and row.s_warehouse == row.t_warehouse:
				errors.append(
					_("Row #{0}: Source and Target Warehouse cannot be the same ({1})").format(
						row.idx, row.s_warehouse
					)
				)

			for warehouse in {row.s_warehouse, row.t_warehouse} - {None, ""}:
				if warehouse not in warehouses:
					warehouses[warehouse] = frappe.db.get_value(
						"Warehouse", warehouse, ["company", "is_group", "disabled"], as_dict=True
					)

				error = get_warehouse_error(warehouses[warehouse], warehouse, self.company)
				if error:
					errors.append(_("Row #{0}: {1}").format(row.idx, error))

		throw_errors(errors, _("Warehouse Validation"))

	def validate_stock_availability(self):
		from erpnext.stock.utils import get_stock_balance

		if cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock")):
			return

		required_qty = defaultdict(float)
		for row in self.items:
			if row.s_warehouse:
				required_qty[(row.item_code, row.s_warehouse)] += flt(row.qty)

		errors = []
		for (item_code, warehouse), qty in required_qty.items():
			if cint(frappe.get_cached_value("Item", item_code, "allow_negative_stock")):
				continue

			available_qty = flt(get_stock_balance(item_code, warehouse, self.posting_date, self.posting_time))
			if qty > available_qty:
				errors.append(
					_("{0} in {1}: required {2}, available {3}").format(
						frappe.bold(item_code), warehouse, frappe.bold(qty), frappe.bold(available_qty)
					)
				)

		throw_errors(errors, _("Insufficient Stock"))

	def make_stock_entry(self):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.update(
			{
				"stock_entry_type": self.stock_entry_type,
				"purpose": self.stock_entry_type,
				"company": self.company,
				"set_posting_time": 1,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"from_warehouse": self.from_warehouse,
				"to_warehouse": self.to_warehouse,
				"remarks": _("Created from AM Stock Entry {0}").format(self.name)
				+ (f"\n{self.remarks}" if self.remarks else ""),
			}
		)

		for row in self.items:
			stock_entry.append(
				"items",
				{
					"item_code": row.item_code,
					"qty": row.qty,
					"uom": row.stock_uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": 1,
					"transfer_qty": row.qty,
					"s_warehouse": row.s_warehouse,
					"t_warehouse": row.t_warehouse,
					"basic_rate": row.basic_rate,
					"barcode": row.barcode,
				},
			)

		stock_entry.flags.ignore_permissions = True
		stock_entry.insert()
		stock_entry.submit()
		self.db_set("stock_entry", stock_entry.name)

	def cancel_stock_entry(self):
		if not self.stock_entry:
			return

		stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
		if stock_entry.docstatus == 1:
			stock_entry.flags.ignore_permissions = True
			stock_entry.cancel()


@frappe.whitelist()
def resolve_barcode(barcode, company=None, warehouse=None):
	check_create_permission()

	barcode = (barcode or "").strip()
	if not barcode:
		frappe.throw(_("Scan a barcode first"))

	item_code = find_item_code(barcode)
	if not item_code:
		frappe.throw(_("No Item found for barcode {0}").format(frappe.bold(barcode)), title=_("Invalid Barcode"))

	return {"barcode": barcode, **get_item_details(item_code, company, warehouse)}


@frappe.whitelist()
def get_item_details(item_code, company=None, warehouse=None):
	check_create_permission()

	item = get_item(item_code)
	error = get_item_error(item, item_code)
	if error:
		frappe.throw(error, title=_("Invalid Item"))

	default_warehouse = None
	if company:
		default_warehouse = frappe.db.get_value(
			"Item Default",
			{"parent": item_code, "parenttype": "Item", "company": company},
			"default_warehouse",
		)

	warehouse = warehouse or default_warehouse

	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"stock_uom": item.stock_uom,
		"basic_rate": get_basic_rate(item_code, warehouse, item),
		"actual_qty": get_actual_qty(item_code, warehouse),
		"default_warehouse": default_warehouse,
	}


def find_item_code(barcode_or_item_code):
	item_code = frappe.db.get_value(
		"Item Barcode", {"barcode": barcode_or_item_code, "parenttype": "Item"}, "parent"
	)
	if item_code:
		return item_code
	if frappe.db.exists("Item", barcode_or_item_code):
		return barcode_or_item_code
	return None


def check_create_permission():
	if not frappe.has_permission("AM Stock Entry", "create"):
		frappe.throw(_("You do not have permission to create AM Stock Entries"), frappe.PermissionError)


def get_item(item_code):
	return frappe.db.get_value(
		"Item",
		item_code,
		[
			"name",
			"item_name",
			"stock_uom",
			"disabled",
			"is_stock_item",
			"has_variants",
			"end_of_life",
			"valuation_rate",
			"standard_rate",
		],
		as_dict=True,
	)


def get_item_error(item, item_code, posting_date=None):
	if not item:
		return _("Item {0} does not exist").format(frappe.bold(item_code))
	if item.disabled:
		return _("Item {0} is disabled").format(frappe.bold(item_code))
	if item.has_variants:
		return _("Item {0} is a template; select one of its variants").format(frappe.bold(item_code))
	if not item.is_stock_item:
		return _("Item {0} is not a stock item").format(frappe.bold(item_code))
	if item.end_of_life and getdate(item.end_of_life) <= getdate(posting_date):
		return _("Item {0} reached its end of life on {1}").format(
			frappe.bold(item_code), frappe.format(item.end_of_life, "Date")
		)
	return None


def get_warehouse_error(warehouse_details, warehouse, company):
	if not warehouse_details:
		return _("Warehouse {0} does not exist").format(warehouse)
	if warehouse_details.is_group:
		return _("Warehouse {0} is a group warehouse; select a leaf warehouse").format(warehouse)
	if warehouse_details.disabled:
		return _("Warehouse {0} is disabled").format(warehouse)
	if warehouse_details.company != company:
		return _("Warehouse {0} does not belong to company {1}").format(warehouse, company)
	return None


def get_basic_rate(item_code, warehouse=None, item=None):
	if warehouse:
		valuation_rate = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate")
		if flt(valuation_rate):
			return flt(valuation_rate)

	item = item or frappe.db.get_value("Item", item_code, ["valuation_rate", "standard_rate"], as_dict=True)
	if not item:
		return 0
	return flt(item.valuation_rate) or flt(item.standard_rate)


def get_actual_qty(item_code, warehouse):
	if not (item_code and warehouse):
		return 0
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))


def throw_errors(errors, title):
	if errors:
		frappe.throw("<br>".join(errors), title=title)
