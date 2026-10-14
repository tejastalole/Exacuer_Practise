// Copyright (c) 2026, Tejas Talole and contributors
// For license information, please see license.txt

const ASSET_STATUS_INDICATORS = {
	Available: "green",
	Assigned: "blue",
	"Under Maintenance": "orange",
	Returned: "yellow",
	Retired: "red",
};

frappe.query_reports["Asset Register"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "AM Asset Category",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", ...Object.keys(ASSET_STATUS_INDICATORS)],
		},
		{
			fieldname: "employee",
			label: __("Assigned To"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "from_date",
			label: __("Purchase From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Purchase To Date"),
			fieldtype: "Date",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "status" && data && data.status) {
			const color = ASSET_STATUS_INDICATORS[data.status] || "gray";
			return `<span class="indicator-pill ${color}">${value}</span>`;
		}

		if (column.fieldname === "last_return_condition" && data && data.last_return_condition) {
			const color = data.last_return_condition === "Good" ? "green" : "orange";
			return `<span class="indicator-pill ${color}">${value}</span>`;
		}

		return value;
	},
};
