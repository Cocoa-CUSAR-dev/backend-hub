-- Contract seed 1/4: places, grades, roles, permissions, users and profiles.
--
-- Loaded on top of the database repo's migrations (V1..V33) to record the
-- Kotlin fixtures, and on top of tests/resources/schema.sql to replay them
-- against backend-hub. Every id is fixed so fixtures stay stable; an id the
-- seed doesn't know (a row a POST created) is masked by tests/contract/fixtures.py.
--
-- Id prefixes: 1 role, 2 user, 3 place, 4 farm/plot/hub/harvest, 5 form side.
-- Roles and the baseline permission keys only exist in the live database,
-- not in any migration, so they are created here from CONVENTIONS.md §5.

-- ---- places ---------------------------------------------------------------
INSERT INTO ref.province_constant (province_id, province_name_th, province_name_en) VALUES
    ('30000000-0000-4000-8000-000000000001', 'ชุมพร', 'Chumphon'),
    ('30000000-0000-4000-8000-000000000002', 'เชียงใหม่', 'Chiang Mai');
INSERT INTO ref.district_constant (district_id, district_name_th, district_name_en, province_id) VALUES
    ('31000000-0000-4000-8000-000000000001', 'เมืองชุมพร', 'Mueang Chumphon', '30000000-0000-4000-8000-000000000001'),
    ('31000000-0000-4000-8000-000000000002', 'แม่ริม', 'Mae Rim', '30000000-0000-4000-8000-000000000002');
INSERT INTO ref.subdistrict_constant (subdistrict_id, subdistrict_name_th, subdistrict_name_en, district_id, zipcode) VALUES
    ('32000000-0000-4000-8000-000000000001', 'ท่าตะเภา', 'Tha Taphao', '31000000-0000-4000-8000-000000000001', '86000'),
    ('32000000-0000-4000-8000-000000000002', 'ริมใต้', 'Rim Tai', '31000000-0000-4000-8000-000000000002', '50180');

INSERT INTO ref.grade_constant (grade_id, grade_name, is_fail) VALUES
    ('33000000-0000-4000-8000-000000000001', 'A', false),
    ('33000000-0000-4000-8000-000000000002', 'B', false),
    ('33000000-0000-4000-8000-000000000003', 'C', true);

-- ---- roles and permissions (CONVENTIONS.md §5) ------------------------------
INSERT INTO auth.role (role_id, role_name) VALUES
    ('10000000-0000-4000-8000-000000000001', 'admin'),
    ('10000000-0000-4000-8000-000000000002', 'researcher'),
    ('10000000-0000-4000-8000-000000000003', 'farmer'),
    ('10000000-0000-4000-8000-000000000004', 'hub_collector'),
    ('10000000-0000-4000-8000-000000000005', 'processor');

INSERT INTO auth.permission (permission_key)
SELECT k FROM (VALUES
    ('read:profile:own'), ('update:profile:own'), ('create:user:any'), ('update:role:any'),
    ('read:researcher:all'), ('update:researcher:any'), ('delete:researcher:any'),
    ('read:researcher:own'), ('create:researcher:own'), ('update:researcher:own'),
    ('read:form:all'), ('create:form:all'), ('update:form:all'), ('read:form:assigned'),
    ('read:task:all'), ('read:response:all'), ('update:response:all'), ('read:response:own'),
    ('read:report:all'), ('create:farm:own'), ('read:farm:own'), ('update:farm:own'), ('read:farm:all')
) AS v(k)
WHERE NOT EXISTS (SELECT 1 FROM auth.permission p WHERE p.permission_key = v.k);

INSERT INTO auth.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM (VALUES
    ('admin', 'read:profile:own'), ('admin', 'update:profile:own'), ('admin', 'create:user:any'),
    ('admin', 'update:role:any'), ('admin', 'read:researcher:all'), ('admin', 'update:researcher:any'),
    ('admin', 'delete:researcher:any'), ('admin', 'read:form:all'), ('admin', 'update:form:all'),
    ('admin', 'read:report:all'),
    ('researcher', 'read:profile:own'), ('researcher', 'update:profile:own'),
    ('researcher', 'read:researcher:own'), ('researcher', 'create:researcher:own'),
    ('researcher', 'update:researcher:own'), ('researcher', 'read:form:all'),
    ('researcher', 'create:form:all'), ('researcher', 'update:form:all'), ('researcher', 'read:task:all'),
    ('researcher', 'read:response:all'), ('researcher', 'update:response:all'),
    ('researcher', 'read:report:all'), ('researcher', 'read:farm:all'),
    ('farmer', 'read:profile:own'), ('farmer', 'update:profile:own'), ('farmer', 'read:form:assigned'),
    ('farmer', 'read:response:own'), ('farmer', 'create:farm:own'), ('farmer', 'read:farm:own'),
    ('farmer', 'update:farm:own'),
    ('hub_collector', 'read:profile:own'), ('hub_collector', 'update:profile:own'),
    ('hub_collector', 'read:form:assigned'), ('hub_collector', 'read:response:own'),
    ('processor', 'read:profile:own'), ('processor', 'update:profile:own'),
    ('processor', 'read:form:assigned'), ('processor', 'read:response:own')
) AS v(role_name, permission_key)
JOIN auth.role r ON r.role_name = v.role_name
JOIN auth.permission p ON p.permission_key = v.permission_key;

-- ---- users (created in different months, for the user analytics) ----------
INSERT INTO auth.user_account (user_id, username, created_at) VALUES
    ('20000000-0000-4000-8000-000000000001', 'contract_admin', '2026-07-03 02:00:00'),
    ('20000000-0000-4000-8000-000000000002', 'contract_researcher', '2026-07-10 02:00:00'),
    ('20000000-0000-4000-8000-000000000003', 'contract_farmer_1', '2026-08-01 02:00:00'),
    ('20000000-0000-4000-8000-000000000004', 'contract_farmer_2', '2026-09-15 02:00:00'),
    ('20000000-0000-4000-8000-000000000005', 'contract_processor', '2026-09-20 02:00:00'),
    ('20000000-0000-4000-8000-000000000006', 'contract_no_role', '2026-10-01 02:00:00'),
    ('20000000-0000-4000-8000-000000000007', 'contract_collector', '2026-08-05 02:00:00'),
    ('20000000-0000-4000-8000-000000000008', 'contract_researcher_2', '2026-10-02 02:00:00');

INSERT INTO auth.user_role (user_id, role_id) VALUES
    ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001'),
    ('20000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002'),
    ('20000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000003'),
    ('20000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000003'),
    ('20000000-0000-4000-8000-000000000005', '10000000-0000-4000-8000-000000000005'),
    ('20000000-0000-4000-8000-000000000007', '10000000-0000-4000-8000-000000000004'),
    ('20000000-0000-4000-8000-000000000008', '10000000-0000-4000-8000-000000000002');

INSERT INTO research.researcher (user_id, first_name, last_name, organization) VALUES
    ('20000000-0000-4000-8000-000000000002', 'วิจัย', 'หนึ่ง', 'CUSAR'),
    ('20000000-0000-4000-8000-000000000008', 'วิจัย', 'สอง', 'CU Intania');

INSERT INTO agriculture.farmer (user_id, first_name, last_name, subdistrict_id, district_id, province_id) VALUES
    ('20000000-0000-4000-8000-000000000003', 'สมชาย', 'ใจดี',
     '32000000-0000-4000-8000-000000000001', '31000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001'),
    ('20000000-0000-4000-8000-000000000004', 'สมหญิง', 'รักสวน',
     '32000000-0000-4000-8000-000000000002', '31000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002');

INSERT INTO processing.processor (user_id, first_name, last_name) VALUES
    ('20000000-0000-4000-8000-000000000005', 'แปรรูป', 'เก่ง');
