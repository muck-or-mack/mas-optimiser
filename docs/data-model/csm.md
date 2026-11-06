# CSM data model

17 tables, from `data/schemas/csm.sql`, the schema the server builds
each attempt's database from. Written by `mas-optimiser schema`; do not edit.

| table | columns | references |
|---|---|---|
| [`account`](#account) | 7 |  |
| [`case_knowledge`](#case_knowledge) | 4 | `customer_case`, `knowledge` |
| [`case_sla`](#case_sla) | 8 | `customer_case`, `sla_definition` |
| [`contact`](#contact) | 7 | `account` |
| [`contract`](#contract) | 9 | `account` |
| [`customer_case`](#customer_case) | 18 | `account`, `contact`, `installed_product`, `product`, `user_group` |
| [`entitlement`](#entitlement) | 10 | `account`, `contract`, `product` |
| [`installed_product`](#installed_product) | 9 | `account`, `location`, `product` |
| [`interaction`](#interaction) | 10 | `account`, `contact`, `customer_case` |
| [`knowledge`](#knowledge) | 10 | `product` |
| [`location`](#location) | 9 |  |
| [`notification`](#notification) | 7 | `customer_case`, `knowledge` |
| [`product`](#product) | 7 |  |
| [`sla_definition`](#sla_definition) | 9 |  |
| [`user`](#user) | 10 | `location` |
| [`user_group`](#user_group) | 6 |  |
| [`user_group_member`](#user_group_member) | 4 | `user_group` |

## account

Key: `account_id`.

| column | type |
|---|---|
| `account_id` | INTEGER |
| `name` | VARCHAR(120) |
| `account_type` | TEXT |
| `email_domain` | VARCHAR(80) |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## case_knowledge

Key: `case_kb_id`.
References: `case_id` to `customer_case.case_id`; `knowledge_id` to `knowledge.knowledge_id`.

| column | type |
|---|---|
| `case_kb_id` | INTEGER |
| `case_id` | INTEGER |
| `knowledge_id` | INTEGER |
| `used_as` | TEXT |

## case_sla

Key: `case_sla_id`.
References: `case_id` to `customer_case.case_id`; `sla_def_id` to `sla_definition.sla_def_id`.

| column | type |
|---|---|
| `case_sla_id` | INTEGER |
| `case_id` | INTEGER |
| `sla_def_id` | INTEGER |
| `stage` | TEXT |
| `start_time` | DATETIME |
| `breach_time` | DATETIME |
| `has_breached` | INTEGER |
| `completed_time` | DATETIME |

## contact

Key: `contact_id`.
References: `account_id` to `account.account_id`.

| column | type |
|---|---|
| `contact_id` | INTEGER |
| `account_id` | INTEGER |
| `portal_user_id` | INTEGER |
| `active` | INTEGER |
| `is_primary` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## contract

Key: `contract_id`.
References: `account_id` to `account.account_id`.

| column | type |
|---|---|
| `contract_id` | INTEGER |
| `account_id` | INTEGER |
| `contract_type` | TEXT |
| `status` | TEXT |
| `contract_price` | INTEGER |
| `start_date` | DATE |
| `end_date` | DATE |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## customer_case

Key: `case_id`.
References: `account_id` to `account.account_id`; `contact_id` to `contact.contact_id`; `product_id` to `product.product_id`; `installed_product_id` to `installed_product.installed_product_id`; `assignment_group_id` to `user_group.group_id`.

| column | type |
|---|---|
| `case_id` | INTEGER |
| `number` | VARCHAR(40) |
| `account_id` | INTEGER |
| `contact_id` | INTEGER |
| `product_id` | INTEGER |
| `installed_product_id` | INTEGER |
| `channel` | TEXT |
| `priority` | TEXT |
| `state` | TEXT |
| `short_description` | VARCHAR(120) |
| `assignment_group_id` | INTEGER |
| `assigned_to` | INTEGER |
| `escalation` | INTEGER |
| `escalation_reason` | TEXT |
| `reopen_count` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |
| `closed_on` | DATETIME |

## entitlement

Key: `entitlement_id`.
References: `account_id` to `account.account_id`; `product_id` to `product.product_id`; `contract_id` to `contract.contract_id`.

| column | type |
|---|---|
| `entitlement_id` | INTEGER |
| `account_id` | INTEGER |
| `product_id` | INTEGER |
| `contract_id` | INTEGER |
| `support_level` | TEXT |
| `coverage_hours` | TEXT |
| `max_cases_per_month` | INTEGER |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## installed_product

Key: `installed_product_id`.
References: `account_id` to `account.account_id`; `product_id` to `product.product_id`; `location_id` to `location.location_id`.

| column | type |
|---|---|
| `installed_product_id` | INTEGER |
| `account_id` | INTEGER |
| `product_id` | INTEGER |
| `location_id` | INTEGER |
| `serial_number` | VARCHAR(60) |
| `status` | TEXT |
| `warranty_end` | DATE |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## interaction

Key: `interaction_id`.
References: `account_id` to `account.account_id`; `contact_id` to `contact.contact_id`; `case_id` to `customer_case.case_id`.

| column | type |
|---|---|
| `interaction_id` | INTEGER |
| `channel` | TEXT |
| `account_id` | INTEGER |
| `contact_id` | INTEGER |
| `case_id` | INTEGER |
| `interacted_user` | INTEGER |
| `status` | TEXT |
| `started_at` | DATETIME |
| `ended_at` | DATETIME |
| `sys_created_on` | DATETIME |

## knowledge

Key: `knowledge_id`.
References: `product_id` to `product.product_id`.

| column | type |
|---|---|
| `knowledge_id` | INTEGER |
| `kb_number` | VARCHAR(40) |
| `title` | VARCHAR(120) |
| `body` | TEXT |
| `state` | TEXT |
| `visibility` | TEXT |
| `product_id` | INTEGER |
| `owner_id` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## location

Key: `location_id`.

| column | type |
|---|---|
| `location_id` | INTEGER |
| `name` | VARCHAR(120) |
| `plot_no` | VARCHAR(60) |
| `street` | VARCHAR(60) |
| `city` | TEXT |
| `country` | TEXT |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## notification

Key: `notification_id`.
References: `case_id` to `customer_case.case_id`; `knowledge_id` to `knowledge.knowledge_id`.

| column | type |
|---|---|
| `notification_id` | INTEGER |
| `case_id` | INTEGER |
| `knowledge_id` | INTEGER |
| `email` | VARCHAR(120) |
| `type` | TEXT |
| `status` | TEXT |
| `sys_created_on` | DATETIME |

## product

Key: `product_id`.

| column | type |
|---|---|
| `product_id` | INTEGER |
| `name` | VARCHAR(120) |
| `category` | TEXT |
| `product_price` | INTEGER |
| `lifecycle_state` | TEXT |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## sla_definition

Key: `sla_def_id`.

| column | type |
|---|---|
| `sla_def_id` | INTEGER |
| `name` | VARCHAR(120) |
| `metric` | TEXT |
| `target_mins` | INTEGER |
| `pause_on_pending` | INTEGER |
| `applies_to_priority` | TEXT |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## user

Key: `user_id`.
References: `location_id` to `location.location_id`.

| column | type |
|---|---|
| `user_id` | INTEGER |
| `first_name` | VARCHAR(60) |
| `last_name` | VARCHAR(60) |
| `email` | VARCHAR(120) |
| `phone` | VARCHAR(30) |
| `role` | TEXT |
| `location_id` | INTEGER |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## user_group

Key: `group_id`.

| column | type |
|---|---|
| `group_id` | INTEGER |
| `name` | VARCHAR(80) |
| `type` | TEXT |
| `active` | INTEGER |
| `sys_created_on` | DATETIME |
| `sys_updated_on` | DATETIME |

## user_group_member

Key: `member_id`.
References: `group_id` to `user_group.group_id`.

| column | type |
|---|---|
| `member_id` | INTEGER |
| `group_id` | INTEGER |
| `user_id` | INTEGER |
| `sys_created_on` | DATETIME |
