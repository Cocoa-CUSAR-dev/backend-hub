"""The rules a form must pass before it is saved, so a researcher can't build
a form that can never be submitted (US2-13). Ported from web-backend's
FormDefinitionValidator, messages included.

Pure: the service gathers the handler, its table's columns and the field
dependencies, and hands them here.
"""

from dataclasses import dataclass

from src.db.forms import FieldDependencyRow, HandlerRow
from src.forms.schemas import HandlerFieldOut


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """One question as the rules see it. section_order, then question_order,
    decide what comes before what."""

    section_order: int
    question_order: int
    label: str
    input_type: str
    field_name: str | None


def problems(
    handler: HandlerRow,
    fields: list[HandlerFieldOut],
    dependencies: dict[str, FieldDependencyRow],
    questions: list[QuestionSpec],
) -> list[str]:
    """Every problem found, not just the first: fixing a form shouldn't take
    five saves to discover five problems."""
    errors: list[str] = []
    by_name = {f.name: f for f in fields}
    asked = [q for q in questions if q.field_name and q.field_name.strip()]

    # The input type must be one the column can hold. A field that isn't a
    # column (e.g. "gis", "notes") is allowed: its answer stays in the response.
    for q in asked:
        field = by_name.get(q.field_name or "")
        if field is not None and q.input_type not in field.allowed_input_types:
            allowed = " / ".join(field.allowed_input_types) or "-"
            errors.append(
                f"คำถาม '{q.label}' ({q.field_name}) ใช้ชนิด {q.input_type} "
                f"ไม่ได้ คอลัมน์นี้รับได้เฉพาะ {allowed}"
            )

    # Each field at most once: a second question would overwrite the first.
    seen: dict[str, list[QuestionSpec]] = {}
    for q in asked:
        seen.setdefault(q.field_name or "", []).append(q)
    for name, same in seen.items():
        if len(same) > 1:
            labels = ", ".join(f"'{q.label}'" for q in same)
            errors.append(f"ฟิลด์ {name} ถูกใช้ซ้ำ {len(same)} คำถาม ({labels}) ต้องมีได้คำถามเดียว")

    # Every required column must be asked, or every submission fails at INSERT.
    asked_fields = {q.field_name for q in asked}
    missing = [f.name for f in fields if f.required and f.name not in asked_fields]
    if missing:
        errors.append(
            f"ตาราง {handler.schema_name}.{handler.table_name} บังคับฟิลด์ {', '.join(missing)} "
            "แต่ฟอร์มยังไม่มีคำถามสำหรับฟิลด์นี้"
        )

    # A dependent field needs its parent asked first, or there is nothing to
    # filter its choices by (plots without a farm). A parent the chatbot's
    # picker fills (the handler's parent_field) counts as asked before all.
    position = {q.field_name: (q.section_order, q.question_order) for q in asked}
    for child in asked:
        dependency = dependencies.get(child.field_name or "")
        if dependency is None or dependency.parent_field == handler.parent_field:
            continue
        parent_position = position.get(dependency.parent_field)
        if parent_position is None or parent_position >= position[child.field_name]:
            parent_label = next(
                (q.label for q in asked if q.field_name == dependency.parent_field), None
            )
            parent = (
                f"'{parent_label}' ({dependency.parent_field})"
                if parent_label is not None
                else dependency.parent_field
            )
            errors.append(
                f"คำถาม '{child.label}' ({child.field_name}) ต้องมีคำถาม {parent} อยู่ก่อน "
                f"({dependency.label_th})"
            )
    return errors
