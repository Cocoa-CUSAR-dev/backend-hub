"""src/diary/writing.py: the template, the fact-guard and the prompt, as
web-backend's DiaryTemplateRenderer and DiaryFactGuard wrote and checked them."""

from src.diary import writing
from src.diary.writing import Fact

FARM = Fact("farm_id", "ฟาร์ม", "สวนโกโก้ชุมพร")
PLOT = Fact("plot_id", "แปลง", "แปลง A")
KG = Fact("quantity_kg", "น้ำหนัก (กก.)", "12.5")
DRY = Fact("is_dry", "แห้งหรือไม่", "ใช่")


def test_template_is_one_bullet_per_fact_in_order() -> None:
    assert writing.render([PLOT, FARM, KG]) == (
        "- แปลง: แปลง A\n- ฟาร์ม: สวนโกโก้ชุมพร\n- น้ำหนัก (กก.): 12.5"
    )


def test_no_plot_answered_means_the_whole_farm() -> None:
    assert writing.render([FARM]).splitlines() == [writing.WHOLE_FARM_LINE, "- ฟาร์ม: สวนโกโก้ชุมพร"]


def test_nothing_answered_is_no_text() -> None:
    assert writing.render([]) == ""


def test_yes_no_reads_as_thai() -> None:
    assert (writing.yes_no("true"), writing.yes_no("false"), writing.yes_no("maybe")) == (
        "ใช่",
        "ไม่",
        "maybe",
    )


def test_the_polish_must_keep_every_value_but_yes_and_no() -> None:
    facts = [FARM, KG, DRY]
    assert writing.preserves_facts(facts, "ที่สวนโกโก้ชุมพร ได้ 12.5 กิโล และแห้งแล้ว")
    assert not writing.preserves_facts(facts, "ที่สวนโกโก้ชุมพร ได้ 13 กิโล")  # number changed
    assert not writing.preserves_facts(facts, "ได้ 12.5 กิโล")  # the farm dropped
    assert not writing.preserves_facts(facts, "   ")


def test_prompt_carries_the_template_and_kotlins_rules() -> None:
    prompt = writing.polish_prompt("- ฟาร์ม: สวน")
    assert prompt.endswith("ข้อความต้นฉบับ:\n- ฟาร์ม: สวน")
    assert "ห้ามเพิ่มข้อเท็จจริงใหม่" in prompt
