"""V&V test registry — single source of truth for test metadata.

Maps test class prefixes to (human-readable name, reference, criterion).
Used by CI summary script and available for future test tooling.

To add ISO/RiMEA tests, append entries here.
"""

TEST_MAP = {
    # IMO-inspired tests
    "TestSA01": ("SA-01 Speed in Corridor", "IMO 1", "Evacuation time ~ d/v +/- 20%"),
    "TestSA02": ("SA-02 Speed Distribution", "IMO 7", "All evacuate in [25s, 120s]"),
    "TestSA04": ("SA-04 Cornering", "IMO 6", "Navigate 90-deg corner, stay in geometry"),
    "TestRE01": ("RE-01 Single Exit Flow", "IMO 4", "Flow rate through 1m exit physically plausible"),
    "TestRE02": ("RE-02 Exit Sensitivity", "IMO 9", "Halving exits increases evacuation time"),
    "TestRE04": ("RE-04 Counterflow", "IMO 8", "Counterflow increases evacuation time"),
    # Behavior/property tests
    "TestBP02": ("BP-02 Bounds Check", "\u2014", "Agents stay within walkable area bounds"),
    "TestBP04": ("BP-04 Full Evacuation", "\u2014", "All agents evacuate within max time"),
    # RiMEA 4.1.1 tests
    "TestRiMEA01": ("RiMEA-01 Speed in Corridor", "RiMEA 4.1.1 Test 1", "Travel time at 1.33 m/s in [26, 34]s"),
    "TestRiMEA02": ("RiMEA-02 Speed Up Stairs", "RiMEA 4.1.1 Test 2", "Walking speed maintained on stairs (up)"),
    "TestRiMEA03": ("RiMEA-03 Speed Down Stairs", "RiMEA 4.1.1 Test 3", "Walking speed maintained on stairs (down)"),
    "TestRiMEA04": ("RiMEA-04 Fundamental Diagram", "RiMEA 4.1.1 Test 4", "Speed-density relation matches empirical data"),
    "TestRiMEA05": ("RiMEA-05 Premovement Time", "RiMEA 4.1.1 Test 5", "Agents respect assigned premovement delay"),
    "TestRiMEA06": ("RiMEA-06 Corner Movement", "RiMEA 4.1.1 Test 6", "Agents navigate corner without wall penetration"),
    "TestRiMEA07": ("RiMEA-07 Demographic Params", "RiMEA 4.1.1 Test 7", "Speed distribution matches age-based table"),
    "TestRiMEA08": ("RiMEA-08 Parameter Study", "RiMEA 4.1.1 Test 8", "Evacuation time varies with parameters"),
    "TestRiMEA09": ("RiMEA-09 Large Public Space", "RiMEA 4.1.1 Test 9", "Closing 2/4 exits increases evacuation time"),
    "TestRiMEA10": ("RiMEA-10 Route Allocation", "RiMEA 4.1.1 Test 10", "Agents use assigned escape routes"),
    "TestRiMEA11": ("RiMEA-11 Escape Route Choice", "RiMEA 4.1.1 Test 11", "Agents prefer closer exit"),
    "TestRiMEA12a": ("RiMEA-12a Goal Position", "RiMEA 4.1.1 Test 12a", "Closer goal yields shorter evacuation"),
    "TestRiMEA12b": ("RiMEA-12b Bottleneck Length", "RiMEA 4.1.1 Test 12b", "Longer bottleneck increases evacuation time"),
    "TestRiMEA12c": ("RiMEA-12c Congestion", "RiMEA 4.1.1 Test 12c", "Measure congestion influence at bottlenecks"),
    "TestRiMEA12d": ("RiMEA-12d Bottleneck Width", "RiMEA 4.1.1 Test 12d", "Wider bottleneck yields faster evacuation"),
    "TestRiMEA13": ("RiMEA-13 FD Stairs", "RiMEA 4.1.1 Test 13", "Down-stair speed > up-stair speed"),
    "TestRiMEA14": ("RiMEA-14 Route Choice", "RiMEA 4.1.1 Test 14", "Document short vs long route preference"),
    "TestRiMEA15": ("RiMEA-15 Large Crowd Corner", "RiMEA 4.1.1 Test 15", "Corner slows evacuation vs straight path"),
    "TestRiMEA16": ("RiMEA-16 1D Fund. Diagram", "RiMEA 4.1.1 Test 16", "1D speed-density within empirical envelope"),
}
