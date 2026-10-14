import frappe


def execute():
	for fieldname in ("am_barcode_section", "am_scan_barcode"):
		custom_field = frappe.db.get_value("Custom Field", {"dt": "Stock Entry", "fieldname": fieldname})
		if custom_field:
			frappe.delete_doc("Custom Field", custom_field, ignore_permissions=True)

	frappe.clear_cache(doctype="Stock Entry")
