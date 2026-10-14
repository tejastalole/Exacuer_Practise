# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class AMAssetReturn(Document):
	def validate(self):
		self.validate_dates()
		self.validate_assignment()
		self.fetch_assignment_details()

	def validate_dates(self):
		if self.return_date and getdate(self.return_date) > getdate(today()):
			frappe.throw(_("Return Date cannot be in the future"))

		if self.assignment and self.return_date:
			assignment_date = frappe.db.get_value("AM Asset Assignment", self.assignment, "assignment_date")
			if assignment_date and getdate(self.return_date) < getdate(assignment_date):
				frappe.throw(_("Return Date cannot be before Assignment Date"))

	def validate_assignment(self):
		if not self.assignment:
			return

		assignment = frappe.get_doc("AM Asset Assignment", self.assignment)

		if assignment.docstatus != 1:
			frappe.throw(_("Assignment {0} must be submitted").format(self.assignment))

		existing_return = frappe.db.exists(
			"AM Asset Return",
			{
				"assignment": self.assignment,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
		)
		if existing_return:
			frappe.throw(
				_("Assignment {0} is already returned via {1}").format(self.assignment, existing_return)
			)

		asset_status = frappe.db.get_value("AM Asset", assignment.asset, "status")
		if asset_status != "Assigned":
			frappe.throw(
				_("Asset {0} is not Assigned. Current status: {1}").format(assignment.asset, asset_status)
			)

	def fetch_assignment_details(self):
		if not self.assignment:
			return
		assignment = frappe.db.get_value(
			"AM Asset Assignment",
			self.assignment,
			["asset", "employee"],
			as_dict=True,
		)
		if assignment:
			self.asset = assignment.asset
			self.employee = assignment.employee

	def on_submit(self):
		self.update_asset_on_return()

	def on_cancel(self):
		self.revert_asset_on_cancel()

	def update_asset_on_return(self):
		"""Automatically update asset status when returned."""
		asset = frappe.get_doc("AM Asset", self.asset)

		if asset.status != "Assigned":
			frappe.throw(_("Asset {0} is not Assigned").format(self.asset))

		if asset.assigned_to and asset.assigned_to != self.employee:
			frappe.throw(
				_("Asset {0} is assigned to {1}, not {2}").format(
					self.asset, asset.assigned_to, self.employee
				)
			)

		asset.flags.from_assignment_flow = True

		# Condition drives next status
		if self.condition_on_return == "Needs Maintenance":
			asset.status = "Under Maintenance"
		elif self.condition_on_return == "Damaged":
			asset.status = "Returned"
		else:
			# Good → Available for reassignment
			asset.status = "Available"

		asset.assigned_to = None
		asset.assigned_on = None
		asset.current_assignment = None
		asset.save(ignore_permissions=True)

	def revert_asset_on_cancel(self):
		# Restore assignment only if no other active assignment exists
		asset = frappe.get_doc("AM Asset", self.asset)
		if asset.status == "Assigned":
			frappe.throw(_("Cannot cancel Return because Asset is already Assigned again"))

		assignment = frappe.get_doc("AM Asset Assignment", self.assignment)
		if assignment.docstatus != 1:
			return

		asset.flags.from_assignment_flow = True
		asset.status = "Assigned"
		asset.assigned_to = assignment.employee
		asset.assigned_on = assignment.assignment_date
		asset.current_assignment = assignment.name
		asset.save(ignore_permissions=True)
