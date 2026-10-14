// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

const AM_STOCK_ENTRY_METHOD = "asset_management.asset_management.doctype.am_stock_entry.am_stock_entry";

frappe.ui.form.on("AM Stock Entry", {
	setup(frm) {
		const warehouse_query = () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		});
		frm.set_query("from_warehouse", warehouse_query);
		frm.set_query("to_warehouse", warehouse_query);
		frm.set_query("s_warehouse", "items", warehouse_query);
		frm.set_query("t_warehouse", "items", warehouse_query);
		frm.set_query("item_code", "items", () => ({
			filters: { is_stock_item: 1, disabled: 0, has_variants: 0 },
		}));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			focus_scan_field(frm);
		}

		if (frm.doc.docstatus === 1 && frm.doc.stock_entry) {
			frm.add_custom_button(__("Stock Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.stock_entry)
			);
			frm.add_custom_button(__("Stock Ledger"), () =>
				frappe.set_route("query-report", "Stock Ledger", {
					company: frm.doc.company,
					voucher_no: frm.doc.stock_entry,
					from_date: frm.doc.posting_date,
					to_date: frm.doc.posting_date,
				})
			);
		}
	},

	stock_entry_type(frm) {
		if (!needs_source_warehouse(frm)) frm.set_value("from_warehouse", null);
		if (!needs_target_warehouse(frm)) frm.set_value("to_warehouse", null);

		(frm.doc.items || []).forEach((row) => {
			if (!needs_source_warehouse(frm)) row.s_warehouse = null;
			if (!needs_target_warehouse(frm)) row.t_warehouse = null;
		});
		frm.refresh_field("items");
	},

	company(frm) {
		frm.set_value("from_warehouse", null);
		frm.set_value("to_warehouse", null);
	},

	from_warehouse(frm) {
		set_warehouse_on_rows(frm, "s_warehouse", frm.doc.from_warehouse);
	},

	to_warehouse(frm) {
		set_warehouse_on_rows(frm, "t_warehouse", frm.doc.to_warehouse);
	},

	scan_barcode(frm) {
		const barcode = (frm.doc.scan_barcode || "").trim();
		if (!barcode) return;

		frm.set_value("scan_barcode", "");

		// Scanners fire faster than the server responds; process scans one at a time
		// so repeated scans of the same item increase one row instead of adding several.
		frm.scan_queue = (frm.scan_queue || Promise.resolve())
			.then(() => add_scanned_item(frm, barcode))
			.catch(() => frappe.utils.play_sound("error"))
			.finally(() => focus_scan_field(frm));
	},

	validate(frm) {
		const errors = [];

		(frm.doc.items || []).forEach((row) => {
			if (!row.item_code) return;

			if (flt(row.qty) <= 0) {
				errors.push(__("Row #{0}: Quantity for {1} must be greater than zero", [row.idx, row.item_code]));
			}
			if (needs_source_warehouse(frm) && !(row.s_warehouse || frm.doc.from_warehouse)) {
				errors.push(__("Row #{0}: Source Warehouse is required", [row.idx]));
			}
			if (needs_target_warehouse(frm) && !(row.t_warehouse || frm.doc.to_warehouse)) {
				errors.push(__("Row #{0}: Target Warehouse is required", [row.idx]));
			}
			if (row.s_warehouse && row.s_warehouse === row.t_warehouse) {
				errors.push(
					__("Row #{0}: Source and Target Warehouse cannot be the same ({1})", [row.idx, row.s_warehouse])
				);
			}
		});

		if (errors.length) {
			frappe.msgprint({
				title: __("Stock Entry Validation"),
				message: errors.join("<br>"),
				indicator: "red",
			});
			frappe.validated = false;
		}
	},
});

frappe.ui.form.on("AM Stock Entry Item", {
	items_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		set_default_row_warehouses(frm, row);
		frm.refresh_field("items");
	},

	items_remove(frm) {
		update_totals(frm);
	},

	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;

		frappe.call({
			method: `${AM_STOCK_ENTRY_METHOD}.get_item_details`,
			args: {
				item_code: row.item_code,
				company: frm.doc.company,
				warehouse: row.s_warehouse || row.t_warehouse,
			},
			callback: ({ message: item }) => {
				if (!item) return;
				apply_item_details(frm, row, item);
				frm.refresh_field("items");
				update_totals(frm);
			},
			error: () => frappe.model.set_value(cdt, cdn, "item_code", null),
		});
	},

	qty(frm, cdt, cdn) {
		update_row_amount(frm, locals[cdt][cdn]);
	},

	basic_rate(frm, cdt, cdn) {
		update_row_amount(frm, locals[cdt][cdn]);
	},

	s_warehouse(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code || !row.s_warehouse) return;

		frappe.call({
			method: `${AM_STOCK_ENTRY_METHOD}.get_item_details`,
			args: { item_code: row.item_code, company: frm.doc.company, warehouse: row.s_warehouse },
			callback: ({ message: item }) => {
				if (!item) return;
				row.actual_qty = item.actual_qty;
				row.basic_rate = item.basic_rate;
				update_row_amount(frm, row);
			},
		});
	},
});

async function add_scanned_item(frm, barcode) {
	const { message: item } = await frappe.call({
		method: `${AM_STOCK_ENTRY_METHOD}.resolve_barcode`,
		args: {
			barcode,
			company: frm.doc.company,
			warehouse: frm.doc.from_warehouse || frm.doc.to_warehouse,
		},
	});
	if (!item) return;

	const existing_row = (frm.doc.items || []).find((row) => row.item_code === item.item_code);
	if (existing_row) {
		existing_row.qty = flt(existing_row.qty) + 1;
		update_row_amount(frm, existing_row);
		show_scan_alert(__("{0}: quantity increased to {1}", [item.item_code, existing_row.qty]), "blue");
		return;
	}

	const row = (frm.doc.items || []).find((d) => !d.item_code) || frm.add_child("items");
	row.qty = 1;
	row.barcode = item.barcode;
	set_default_row_warehouses(frm, row, item.default_warehouse);
	apply_item_details(frm, row, item);
	update_row_amount(frm, row);

	show_scan_alert(__("{0} ({1}) added", [item.item_name || item.item_code, item.item_code]), "green");
}

function apply_item_details(frm, row, item) {
	row.item_code = item.item_code;
	row.item_name = item.item_name;
	row.stock_uom = item.stock_uom;
	row.actual_qty = item.actual_qty;
	if (!flt(row.basic_rate)) {
		row.basic_rate = item.basic_rate;
	}
	row.amount = flt(row.qty) * flt(row.basic_rate);
}

function set_default_row_warehouses(frm, row, item_default_warehouse) {
	if (needs_source_warehouse(frm)) {
		row.s_warehouse = row.s_warehouse || frm.doc.from_warehouse || item_default_warehouse;
	}
	if (needs_target_warehouse(frm)) {
		row.t_warehouse =
			row.t_warehouse ||
			frm.doc.to_warehouse ||
			(frm.doc.stock_entry_type === "Material Receipt" ? item_default_warehouse : null);
	}
}

function set_warehouse_on_rows(frm, fieldname, warehouse) {
	if (!warehouse) return;
	(frm.doc.items || []).forEach((row) => {
		row[fieldname] = warehouse;
	});
	frm.refresh_field("items");
}

function update_row_amount(frm, row) {
	row.amount = flt(row.qty) * flt(row.basic_rate);
	frm.refresh_field("items");
	update_totals(frm);
}

function update_totals(frm) {
	const items = frm.doc.items || [];
	frm.set_value(
		"total_qty",
		items.reduce((total, row) => total + flt(row.qty), 0)
	);
	frm.set_value(
		"total_amount",
		items.reduce((total, row) => total + flt(row.amount), 0)
	);
}

function needs_source_warehouse(frm) {
	return ["Material Issue", "Material Transfer"].includes(frm.doc.stock_entry_type);
}

function needs_target_warehouse(frm) {
	return ["Material Receipt", "Material Transfer"].includes(frm.doc.stock_entry_type);
}

function show_scan_alert(message, indicator) {
	frappe.show_alert({ message, indicator }, 3);
	frappe.utils.play_sound("submit");
}

function focus_scan_field(frm) {
	frm.get_field("scan_barcode")?.$input?.trigger("focus");
}
