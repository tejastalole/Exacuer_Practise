# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class AMAssetAssignment(Document):
	def validate(self):
		self.validate_dates()
		self.validate_employee_active()
		self.validate_asset_availability()

	def validate_dates(self):
		if self.assignment_date and getdate(self.assignment_date) > getdate(today()):
			frappe.throw(_("Assignment Date cannot be in the future"))

	def validate_employee_active(self):
		if not self.employee:
			return
		status = frappe.db.get_value("Employee", self.employee, "status")
		if status != "Active":
			frappe.throw(_("Employee {0} is not Active").format(self.employee))

	def validate_asset_availability(self):
		if not self.asset:
			return

		asset = frappe.get_doc("AM Asset", self.asset)

		if asset.status == "Retired":
			frappe.throw(_("Asset {0} is Retired and cannot be assigned").format(self.asset))

		if asset.status == "Under Maintenance":
			frappe.throw(_("Asset {0} is Under Maintenance and cannot be assigned").format(self.asset))

		# One asset → only one employee at a time
		if asset.status == "Assigned":
			frappe.throw(
				_("Asset {0} is already assigned to {1}. Return it before assigning again.").format(
					self.asset, asset.assigned_to or _("another employee")
				)
			)

		if asset.status not in ("Available", "Returned"):
			frappe.throw(
				_("Asset {0} must be Available or Returned to assign. Current status: {1}").format(
					self.asset, asset.status
				)
			)

		open_assignment = frappe.db.exists(
			"AM Asset Assignment",
			{
				"asset": self.asset,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
		)
		if open_assignment:
			# Check if that assignment was returned
			returned = frappe.db.exists(
				"AM Asset Return",
				{"assignment": open_assignment, "docstatus": 1},
			)
			if not returned:
				frappe.throw(
					_("Asset {0} already has an open Assignment {1}").format(self.asset, open_assignment)
				)

	def on_submit(self):
		self.mark_asset_assigned()

	def on_cancel(self):
		self.revert_asset_on_cancel()

	def mark_asset_assigned(self):
		asset = frappe.get_doc("AM Asset", self.asset)
		# Re-check at submit time (race condition guard)
		if asset.status == "Assigned" and asset.current_assignment != self.name:
			frappe.throw(
				_("Asset {0} is already assigned. Only one employee can hold an asset at a time.").format(
					self.asset
				)
			)

		asset.flags.from_assignment_flow = True
		asset.status = "Assigned"
		asset.assigned_to = self.employee
		asset.assigned_on = self.assignment_date
		asset.current_assignment = self.name
		asset.save(ignore_permissions=True)

	def revert_asset_on_cancel(self):
		# Block cancel if a return already exists against this assignment
		if frappe.db.exists("AM Asset Return", {"assignment": self.name, "docstatus": 1}):
			frappe.throw(_("Cannot cancel Assignment {0} because a Return already exists").format(self.name))

		asset = frappe.get_doc("AM Asset", self.asset)
		if asset.current_assignment == self.name:
			asset.flags.from_assignment_flow = True
			asset.status = "Available"
			asset.assigned_to = None
			asset.assigned_on = None
			asset.current_assignment = None
			asset.save(ignore_permissions=True)
