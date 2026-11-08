-- Contract seed 3/4: tasks, their forms, who they are for, a repeat rule and a reminder.
-- Needs base.sql. Times are UTC, as stored.
--
--   task 1  harvest form, open to everyone, single submit
--   task 2  farm_activity form, assigned to farmer 1 only, multi submit, repeats weekly
--   task 3  pest/disease form with an inactive section and an inactive question
--   task 4  a form with no section: Kotlin's GET /forms hides it, GET /tasks shows it
--   task 5  a task with no form at all

INSERT INTO form.task (task_id, title, description, task_type, open_at, close_at) VALUES
    ('50000000-0000-4000-8000-000000000001', 'บันทึกยอดส่งโกโก้', 'ส่งผลผลิตที่จุดรวบรวม', 'FORM',
     '2026-09-01 00:00:00', '2026-12-31 16:59:59'),
    ('50000000-0000-4000-8000-000000000002', 'กิจกรรมในสวนประจำสัปดาห์', 'จดกิจกรรมในสวน', 'FORM',
     '2026-10-01 00:00:00', '2026-10-07 16:59:59'),
    ('50000000-0000-4000-8000-000000000003', 'รายงานโรคและศัตรูพืช', 'พบโรคหรือแมลงให้รายงาน', 'FORM',
     '2026-08-01 00:00:00', '2026-11-30 16:59:59'),
    ('50000000-0000-4000-8000-000000000004', 'แบบฟอร์มเก่าไม่มีหัวข้อ', 'legacy', 'FORM',
     '2026-06-01 00:00:00', '2026-06-30 16:59:59'),
    ('50000000-0000-4000-8000-000000000005', 'งานไม่มีแบบฟอร์ม', 'no form', 'TASK',
     '2026-10-10 00:00:00', NULL);

INSERT INTO form.task_form (form_id, task_id, title, description, content, handler, is_multiple_submit, created_at) VALUES
    ('51000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000001', 'บันทึกยอดส่งโกโก้',
     'ส่งผลผลิตที่จุดรวบรวม', 'harvest form', 'harvest', false, '2026-08-25 03:00:00'),
    ('51000000-0000-4000-8000-000000000002', '50000000-0000-4000-8000-000000000002', 'กิจกรรมในสวนประจำสัปดาห์',
     'จดกิจกรรมในสวน', 'activity form', 'farm_activity', true, '2026-09-28 03:00:00'),
    ('51000000-0000-4000-8000-000000000003', '50000000-0000-4000-8000-000000000003', 'รายงานโรคและศัตรูพืช',
     'พบโรคหรือแมลงให้รายงาน', 'pest form', 'farm_pest_disease_record', false, '2026-07-30 03:00:00'),
    ('51000000-0000-4000-8000-000000000004', '50000000-0000-4000-8000-000000000004', 'แบบฟอร์มเก่าไม่มีหัวข้อ',
     NULL, 'legacy', 'harvest', false, '2026-05-30 03:00:00');

INSERT INTO form.section (section_id, form_id, title, description, sort_order, is_active) VALUES
    ('52000000-0000-4000-8000-000000000001', '51000000-0000-4000-8000-000000000001', 'การส่งผลผลิต', NULL, 1, true),
    ('52000000-0000-4000-8000-000000000002', '51000000-0000-4000-8000-000000000002', 'กิจกรรม', NULL, 1, true),
    ('52000000-0000-4000-8000-000000000003', '51000000-0000-4000-8000-000000000003', 'สิ่งที่พบ', NULL, 1, true),
    ('52000000-0000-4000-8000-000000000004', '51000000-0000-4000-8000-000000000003', 'หัวข้อที่ปิดไว้', 'old', 2, false);

INSERT INTO form.question (question_id, section_id, label, description, input_type, field_name, sort_order,
                           is_mandatory, is_active, carry_forward) VALUES
    ('53000000-0000-4000-8000-000000000001', '52000000-0000-4000-8000-000000000001', 'เลือกฟาร์มที่จัดส่ง', NULL, 'OPTION', 'farm_id', 0, true, true, true),
    ('53000000-0000-4000-8000-000000000002', '52000000-0000-4000-8000-000000000001', 'หน่วยรวบรวมที่จัดส่ง', NULL, 'OPTION', 'hub_id', 1, true, true, false),
    ('53000000-0000-4000-8000-000000000003', '52000000-0000-4000-8000-000000000001', 'วันที่ส่งมา', NULL, 'DATE', 'harvest_date', 2, true, true, false),
    ('53000000-0000-4000-8000-000000000004', '52000000-0000-4000-8000-000000000001', 'อธิบายการขนส่ง', 'ไม่บังคับ', 'VARCHAR', 'logistic_result', 3, false, true, false),
    ('53000000-0000-4000-8000-000000000011', '52000000-0000-4000-8000-000000000002', 'ฟาร์มที่ทำกิจกรรม', NULL, 'OPTION', 'farm_id', 0, true, true, true),
    ('53000000-0000-4000-8000-000000000012', '52000000-0000-4000-8000-000000000002', 'แปลงที่ดำเนินการ', NULL, 'OPTION', 'plot_id', 1, false, true, false),
    ('53000000-0000-4000-8000-000000000013', '52000000-0000-4000-8000-000000000002', 'รายละเอียด', NULL, 'VARCHAR', 'description', 2, false, true, false),
    ('53000000-0000-4000-8000-000000000014', '52000000-0000-4000-8000-000000000002', 'ตำแหน่งปัจจุบัน', NULL, 'GEODATA', 'gis', 3, false, true, false),
    ('53000000-0000-4000-8000-000000000021', '52000000-0000-4000-8000-000000000003', 'ฟาร์มที่พบปัญหา', NULL, 'OPTION', 'farm_id', 0, true, true, false),
    ('53000000-0000-4000-8000-000000000022', '52000000-0000-4000-8000-000000000003', 'วิธีจัดการ', NULL, 'VARCHAR', 'management_method', 1, false, true, false),
    ('53000000-0000-4000-8000-000000000023', '52000000-0000-4000-8000-000000000003', 'ทำให้ผลเสียคุณภาพหรือไม่', NULL, 'BOOLEAN', 'is_quality_damage', 2, true, true, false),
    ('53000000-0000-4000-8000-000000000024', '52000000-0000-4000-8000-000000000003', 'คำถามที่ปิดไว้', NULL, 'INT', 'notes', 3, false, false, false),
    ('53000000-0000-4000-8000-000000000025', '52000000-0000-4000-8000-000000000004', 'ในหัวข้อที่ปิดไว้', NULL, 'VARCHAR', 'notes', 0, false, true, false);

-- Task 2 is only for farmer 1 (US4-4: a recipient + its resolved assignment).
INSERT INTO form.task_recipient (recipient_id, task_id, recipient_type, user_id) VALUES
    ('57000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000002', 'USER', '20000000-0000-4000-8000-000000000003');
INSERT INTO form.assignment (assignment_id, task_id, user_id) VALUES
    ('58000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000003');

-- Task 2 repeats every Monday, Wednesday and Friday.
INSERT INTO form.task_recurrence (recurrence_id, template_task_id, frequency, weekdays, start_date, until_date,
                                  created_by, created_at) VALUES
    ('56000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000002', 'WEEKLY', '{1,3,5}',
     '2026-10-01', '2026-12-31', '20000000-0000-4000-8000-000000000002', '2026-09-28 03:00:00');
UPDATE form.task SET recurrence_id = '56000000-0000-4000-8000-000000000001', period_date = '2026-10-01'
WHERE task_id = '50000000-0000-4000-8000-000000000002';

-- Task 1 reminds every farmer at 08:00 Bangkok time.
INSERT INTO notify.reminder_schedule (schedule_id, task_id, cadence, time_of_day, is_active, created_by) VALUES
    ('59000000-0000-4000-8000-000000000001', '50000000-0000-4000-8000-000000000001', 'DAILY', '08:00', true,
     '20000000-0000-4000-8000-000000000002');
INSERT INTO notify.reminder_recipient (recipient_id, schedule_id, recipient_type, role_id) VALUES
    ('59000000-0000-4000-8000-000000000101', '59000000-0000-4000-8000-000000000001', 'ROLE', '10000000-0000-4000-8000-000000000003');
