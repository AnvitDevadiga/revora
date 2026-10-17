from app.release_controller import ReleaseController


def test_real_gateway_rolls_back_and_uses_persistent_incident_memory():
    controller = ReleaseController()
    try:
        first = controller.run("candidate-17", "mixed")
        assert first["decision"] == "rollback"
        assert first["metrics"]["sample_count"] == 12
        assert first["metrics"]["error_rate"] > 0
        assert first["canary_samples"]
        assert first["recovered"] is True
        assert first["before"]["status"] == 200
        assert first["after"]["route"] == "stable"
        assert controller.status()["gateway_probe"]["version"] == "stable"

        second = controller.run("candidate-17", "mixed")
        assert second["decision"] == "blocked_from_memory"
        assert second["prior_evidence"]["id"] == first["id"]
        assert second["canary_samples"] == []
        assert second["after"]["status"] == 200

        repaired = controller.run("candidate-17", "healthy", force_recheck=True)
        assert repaired["decision"] == "promote"
        assert repaired["recheck_override"] is True
        assert repaired["canary_samples"][0]["route"] is None
        assert repaired["prior_evidence"]["id"] == first["id"]
        assert controller._prior_failure("revora-demo", "candidate-17") is None

        third = controller.run("candidate-18", "healthy")
        assert third["decision"] == "promote"
        assert third["metrics"]["error_rate"] == 0
        assert third["after"]["route"] == "candidate"
        assert controller.status()["gateway_probe"]["version"] == "candidate-18"

        controller.run("candidate-19", "mixed")
        controller.run("candidate-18", "healthy")
        blocked = controller.run("candidate-19", "mixed")
        assert blocked["decision"] == "blocked_from_memory"
        assert blocked["after"]["route"] == "stable"
        assert blocked["after"]["version"] == "stable"
    finally:
        controller.close()


def test_failed_version_force_recheck_stays_off_live_route_until_preflight_passes():
    controller = ReleaseController()
    try:
        failed = controller.run("known-bad", "errors")
        assert failed["decision"] == "rollback"
        recheck = controller.run("known-bad", "errors", force_recheck=True)
        assert recheck["decision"] == "rollback"
        assert recheck["recheck_override"] is True
        assert recheck["after"]["route"] == "stable"
        assert all(sample["route"] is None for sample in recheck["canary_samples"])
        assert len(recheck["canary_samples"]) == 12
        controller.candidate_mode = "healthy"
        repaired = controller.run("known-bad", "healthy", force_recheck=True)
        assert repaired["decision"] == "promote"
        assert repaired["after"]["route"] == "candidate"
    finally:
        controller.close()


def test_staged_incident_exposes_real_503_until_recovery():
    controller = ReleaseController()
    try:
        onset = controller.inject("staged-v1", "errors")
        assert onset["before"]["status"] == 200
        assert onset["during"]["status"] == 503
        assert controller.status()["incident_active"] is True
        assert controller.status()["gateway_probe"]["status"] == 503
        fixed = controller.recover()
        assert fixed["before"]["status"] == 200
        assert fixed["during"]["status"] == 503
        assert fixed["after"]["status"] == 200
        assert fixed["recovered"] is True
        assert fixed["prior_evidence"]["id"] == onset["id"]
        assert len(fixed["recovery_samples"]) == 3
        assert controller.status()["incident_active"] is False
        assert controller.status()["gateway_probe"]["status"] == 200
        blocked = controller.inject("staged-v1", "errors")
        assert blocked["decision"] == "blocked_from_memory"
        assert blocked["canary_samples"] == []
    finally:
        controller.close()


def test_measured_slow_candidate_rolls_back():
    controller = ReleaseController()
    try:
        result = controller.run("slow-1", "slow")
        assert result["metrics"]["p95_latency_ms"] > result["metrics"]["max_p95_latency_ms"]
        assert result["decision"] == "rollback"
        assert result["recovered"] is True
    finally:
        controller.close()


def test_configured_service_is_probed_before_route_changes():
    fixture = ReleaseController()
    controller = ReleaseController(stable_url=fixture.stable_url, candidate_url=fixture.candidate_url)
    try:
        with fixture.lock:
            fixture.candidate_mode = "mixed"
        failed = controller.run("external-v1", "healthy")
        assert failed["decision"] == "rollback"
        assert failed["metrics"]["error_rate"] > 0
        assert all(item["route"] == "candidate" for item in failed["canary_samples"])
        assert controller.status()["gateway_probe"]["version"] == "stable"

        with fixture.lock:
            fixture.candidate_mode = "healthy"
        passed = controller.run("external-v1", "healthy", force_recheck=True)
        assert passed["decision"] == "promote"
        assert passed["prior_evidence"]["id"] == failed["id"]
        assert controller.status()["active_route"] == "candidate"
        assert controller.park()["active_route"] == "stable"
    finally:
        controller.close()
        fixture.close()


def test_unreachable_external_target_returns_structured_unhealthy_probe(monkeypatch):
    from urllib.error import URLError
    from app import release_controller as module

    def unavailable(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(module, "urlopen", unavailable)
    result = ReleaseController._probe("http://unreachable.invalid")
    assert result["status"] == 503
    assert result["version"] == "unknown"
    assert result["route"] is None
    assert result["probe_error"] == "URLError"


def test_malformed_persisted_diagnosis_returns_clean_conflict_without_clearing_incident():
    import pytest

    controller = ReleaseController()
    try:
        onset = controller.inject("malformed-diagnosis-v1", "errors")
        bad_assessment = {"id": "bad-diagnosis", "decision": "diagnosed",
                          "onset_id": onset["id"], "service": onset["service"],
                          "version": onset["version"], "timestamp": onset["timestamp"]}
        with pytest.raises(ValueError, match="canary samples"):
            controller.recover(expected_onset_id=onset["id"], handoff_id="bad-handoff",
                               assessment=bad_assessment)
        assert controller.pending["id"] == onset["id"]
        assert controller.route == "candidate"
        assert controller.recover()["recovered"] is True
    finally:
        controller.close()
