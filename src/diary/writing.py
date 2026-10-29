"""How a day's answers become diary text, ported from web-backend's
DiaryTemplateRenderer, DiaryFactGuard and DiaryLlmClient's prompt.

"Template first, the LLM only polishes the wording": the template echoes the
facts as a bullet list and can't invent a value; the polish is kept only if
every value still appears in it word for word (the fact-guard). Pure
functions: no database, no LLM call.
"""

from dataclasses import dataclass

PLOT_FIELD = "plot_id"
# An unanswered plot_id means the whole farm, not "nothing about where".
WHOLE_FARM_LINE = "- ขอบเขต: ไม่ได้ระบุแปลงที่เฉพาะเจาะจง (ถือว่าทำทั้งฟาร์ม)"

YES, NO = "ใช่", "ไม่"


@dataclass(frozen=True, slots=True)
class Fact:
    """One answered field, its value already readable (a name, not an id)."""

    field_name: str
    label: str
    value: str


def yes_no(raw: str) -> str:
    """A BOOLEAN answer as the farmer reads it."""
    return {"true": YES, "false": NO}.get(raw, raw)


def render(facts: list[Fact]) -> str:
    """The day as a bullet list, one line per fact; "" for no facts."""
    if not facts:
        return ""
    lines = [f"- {fact.label}: {fact.value}" for fact in facts]
    if not any(fact.field_name == PLOT_FIELD for fact in facts):
        lines.insert(0, WHOLE_FARM_LINE)
    return "\n".join(lines)


def preserves_facts(facts: list[Fact], polished: str) -> bool:
    """Every value is still in the polished text, word for word. ใช่ and ไม่
    are exempt: a faithful rewrite states the fact instead ("ทำให้โกโก้เสีย
    คุณภาพหรือไม่: ใช่" -> "ทำให้โกโก้เสียคุณภาพ"). Added sentences that
    contradict nothing aren't caught; that's beyond a check this light."""
    if not polished.strip():
        return False
    return all(fact.value in polished for fact in facts if fact.value not in (YES, NO))


def polish_prompt(template_text: str) -> str:
    """Rephrase only, never add or drop a fact. The fact-guard is what
    enforces it; the prompt only makes the ask unambiguous."""
    return f"""เกลาข้อความบันทึกการเกษตรต่อไปนี้ให้เป็นภาษาไทยที่อ่านลื่นเป็นธรรมชาติ เหมือนไดอารี่ที่คนเขียนเอง
ไม่ใช่การทวนคำถามฟอร์มแล้วตอบทีละข้อ

กฎการเกลา:
- ห้ามเพิ่มข้อเท็จจริงใหม่ ห้ามเปลี่ยนตัวเลขหรือชื่อเฉพาะใดๆ ที่มีอยู่ในข้อความต้นฉบับ
- โครงประโยคใช่/ไม่ใช่ (เช่น "...หรือไม่: ใช่") ให้เขียนเป็นประโยคบอกเล่าตรงๆ แทนการทวนคำถามแล้วตอบ เช่น
  "ในประเด็นที่ว่าทำให้โกโก้เสียคุณภาพหรือไม่ ตอบว่า ใช่" -> "ทำให้โกโก้เสียคุณภาพ"
  "รดน้ำเพิ่มเติมหรือไม่ ตอบว่า ไม่" -> "ไม่ได้รดน้ำเพิ่มเติม"
- ป้ายชื่อคำถามบางอันมีคำแนะนำการกรอกฟอร์มติดมาด้วย เช่น "กรอกเฉพาะหากเป็นการหมัก",
  "หากจัดการได้โปรดระบุ", "หากทำทั้งฟาร์มไม่ต้องระบุ" -- นี่คือคำแนะนำสำหรับคนกรอกฟอร์ม
  ไม่ใช่เหตุการณ์ที่เกิดขึ้นจริง ให้ตัดส่วนนี้ทิ้ง เหลือแต่เนื้อหาที่ตอบจริงเท่านั้น
- ตอบกลับเป็นข้อความไดอารี่ล้วนๆ ไม่ต้องมีคำนำหรือคำอธิบายอื่น

ข้อความต้นฉบับ:
{template_text}"""
