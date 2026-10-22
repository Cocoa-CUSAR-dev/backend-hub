"""The farmer's own plots, for the plot question in the multi-choice picker.

Every other OPTION question in the picker uses its own choices as Kotlin sent
them; the plot question is the one exception, for the reason below.

Why this exists at all: the plot question's choices normally come from Kotlin
(`FormRepository.fetchRefChoices` -> `SELECT plot_id, plot_name FROM
ref.plot_constant`), which is EVERY plot in the system, not this farmer's.
That is survivable for a one-of-many Quick Reply -- a farmer scrolls past
other people's plots -- but it is not survivable for a picker whose whole
point is tapping several plots in one go: the list has to be short, and every
row in it has to be one the farmer could legitimately file an activity
against. So this picker reads the farmer's plots directly, scoped through
agriculture.farmer_farm, exactly the way src/line/parent_picker.py already
scopes its parent rows.

Deliberate, permanent exception to "the chatbot never touches form.* directly"
(ADR 0001), and the same one parent_picker.py documents: agriculture.* is a
domain Go owns for writes but never exposed a scoped read API for. Note the
narrower scope than fixing the global list everywhere -- the ref.plot_constant
problem on ordinary plot questions is a real issue, but a separate one (see
the design doc's open questions).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from src.config import settings
from src.database import Session

# Fields whose choices are scoped to the farmer's own rows (US2-13). Kotlin
# sends both as global lists (ref.farm_constant / ref.plot_constant).
FARMER_SCOPED_FIELDS = frozenset({"farm_id", "plot_id"})

# A Flex bubble stays readable at roughly a dozen buttons, and the carousel
# the builder falls back to is capped at 12 bubbles by LINE itself. This cap
# is on the DATA, so the builder never has to silently drop a plot a farmer
# can see in the count -- see build_multi_choice_flex, which logs if it ever
# has to.
MAX_PLOTS = 60


@dataclass(frozen=True)
class PlotOption:
    """One tappable plot. `label` is what the farmer reads on the button and
    what ends up in the stored answer's text, so it already carries the farm
    name when it needs to disambiguate (see list_plots).
    """

    id: str
    label: str


async def list_plots(
    session: Session, user_id: UUID, *, farm_id: str | None = None
) -> list[PlotOption]:
    """Every plot on a farm this farmer is linked to, newest farm/plot name
    order, optionally narrowed to one farm.

    `farm_id` is the answer the farmer already gave to this form's own farm
    question, when it has one and it comes before the plot question -- in
    that case showing plots from their OTHER farms would be offering an
    answer that contradicts what they just said. With no farm answer (the
    common case on "จดกิจกรรมในสวน", which has no farm question) it stays
    None and every plot of theirs is listed.

    The farm name is appended to the label only when the farmer actually has
    plots on more than one farm -- "แปลง 1" is ambiguous across two farms, but
    adding "(ไร่เหนือ)" to every button of a single-farm farmer is noise on a
    button whose text budget is already tight.
    """
    # US2-13: PLOT_SCOPE_MODE=all drops the farmer scoping (dev/test accounts
    # aren't linked to any farm). The farm narrowing still applies -- a plot
    # of another farm would contradict the farm the farmer just picked.
    scope_to_farmer = settings.PLOT_SCOPE_MODE == "farmer"
    rows = (
        await session.execute(
            text(
                """
                SELECT p.plot_id, p.plot_name, f.farm_name
                FROM agriculture.plot p
                JOIN agriculture.farm f ON f.farm_id = p.farm_id
                WHERE (
                    NOT CAST(:scope_to_farmer AS boolean)
                    OR EXISTS (
                        SELECT 1 FROM agriculture.farmer_farm ff
                        WHERE ff.farm_id = p.farm_id AND ff.farmer_id = CAST(:user_id AS uuid)
                    )
                )
                  AND (CAST(:farm_id AS uuid) IS NULL OR p.farm_id = CAST(:farm_id AS uuid))
                ORDER BY f.farm_name, p.plot_name
                """
            ),
            {"user_id": str(user_id), "farm_id": farm_id, "scope_to_farmer": scope_to_farmer},
        )
    ).all()

    farm_names = {row.farm_name for row in rows}
    show_farm = len(farm_names) > 1
    options = []
    for row in rows:
        name = row.plot_name or "แปลงไม่มีชื่อ"
        label = f"{name} ({row.farm_name})" if show_farm and row.farm_name else name
        options.append(PlotOption(id=str(row.plot_id), label=label))
    return options[:MAX_PLOTS]


async def farmer_owned_ids(session: Session, user_id: UUID, field_name: str) -> set[str] | None:
    """The ids of `field_name`'s choices this farmer may pick: the farms
    they're linked to through agriculture.farmer_farm, or those farms' plots.

    None means "don't narrow": PLOT_SCOPE_MODE=all, or a field this scoping
    doesn't apply to. An empty set is a real answer -- a farmer linked to no
    farm is offered none, rather than everyone's (US2-13; until now only the
    multi-choice plot picker above was scoped, and every ordinary farm/plot
    question showed every farm and plot in the system).
    """
    if settings.PLOT_SCOPE_MODE != "farmer" or field_name not in FARMER_SCOPED_FIELDS:
        return None
    if field_name == "farm_id":
        query = """
            SELECT CAST(ff.farm_id AS text) AS id FROM agriculture.farmer_farm ff
            WHERE ff.farmer_id = CAST(:user_id AS uuid)
        """
    else:
        query = """
            SELECT CAST(p.plot_id AS text) AS id FROM agriculture.plot p
            JOIN agriculture.farmer_farm ff ON ff.farm_id = p.farm_id
            WHERE ff.farmer_id = CAST(:user_id AS uuid)
        """
    rows = await session.execute(text(query), {"user_id": str(user_id)})
    return {row.id for row in rows}
