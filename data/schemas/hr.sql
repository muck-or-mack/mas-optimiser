CREATE TABLE assignment_rules (
	assignment_rule_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	based_on_skills JSON, 
	based_on_country VARCHAR(2), 
	based_on_priority VARCHAR(8) NOT NULL, 
	active BOOLEAN NOT NULL, 
	user_group INTEGER, 
	users JSON, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (assignment_rule_id), 
	CONSTRAINT uq_assignment_rule_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(user_group) REFERENCES user_group (group_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE checklist (
	checklist_id INTEGER NOT NULL, 
	checklist_item_description TEXT, 
	checked BOOLEAN NOT NULL, 
	case_task_id INTEGER, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (checklist_id), 
	FOREIGN KEY(case_task_id) REFERENCES hr_case_task (hr_case_task_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE escalation_rule (
	escalation_rule_id INTEGER NOT NULL, 
	escalate_from INTEGER, 
	escalate_to INTEGER, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (escalation_rule_id), 
	FOREIGN KEY(escalate_from) REFERENCES user_group (group_id), 
	FOREIGN KEY(escalate_to) REFERENCES user_group (group_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_case (
	hr_case_id INTEGER NOT NULL, 
	number VARCHAR(120) NOT NULL, 
	status VARCHAR(19) NOT NULL, 
	hr_service INTEGER, 
	opened_for INTEGER, 
	opened_by INTEGER, 
	priority VARCHAR(8) NOT NULL, 
	source VARCHAR(13), 
	assigned_to INTEGER, 
	assignment_group INTEGER, 
	short_description VARCHAR(120) NOT NULL, 
	worknotes VARCHAR(120), 
	skills JSON, 
	approval_criteria INTEGER, 
	coe_type VARCHAR(24) NOT NULL, 
	approver INTEGER, 
	request_status VARCHAR(9), 
	account_number VARCHAR(120), 
	account_type VARCHAR(8), 
	created_at DATETIME, 
	updated_at DATETIME, 
	org_id INTEGER NOT NULL, 
	PRIMARY KEY (hr_case_id), 
	CONSTRAINT uq_case_org_number UNIQUE (org_id, number), 
	FOREIGN KEY(hr_service) REFERENCES hr_service (hr_service_id), 
	FOREIGN KEY(opened_for) REFERENCES hr_profile (hr_profile_id), 
	FOREIGN KEY(opened_by) REFERENCES hr_profile (hr_profile_id), 
	FOREIGN KEY(assigned_to) REFERENCES user (user_id), 
	FOREIGN KEY(assignment_group) REFERENCES user_group (group_id), 
	FOREIGN KEY(approval_criteria) REFERENCES hr_criteria (hr_criteria_id), 
	FOREIGN KEY(approver) REFERENCES user (user_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_case_knowledge (
	hr_case_kb_id INTEGER NOT NULL, 
	hr_case_id INTEGER NOT NULL, 
	knowledge_id INTEGER NOT NULL, 
	used_as VARCHAR(10) NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (hr_case_kb_id), 
	CONSTRAINT uq_case_knowledge_org UNIQUE (org_id, hr_case_id, knowledge_id), 
	FOREIGN KEY(hr_case_id) REFERENCES hr_case (hr_case_id), 
	FOREIGN KEY(knowledge_id) REFERENCES knowledge (knowledge_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_case_task (
	hr_case_task_id INTEGER NOT NULL, 
	number VARCHAR(120) NOT NULL, 
	status VARCHAR(19) NOT NULL, 
	assigned_to INTEGER, 
	assignment_group INTEGER, 
	short_description VARCHAR(120) NOT NULL, 
	task_type VARCHAR(18), 
	hr_service INTEGER, 
	external_url TEXT, 
	worknotes VARCHAR(120), 
	active BOOLEAN NOT NULL, 
	parent_case INTEGER, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (hr_case_task_id), 
	CONSTRAINT uq_task_org_number UNIQUE (org_id, number), 
	FOREIGN KEY(assigned_to) REFERENCES user (user_id), 
	FOREIGN KEY(assignment_group) REFERENCES user_group (group_id), 
	FOREIGN KEY(hr_service) REFERENCES hr_service (hr_service_id), 
	FOREIGN KEY(parent_case) REFERENCES hr_case (hr_case_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_criteria (
	hr_criteria_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	type VARCHAR(15) NOT NULL, 
	active BOOLEAN NOT NULL, 
	user_group INTEGER, 
	users JSON, 
	created_at DATETIME, 
	updated_at DATETIME, 
	org_id INTEGER NOT NULL, 
	PRIMARY KEY (hr_criteria_id), 
	CONSTRAINT uq_hr_criteria_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(user_group) REFERENCES user_group (group_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_profile (
	hr_profile_id INTEGER NOT NULL, 
	number VARCHAR(60) NOT NULL, 
	gender VARCHAR(6), 
	user_id INTEGER NOT NULL, 
	personal_email VARCHAR(120) NOT NULL, 
	marital_status VARCHAR(8), 
	type VARCHAR(10), 
	dob DATETIME, 
	nationality VARCHAR(2), 
	employment_start_date DATETIME, 
	employment_end_date DATETIME, 
	manager INTEGER, 
	location_type VARCHAR(12), 
	national_tax_id VARCHAR(60) NOT NULL, 
	position VARCHAR(17), 
	created_at DATETIME, 
	updated_at DATETIME, 
	org_id INTEGER NOT NULL, 
	PRIMARY KEY (hr_profile_id), 
	CONSTRAINT uq_profile_org_number UNIQUE (org_id, number), 
	CONSTRAINT uq_profile_org_user UNIQUE (org_id, user_id), 
	CONSTRAINT uq_profile_org_email UNIQUE (org_id, personal_email), 
	CONSTRAINT uq_profile_org_tax_id UNIQUE (org_id, national_tax_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(manager) REFERENCES hr_profile (hr_profile_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE hr_service (
	hr_service_id INTEGER NOT NULL, 
	service_name VARCHAR(60) NOT NULL, 
	fulfillment_type VARCHAR(16) NOT NULL, 
	topic_detail INTEGER, 
	service_template INTEGER, 
	hr_criteria INTEGER, 
	active BOOLEAN NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (hr_service_id), 
	CONSTRAINT uq_service_org_name UNIQUE (org_id, service_name), 
	FOREIGN KEY(topic_detail) REFERENCES topic_detail (topic_detail_id), 
	FOREIGN KEY(service_template) REFERENCES service_template (service_template_id), 
	FOREIGN KEY(hr_criteria) REFERENCES hr_criteria (hr_criteria_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE knowledge (
	knowledge_id INTEGER NOT NULL, 
	kb_number VARCHAR(40) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	body TEXT, 
	state VARCHAR(9) NOT NULL, 
	visibility VARCHAR(8) NOT NULL, 
	owner_id INTEGER, 
	created_at DATETIME, 
	updated_at DATETIME, 
	org_id INTEGER NOT NULL, 
	PRIMARY KEY (knowledge_id), 
	CONSTRAINT uq_kb_org_number UNIQUE (org_id, kb_number), 
	FOREIGN KEY(owner_id) REFERENCES user (user_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE notification (
	notification_id INTEGER NOT NULL, 
	hr_case_id INTEGER NOT NULL, 
	email VARCHAR(120) NOT NULL, 
	type VARCHAR(17) NOT NULL, 
	status VARCHAR(19), 
	created_at DATETIME, 
	org_id INTEGER NOT NULL, 
	updated_at DATETIME, 
	PRIMARY KEY (notification_id), 
	FOREIGN KEY(hr_case_id) REFERENCES hr_case (hr_case_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE organization (
	org_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (org_id), 
	UNIQUE (name)
);

CREATE TABLE permission (
	perm_id INTEGER NOT NULL, 
	resource VARCHAR(17) NOT NULL, 
	action VARCHAR(4) NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (perm_id), 
	CONSTRAINT uq_perm_resource_action UNIQUE (resource, action)
);

CREATE TABLE role (
	role_id INTEGER NOT NULL, 
	name VARCHAR(8) NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (role_id), 
	UNIQUE (name)
);

CREATE TABLE role_permission (
	role_id INTEGER NOT NULL, 
	perm_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (role_id, perm_id), 
	FOREIGN KEY(role_id) REFERENCES role (role_id), 
	FOREIGN KEY(perm_id) REFERENCES permission (perm_id)
);

CREATE TABLE service_template (
	service_template_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	change_request_values JSON NOT NULL, 
	active BOOLEAN NOT NULL, 
	coe_type VARCHAR(24) NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (service_template_id), 
	CONSTRAINT uq_service_template_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE skill (
	skill_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (skill_id), 
	CONSTRAINT uq_skill_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE survey (
	survey_id INTEGER NOT NULL, 
	question_1 TEXT, 
	question_2 TEXT, 
	question_3 TEXT, 
	question_4 TEXT, 
	question_5 TEXT, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (survey_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE survey_instance (
	survey_instance_id INTEGER NOT NULL, 
	survey_id INTEGER, 
	case_task_id INTEGER, 
	assigned_to INTEGER, 
	org_id INTEGER NOT NULL, 
	answer_1 TEXT, 
	answer_2 TEXT, 
	answer_3 TEXT, 
	answer_4 TEXT, 
	answer_5 TEXT, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (survey_instance_id), 
	CONSTRAINT uq_survey_instance_org UNIQUE (org_id, survey_id, case_task_id, assigned_to), 
	FOREIGN KEY(survey_id) REFERENCES survey (survey_id), 
	FOREIGN KEY(case_task_id) REFERENCES hr_case_task (hr_case_task_id), 
	FOREIGN KEY(assigned_to) REFERENCES user (user_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE topic_category (
	topic_category_id INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	coe_type VARCHAR(24) NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (topic_category_id), 
	CONSTRAINT uq_topic_cat_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE topic_detail (
	topic_detail_id INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	topic_category INTEGER, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (topic_detail_id), 
	CONSTRAINT uq_topic_detail_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(topic_category) REFERENCES topic_category (topic_category_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE user (
	user_id INTEGER NOT NULL, 
	first_name VARCHAR(60) NOT NULL, 
	last_name VARCHAR(60) NOT NULL, 
	email VARCHAR(120) NOT NULL, 
	phone VARCHAR(30), 
	role VARCHAR(50) NOT NULL, 
	active BOOLEAN NOT NULL, 
	org_id INTEGER NOT NULL, 
	static_token VARCHAR(255), 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (user_id), 
	CONSTRAINT uq_user_org_email UNIQUE (org_id, email), 
	FOREIGN KEY(role) REFERENCES role (name), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE user_group (
	group_id INTEGER NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	type VARCHAR(27) NOT NULL, 
	active BOOLEAN NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (group_id), 
	CONSTRAINT uq_group_org_name UNIQUE (org_id, name), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE user_group_member (
	member_id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (member_id), 
	CONSTRAINT uq_group_member_org UNIQUE (org_id, group_id, user_id), 
	FOREIGN KEY(group_id) REFERENCES user_group (group_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE user_role (
	user_id INTEGER NOT NULL, 
	role_id INTEGER NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (user_id, role_id, org_id), 
	CONSTRAINT uq_user_role_org UNIQUE (user_id, role_id, org_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(role_id) REFERENCES role (role_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);

CREATE TABLE user_skill (
	user_skill_id INTEGER NOT NULL, 
	skill INTEGER NOT NULL, 
	user INTEGER NOT NULL, 
	org_id INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (user_skill_id), 
	CONSTRAINT uq_user_skill_org UNIQUE (org_id, skill, user), 
	FOREIGN KEY(skill) REFERENCES skill (skill_id), 
	FOREIGN KEY(user) REFERENCES user (user_id), 
	FOREIGN KEY(org_id) REFERENCES organization (org_id)
);
