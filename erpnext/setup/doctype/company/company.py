# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe, os, json
from frappe import _
from frappe.utils import get_timestamp

from frappe.utils import cint, today, formatdate
import frappe.defaults
from frappe.cache_manager import clear_defaults_cache

from frappe.model.document import Document
from frappe.contacts.address_and_contact import load_address_and_contact
from frappe.utils.nestedset import NestedSet

class Company(NestedSet):
	nsm_parent_field = 'parent_company'

	def validate(self):
		self.validate_abbr()
		#self.validate_default_accounts()
		self.validate_currency()
		#self.validate_coa_input()
		#self.validate_perpetual_inventory()
		self.check_country_change()

	def validate_abbr(self):
		if not self.abbr:
			self.abbr = ''.join([c[0] for c in self.company_name.split()]).upper()

		self.abbr = self.abbr.strip()

		# if self.get('__islocal') and len(self.abbr) > 5:
		# 	frappe.throw(_("Abbreviation cannot have more than 5 characters"))

		if not self.abbr.strip():
			frappe.throw(_("Abbreviation is mandatory"))

		if frappe.db.sql("select abbr from tabCompany where name!=%s and abbr=%s", (self.name, self.abbr)):
			frappe.throw(_("Abbreviation already used for another company"))

	def create_default_tax_template(self):
		from erpnext.setup.setup_wizard.operations.taxes_setup import create_sales_tax
		create_sales_tax({
			'country': self.country,
			'company_name': self.name
		})


	def validate_currency(self):
		self.previous_default_currency = frappe.db.get_value("Company", self.name, "default_currency")
		if self.default_currency and self.previous_default_currency and \
			self.default_currency != self.previous_default_currency :
					frappe.throw(_("Cannot change company's default currency, because there are existing transactions. Transactions must be cancelled to change the default currency."))

		if frappe.flags.country_change:
			install_country_fixtures(self.name)

		#if not frappe.db.get_value("Cost Center", {"is_group": 0, "company": self.name}):
			#self.create_default_cost_center()

		#if not frappe.local.flags.ignore_chart_of_accounts:
			#self.set_default_accounts()
			#if self.default_cash_account:
				#self.set_mode_of_payment_account()

		if self.default_currency:
			frappe.db.set_value("Currency", self.default_currency, "enabled", 1)

		#if hasattr(frappe.local, 'enable_perpetual_inventory') and \
			#self.name in frappe.local.enable_perpetual_inventory:
			#frappe.local.enable_perpetual_inventory[self.name] = self.enable_perpetual_inventory

		frappe.clear_cache()

	def check_country_change(self):
		frappe.flags.country_change = False

		if not self.get('__islocal') and \
			self.country != frappe.db.get_value('Company', self.name, 'country'):
			frappe.flags.country_change = True


	def after_rename(self, olddn, newdn, merge=False):
		frappe.db.set(self, "company_name", newdn)

		frappe.db.sql("""update `tabDefaultValue` set defvalue=%s
			where defkey='Company' and defvalue=%s""", (newdn, olddn))

		clear_defaults_cache()

	def abbreviate(self):
		self.abbr = ''.join([c[0].upper() for c in self.company_name.split()])


@frappe.whitelist()
def enqueue_replace_abbr(company, old, new):
	kwargs = dict(company=company, old=old, new=new)
	frappe.enqueue('erpnext.setup.doctype.company.company.replace_abbr', **kwargs)


@frappe.whitelist()
def replace_abbr(company, old, new):
	new = new.strip()
	if not new:
		frappe.throw(_("Abbr can not be blank or space"))

	frappe.only_for("System Manager")

	frappe.db.set_value("Company", company, "abbr", new)

	def _rename_record(doc):
		parts = doc[0].rsplit(" - ", 1)
		if len(parts) == 1 or parts[1].lower() == old.lower():
			frappe.rename_doc(dt, doc[0], parts[0] + " - " + new)

	def _rename_records(dt):
		# rename is expensive so let's be economical with memory usage
		doc = (d for d in frappe.db.sql("select name from `tab%s` where company=%s" % (dt, '%s'), company))
		for d in doc:
			_rename_record(d)

def get_name_with_abbr(name, company):
	company_abbr = frappe.db.get_value("Company", company, "abbr")
	parts = name.split(" - ")

	if parts[-1].lower() != company_abbr.lower():
		parts.append(company_abbr)

	return " - ".join(parts)

def install_country_fixtures(company):
	company_doc = frappe.get_doc("Company", company)
	path = frappe.get_app_path('erpnext', 'regional', frappe.scrub(company_doc.country))
	if os.path.exists(path.encode("utf-8")):
		frappe.get_attr("erpnext.regional.{0}.setup.setup"
			.format(frappe.scrub(company_doc.country)))(company_doc)



@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False):
	if parent == None or parent == "All Companies":
		parent = ""

	return frappe.db.sql("""
		select
			name as value,
			is_group as expandable
		from
			`tab{doctype}` comp
		where
			ifnull(parent_company, "")="{parent}"
		""".format(
			doctype = frappe.db.escape(doctype),
			parent=frappe.db.escape(parent)
		), as_dict=1)

@frappe.whitelist()
def add_node():
	from frappe.desk.treeview import make_tree_args
	args = frappe.form_dict
	args = make_tree_args(**args)

	if args.parent_company == 'All Companies':
		args.parent_company = None

	frappe.get_doc(args).insert()


