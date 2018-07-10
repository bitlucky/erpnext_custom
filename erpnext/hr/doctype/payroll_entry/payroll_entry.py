# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from dateutil.relativedelta import relativedelta
from frappe.utils import cint, flt, nowdate, add_days, getdate, fmt_money, add_to_date, DATE_FORMAT
from frappe import _
#from erpnext.accounts.utils import get_fiscal_year


class PayrollEntry(Document):

	def on_submit(self):
		self.create_salary_slips()

	def get_emp_list(self):
		"""
			Returns list of active employees based on selected criteria
			and for which salary structure exists
		"""
		cond = self.get_filter_condition()
		cond += self.get_joining_releiving_condition()

		condition = ''
		if self.payroll_frequency:
			condition = """and payroll_frequency = '%(payroll_frequency)s'"""% {"payroll_frequency": self.payroll_frequency}

		sal_struct = frappe.db.sql("""
				select
					name from `tabSalary Structure`
				where
					docstatus = 1 and
					is_active = 'Yes'
					and company = %(company)s and
					ifnull(salary_slip_based_on_timesheet,0) = %(salary_slip_based_on_timesheet)s
					{condition}""".format(condition=condition),
				{"company": self.company, "salary_slip_based_on_timesheet":self.salary_slip_based_on_timesheet})
		if sal_struct:
			cond += "and t2.salary_structure IN %(sal_struct)s "
			cond += "and %(from_date)s >= t2.from_date"
			emp_list = frappe.db.sql("""
				select
					t1.name as employee, t1.employee_name, t1.department, t1.designation, t2.name
				from
					`tabEmployee` t1, `tabSalary Structure Assignment` t2
				where
					t1.name = t2.employee
					and t2.docstatus = 1
			%s order by t2.from_date desc limit 1"""% cond, {"sal_struct": sal_struct, "from_date": self.start_date}, as_dict=True)
			return emp_list

	def fill_employee_details(self):
		self.set('employees', [])
		employees = self.get_emp_list()
		if not employees:
			frappe.throw(_("No employees for the mentioned criteria"))

		for d in employees:
			self.append('employees', d)

	def get_filter_condition(self):
		self.check_mandatory()

		cond = ''
		for f in ['company', 'branch', 'department', 'designation']:
			if self.get(f):
				cond += " and t1." + f + " = '" + self.get(f).replace("'", "\'") + "'"

		return cond

	def get_joining_releiving_condition(self):
		cond = """
			and ifnull(t1.date_of_joining, '0000-00-00') <= '%(end_date)s'
			and ifnull(t1.relieving_date, '2199-12-31') >= '%(start_date)s'
		""" % {"start_date": self.start_date, "end_date": self.end_date}
		return cond

	def check_mandatory(self):
		for fieldname in ['company', 'start_date', 'end_date']:
			if not self.get(fieldname):
				frappe.throw(_("Please set {0}").format(self.meta.get_label(fieldname)))

	def create_salary_slips(self):
		"""
			Creates salary slip for selected employees if already not created
		"""
		self.check_permission('write')
		self.created = 1;
		emp_list = self.get_emp_list()
		ss_list = []
		if emp_list:
			for emp in emp_list:
				if not frappe.db.sql("""select
						name from `tabSalary Slip`
					where
						docstatus!= 2 and
						employee = %s and
						start_date >= %s and
						end_date <= %s and
						company = %s
						""", (emp['employee'], self.start_date, self.end_date, self.company)):
					ss = frappe.get_doc({
						"doctype": "Salary Slip",
						"salary_slip_based_on_timesheet": self.salary_slip_based_on_timesheet,
						"payroll_frequency": self.payroll_frequency,
						"start_date": self.start_date,
						"end_date": self.end_date,
						"employee": emp['employee'],
						"employee_name": frappe.get_value("Employee", {"name":emp['employee']}, "employee_name"),
						"company": self.company,
						"posting_date": self.posting_date
					})
					ss.insert()
					ss_dict = {}
					ss_dict["Employee Name"] = ss.employee_name
					ss_dict["Total Pay"] = fmt_money(ss.rounded_total,currency = frappe.defaults.get_global_default("currency"))
					ss_dict["Salary Slip"] = format_as_links(ss.name)[0]
					ss_list.append(ss_dict)
		return create_log(ss_list)

	def get_sal_slip_list(self, ss_status, as_dict=False):
		"""
			Returns list of salary slips based on selected criteria
		"""
		cond = self.get_filter_condition()

		ss_list = frappe.db.sql("""
			select t1.name, t1.salary_structure from `tabSalary Slip` t1
			where t1.docstatus = %s and t1.start_date >= %s and t1.end_date <= %s
			and (t1.journal_entry is null or t1.journal_entry = "") and ifnull(salary_slip_based_on_timesheet,0) = %s %s
		""" % ('%s', '%s', '%s','%s', cond), (ss_status, self.start_date, self.end_date, self.salary_slip_based_on_timesheet), as_dict=as_dict)
		return ss_list

	def submit_salary_slips(self):
		"""
			Submit all salary slips based on selected criteria
		"""
		self.check_permission('write')

		# self.create_salary_slips()

		jv_name = ""
		ss_list = self.get_sal_slip_list(ss_status=0)
		submitted_ss = []
		not_submitted_ss = []
		frappe.flags.via_payroll_entry = True
		for ss in ss_list:
			ss_obj = frappe.get_doc("Salary Slip",ss[0])
			ss_dict = {}
			ss_dict["Employee Name"] = ss_obj.employee_name
			ss_dict["Total Pay"] = fmt_money(ss_obj.net_pay,
				currency = frappe.defaults.get_global_default("currency"))
			ss_dict["Salary Slip"] = format_as_links(ss_obj.name)[0]

			if ss_obj.net_pay<0:
				not_submitted_ss.append(ss_dict)
			else:
				try:
					ss_obj.submit()
					submitted_ss.append(ss_obj)

				except frappe.ValidationError:
					not_submitted_ss.append(ss_dict)
		if submitted_ss:
			jv_name = self.make_accrual_jv_entry()
			frappe.msgprint(_("Salary Slip submitted for period from {0} to {1}")
				.format(ss_obj.start_date, ss_obj.end_date))

			self.email_salary_slip(submitted_ss)

		return create_submit_log(submitted_ss, not_submitted_ss, jv_name)

	def email_salary_slip(self, submitted_ss):
		if frappe.db.get_single_value("HR Settings", "email_salary_slip_to_employee"):
			for ss in submitted_ss:
				ss.email_salary_slip()


	def get_salary_components(self, component_type):
		salary_slips = self.get_sal_slip_list(ss_status = 1, as_dict = True)
		if salary_slips:
			salary_components = frappe.db.sql("""select salary_component, amount, parentfield
				from `tabSalary Detail` where parentfield = '%s' and parent in (%s)""" %
				(component_type, ', '.join(['%s']*len(salary_slips))), tuple([d.name for d in salary_slips]), as_dict=True)
			return salary_components

	def get_salary_component_total(self, component_type = None):
		salary_components = self.get_salary_components(component_type)
		if salary_components:
			component_dict = {}
			for item in salary_components:
				add_component_to_accrual_jv_entry = True
				if component_type == "earnings":
					is_flexible_benefit, only_tax_impact = frappe.db.get_value("Salary Component", item['salary_component'], ['is_flexible_benefit', 'only_tax_impact'])
					if is_flexible_benefit == 1 and only_tax_impact ==1:
						add_component_to_accrual_jv_entry = False
				if add_component_to_accrual_jv_entry:
					component_dict[item['salary_component']] = component_dict.get(item['salary_component'], 0) + item['amount']




	def update_salary_slip_status(self, jv_name = None):
		ss_list = self.get_sal_slip_list(ss_status=1)
		for ss in ss_list:
			ss_obj = frappe.get_doc("Salary Slip",ss[0])
			frappe.db.set_value("Salary Slip", ss_obj.name, "status", "Paid")
			frappe.db.set_value("Salary Slip", ss_obj.name, "journal_entry", jv_name)

	def set_start_end_dates(self):
		self.update(get_start_end_dates(self.payroll_frequency,
			self.start_date or self.posting_date, self.company))


@frappe.whitelist()
def get_start_end_dates(payroll_frequency, start_date=None, company=None):
	'''Returns dict of start and end dates for given payroll frequency based on start_date'''


	if payroll_frequency == "Weekly":
		end_date = add_days(start_date, 6)

	if payroll_frequency == "Fortnightly":
		end_date = add_days(start_date, 13)

	if payroll_frequency == "Daily":
		end_date = start_date

	return frappe._dict({
		'start_date': start_date, 'end_date': end_date
	})


def get_frequency_kwargs(frequency_name):
	frequency_dict = {
		'monthly': {'months': 1},
		'fortnightly': {'days': 14},
		'weekly': {'days': 7},
		'daily': {'days': 1}
	}
	return frequency_dict.get(frequency_name)


@frappe.whitelist()
def get_end_date(start_date, frequency):
	start_date = getdate(start_date)
	frequency = frequency.lower() if frequency else 'monthly'
	kwargs = get_frequency_kwargs(frequency) if frequency != 'bimonthly' else get_frequency_kwargs('monthly')

	# weekly, fortnightly and daily intervals have fixed days so no problems
	end_date = add_to_date(start_date, **kwargs) - relativedelta(days=1)
	if frequency != 'bimonthly':
		return dict(end_date=end_date.strftime(DATE_FORMAT))

	else:
		return dict(end_date='')



@frappe.whitelist()
def create_log(ss_list):
	if not ss_list:
		frappe.throw(
			_("There's no employee for the given criteria. Check that Salary Slips have not already been created."),
			title='Error'
		)
	return ss_list


def format_as_links(salary_slip):
	return ['<a href="#Form/Salary Slip/{0}">{0}</a>'.format(salary_slip)]


def create_submit_log(submitted_ss, not_submitted_ss, jv_name):

	if not submitted_ss and not not_submitted_ss:
		frappe.msgprint(_("No salary slip found to submit for the above selected criteria OR salary slip already submitted"))

	if not_submitted_ss:
		frappe.msgprint(_("Could not submit any Salary Slip <br>\
			Possible reasons: <br>\
			1. Net pay is less than 0. <br>\
			2. Company Email Address specified in employee master is not valid. <br>"))


def get_salary_slip_list(name, docstatus, as_dict=0):
	payroll_entry = frappe.get_doc('Payroll Entry', name)

	salary_slip_list = frappe.db.sql(
		"select t1.name, t1.salary_structure from `tabSalary Slip` t1 "
		"where t1.docstatus = %s "
		"and t1.start_date >= %s "
		"and t1.end_date <= %s",
		(docstatus, payroll_entry.start_date, payroll_entry.end_date),
		as_dict=as_dict
	)

	return salary_slip_list


@frappe.whitelist()
def payroll_entry_has_created_slips(name):
	response = {}

	draft_salary_slips = get_salary_slip_list(name, docstatus=0)
	submitted_salary_slips = get_salary_slip_list(name, docstatus=1)

	response['draft'] = 1 if draft_salary_slips else 0
	response['submitted'] = 1 if submitted_salary_slips else 0

	return response

