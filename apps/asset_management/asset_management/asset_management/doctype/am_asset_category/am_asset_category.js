// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

frappe.ui.form.on("AM Asset Category", {
	validate(frm) {
		if (!frm.doc.category_name || !frm.doc.category_name.trim()) {
			frappe.throw(__("Category Name is required"));
		}
	},
});
