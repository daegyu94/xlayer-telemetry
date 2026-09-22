from xlayer_telemetry.run_summary import SUMMARY_SECTIONS, make_run_summary


def test_make_run_summary_emits_stable_top_level_schema() -> None:
    summary = make_run_summary(configuration={"component": "telemetry"})

    assert summary["schema_version"] == 1
    assert tuple(summary) == ("schema_version", *SUMMARY_SECTIONS)
    assert summary["configuration"] == {"component": "telemetry"}
    for section in SUMMARY_SECTIONS[1:]:
        assert summary[section] == {}
