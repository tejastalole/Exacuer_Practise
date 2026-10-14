// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

frappe.ui.form.on("AM Asset Return", {
	setup(frm) {
		frm.set_query("assignment", () => {
			return {
				filters: {
					docstatus: 1,
				},
			};
		});
	},

	validate(frm) {
		if (!frm.doc.assignment) {
			frappe.throw(__("Assignment is required"));
		}
		if (!frm.doc.return_date) {
			frappe.throw(__("Return Date is required"));
		}
		if (
			frm.doc.return_date &&
			frappe.datetime.get_diff(frm.doc.return_date, frappe.datetime.get_today()) > 0
		) {
			frappe.throw(__("Return Date cannot be in the future"));
		}
	},

	assignment(frm) {
		if (!frm.doc.assignment) {
			return;
		}

		frappe.db.get_value(
			"AM Asset Assignment",
			frm.doc.assignment,
			["asset", "employee", "assignment_date", "docstatus"],
			(r) => {
				if (!r) {
					return;
				}
				if (cint(r.docstatus) !== 1) {
					frappe.msgprint(__("Please select a submitted Assignment"));
					frm.set_value("assignment", null);
					return;
				}
				frm.set_value("asset", r.asset);
				frm.set_value("employee", r.employee);

				if (
					frm.doc.return_date &&
					r.assignment_date &&
					frappe.datetime.get_diff(frm.doc.return_date, r.assignment_date) < 0
				) {
					frappe.msgprint(__("Return Date cannot be before Assignment Date"));
				}
			}
		);

		// Block if already returned
		frappe.db.get_value(
			"AM Asset Return",
			{ assignment: frm.doc.assignment, docstatus: 1 },
			"name",
			(r) => {
				if (r && r.name) {
					frappe.msgprint(__("This Assignment is already returned via {0}", [r.name]));
				}
			}
		);
	},
});
