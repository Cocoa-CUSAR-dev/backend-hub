"""Page/page_size query parameters, read the way mobile-backend read them.

Out-of-range values fall back instead of failing, as Go's paginationParams
did: a negative page means the first, a size outside 1..500 means 50 or 500.
"""

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def window(page: int, page_size: int) -> tuple[int, int]:
    """(limit, offset) for a zero-based page."""
    if page_size <= 0:
        page_size = DEFAULT_PAGE_SIZE
    page_size = min(page_size, MAX_PAGE_SIZE)
    return page_size, max(page, 0) * page_size
