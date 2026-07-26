"""§10: the timezone database is a pinned dependency, not the host's.

Every DST fixture in this suite is an assertion about a specific release of the
tz database. If the pin moves, those fixtures may still pass while describing a
world that no longer exists — Ukraine debated abolishing DST while this was
being written, and the day that lands, `2027-03-28 01:00Z` stops being a
transition at all.

So the version is asserted in one place, and moving it is a deliberate change
that costs a re-run of the fixtures rather than a silent `uv lock`.
"""

from __future__ import annotations

import tomllib
import zoneinfo
from datetime import UTC, datetime, timedelta
from importlib import resources
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.domain.identity import UnknownTimezone
from app.domain.reminders import zone_for

API_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TZDATA = "2026.3"


def test_the_installed_timezone_database_is_the_pinned_one() -> None:
    assert version("tzdata") == EXPECTED_TZDATA


def test_the_pin_in_the_manifest_says_the_same_thing() -> None:
    """Otherwise this test only proves what the environment happens to hold."""
    manifest = tomllib.loads((API_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = manifest["project"]["dependencies"]

    assert f"tzdata=={EXPECTED_TZDATA}" in dependencies


def test_the_host_database_cannot_override_the_pinned_one(
    tmp_path: Path,
) -> None:
    """A pin the runtime ignores is not a pin, and this is how you tell.

    `ZoneInfo(name)` searches `TZPATH` first, and `/usr/share/zoneinfo` exists
    on macOS, on `ubuntu-latest` and in every Debian image — so the plain
    constructor reads the **host** database and the pin above would decide
    nothing. `zone_for` loads out of the package instead.

    Asserting that as «two different objects» would pass on two identical
    databases and prove nothing. So the host database is replaced by one that
    is *wrong on purpose*: Tokyo's rules filed under Kyiv's name. The plain
    constructor takes the bait; `zone_for` must not.
    """
    planted = tmp_path / "Europe"
    planted.mkdir()
    tokyo = resources.files("tzdata.zoneinfo.Asia").joinpath("Tokyo").read_bytes()
    (planted / "Kyiv").write_bytes(tokyo)

    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    ZoneInfo.clear_cache()
    try:
        winter = datetime(2026, 1, 15, 12, tzinfo=UTC)
        misled = ZoneInfo("Europe/Kyiv")
        honest = zone_for("Europe/Kyiv")

        assert winter.astimezone(misled).utcoffset() == timedelta(hours=9)
        assert winter.astimezone(honest).utcoffset() == timedelta(hours=2)
    finally:
        zoneinfo.reset_tzpath()
        ZoneInfo.clear_cache()


def test_a_name_the_pinned_database_does_not_ship_is_unknown() -> None:
    """The membership test is also the traversal guard on the resource lookup."""
    for name in ("Mars/Olympus", "../../etc/passwd", "Europe/Kyiv/../Kyiv"):
        with pytest.raises(UnknownTimezone):
            zone_for(name)


def test_a_deprecated_alias_is_accepted_and_never_canonicalised() -> None:
    """§10: aliases resolve, and the name the user gave is the name we keep."""
    alias = zone_for("Europe/Kiev")

    assert str(alias) == "Europe/Kiev"


def test_the_pinned_database_still_carries_the_fixtures_the_suite_asserts() -> None:
    """The reason the version is pinned at all, stated as an assertion.

    Ukraine has debated abolishing seasonal clock changes. The day that lands
    in the tz database, these two instants stop being transitions — and the DST
    tests would keep passing against a host database that still had them.
    """
    zone = zone_for("Europe/Kyiv")
    before_spring = datetime(2026, 3, 28, 12, tzinfo=UTC).astimezone(zone)
    after_spring = datetime(2026, 3, 30, 12, tzinfo=UTC).astimezone(zone)
    before_autumn = datetime(2026, 10, 24, 12, tzinfo=UTC).astimezone(zone)
    after_autumn = datetime(2026, 10, 26, 12, tzinfo=UTC).astimezone(zone)

    assert before_spring.utcoffset() != after_spring.utcoffset()
    assert before_autumn.utcoffset() != after_autumn.utcoffset()
