-- Minimal schema for the DB-backed integration tests under tests/conversation/
-- (validation e2e, see tests/conversation/test_validation_e2e.py once added).
--
-- Mirrors only the tables this service's own code actually touches or has a
-- real FK dependency on, taken from the `database` repo's Flyway migrations
-- (V1__baseline.sql for auth.user_account/form.task/form.task_form/
-- form.question, V9__line_chat_notify_models.sql for auth.line_identity/
-- chat.conversation/chat.conversation_answer). Copied rather than pulled in
-- live -- `database` is a separate git repo with its own history, same
-- reasoning mobile-backend's testSchemaDDL and web-backend's ci-schema.sql
-- already use. If those migrations change shape, this needs a manual update
-- to match.
--
-- form.section and form.field_validation_rule are deliberately NOT included:
-- this service never queries either directly (ADR 0001's seam) -- form
-- structure and validation rules arrive over HTTP from Kotlin's
-- GET /forms/{formId}, which the tests mock instead of hitting a real DB.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA auth;
CREATE SCHEMA form;
CREATE SCHEMA chat;
CREATE SCHEMA notify;
CREATE SCHEMA agriculture;
CREATE SCHEMA processing;
CREATE SCHEMA research;

CREATE TABLE auth.user_account (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying,
    password_hash character varying,
    is_password_reset boolean DEFAULT false,
    is_requires_password_reset boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_user_account PRIMARY KEY (user_id),
    CONSTRAINT uq_user_account_username UNIQUE (username)
);

CREATE TABLE auth.role (
    role_id uuid DEFAULT gen_random_uuid() NOT NULL,
    role_name character varying NOT NULL,
    CONSTRAINT pk_role PRIMARY KEY (role_id)
);

CREATE TABLE auth.user_role (
    user_id uuid NOT NULL,
    role_id uuid NOT NULL,
    CONSTRAINT pk_user_role PRIMARY KEY (user_id, role_id),
    CONSTRAINT fk_user_role_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id),
    CONSTRAINT fk_user_role_role FOREIGN KEY (role_id) REFERENCES auth.role (role_id)
);

-- src/auth: permissions (V1 baseline), refresh tokens (V19), SSO jti (V27).
CREATE TABLE auth.permission (
    permission_id uuid DEFAULT gen_random_uuid() NOT NULL,
    permission_key character varying NOT NULL,
    description character varying,
    CONSTRAINT pk_permission PRIMARY KEY (permission_id),
    CONSTRAINT uq_permission_key UNIQUE (permission_key)
);

CREATE TABLE auth.role_permission (
    role_id uuid NOT NULL,
    permission_id uuid NOT NULL,
    CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission_id),
    CONSTRAINT fk_role_permission_role FOREIGN KEY (role_id) REFERENCES auth.role (role_id),
    CONSTRAINT fk_role_permission_permission FOREIGN KEY (permission_id) REFERENCES auth.permission (permission_id)
);

CREATE TABLE auth.refresh_token (
    refresh_token_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    token_hash character varying NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used_at timestamp without time zone,
    revoked_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_refresh_token PRIMARY KEY (refresh_token_id),
    CONSTRAINT fk_refresh_token_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id),
    CONSTRAINT uq_refresh_token_hash UNIQUE (token_hash)
);

CREATE TABLE auth.sso_used_token (
    jti uuid NOT NULL,
    used_at timestamp without time zone NOT NULL DEFAULT now(),
    expires_at timestamp without time zone NOT NULL,
    CONSTRAINT pk_sso_used_token PRIMARY KEY (jti)
);

-- Role profiles: one row per user per role (multi-role). FKs to ref.* geo
-- tables are left out -- this schema has no ref.* tables.
CREATE TABLE agriculture.farmer (
    user_id uuid NOT NULL,
    first_name character varying,
    last_name character varying,
    nickname character varying,
    birth_date date,
    nationality character varying,
    ethnicity character varying,
    religion character varying,
    id_card_number character varying,
    address_detail character varying,
    subdistrict_id uuid NOT NULL,
    district_id uuid NOT NULL,
    province_id uuid NOT NULL,
    zip_code character varying,
    phone_number character varying,
    line character varying,
    facebook character varying,
    salary_income numeric,
    family_member_count integer DEFAULT 1,
    agri_worker_count integer DEFAULT 1,
    agri_experience date DEFAULT CURRENT_DATE,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_farmer PRIMARY KEY (user_id),
    CONSTRAINT fk_farmer_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE processing.processor (
    user_id uuid NOT NULL,
    first_name character varying,
    last_name character varying,
    nickname character varying,
    birth_date date,
    id_card_number character varying,
    address_detail character varying,
    subdistrict_id uuid,
    district_id uuid,
    province_id uuid,
    zip_code character varying,
    phone_number character varying,
    line character varying,
    facebook character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_processor PRIMARY KEY (user_id),
    CONSTRAINT fk_processor_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE processing.hub_collector (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    hub_id uuid,
    first_name character varying,
    last_name character varying,
    nickname character varying,
    birthdate date,
    id_card_number character varying,
    phone_number character varying,
    line character varying,
    facebook character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    subdistrict_id uuid,
    district_id uuid,
    province_id uuid,
    zip_code character varying,
    CONSTRAINT pk_hub_collector PRIMARY KEY (user_id),
    CONSTRAINT fk_hub_collector_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE research.researcher (
    user_id uuid NOT NULL,
    first_name character varying,
    last_name character varying,
    organization character varying,
    CONSTRAINT pk_researcher PRIMARY KEY (user_id),
    CONSTRAINT fk_researcher_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE auth.line_identity (
    line_identity_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    line_user_id character varying NOT NULL,
    display_name character varying,
    linked_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_line_identity PRIMARY KEY (line_identity_id),
    CONSTRAINT uq_line_identity_user UNIQUE (user_id),
    CONSTRAINT uq_line_identity_line_user_id UNIQUE (line_user_id),
    CONSTRAINT fk_line_identity_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE form.task (
    task_id uuid DEFAULT gen_random_uuid() NOT NULL,
    title character varying,
    open_at timestamp without time zone,
    close_at timestamp without time zone,
    CONSTRAINT pk_task PRIMARY KEY (task_id)
);

CREATE TABLE form.task_form (
    form_id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    title character varying,
    handler character varying,
    -- V1 baseline column. Nothing read or wrote it until multi-submit;
    -- temp_task_picker's pending-task query now does, so the mirror needs it.
    is_multiple_submit boolean NOT NULL DEFAULT false,
    CONSTRAINT pk_form PRIMARY KEY (form_id),
    CONSTRAINT fk_form_task FOREIGN KEY (task_id) REFERENCES form.task (task_id)
);

CREATE TABLE form.response (
    response_id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_log_id uuid NOT NULL,
    user_id uuid NOT NULL,
    CONSTRAINT pk_response PRIMARY KEY (response_id),
    CONSTRAINT fk_response_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

-- V1 baseline (+ V31's UNIQUE (task_id, user_id)): who a task is assigned to.
-- temp_task_picker and the reminder query only ask whether a task has any
-- rows and whether one of them is a given user's.
CREATE TABLE form.assignment (
    assignment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    status character varying NOT NULL DEFAULT 'NOT_STARTED',
    CONSTRAINT pk_assignment PRIMARY KEY (assignment_id),
    CONSTRAINT uq_assignment_task_user UNIQUE (task_id, user_id),
    CONSTRAINT fk_assignment_task FOREIGN KEY (task_id) REFERENCES form.task (task_id),
    CONSTRAINT fk_assignment_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

CREATE TABLE form.question (
    question_id uuid DEFAULT gen_random_uuid() NOT NULL,
    section_id uuid NOT NULL,
    label character varying,
    field_name character varying,
    input_type character varying DEFAULT 'VARCHAR'::character varying,
    sort_order integer,
    is_mandatory boolean DEFAULT false,
    CONSTRAINT pk_question PRIMARY KEY (question_id)
);

CREATE TABLE chat.conversation (
    conversation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    task_id uuid NOT NULL,
    task_form_id uuid NOT NULL,
    status character varying NOT NULL DEFAULT 'active',
    current_question_id uuid,
    parent_answer jsonb,
    current_page integer NOT NULL DEFAULT 0,
    CONSTRAINT pk_chat_conversation PRIMARY KEY (conversation_id),
    CONSTRAINT fk_chat_conversation_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id),
    CONSTRAINT fk_chat_conversation_task FOREIGN KEY (task_id) REFERENCES form.task (task_id),
    CONSTRAINT fk_chat_conversation_task_form FOREIGN KEY (task_form_id) REFERENCES form.task_form (form_id),
    CONSTRAINT fk_chat_conversation_current_question FOREIGN KEY (current_question_id) REFERENCES form.question (question_id),
    CONSTRAINT ck_chat_conversation_status CHECK (status IN ('active', 'paused', 'completed', 'cancelled'))
);

CREATE TABLE chat.conversation_answer (
    conversation_answer_id uuid NOT NULL DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL,
    question_id uuid NOT NULL,
    answer jsonb,
    source character varying NOT NULL,
    CONSTRAINT pk_chat_conversation_answer PRIMARY KEY (conversation_answer_id),
    CONSTRAINT fk_conversation_answer_conversation FOREIGN KEY (conversation_id) REFERENCES chat.conversation (conversation_id),
    CONSTRAINT fk_conversation_answer_question FOREIGN KEY (question_id) REFERENCES form.question (question_id),
    CONSTRAINT ck_conversation_answer_source CHECK (source IN ('guided_flow', 'llm_extracted'))
);

-- notify.* -- reminder scheduling + delivery log (ADR 0005/0006). Mirrors
-- src/reminders/models.py; the database repo's Flyway migration is the real
-- source of this shape.
CREATE TABLE notify.reminder_schedule (
    schedule_id uuid NOT NULL DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL,
    cadence character varying NOT NULL,
    time_of_day time without time zone NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_by uuid NOT NULL,
    CONSTRAINT pk_reminder_schedule PRIMARY KEY (schedule_id),
    CONSTRAINT fk_reminder_schedule_task FOREIGN KEY (task_id) REFERENCES form.task (task_id),
    CONSTRAINT fk_reminder_schedule_user FOREIGN KEY (created_by) REFERENCES auth.user_account (user_id)
);

CREATE TABLE notify.reminder_recipient (
    recipient_id uuid NOT NULL DEFAULT gen_random_uuid(),
    schedule_id uuid NOT NULL,
    recipient_type character varying NOT NULL,
    role_id uuid,
    user_id uuid,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_reminder_recipient PRIMARY KEY (recipient_id),
    CONSTRAINT fk_reminder_recipient_schedule FOREIGN KEY (schedule_id) REFERENCES notify.reminder_schedule (schedule_id) ON DELETE CASCADE,
    CONSTRAINT fk_reminder_recipient_role FOREIGN KEY (role_id) REFERENCES auth.role (role_id),
    CONSTRAINT fk_reminder_recipient_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id),
    CONSTRAINT ck_reminder_recipient_type CHECK (recipient_type IN ('ROLE', 'USER')),
    CONSTRAINT ck_reminder_recipient_target CHECK (
        (recipient_type = 'ROLE' AND role_id IS NOT NULL AND user_id IS NULL)
        OR (recipient_type = 'USER' AND user_id IS NOT NULL AND role_id IS NULL)
    ),
    CONSTRAINT uq_reminder_recipient_role UNIQUE (schedule_id, role_id),
    CONSTRAINT uq_reminder_recipient_user UNIQUE (schedule_id, user_id)
);

CREATE TABLE notify.reminder_log (
    log_id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    task_id uuid NOT NULL,
    sent_at timestamp without time zone NOT NULL DEFAULT now(),
    channel character varying NOT NULL,
    status character varying NOT NULL,
    CONSTRAINT pk_reminder_log PRIMARY KEY (log_id),
    CONSTRAINT fk_reminder_log_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id),
    CONSTRAINT fk_reminder_log_task FOREIGN KEY (task_id) REFERENCES form.task (task_id)
);

-- agriculture.* -- read directly (never written) by src/line/plot_picker.py,
-- the same documented ADR 0001 exception src/line/parent_picker.py takes.
-- Only the three tables and the columns that picker's query actually names;
-- the real tables (database repo V1__baseline.sql) have many more.
CREATE TABLE agriculture.farm (
    farm_id uuid DEFAULT gen_random_uuid() NOT NULL,
    farm_name character varying,
    CONSTRAINT pk_farm PRIMARY KEY (farm_id)
);

CREATE TABLE agriculture.farmer_farm (
    farmer_farm_id uuid DEFAULT gen_random_uuid() NOT NULL,
    farmer_id uuid NOT NULL,
    farm_id uuid NOT NULL,
    CONSTRAINT pk_farmer_farm PRIMARY KEY (farmer_farm_id),
    CONSTRAINT fk_farmer_farm_farm FOREIGN KEY (farm_id) REFERENCES agriculture.farm (farm_id)
);

CREATE TABLE agriculture.plot (
    plot_id uuid DEFAULT gen_random_uuid() NOT NULL,
    farm_id uuid NOT NULL,
    plot_name character varying,
    CONSTRAINT pk_plot PRIMARY KEY (plot_id),
    CONSTRAINT fk_plot_farm FOREIGN KEY (farm_id) REFERENCES agriculture.farm (farm_id)
);

-- form.handler_registry (database V30, US2-13) -- read by
-- src/line/handler_registry.py to know which handlers need a parent picked
-- first. Only the columns that module reads, seeded with V30's child rows
-- (plus one standalone row) exactly as the migration inserts them. Static
-- reference data: deliberately NOT in tests/integration/conftest.py's
-- cleanup list, so every test sees it.
CREATE TABLE form.handler_registry (
    handler character varying NOT NULL,
    parent_handler character varying,
    parent_field character varying,
    is_active boolean NOT NULL DEFAULT true,
    CONSTRAINT pk_handler_registry PRIMARY KEY (handler)
);

INSERT INTO form.handler_registry (handler, parent_handler, parent_field) VALUES
    ('farm_activity', NULL, NULL),
    ('harvest', NULL, NULL),
    ('batch', NULL, NULL),
    ('farm_activity_fertilizer', 'farm_activity', 'farm_activity_id'),
    ('farm_activity_chemical', 'farm_activity', 'farm_activity_id'),
    ('harvest_grade_detail', 'harvest', 'harvest_id'),
    ('fermentation_batch', 'batch', 'batch_id'),
    ('drying_batch', 'batch', 'batch_id');

-- docs-and-plan#261: what src/db/tasks.py, src/db/forms.py and src/db/ref.py
-- read for the mobile task routes (GET /api/v1/tasks...). Columns the
-- tables above left out, plus form.section / field_validation_rule /
-- field_dependency and a few ref tables, all shaped as in the database repo
-- (V1 baseline, V9, V16, V21, V30). The new tables have no FKs so older
-- tests that insert questions with made-up section ids keep working.
ALTER TABLE form.task
    ADD COLUMN description character varying DEFAULT ''::character varying;
ALTER TABLE form.task_form
    ADD COLUMN description character varying,
    ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE form.question
    ADD COLUMN description character varying,
    ADD COLUMN default_value jsonb,
    ADD COLUMN is_active boolean DEFAULT true,
    ADD COLUMN carry_forward boolean NOT NULL DEFAULT false;
ALTER TABLE form.response
    ADD COLUMN submitted_at timestamp without time zone DEFAULT now(),
    ADD COLUMN answer jsonb,
    ADD COLUMN status character varying,
    ADD COLUMN task_form_id uuid;

CREATE TABLE form.section (
    section_id uuid DEFAULT gen_random_uuid() NOT NULL,
    form_id uuid NOT NULL,
    title character varying,
    description character varying,
    sort_order integer,
    is_active boolean DEFAULT true,
    CONSTRAINT pk_section PRIMARY KEY (section_id)
);

CREATE TABLE form.field_validation_rule (
    field_name character varying PRIMARY KEY,
    validation_rule jsonb NOT NULL
);

CREATE TABLE form.field_dependency (
    child_field character varying NOT NULL,
    parent_field character varying NOT NULL,
    link_table character varying NOT NULL,
    link_column character varying NOT NULL,
    label_th character varying NOT NULL,
    CONSTRAINT pk_field_dependency PRIMARY KEY (child_field, parent_field)
);

CREATE SCHEMA ref;

-- A synced copy of agriculture.plot, sharing plot_id: no farm_id of its own,
-- which is why a dependent choice's parent comes from the link table.
CREATE TABLE ref.plot_constant (
    plot_id uuid NOT NULL,
    plot_name character varying,
    CONSTRAINT pk_plot_constant PRIMARY KEY (plot_id)
);

CREATE TABLE ref.province_constant (
    province_id uuid DEFAULT gen_random_uuid() NOT NULL,
    province_name_th character varying,
    CONSTRAINT pk_province_constant PRIMARY KEY (province_id)
);

CREATE TABLE ref.grade_constant (
    grade_id uuid DEFAULT gen_random_uuid() NOT NULL,
    grade_name character varying NOT NULL,
    CONSTRAINT pk_grade_constant PRIMARY KEY (grade_id)
);

-- docs-and-plan#265: what src/db/agriculture.py and src/db/storage.py read
-- and write for the farm/plot routes. Columns as in the database repo's V1
-- baseline (+ V7's storage.file.created_at), except that the NOT NULL
-- province/district/subdistrict ids stay nullable here so older tests that
-- insert a bare farm keep working.
ALTER TABLE agriculture.farm
    ADD COLUMN hub_id uuid,
    ADD COLUMN geo_id uuid,
    ADD COLUMN found_date date DEFAULT CURRENT_DATE,
    ADD COLUMN total_area numeric,
    ADD COLUMN address_detail character varying,
    ADD COLUMN subdistrict_id uuid,
    ADD COLUMN district_id uuid,
    ADD COLUMN province_id uuid,
    ADD COLUMN zip_code character varying,
    ADD COLUMN contact_name character varying,
    ADD COLUMN phone_number character varying,
    ADD COLUMN line character varying,
    ADD COLUMN facebook character varying,
    ADD COLUMN created_at timestamp without time zone DEFAULT now() NOT NULL,
    ADD COLUMN updated_at timestamp without time zone DEFAULT now() NOT NULL;
ALTER TABLE agriculture.plot
    ADD COLUMN total_area numeric,
    ADD COLUMN land_ownership character varying,
    ADD COLUMN cocoa_planted_area numeric,
    ADD COLUMN has_chemical_use boolean DEFAULT false,
    ADD COLUMN land_type_id uuid,
    ADD COLUMN soil_type_id uuid,
    ADD COLUMN water_source_id uuid,
    ADD COLUMN watering_system_id uuid,
    ADD COLUMN found_date date DEFAULT CURRENT_DATE,
    ADD COLUMN created_at timestamp without time zone DEFAULT now() NOT NULL,
    ADD COLUMN updated_at timestamp without time zone DEFAULT now() NOT NULL;

CREATE SCHEMA storage;

CREATE TABLE storage.file (
    file_id uuid DEFAULT gen_random_uuid() NOT NULL,
    file_name character varying(255),
    table_name character varying,
    ref_id uuid NOT NULL,
    code character varying,
    original_name character varying(255),
    file_extension character varying(20),
    mime_type character varying(100),
    file_size bigint,
    storage_path character varying,
    storage_provider character varying(50),
    checksum character varying(128),
    version integer DEFAULT 1,
    is_public boolean DEFAULT false,
    status character varying(20) DEFAULT 'active'::character varying,
    uploaded_by uuid NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_file PRIMARY KEY (file_id)
);

-- The real geom column is PostGIS geometry(Geometry,4326). CI's Postgres has
-- no PostGIS, so geom is text here and ST_GeomFromText is a stand-in that
-- keeps the SRID and WKT it was given -- enough to check what was written.
CREATE FUNCTION public.ST_GeomFromText(wkt text, srid integer) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$ SELECT 'SRID=' || srid || ';' || wkt $$;

CREATE TABLE storage.geo (
    geo_id uuid DEFAULT gen_random_uuid() NOT NULL,
    uploaded_by uuid NOT NULL,
    geom text,
    code character varying,
    source_type character varying(20),
    area_sq_m double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_geo PRIMARY KEY (geo_id)
);

-- docs-and-plan#263: src/forms/registry.py also reads which table each
-- handler writes to (V30's schema_name / table_name), filled in for the
-- rows seeded above with the values V30 inserts.
ALTER TABLE form.handler_registry
    ADD COLUMN schema_name character varying,
    ADD COLUMN table_name character varying;
UPDATE form.handler_registry SET schema_name = v.s, table_name = v.t
FROM (VALUES
    ('farm_activity', 'agriculture', 'farm_activity'),
    ('farm_activity_fertilizer', 'agriculture', 'farm_activity_fertilizer'),
    ('farm_activity_chemical', 'agriculture', 'farm_activity_chemical'),
    ('harvest', 'collection', 'harvest'),
    ('harvest_grade_detail', 'collection', 'harvest_grade_detail'),
    ('batch', 'processing', 'batch'),
    ('fermentation_batch', 'processing', 'fermentation_batch'),
    ('drying_batch', 'processing', 'drying_batch')
) AS v(h, s, t)
WHERE handler = v.h;
ALTER TABLE form.handler_registry
    ALTER COLUMN schema_name SET NOT NULL,
    ALTER COLUMN table_name SET NOT NULL;

-- What the chatbot's parent picker lists (src/db/agriculture.py, collection.py,
-- processing.py and ref.py) and the mobile hub / station / batch / harvest
-- routes (docs-and-plan#266) read and write. Shaped as in the database repo's
-- V1 baseline, without FKs so tests can insert rows in any order; the
-- address and owner columns that are NOT NULL there are nullable here so a
-- test can insert just the columns it cares about.
CREATE SCHEMA collection;

CREATE TABLE ref.farm_activity_type_constant (
    farm_activity_type_id uuid DEFAULT gen_random_uuid() NOT NULL,
    farm_activity_type_name character varying,
    CONSTRAINT pk_farm_activity_type_constant PRIMARY KEY (farm_activity_type_id)
);

CREATE TABLE agriculture.farm_activity (
    farm_activity_id uuid DEFAULT gen_random_uuid() NOT NULL,
    farm_id uuid NOT NULL,
    farm_activity_type_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_farm_activity PRIMARY KEY (farm_activity_id),
    CONSTRAINT fk_farm_activity_farm FOREIGN KEY (farm_id) REFERENCES agriculture.farm (farm_id)
);

CREATE TABLE processing.hub (
    hub_id uuid DEFAULT gen_random_uuid() NOT NULL,
    hub_name character varying,
    geo_id uuid,
    found_date date,
    address_detail character varying,
    subdistrict_id uuid,
    district_id uuid,
    province_id uuid,
    zip_code character varying,
    contact_name character varying,
    phone_number character varying,
    line character varying,
    facebook character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_hub PRIMARY KEY (hub_id)
);

CREATE TABLE processing.processing_station (
    processing_station_id uuid DEFAULT gen_random_uuid() NOT NULL,
    processing_station_name character varying,
    hub_id uuid,
    geo_id uuid,
    found_date date,
    processing_station_area numeric,
    address_detail character varying,
    subdistrict_id uuid,
    district_id uuid,
    province_id uuid,
    zip_code character varying,
    contact_name character varying,
    phone_number character varying,
    line character varying,
    facebook character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_processing_station PRIMARY KEY (processing_station_id)
);

CREATE TABLE processing.processor_processing_station (
    processor_processing_station_id uuid DEFAULT gen_random_uuid() NOT NULL,
    processor_id uuid NOT NULL,
    processing_station_id uuid NOT NULL,
    CONSTRAINT pk_processor_processing_station PRIMARY KEY (processor_processing_station_id)
);

CREATE TABLE processing.batch (
    batch_id uuid DEFAULT gen_random_uuid() NOT NULL,
    processing_station_id uuid NOT NULL,
    origin character varying,
    notes character varying DEFAULT ''::character varying,
    quantity_kg numeric,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_batch PRIMARY KEY (batch_id)
);

-- docs-and-plan#271 (and the contract seed, tests/contract/web/seed): the
-- researcher's form list and task view read these columns, and the seed's
-- base.sql and forms.sql write them. Copied from the database repo's V1
-- baseline and V31 (recurring tasks, task recipients).
ALTER TABLE form.task
    ADD COLUMN IF NOT EXISTS description character varying DEFAULT '',
    ADD COLUMN IF NOT EXISTS task_type character varying DEFAULT 'FORM',
    ADD COLUMN IF NOT EXISTS recurrence_id uuid,
    ADD COLUMN IF NOT EXISTS period_date date;
ALTER TABLE form.task_form
    ADD COLUMN IF NOT EXISTS description character varying,
    ADD COLUMN IF NOT EXISTS content character varying DEFAULT '',
    ADD COLUMN IF NOT EXISTS created_at timestamp without time zone NOT NULL DEFAULT now();
ALTER TABLE ref.province_constant
    ADD COLUMN IF NOT EXISTS province_name_en character varying(255),
    ADD COLUMN IF NOT EXISTS description character varying DEFAULT '';
ALTER TABLE ref.grade_constant
    ADD COLUMN IF NOT EXISTS is_fail boolean DEFAULT false;

CREATE TABLE ref.district_constant (
    district_id uuid DEFAULT gen_random_uuid() NOT NULL,
    district_name_th character varying(255),
    district_name_en character varying(255),
    province_id uuid NOT NULL,
    description character varying DEFAULT '',
    CONSTRAINT pk_district PRIMARY KEY (district_id),
    CONSTRAINT fk_district_province FOREIGN KEY (province_id) REFERENCES ref.province_constant (province_id)
);

CREATE TABLE ref.subdistrict_constant (
    subdistrict_id uuid DEFAULT gen_random_uuid() NOT NULL,
    subdistrict_name_th character varying(255),
    subdistrict_name_en character varying(255),
    district_id uuid NOT NULL,
    description character varying DEFAULT '',
    zipcode character varying,
    CONSTRAINT pk_subdistrict PRIMARY KEY (subdistrict_id),
    CONSTRAINT fk_subdistrict_district FOREIGN KEY (district_id) REFERENCES ref.district_constant (district_id)
);

CREATE TABLE form.task_recurrence (
    recurrence_id uuid DEFAULT gen_random_uuid() NOT NULL,
    template_task_id uuid NOT NULL,
    frequency character varying NOT NULL,
    weekdays smallint[],
    start_date date NOT NULL,
    until_date date,
    is_active boolean DEFAULT true NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_task_recurrence PRIMARY KEY (recurrence_id),
    CONSTRAINT uq_task_recurrence_template UNIQUE (template_task_id),
    CONSTRAINT fk_task_recurrence_template FOREIGN KEY (template_task_id) REFERENCES form.task (task_id),
    CONSTRAINT ck_task_recurrence_frequency CHECK (frequency IN ('DAILY', 'WEEKLY'))
);
ALTER TABLE form.task
    ADD CONSTRAINT fk_task_recurrence FOREIGN KEY (recurrence_id) REFERENCES form.task_recurrence (recurrence_id);

CREATE TABLE form.task_recipient (
    recipient_id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    recipient_type character varying NOT NULL,
    role_id uuid,
    user_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_task_recipient PRIMARY KEY (recipient_id),
    CONSTRAINT fk_task_recipient_task FOREIGN KEY (task_id) REFERENCES form.task (task_id) ON DELETE CASCADE,
    CONSTRAINT ck_task_recipient_type CHECK (recipient_type IN ('ROLE', 'USER'))
);

CREATE TABLE collection.harvest (
    harvest_id uuid DEFAULT gen_random_uuid() NOT NULL,
    hub_id uuid,
    farm_id uuid,
    plot_id uuid,
    tree_id uuid,
    harvest_date timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT pk_harvest PRIMARY KEY (harvest_id)
);

CREATE TABLE collection.harvest_grade_detail (
    harvest_id uuid NOT NULL,
    grade_code character varying NOT NULL,
    quantity_kg numeric,
    description character varying DEFAULT ''::character varying,
    is_clean boolean,
    is_appropriate_size boolean,
    weight_gram_per_pod numeric,
    is_sprout boolean,
    is_dry boolean,
    is_shriveled boolean,
    cut_test_result character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

CREATE TABLE collection.harvest_collection (
    collection_id uuid DEFAULT gen_random_uuid() NOT NULL,
    harvest_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    CONSTRAINT pk_harvest_collection PRIMARY KEY (collection_id)
);

-- docs-and-plan#272 (the form builder's handler pickers and the Data Map):
-- the registry's display columns from the database repo's V30, with defaults
-- so the bare rows inserted above, and the tests' own, still load.
ALTER TABLE form.handler_registry
    ADD COLUMN IF NOT EXISTS label_th character varying NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS description_th character varying NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category character varying NOT NULL DEFAULT 'garden',
    ADD COLUMN IF NOT EXISTS frequency character varying NOT NULL DEFAULT 'on_demand',
    ADD COLUMN IF NOT EXISTS extra_required_fields character varying[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS sort_order integer NOT NULL DEFAULT 0;

-- docs-and-plan#276 (the round job of repeating tasks): who may see each
-- question, which a new round copies, and one round per recurrence and day,
-- as in the database repo's V1 baseline and V31.
CREATE TABLE form.question_visibility (
    visibility_id uuid DEFAULT gen_random_uuid() NOT NULL,
    question_id uuid NOT NULL,
    role character varying,
    province_id uuid,
    CONSTRAINT pk_question_visibility PRIMARY KEY (visibility_id),
    CONSTRAINT fk_question_visibility_question FOREIGN KEY (question_id) REFERENCES form.question (question_id)
);
CREATE UNIQUE INDEX uq_task_recurrence_period ON form.task (recurrence_id, period_date)
    WHERE recurrence_id IS NOT NULL;

-- docs-and-plan#277 (the farmer's daily diary): as in the database repo's V22.
CREATE TABLE form.diary_entry (
    diary_entry_id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    entry_date date NOT NULL,
    diary_text text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_diary_entry PRIMARY KEY (diary_entry_id),
    CONSTRAINT uq_diary_entry_user_date UNIQUE (user_id, entry_date),
    CONSTRAINT fk_diary_entry_user FOREIGN KEY (user_id) REFERENCES auth.user_account (user_id)
);

-- The contract seed's answers.sql writes it, and the researcher's corrections
-- (docs-and-plan#275) append to it: as in the database repo's V29.
CREATE TABLE form.response_correction_log (
    correction_log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    response_id uuid NOT NULL,
    field_name character varying NOT NULL,
    old_value text,
    new_value text,
    corrected_by uuid NOT NULL,
    reason text,
    corrected_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT pk_response_correction_log PRIMARY KEY (correction_log_id),
    CONSTRAINT fk_response_correction_log_response FOREIGN KEY (response_id) REFERENCES form.response (response_id),
    CONSTRAINT fk_response_correction_log_user FOREIGN KEY (corrected_by) REFERENCES auth.user_account (user_id)
);
-- ...and chat.conversation's timestamps, which the review table uses to tell
-- which conversation sent an answer (V1 baseline; the ORM model maps neither).
ALTER TABLE chat.conversation
    ADD COLUMN IF NOT EXISTS created_at timestamp without time zone DEFAULT now() NOT NULL,
    ADD COLUMN IF NOT EXISTS updated_at timestamp without time zone DEFAULT now() NOT NULL;
