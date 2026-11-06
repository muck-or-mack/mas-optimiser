# HR data model

26 tables, from `data/schemas/hr.sql`, the schema the server builds
each attempt's database from. Written by `mas-optimiser schema`; do not edit.

| table | columns | references |
|---|---|---|
| [`assignment_rules`](#assignment_rules) | 11 | `organization`, `user_group` |
| [`checklist`](#checklist) | 7 | `hr_case_task`, `organization` |
| [`escalation_rule`](#escalation_rule) | 6 | `organization`, `user_group` |
| [`hr_case`](#hr_case) | 22 | `hr_criteria`, `hr_profile`, `hr_service`, `organization`, `user_group`, `user` |
| [`hr_case_knowledge`](#hr_case_knowledge) | 7 | `hr_case`, `knowledge`, `organization` |
| [`hr_case_task`](#hr_case_task) | 15 | `hr_case`, `hr_service`, `organization`, `user_group`, `user` |
| [`hr_criteria`](#hr_criteria) | 9 | `organization`, `user_group` |
| [`hr_profile`](#hr_profile) | 18 | `hr_profile`, `organization`, `user` |
| [`hr_service`](#hr_service) | 10 | `hr_criteria`, `organization`, `service_template`, `topic_detail` |
| [`knowledge`](#knowledge) | 10 | `organization`, `user` |
| [`notification`](#notification) | 8 | `hr_case`, `organization` |
| [`organization`](#organization) | 5 |  |
| [`permission`](#permission) | 5 |  |
| [`role`](#role) | 4 |  |
| [`role_permission`](#role_permission) | 4 | `permission`, `role` |
| [`service_template`](#service_template) | 8 | `organization` |
| [`skill`](#skill) | 6 | `organization` |
| [`survey`](#survey) | 9 | `organization` |
| [`survey_instance`](#survey_instance) | 12 | `hr_case_task`, `organization`, `survey`, `user` |
| [`topic_category`](#topic_category) | 7 | `organization` |
| [`topic_detail`](#topic_detail) | 7 | `organization`, `topic_category` |
| [`user`](#user) | 11 | `organization`, `role` |
| [`user_group`](#user_group) | 7 | `organization` |
| [`user_group_member`](#user_group_member) | 6 | `organization`, `user_group`, `user` |
| [`user_role`](#user_role) | 5 | `organization`, `role`, `user` |
| [`user_skill`](#user_skill) | 6 | `organization`, `skill`, `user` |

## assignment_rules

Key: `assignment_rule_id`.
References: `user_group` to `user_group.group_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `assignment_rule_id` | INTEGER |
| `name` | VARCHAR(120) |
| `based_on_skills` | JSON |
| `based_on_country` | VARCHAR(2) |
| `based_on_priority` | VARCHAR(8) |
| `active` | BOOLEAN |
| `user_group` | INTEGER |
| `users` | JSON |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## checklist

Key: `checklist_id`.
References: `case_task_id` to `hr_case_task.hr_case_task_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `checklist_id` | INTEGER |
| `checklist_item_description` | TEXT |
| `checked` | BOOLEAN |
| `case_task_id` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## escalation_rule

Key: `escalation_rule_id`.
References: `escalate_from` to `user_group.group_id`; `escalate_to` to `user_group.group_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `escalation_rule_id` | INTEGER |
| `escalate_from` | INTEGER |
| `escalate_to` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## hr_case

Key: `hr_case_id`.
References: `hr_service` to `hr_service.hr_service_id`; `opened_for` to `hr_profile.hr_profile_id`; `opened_by` to `hr_profile.hr_profile_id`; `assigned_to` to `user.user_id`; `assignment_group` to `user_group.group_id`; `approval_criteria` to `hr_criteria.hr_criteria_id`; `approver` to `user.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_case_id` | INTEGER |
| `number` | VARCHAR(120) |
| `status` | VARCHAR(19) |
| `hr_service` | INTEGER |
| `opened_for` | INTEGER |
| `opened_by` | INTEGER |
| `priority` | VARCHAR(8) |
| `source` | VARCHAR(13) |
| `assigned_to` | INTEGER |
| `assignment_group` | INTEGER |
| `short_description` | VARCHAR(120) |
| `worknotes` | VARCHAR(120) |
| `skills` | JSON |
| `approval_criteria` | INTEGER |
| `coe_type` | VARCHAR(24) |
| `approver` | INTEGER |
| `request_status` | VARCHAR(9) |
| `account_number` | VARCHAR(120) |
| `account_type` | VARCHAR(8) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
| `org_id` | INTEGER |

## hr_case_knowledge

Key: `hr_case_kb_id`.
References: `hr_case_id` to `hr_case.hr_case_id`; `knowledge_id` to `knowledge.knowledge_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_case_kb_id` | INTEGER |
| `hr_case_id` | INTEGER |
| `knowledge_id` | INTEGER |
| `used_as` | VARCHAR(10) |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## hr_case_task

Key: `hr_case_task_id`.
References: `assigned_to` to `user.user_id`; `assignment_group` to `user_group.group_id`; `hr_service` to `hr_service.hr_service_id`; `parent_case` to `hr_case.hr_case_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_case_task_id` | INTEGER |
| `number` | VARCHAR(120) |
| `status` | VARCHAR(19) |
| `assigned_to` | INTEGER |
| `assignment_group` | INTEGER |
| `short_description` | VARCHAR(120) |
| `task_type` | VARCHAR(18) |
| `hr_service` | INTEGER |
| `external_url` | TEXT |
| `worknotes` | VARCHAR(120) |
| `active` | BOOLEAN |
| `parent_case` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## hr_criteria

Key: `hr_criteria_id`.
References: `user_group` to `user_group.group_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_criteria_id` | INTEGER |
| `name` | VARCHAR(120) |
| `type` | VARCHAR(15) |
| `active` | BOOLEAN |
| `user_group` | INTEGER |
| `users` | JSON |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
| `org_id` | INTEGER |

## hr_profile

Key: `hr_profile_id`.
References: `user_id` to `user.user_id`; `manager` to `hr_profile.hr_profile_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_profile_id` | INTEGER |
| `number` | VARCHAR(60) |
| `gender` | VARCHAR(6) |
| `user_id` | INTEGER |
| `personal_email` | VARCHAR(120) |
| `marital_status` | VARCHAR(8) |
| `type` | VARCHAR(10) |
| `dob` | DATETIME |
| `nationality` | VARCHAR(2) |
| `employment_start_date` | DATETIME |
| `employment_end_date` | DATETIME |
| `manager` | INTEGER |
| `location_type` | VARCHAR(12) |
| `national_tax_id` | VARCHAR(60) |
| `position` | VARCHAR(17) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
| `org_id` | INTEGER |

## hr_service

Key: `hr_service_id`.
References: `topic_detail` to `topic_detail.topic_detail_id`; `service_template` to `service_template.service_template_id`; `hr_criteria` to `hr_criteria.hr_criteria_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `hr_service_id` | INTEGER |
| `service_name` | VARCHAR(60) |
| `fulfillment_type` | VARCHAR(16) |
| `topic_detail` | INTEGER |
| `service_template` | INTEGER |
| `hr_criteria` | INTEGER |
| `active` | BOOLEAN |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## knowledge

Key: `knowledge_id`.
References: `owner_id` to `user.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `knowledge_id` | INTEGER |
| `kb_number` | VARCHAR(40) |
| `title` | VARCHAR(120) |
| `body` | TEXT |
| `state` | VARCHAR(9) |
| `visibility` | VARCHAR(8) |
| `owner_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
| `org_id` | INTEGER |

## notification

Key: `notification_id`.
References: `hr_case_id` to `hr_case.hr_case_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `notification_id` | INTEGER |
| `hr_case_id` | INTEGER |
| `email` | VARCHAR(120) |
| `type` | VARCHAR(17) |
| `status` | VARCHAR(19) |
| `created_at` | DATETIME |
| `org_id` | INTEGER |
| `updated_at` | DATETIME |

## organization

Key: `org_id`.

| column | type |
|---|---|
| `org_id` | INTEGER |
| `name` | VARCHAR(120) |
| `active` | BOOLEAN |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## permission

Key: `perm_id`.

| column | type |
|---|---|
| `perm_id` | INTEGER |
| `resource` | VARCHAR(17) |
| `action` | VARCHAR(4) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## role

Key: `role_id`.

| column | type |
|---|---|
| `role_id` | INTEGER |
| `name` | VARCHAR(8) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## role_permission

Key: `role_id`, `perm_id`.
References: `role_id` to `role.role_id`; `perm_id` to `permission.perm_id`.

| column | type |
|---|---|
| `role_id` | INTEGER |
| `perm_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## service_template

Key: `service_template_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `service_template_id` | INTEGER |
| `name` | VARCHAR(120) |
| `change_request_values` | JSON |
| `active` | BOOLEAN |
| `coe_type` | VARCHAR(24) |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## skill

Key: `skill_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `skill_id` | INTEGER |
| `name` | VARCHAR(120) |
| `active` | BOOLEAN |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## survey

Key: `survey_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `survey_id` | INTEGER |
| `question_1` | TEXT |
| `question_2` | TEXT |
| `question_3` | TEXT |
| `question_4` | TEXT |
| `question_5` | TEXT |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## survey_instance

Key: `survey_instance_id`.
References: `survey_id` to `survey.survey_id`; `case_task_id` to `hr_case_task.hr_case_task_id`; `assigned_to` to `user.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `survey_instance_id` | INTEGER |
| `survey_id` | INTEGER |
| `case_task_id` | INTEGER |
| `assigned_to` | INTEGER |
| `org_id` | INTEGER |
| `answer_1` | TEXT |
| `answer_2` | TEXT |
| `answer_3` | TEXT |
| `answer_4` | TEXT |
| `answer_5` | TEXT |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## topic_category

Key: `topic_category_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `topic_category_id` | INTEGER |
| `active` | BOOLEAN |
| `name` | VARCHAR(60) |
| `coe_type` | VARCHAR(24) |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## topic_detail

Key: `topic_detail_id`.
References: `topic_category` to `topic_category.topic_category_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `topic_detail_id` | INTEGER |
| `active` | BOOLEAN |
| `name` | VARCHAR(60) |
| `topic_category` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## user

Key: `user_id`.
References: `role` to `role.name`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `user_id` | INTEGER |
| `first_name` | VARCHAR(60) |
| `last_name` | VARCHAR(60) |
| `email` | VARCHAR(120) |
| `phone` | VARCHAR(30) |
| `role` | VARCHAR(50) |
| `active` | BOOLEAN |
| `org_id` | INTEGER |
| `static_token` | VARCHAR(255) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## user_group

Key: `group_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `group_id` | INTEGER |
| `name` | VARCHAR(80) |
| `type` | VARCHAR(27) |
| `active` | BOOLEAN |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## user_group_member

Key: `member_id`.
References: `group_id` to `user_group.group_id`; `user_id` to `user.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `member_id` | INTEGER |
| `group_id` | INTEGER |
| `user_id` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## user_role

Key: `user_id`, `role_id`, `org_id`.
References: `user_id` to `user.user_id`; `role_id` to `role.role_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `user_id` | INTEGER |
| `role_id` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## user_skill

Key: `user_skill_id`.
References: `skill` to `skill.skill_id`; `user` to `user.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `user_skill_id` | INTEGER |
| `skill` | INTEGER |
| `user` | INTEGER |
| `org_id` | INTEGER |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
