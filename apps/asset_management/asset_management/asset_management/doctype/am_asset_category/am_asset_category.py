# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AMAssetCategory(Document):
	def validate(self):
		if not self.category_name or not self.category_name.strip():
			frappe.throw(_("Category Name is required"))

		self.category_name = self.category_name.strip()
