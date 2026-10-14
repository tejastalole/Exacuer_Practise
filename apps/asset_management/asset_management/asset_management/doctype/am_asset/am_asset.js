// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

frappe.ui.form.on("AM Asset", {
	setup(frm) {
		frm.set_query("asset_category", () => {
			return {
				filters: {
					is_active: 1,
				},
			};
		});
	},

	validate(frm) {
		if (!frm.doc.asset_name) {
			frappe.throw(__("Asset Name is required"));
		}
		if (!frm.doc.asset_category) {
			frappe.throw(__("Asset Category is required"));
		}
		if (frm.doc.purchase_value != null && frm.doc.purchase_value < 0) {
			frappe.throw(__("Purchase Value cannot be negative"));
		}
		if (frm.doc.purchase_date && frappe.datetime.get_diff(frm.doc.purchase_date, frappe.datetime.get_today()) > 0) {
			frappe.throw(__("Purchase Date cannot be in the future"));
		}
		if (frm.doc.status === "Assigned" && !frm.doc.assigned_to) {
			frappe.throw(__("Assigned To is required when status is Assigned. Use Asset Assignment."));
		}
	},

	status(frm) {
		if (["Available", "Returned", "Retired"].includes(frm.doc.status)) {
			frm.set_value("assigned_to", null);
			frm.set_value("assigned_on", null);
			frm.set_value("current_assignment", null);
		}
	},
});
