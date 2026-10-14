# Copyright (c) 2026, Tejas Talole and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

SYNC_INTERVALS = (30, 60, 90, 120)


class AMGoogleSheetSettings(Document):
	def validate(self):
		self.web_app_url = (self.web_app_url or "").strip()
		if self.enabled and not self.web_app_url.startswith("https://script.google.com/"):
			frappe.throw(_("Web App URL must be an Apps Script URL starting with https://script.google.com/"))

		if self.auto_sync and self.daily_sync:
			frappe.throw(_("Choose either Auto Sync or Daily Sync, not both"))

		if self.auto_sync and cint(self.sync_interval) not in SYNC_INTERVALS:
			frappe.throw(
				_("Sync Interval must be one of: {0} seconds").format(", ".join(map(str, SYNC_INTERVALS)))
			)

		# A new or changed daily schedule only counts from now on, so a Sync Time
		# that already passed today doesn't trigger an immediate sync.
		if self.daily_sync and any(
			self.has_value_changed(field) for field in ("daily_sync", "daily_sync_from", "daily_sync_time")
		):
			self.last_daily_sync_on = now_datetime()

