# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


ASSET_STATUSES = [
	"Available",
	"Assigned",
	"Under Maintenance",
	"Returned",
	"Retired",
]


class AMAsset(Document):
	def validate(self):
		self.validate_status()
		self.validate_purchase_date()
		self.validate_purchase_value()
		self.validate_category_active()
		self.sync_assignment_fields_on_status_change()

	def validate_status(self):
		if self.status not in ASSET_STATUSES:
			frappe.throw(_("Invalid Asset Status: {0}").format(self.status))

	def validate_purchase_date(self):
		if self.purchase_date and getdate(self.purchase_date) > getdate(today()):
			frappe.throw(_("Purchase Date cannot be in the future"))

	def validate_purchase_value(self):
		if self.purchase_value is not None and self.purchase_value < 0:
			frappe.throw(_("Purchase Value cannot be negative"))

	def validate_category_active(self):
		if not self.asset_category:
			return
		is_active = frappe.db.get_value("AM Asset Category", self.asset_category, "is_active")
		if is_active == 0:
			frappe.throw(_("Asset Category {0} is inactive").format(self.asset_category))

	def sync_assignment_fields_on_status_change(self):
		"""Clear employee link when asset is freed / retired manually."""
		if self.flags.from_assignment_flow:
			return

		if self.has_value_changed("status") and self.status in ("Available", "Returned", "Retired"):
			self.assigned_to = None
			self.assigned_on = None
			self.current_assignment = None

		if self.status == "Assigned" and not self.assigned_to:
			frappe.throw(_("Assigned To is required when status is Assigned. Use Asset Assignment."))

	def on_trash(self):
		if self.status == "Assigned":
			frappe.throw(_("Cannot delete an Assigned asset. Return it first."))
