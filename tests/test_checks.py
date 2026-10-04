from gnn import checks

EVIDENCE = """Wind farms generated 47% of the country's electricity in September 2026, up from 38%
a year earlier, the grid operator said on 3 October. Output reached 1,486,000 megawatt hours.
"This is the highest monthly share we have recorded," said Jane Murphy of EirGrid.
The operator cautioned that one windy month does not make a trend."""


def test_numbers_pass_and_formatting_tolerance():
    draft = "Wind supplied 47% of electricity, up from 38 per cent. Output was 1,486,000 MWh."
    assert checks.check_numbers(draft, EVIDENCE).status == checks.PASS


def test_numbers_fail_on_invented_figure():
    r = checks.check_numbers("Wind supplied 52% of electricity.", EVIDENCE)
    assert r.status == checks.FAIL and "52%" in r.details[0]


def test_approximate_rounding_allowed_only_when_marked():
    assert checks.check_numbers("Output was about 1.5 million MWh.", EVIDENCE).status == checks.PASS
    assert checks.check_numbers("Output was 1.5 million MWh.", EVIDENCE).status == checks.FAIL


def test_dates_and_relative_words():
    assert checks.check_dates("Figures published on 3 October 2026.", EVIDENCE).status == checks.PASS
    assert checks.check_dates("Figures published on 4 October 2026.", EVIDENCE).status == checks.FAIL
    assert checks.check_dates("The operator said yesterday.", EVIDENCE).status == checks.FAIL
    assert checks.check_dates("It happened in 2019.", EVIDENCE).status == checks.FAIL


def test_quotes_must_be_verbatim():
    ok = 'Jane Murphy said: "This is the highest monthly share we have recorded."'
    bad = 'Jane Murphy said: "This is the best month we have ever had for wind."'
    assert checks.check_quotes(ok, EVIDENCE).status == checks.PASS
    assert checks.check_quotes(bad, EVIDENCE).status == checks.FAIL


def test_entities_warn_on_unknown_names():
    assert checks.check_entities("Jane Murphy of EirGrid welcomed it.", EVIDENCE).status == checks.PASS
    assert checks.check_entities("John Kelly of Bord Gáis welcomed it.", EVIDENCE).status == checks.WARN


def test_hype_words_need_support():
    r = checks.check_hype("A breakthrough month for wind.", EVIDENCE, ["breakthrough", "record"])
    assert r.status == checks.FAIL
    assert checks.check_hype("A strong month.", EVIDENCE, ["breakthrough"]).status == checks.PASS


def test_copying_detects_long_verbatim_runs():
    copied = "Wind farms generated 47% of the country's electricity in September 2026, up from 38% a year earlier, the grid operator said."
    assert checks.check_copying(copied, [EVIDENCE]).status == checks.FAIL
    original = "September was the windiest month on record for Irish electricity, with turbines meeting almost half of demand."
    assert checks.check_copying(original, [EVIDENCE]).status == checks.PASS


def test_structure_requires_limitations_and_primary_source():
    meta = {"dek": "x", "why_it_matters": "y", "limitations": [], "sources": [{"role": "lead"}]}
    r = checks.check_structure(meta, "word " * 200)
    assert r.status == checks.FAIL
    assert any("limitations" in d for d in r.details) and any("primary" in d for d in r.details)
