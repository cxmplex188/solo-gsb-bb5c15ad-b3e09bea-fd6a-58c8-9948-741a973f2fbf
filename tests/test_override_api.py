"""HTTP-level tests for POST /override-occurrences."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body: dict):
    return client.post("/override-occurrences", json=body)


BASE = {
    "timezone": "UTC",
    "start_local": "2025-01-01T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=5",  # Jan 1 .. Jan 5, 09:00 UTC
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z",
}


# ----------------------------------------------------------------- basics ----

def test_no_overrides_behaves_like_occurrences_with_annotations():
    resp = post(BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 5
    assert [o["utc"] for o in body["occurrences"]] == [
        f"2025-01-0{day}T09:00:00Z" for day in range(1, 6)
    ]
    for occ in body["occurrences"]:
        # Untouched instances point at themselves and are marked 'keep'.
        assert occ["action"] == "keep"
        assert occ["original_local"] == occ["local"]


def test_cancel_removes_single_instance():
    resp = post(
        {
            **BASE,
            "overrides": [{"original_local": "2025-01-03T09:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [o["local"] for o in occ] == [
        "2025-01-01T09:00:00",
        "2025-01-02T09:00:00",
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
    ]
    assert all(o["action"] == "keep" for o in occ)


def test_replace_moves_instance_and_marks_result():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-03T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-03T15:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [o["local"] for o in occ] == [
        "2025-01-01T09:00:00",
        "2025-01-02T09:00:00",
        "2025-01-03T15:30:00",
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
    ]
    moved = occ[2]
    assert moved["action"] == "replace"
    assert moved["original_local"] == "2025-01-03T09:00:00"
    assert moved["utc"] == "2025-01-03T15:30:00Z"
    assert moved["source"] == "rrule"


def test_replace_to_same_wall_time_is_allowed():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T09:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert len(occ) == 5
    moved = [o for o in occ if o["action"] == "replace"]
    assert len(moved) == 1
    assert moved[0]["local"] == "2025-01-02T09:00:00"
    assert moved[0]["original_local"] == "2025-01-02T09:00:00"


def test_original_local_with_offset_is_converted_to_zone_wall():
    resp = post(
        {
            "timezone": "Asia/Shanghai",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2024-12-31T16:00:00Z",
            "interval_end": "2025-01-04T16:00:00Z",
            # 01:00 UTC == 09:00 Shanghai wall time on 2025-01-02.
            "overrides": [{"original_local": "2025-01-02T01:00:00Z", "action": "cancel"}],
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T09:00:00",
        "2025-01-03T09:00:00",
    ]


# ------------------------------------------- overrides before window clip ----

def test_replace_brings_out_of_window_instance_into_window():
    # Jan 10 is outside the [Jan 1, Jan 4) window; rescheduling it to Jan 3
    # must still return it (overrides apply before the window clip).
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=10",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-04T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-01-10T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-03T10:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["action"]) for o in occ] == [
        ("2025-01-01T09:00:00", "keep"),
        ("2025-01-02T09:00:00", "keep"),
        ("2025-01-03T09:00:00", "keep"),
        ("2025-01-03T10:00:00", "replace"),
    ]
    assert occ[3]["original_local"] == "2025-01-10T09:00:00"


def test_replace_moves_in_window_instance_out_of_window():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-20T09:00:00",  # outside the window
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [o["local"] for o in occ] == [
        "2025-01-01T09:00:00",
        "2025-01-03T09:00:00",
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
    ]


def test_cancel_out_of_window_instance_is_accepted():
    # The instance exists in the recurrence set even though it is outside the
    # query window, so cancelling it is valid (and simply changes nothing).
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=10",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-04T00:00:00Z",
            "overrides": [{"original_local": "2025-01-10T09:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T09:00:00",
        "2025-01-02T09:00:00",
        "2025-01-03T09:00:00",
    ]


# ------------------------------------------------------- DTSTART and RDATE ----

def test_override_dtstart_when_no_rrule():
    base = {
        "timezone": "UTC",
        "start_local": "2025-01-01T09:00:00",
        "interval_start": "2025-01-01T00:00:00Z",
        "interval_end": "2025-01-10T00:00:00Z",
    }
    resp = post(
        {
            **base,
            "overrides": [
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T11:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    (occ,) = resp.json()["occurrences"]
    assert occ["local"] == "2025-01-02T11:00:00"
    assert occ["action"] == "replace"
    assert occ["original_local"] == "2025-01-01T09:00:00"

    resp = post(
        {**base, "overrides": [{"original_local": "2025-01-01T09:00:00", "action": "cancel"}]}
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_override_rdate_instance_keeps_rdate_source():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",  # outside the query window
            "rdate": ["2025-01-05T08:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-01-05T08:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-06T08:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    (occ,) = resp.json()["occurrences"]
    assert occ["local"] == "2025-01-06T08:00:00"
    assert occ["source"] == "rdate"
    assert occ["action"] == "replace"
    assert occ["original_local"] == "2025-01-05T08:00:00"


# ----------------------------------------------------------------- errors ----

def test_unknown_original_local_is_invalid_override():
    resp = post(
        {
            **BASE,
            "overrides": [{"original_local": "2025-02-01T09:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_override"
    assert "2025-02-01T09:00:00" in body["error"]["message"]


def test_original_local_must_match_exact_wall_time():
    # 09:30 is not an instance of the 09:00 daily rule.
    resp = post(
        {
            **BASE,
            "overrides": [{"original_local": "2025-01-02T09:30:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_duplicate_override_is_rejected():
    resp = post(
        {
            **BASE,
            "overrides": [
                {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T10:00:00",
                },
            ],
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_duplicate_detected_after_offset_conversion():
    # Same wall time written naively and with an offset (UTC zone here).
    resp = post(
        {
            **BASE,
            "overrides": [
                {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
                {"original_local": "2025-01-02T09:00:00Z", "action": "cancel"},
            ],
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_exdate_excluded_instance_cannot_be_overridden():
    resp = post(
        {
            **BASE,
            "exdate": ["2025-01-02T09:00:00"],
            "overrides": [{"original_local": "2025-01-02T09:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_override"
    assert "EXDATE" in body["error"]["message"]


def test_exdate_excluded_rdate_cannot_be_overridden():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-01-05T08:00:00"],
            "exdate": ["2025-01-05T08:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-02-01T00:00:00Z",
            "overrides": [{"original_local": "2025-01-05T08:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_replace_without_replacement_local_is_validation_error():
    resp = post(
        {
            **BASE,
            "overrides": [{"original_local": "2025-01-02T09:00:00", "action": "replace"}],
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_cancel_with_replacement_local_is_validation_error():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "cancel",
                    "replacement_local": "2025-01-03T09:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unknown_action_is_validation_error():
    resp = post(
        {
            **BASE,
            "overrides": [{"original_local": "2025-01-02T09:00:00", "action": "move"}],
        }
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# -------------------------------------------------------------- dst_policy ----

def test_replacement_in_gap_is_skipped_by_default():
    # 02:30 does not exist on 2025-03-09 in New York (jump 02:00 -> 03:00).
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-08T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-03-09T02:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-03-09T09:00:00",
        "2025-03-10T09:00:00",
    ]


def test_replacement_in_gap_shifted_forward_by_policy():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-08T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-03-09T02:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["utc"], o["action"]) for o in occ] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z", "replace"),  # shifted
        ("2025-03-09T09:00:00", "2025-03-09T13:00:00Z", "keep"),
        ("2025-03-10T09:00:00", "2025-03-10T13:00:00Z", "keep"),
    ]
    assert occ[0]["original_local"] == "2025-03-08T09:00:00"
    assert occ[0]["utc_offset"] == "-04:00"


def test_replacement_in_fold_returns_both_passes():
    # 01:30 occurs twice on 2025-11-02 in New York (fall-back).
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=2",
            "interval_start": "2025-11-01T00:00:00Z",
            "interval_end": "2025-11-04T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-11-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-11-02T01:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    replaced = [o for o in occ if o["action"] == "replace"]
    assert [(o["utc"], o["fold"]) for o in replaced] == [
        ("2025-11-02T05:30:00Z", 0),
        ("2025-11-02T06:30:00Z", 1),
    ]
    assert all(o["original_local"] == "2025-11-01T09:00:00" for o in replaced)


def test_replacement_fold_policy_selects_single_pass():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=2",
            "dst_policy": {"fold": "later"},
            "interval_start": "2025-11-01T00:00:00Z",
            "interval_end": "2025-11-04T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-11-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-11-02T01:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    replaced = [o for o in resp.json()["occurrences"] if o["action"] == "replace"]
    assert [(o["utc"], o["fold"]) for o in replaced] == [("2025-11-02T06:30:00Z", 1)]


# ---------------------------------------------------- dedupe and conflicts ----

def test_replacement_wins_collision_with_regular_instance():
    # Move Jan 1 onto Jan 2 09:00, where a regular instance already sits.
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T09:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["action"]) for o in occ] == [
        ("2025-01-02T09:00:00", "replace"),
        ("2025-01-03T09:00:00", "keep"),
        ("2025-01-04T09:00:00", "keep"),
        ("2025-01-05T09:00:00", "keep"),
    ]
    assert occ[0]["original_local"] == "2025-01-01T09:00:00"


def test_cancel_frees_slot_for_another_replacement():
    resp = post(
        {
            **BASE,
            "overrides": [
                {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T09:00:00",
                },
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [(o["local"], o["action"]) for o in occ] == [
        ("2025-01-02T09:00:00", "replace"),
        ("2025-01-03T09:00:00", "keep"),
        ("2025-01-04T09:00:00", "keep"),
        ("2025-01-05T09:00:00", "keep"),
    ]


def test_two_replacements_on_same_instant_first_wins():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-05T12:00:00",
                },
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-05T12:00:00",
                },
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    moved = [o for o in occ if o["action"] == "replace"]
    assert len(moved) == 1
    assert moved[0]["local"] == "2025-01-05T12:00:00"
    assert moved[0]["original_local"] == "2025-01-01T09:00:00"
    assert [o["local"] for o in occ] == [
        "2025-01-03T09:00:00",
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
        "2025-01-05T12:00:00",
    ]


def test_results_sorted_by_utc_after_overrides():
    resp = post(
        {
            **BASE,
            "overrides": [
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-04T08:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    utcs = [o["utc"] for o in occ]
    assert utcs == sorted(utcs)
    assert [o["local"] for o in occ] == [
        "2025-01-02T09:00:00",
        "2025-01-03T09:00:00",
        "2025-01-04T08:00:00",
        "2025-01-04T09:00:00",
        "2025-01-05T09:00:00",
    ]


def test_exdate_still_applies_to_untouched_instances():
    resp = post(
        {
            **BASE,
            "exdate": ["2025-01-04T09:00:00"],
            "overrides": [{"original_local": "2025-01-02T09:00:00", "action": "cancel"}],
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T09:00:00",
        "2025-01-03T09:00:00",
        "2025-01-05T09:00:00",
    ]
