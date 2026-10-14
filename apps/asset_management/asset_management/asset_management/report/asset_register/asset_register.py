# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

from asset_management.asset_management.doctype.am_asset.am_asset import ASSET_STATUSES

STATUS_COLORS = {
	"Available": "#28a745",
	"Assigned": "#2490ef",
	"Under Maintenance": "#fd7e14",
	"Returned": "#ffc107",
	"Retired": "#e03636",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	columns = get_columns()
	data = get_data(filters)

	return columns, data, None, get_chart(data), get_report_summary(data)


def validate_filters(filters):
	if filters.status and filters.status not in ASSET_STATUSES:
		frappe.throw(_("Invalid asset status"))

	if filters.from_date and filters.to_date and getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"label": _("Asset"), "fieldname": "name", "fieldtype": "Link", "options": "AM Asset", "width": 170},
		{"label": _("Asset Name"), "fieldname": "asset_name", "fieldtype": "Data", "width": 180},
		{
			"label": _("Category"),
			"fieldname": "asset_category",
			"fieldtype": "Link",
			"options": "AM Asset Category",
			"width": 140,
		},
		{"label": _("Serial Number"), "fieldname": "serial_number", "fieldtype": "Data", "width": 130},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 140},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 150},
		{"label": _("Purchase Date"), "fieldname": "purchase_date", "fieldtype": "Date", "width": 110},
		{"label": _("Purchase Value"), "fieldname": "purchase_value", "fieldtype": "Currency", "width": 130},
		{
			"label": _("Assigned To"),
			"fieldname": "assigned_to",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 130,
		},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("Assigned On"), "fieldname": "assigned_on", "fieldtype": "Date", "width": 110},
		{"label": _("Days Assigned"), "fieldname": "days_assigned", "fieldtype": "Int", "width": 110},
		{
			"label": _("Current Assignment"),
			"fieldname": "current_assignment",
			"fieldtype": "Link",
			"options": "AM Asset Assignment",
			"width": 170,
		},
		{"label": _("Total Assignments"), "fieldname": "total_assignments", "fieldtype": "Int", "width": 130},
		{"label": _("Last Return Date"), "fieldname": "last_return_date", "fieldtype": "Date", "width": 120},
		{
			"label": _("Last Return Condition"),
			"fieldname": "last_return_condition",
			"fieldtype": "Data",
			"width": 150,
		},
	]


def get_asset_filters(filters):
	asset_filters = {}
	for field in ("company", "asset_category", "status"):
		if filters.get(field):
			asset_filters[field] = filters.get(field)

	if filters.employee:
		asset_filters["assigned_to"] = filters.employee

	if filters.from_date and filters.to_date:
		asset_filters["purchase_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		asset_filters["purchase_date"] = [">=", filters.from_date]
	elif filters.to_date:
		asset_filters["purchase_date"] = ["<=", filters.to_date]

	return asset_filters


def get_data(filters):
	assets = frappe.get_list(
		"AM Asset",
		filters=get_asset_filters(filters),
		fields=[
			"name",
			"asset_name",
			"asset_category",
			"serial_number",
			"status",
			"company",
			"purchase_date",
			"purchase_value",
			"assigned_to",
			"assigned_on",
			"current_assignment",
		],
		order_by="asset_category asc, name asc",
	)
	if not assets:
		return []

	asset_names = [asset.name for asset in assets]
	assignment_counts = get_assignment_counts(asset_names)
	last_returns = get_last_returns(asset_names)
	employee_names = get_employee_names({asset.assigned_to for asset in assets if asset.assigned_to})

	current_date = today()
	for asset in assets:
		last_return = last_returns.get(asset.name, {})
		asset.update(
			{
				"employee_name": employee_names.get(asset.assigned_to),
				"days_assigned": date_diff(current_date, asset.assigned_on)
				if asset.status == "Assigned" and asset.assigned_on
				else None,
				"total_assignments": assignment_counts.get(asset.name, 0),
				"last_return_date": last_return.get("return_date"),
				"last_return_condition": last_return.get("condition_on_return"),
			}
		)

	return assets


def get_assignment_counts(asset_names):
	rows = frappe.get_all(
		"AM Asset Assignment",
		filters={"asset": ["in", asset_names], "docstatus": 1},
		fields=["asset", "count(name) as count"],
		group_by="asset",
	)
	return {row.asset: row.count for row in rows}


def get_last_returns(asset_names):
	rows = frappe.get_all(
		"AM Asset Return",
		filters={"asset": ["in", asset_names], "docstatus": 1},
		fields=["asset", "return_date", "condition_on_return"],
		order_by="return_date desc, creation desc",
	)

	last_returns = {}
	for row in rows:
		last_returns.setdefault(row.asset, row)
	return last_returns


def get_employee_names(employees):
	if not employees:
		return {}

	rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", list(employees)]},
		fields=["name", "employee_name"],
	)
	return {row.name: row.employee_name for row in rows}


def get_chart(data):
	if not data:
		return None

	counts = {status: 0 for status in ASSET_STATUSES}
	for row in data:
		counts[row.status] = counts.get(row.status, 0) + 1

	labels = [status for status in ASSET_STATUSES if counts[status]]
	return {
		"data": {
			"labels": [_(label) for label in labels],
			"datasets": [{"name": _("Assets"), "values": [counts[label] for label in labels]}],
		},
		"type": "donut",
		"colors": [STATUS_COLORS[label] for label in labels],
		"height": 260,
	}


def get_report_summary(data):
	if not data:
		return None

	def count_status(status):
		return sum(1 for row in data if row.status == status)

	total_value = sum(flt(row.purchase_value) for row in data)
	currency = frappe.get_cached_value("Company", data[0].company, "default_currency") if data[0].company else None

	return [
		{"value": len(data), "label": _("Total Assets"), "datatype": "Int", "indicator": "Blue"},
		{"value": count_status("Available"), "label": _("Available"), "datatype": "Int", "indicator": "Green"},
		{"value": count_status("Assigned"), "label": _("Assigned"), "datatype": "Int", "indicator": "Blue"},
		{
			"value": count_status("Under Maintenance"),
			"label": _("Under Maintenance"),
			"datatype": "Int",
			"indicator": "Orange",
		},
		{"value": count_status("Retired"), "label": _("Retired"), "datatype": "Int", "indicator": "Red"},
		{
			"value": total_value,
			"label": _("Total Purchase Value"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Grey",
		},
	]
