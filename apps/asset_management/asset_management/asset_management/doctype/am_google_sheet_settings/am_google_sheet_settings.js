// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

frappe.ui.form.on("AM Google Sheet Settings", {
	refresh(frm) {
		if (!frm.doc.enabled) return;

		frm.add_custom_button(__("Sync Now"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings before syncing"));
				return;
			}

			frappe.call({
				method: "asset_management.google_sheet_sync.sync_now",
				freeze: true,
				freeze_message: __("Syncing with Google Sheet..."),
				callback: ({ message }) => {
					frappe.msgprint({ title: __("Google Sheet Sync"), message, indicator: "blue" });
					frm.reload_doc();
				},
			});
		});
	},

	auto_sync(frm) {
		if (frm.doc.auto_sync) frm.set_value("daily_sync", 0);
	},

	daily_sync(frm) {
		if (frm.doc.daily_sync) frm.set_value("auto_sync", 0);
	},
});
