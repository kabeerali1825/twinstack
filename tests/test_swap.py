"""Service Swap's repeat check: an idea with the same size but another tier is new, not a repeat."""
from app.agents.swap import _key


def test_tier_is_part_of_the_repeat_check():
    bc = _key("c4", "SQL Database", "8 vCore", "Business Critical, 8 vCore, Gen5, zone redundant, 250 GB")
    gp = _key("c4", "SQL Database", "8 vCore", "General Purpose provisioned, zone redundant, 250 GB")
    assert bc != gp  # seen live: GP 8 vCore was dropped as a repeat of today's BC 8 vCore
    assert gp == _key("c4", "SQL Database", "8 VCore", "250 GB, general purpose")  # true repeat, other wording
    # 'Standard_D4ds_v5' is a size, not the Standard tier
    assert _key("c1", "Virtual Machines", "Standard_D4ds_v5", "") == _key("c1", "Virtual Machines", "Standard D4ds v5", "")
