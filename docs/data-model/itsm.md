# ITSM data model

24 tables, from `data/schemas/itsm.sql`, the schema the server builds
each attempt's database from. Written by `mas-optimiser schema`; do not edit.

| table | columns | references |
|---|---|---|
| [`change`](#change) | 23 | `configuration_item`, `organization`, `service_offering`, `service`, `user_group`, `users` |
| [`change_request_mapping`](#change_request_mapping) | 7 | `change`, `incident`, `organization`, `problem` |
| [`child_incident`](#child_incident) | 5 | `incident` |
| [`configuration_item`](#configuration_item) | 10 | `location`, `organization`, `users` |
| [`incident`](#incident) | 41 | `change`, `configuration_item`, `incident_template`, `organization`, `problem`, `service_offering`, `service`, `user_group`, `users` |
| [`incident_affected_cis`](#incident_affected_cis) | 6 | `configuration_item`, `incident`, `organization` |
| [`incident_knowledge`](#incident_knowledge) | 7 | `incident`, `knowledge`, `organization` |
| [`incident_sla`](#incident_sla) | 11 | `incident`, `organization`, `sla_definition` |
| [`incident_template`](#incident_template) | 16 | `configuration_item`, `organization`, `service_offering`, `service`, `users` |
| [`knowledge`](#knowledge) | 11 | `organization`, `users` |
| [`location`](#location) | 11 | `organization` |
| [`notification`](#notification) | 10 | `incident`, `organization` |
| [`organization`](#organization) | 5 |  |
| [`permission`](#permission) | 3 |  |
| [`problem`](#problem) | 22 | `configuration_item`, `incident`, `organization`, `service_offering`, `service`, `user_group`, `users` |
| [`role`](#role) | 2 |  |
| [`role_permission`](#role_permission) | 2 | `permission`, `role` |
| [`service`](#service) | 11 | `organization`, `users` |
| [`service_offering`](#service_offering) | 13 | `organization`, `service`, `users` |
| [`sla_definition`](#sla_definition) | 11 | `organization` |
| [`user_group`](#user_group) | 10 | `organization`, `users` |
| [`user_group_member`](#user_group_member) | 6 | `organization`, `user_group`, `users` |
| [`user_role`](#user_role) | 3 | `organization`, `role`, `users` |
| [`users`](#users) | 13 | `organization`, `role` |

## change

Key: `change_id`.
References: `requested_by` to `users.user_id`; `service` to `service.service_id`; `service_offering` to `service_offering.service_offering_id`; `configuration_item` to `configuration_item.configuration_item_id`; `assigned_to` to `users.user_id`; `assignment_group` to `user_group.group_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `change_id` | VARCHAR(32) |
| `number` | VARCHAR(120) |
| `short_description` | VARCHAR(120) |
| `requested_by` | VARCHAR(32) |
| `service` | VARCHAR(32) |
| `service_offering` | VARCHAR(32) |
| `configuration_item` | VARCHAR(32) |
| `assigned_to` | VARCHAR(32) |
| `assignment_group` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `status` | VARCHAR(30) |
| `category` | VARCHAR(50) |
| `description` | VARCHAR(120) |
| `implementation_plan` | VARCHAR(120) |
| `testing_plan` | VARCHAR(120) |
| `close_notes` | VARCHAR(120) |
| `cab_required` | BOOLEAN |
| `impact` | VARCHAR(20) |
| `priority` | VARCHAR(20) |
| `risk` | VARCHAR(20) |
| `close_code` | VARCHAR(50) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## change_request_mapping

Key: `change_request_mapping_id`.
References: `change_id` to `change.change_id`; `incident_id` to `incident.incident_id`; `problem_id` to `problem.problem_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `change_request_mapping_id` | VARCHAR(32) |
| `change_id` | VARCHAR(32) |
| `incident_id` | VARCHAR(32) |
| `problem_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## child_incident

Key: `child_incident_mapping_id`.
References: `parent_incident` to `incident.incident_id`; `child_incident` to `incident.incident_id`.

| column | type |
|---|---|
| `child_incident_mapping_id` | VARCHAR(32) |
| `parent_incident` | VARCHAR(32) |
| `child_incident` | VARCHAR(32) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## configuration_item

Key: `configuration_item_id`.
References: `owner_id` to `users.user_id`; `location_id` to `location.location_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `configuration_item_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `serial_number` | VARCHAR(60) |
| `owner_id` | VARCHAR(32) |
| `location_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `status` | VARCHAR(11) |
| `cost` | NUMERIC(10, 2) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## incident

Key: `incident_id`.
References: `caller_id` to `users.user_id`; `service` to `service.service_id`; `service_offering` to `service_offering.service_offering_id`; `configuration_item` to `configuration_item.configuration_item_id`; `assigned_to` to `users.user_id`; `assignment_group` to `user_group.group_id`; `resolved_by` to `users.user_id`; `problem` to `problem.problem_id`; `change_request` to `change.change_id`; `caused_by_change` to `change.change_id`; `incident_template` to `incident_template.incident_template_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `incident_id` | VARCHAR(32) |
| `number` | VARCHAR(120) |
| `short_description` | VARCHAR(120) |
| `caller_id` | VARCHAR(32) |
| `service` | VARCHAR(32) |
| `service_offering` | VARCHAR(32) |
| `configuration_item` | VARCHAR(32) |
| `assigned_to` | VARCHAR(32) |
| `assignment_group` | VARCHAR(32) |
| `resolved_by` | VARCHAR(32) |
| `problem` | VARCHAR(32) |
| `change_request` | VARCHAR(32) |
| `caused_by_change` | VARCHAR(32) |
| `incident_template` | VARCHAR(32) |
| `parent_incident` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `channel` | VARCHAR(13) |
| `contact_type` | VARCHAR(7) |
| `status` | VARCHAR(11) |
| `category` | VARCHAR(14) |
| `description` | VARCHAR(120) |
| `worknotes` | VARCHAR(120) |
| `resolution_notes` | VARCHAR(120) |
| `close_notes` | VARCHAR(120) |
| `impact` | VARCHAR(6) |
| `urgency` | VARCHAR(6) |
| `priority` | VARCHAR(8) |
| `resolution_code` | VARCHAR(22) |
| `on_hold_reason` | VARCHAR(16) |
| `resolved` | DATETIME |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |
| `service_display` | VARCHAR(255) |
| `service_offering_display` | VARCHAR(255) |
| `configuration_item_display` | VARCHAR(255) |
| `assigned_to_display` | VARCHAR(255) |
| `assignment_group_display` | VARCHAR(255) |
| `parent_incident_display` | VARCHAR(255) |
| `problem_display` | VARCHAR(255) |
| `change_request_display` | VARCHAR(255) |
| `incident_template_display` | VARCHAR(255) |

## incident_affected_cis

Key: `incident_affected_cis_id`.
References: `configuration_item` to `configuration_item.configuration_item_id`; `incident_id` to `incident.incident_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `incident_affected_cis_id` | VARCHAR(32) |
| `configuration_item` | VARCHAR(32) |
| `incident_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## incident_knowledge

Key: `incident_kb_id`.
References: `incident_id` to `incident.incident_id`; `knowledge_id` to `knowledge.knowledge_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `incident_kb_id` | VARCHAR(32) |
| `incident_id` | VARCHAR(32) |
| `knowledge_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `used_as` | VARCHAR(10) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## incident_sla

Key: `incident_sla_id`.
References: `incident_id` to `incident.incident_id`; `sla_def_id` to `sla_definition.sla_def_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `incident_sla_id` | VARCHAR(32) |
| `incident_id` | VARCHAR(32) |
| `sla_def_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `stage` | VARCHAR(11) |
| `has_breached` | BOOLEAN |
| `start_time` | DATETIME |
| `breach_time` | DATETIME |
| `completed_time` | DATETIME |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## incident_template

Key: `incident_template_id`.
References: `caller_id` to `users.user_id`; `configuration_item` to `configuration_item.configuration_item_id`; `service` to `service.service_id`; `service_offering` to `service_offering.service_offering_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `incident_template_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `active` | BOOLEAN |
| `caller_id` | VARCHAR(32) |
| `channel` | VARCHAR(13) |
| `short_description` | VARCHAR(120) |
| `category` | VARCHAR(14) |
| `impact` | VARCHAR(6) |
| `urgency` | VARCHAR(6) |
| `priority` | VARCHAR(8) |
| `configuration_item` | VARCHAR(32) |
| `service` | VARCHAR(32) |
| `service_offering` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## knowledge

Key: `knowledge_id`.
References: `owner_id` to `users.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `knowledge_id` | VARCHAR(32) |
| `kb_number` | VARCHAR(40) |
| `title` | VARCHAR(120) |
| `short_description` | VARCHAR(255) |
| `body` | TEXT |
| `state` | VARCHAR(9) |
| `visibility` | VARCHAR(8) |
| `owner_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## location

Key: `location_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `location_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `org_id` | VARCHAR(32) |
| `plot_no` | VARCHAR(60) |
| `street` | VARCHAR(60) |
| `city` | VARCHAR(100) |
| `state` | VARCHAR(100) |
| `country` | VARCHAR(100) |
| `active` | BOOLEAN |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## notification

Key: `notification_id`.
References: `incident_id` to `incident.incident_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `notification_id` | VARCHAR(32) |
| `incident_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `email` | VARCHAR(120) |
| `subject` | VARCHAR(255) |
| `message` | VARCHAR(2000) |
| `type` | VARCHAR(17) |
| `status` | VARCHAR(9) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## organization

Key: `org_id`.

| column | type |
|---|---|
| `org_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `active` | BOOLEAN |
| `created_at` | DATETIME |
| `updated_at` | DATETIME |

## permission

Key: `perm_id`.

| column | type |
|---|---|
| `perm_id` | VARCHAR(32) |
| `resource` | VARCHAR(50) |
| `action` | VARCHAR(50) |

## problem

Key: `problem_id`.
References: `opened_by` to `users.user_id`; `service` to `service.service_id`; `service_offering` to `service_offering.service_offering_id`; `configuration_item` to `configuration_item.configuration_item_id`; `assigned_to` to `users.user_id`; `assignment_group` to `user_group.group_id`; `original_task` to `incident.incident_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `problem_id` | VARCHAR(32) |
| `number` | VARCHAR(120) |
| `problem_statement` | VARCHAR |
| `short_description` | VARCHAR(255) |
| `opened_by` | VARCHAR(32) |
| `service` | VARCHAR(32) |
| `service_offering` | VARCHAR(32) |
| `configuration_item` | VARCHAR(32) |
| `assigned_to` | VARCHAR(32) |
| `assignment_group` | VARCHAR(32) |
| `original_task` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `status` | VARCHAR(15) |
| `category` | VARCHAR(8) |
| `worknotes` | TEXT |
| `workaround` | TEXT |
| `fix_notes` | TEXT |
| `impact` | VARCHAR(6) |
| `urgency` | VARCHAR(6) |
| `priority` | VARCHAR(8) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## role

Key: `role_id`.

| column | type |
|---|---|
| `role_id` | VARCHAR(32) |
| `name` | VARCHAR(8) |

## role_permission

Key: `role_id`, `perm_id`.
References: `role_id` to `role.role_id`; `perm_id` to `permission.perm_id`.

| column | type |
|---|---|
| `role_id` | VARCHAR(32) |
| `perm_id` | VARCHAR(32) |

## service

Key: `service_id`.
References: `owned_by` to `users.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `service_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `owned_by` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `used_for` | VARCHAR(11) |
| `status` | VARCHAR(18) |
| `service_classification` | VARCHAR(21) |
| `business_criticality` | VARCHAR(17) |
| `description` | VARCHAR(500) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## service_offering

Key: `service_offering_id`.
References: `owned_by` to `users.user_id`; `business_service` to `service.service_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `service_offering_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `short_description` | VARCHAR(120) |
| `owned_by` | VARCHAR(32) |
| `business_service` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `used_for` | VARCHAR(11) |
| `status` | VARCHAR(18) |
| `service_classification` | VARCHAR(21) |
| `business_criticality` | VARCHAR(17) |
| `description` | VARCHAR(500) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## sla_definition

Key: `sla_def_id`.
References: `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `sla_def_id` | VARCHAR(32) |
| `name` | VARCHAR(120) |
| `metric` | VARCHAR(10) |
| `target_mins` | INTEGER |
| `pause_on_pending` | BOOLEAN |
| `applies_to_priority` | VARCHAR(8) |
| `active` | BOOLEAN |
| `schedule` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## user_group

Key: `group_id`.
References: `manager_id` to `users.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `group_id` | VARCHAR(32) |
| `name` | VARCHAR(80) |
| `type` | VARCHAR(27) |
| `active` | BOOLEAN |
| `email` | VARCHAR(120) |
| `description` | VARCHAR(255) |
| `manager_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## user_group_member

Key: `member_id`.
References: `group_id` to `user_group.group_id`; `user_id` to `users.user_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `member_id` | VARCHAR(32) |
| `group_id` | VARCHAR(32) |
| `user_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |

## user_role

Key: `user_id`, `role_id`, `org_id`.
References: `user_id` to `users.user_id`; `role_id` to `role.role_id`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `user_id` | VARCHAR(32) |
| `role_id` | VARCHAR(32) |
| `org_id` | VARCHAR(32) |

## users

Key: `user_id`.
References: `role` to `role.name`; `org_id` to `organization.org_id`.

| column | type |
|---|---|
| `user_id` | VARCHAR(32) |
| `user_name` | VARCHAR(100) |
| `first_name` | VARCHAR(60) |
| `last_name` | VARCHAR(60) |
| `email` | VARCHAR(120) |
| `phone` | VARCHAR(30) |
| `role` | VARCHAR(50) |
| `active` | BOOLEAN |
| `static_token` | VARCHAR(255) |
| `org_id` | VARCHAR(32) |
| `location_id` | VARCHAR(32) |
| `created_on` | DATETIME |
| `updated_on` | DATETIME |
