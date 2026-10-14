// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

frappe.ui.form.on("AM Asset Assignment", {
	setup(frm) {
		frm.set_query("asset", () => {
			return {
				filters: {
					status: ["in", ["Available", "Returned"]],
				},
			};
		});

		frm.set_query("employee", () => {
			return {
				filters: {
					status: "Active",
				},
			};
		});
	},

	validate(frm) {
		if (!frm.doc.asset) {
			frappe.throw(__("Asset is required"));
		}
		if (!frm.doc.employee) {
			frappe.throw(__("Employee is required"));
		}
		if (
			frm.doc.assignment_date &&
			frappe.datetime.get_diff(frm.doc.assignment_date, frappe.datetime.get_today()) > 0
		) {
			frappe.throw(__("Assignment Date cannot be in the future"));
		}

		if (frm.doc.asset_status && !["Available", "Returned"].includes(frm.doc.asset_status)) {
			frappe.throw(
				__("Asset must be Available or Returned to assign. Current status: {0}", [
					frm.doc.asset_status,
				])
			);
		}
	},

	asset(frm) {
		if (!frm.doc.asset) {
			return;
		}
		frappe.db.get_value("AM Asset", frm.doc.asset, ["status", "assigned_to"], (r) => {
			if (!r) {
				return;
			}
			frm.set_value("asset_status", r.status);
			if (r.status === "Assigned") {
				frappe.msgprint(
					__("This asset is already assigned to {0}. Return it first.", [r.assigned_to || __("someone")])
				);
			}
		});
	},
});
