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
    "rrule": "FREQ=DAILY;COUNT=5",
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z",
}


# ----------------------------------------------------------------- basics ----

def test_no_overrides_matches_plain_expansion_with_keep_markers():
    resp = post(BASE)
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert len(occ) == 5
    assert all(o["action"] == "keep" for o in occ)
    # original_local equals the occurrence's own wall time for untouched items.
    assert all(o["original_local"] == o["local"] for o in occ)
    utcs = [o["utc"] for o in occ]
    assert utcs == sorted(utcs)


def test_cancel_drops_one_instance():
    resp = post({**BASE, "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert [o["utc"] for o in occ] == [
        "2025-01-01T09:00:00Z",
        "2025-01-03T09:00:00Z",
        "2025-01-04T09:00:00Z",
        "2025-01-05T09:00:00Z",
    ]
    assert all(o["action"] == "keep" for o in occ)
    assert "2025-01-02T09:00:00" not in [o["original_local"] for o in occ]


def test_replace_reschedules_and_marks_original():
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-02T14:00:00",
        },
    ]})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    replaced = [o for o in occ if o["action"] == "replace"]
    assert len(replaced) == 1
    (r,) = replaced
    assert r["local"] == "2025-01-02T14:00:00"
    assert r["utc"] == "2025-01-02T14:00:00Z"
    assert r["original_local"] == "2025-01-02T09:00:00"
    assert r["source"] == "rrule"
    # The old instant is gone; the rest survive as keeps.
    assert "2025-01-02T09:00:00Z" not in [o["utc"] for o in occ]
    keeps = [o for o in occ if o["action"] == "keep"]
    assert [o["utc"] for o in keeps] == [
        "2025-01-01T09:00:00Z",
        "2025-01-03T09:00:00Z",
        "2025-01-04T09:00:00Z",
        "2025-01-05T09:00:00Z",
    ]


def test_replace_can_target_dtstart_without_rrule():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-03-01T08:00:00",
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-02T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-01T08:00:00",
                    "action": "replace",
                    "replacement_local": "2025-03-01T10:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert (o["utc"], o["action"], o["original_local"], o["source"]) == (
        "2025-03-01T10:30:00Z",
        "replace",
        "2025-03-01T08:00:00",
        "rrule",
    )


def test_replace_targets_rdate_instance_and_keeps_rdate_source():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",  # outside window
            "rdate": ["2025-02-01T08:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-03-01T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-02-01T08:00:00",
                    "action": "replace",
                    "replacement_local": "2025-02-01T15:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["source"] == "rdate"
    assert o["action"] == "replace"
    assert o["utc"] == "2025-02-01T15:00:00Z"
    assert o["original_local"] == "2025-02-01T08:00:00"


# --------------------------------------- overrides apply before the window ----

def test_original_outside_window_can_be_replaced_into_window():
    # DTSTART and the first instances are before the window; replacing the
    # out-of-window instance with an in-window wall time must return it.
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-30T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=5",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2024-12-30T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-05T08:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    moved = [o for o in occ if o["action"] == "replace"]
    assert len(moved) == 1
    assert moved[0]["utc"] == "2025-01-05T08:00:00Z"
    assert moved[0]["original_local"] == "2024-12-30T09:00:00"


def test_replacement_outside_window_is_clipped_away():
    # The original is inside the window, the replacement after its end: the
    # instance must simply disappear from the response.
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-02-02T09:00:00",
        },
    ]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 4
    assert all(o["action"] == "keep" for o in resp.json()["occurrences"])


def test_replacement_beyond_naive_wall_margin_still_found_via_utc():
    # Replacement wall time is much later than the naive wall window plus its
    # 2-day margin, but the original is in the set; the replacement must still
    # be resolved and kept when its UTC instant is inside the window.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-06-01T12:00:00",
            "rdate": ["2025-06-10T12:00:00"],
            "interval_start": "2025-06-04T00:00:00Z",
            "interval_end": "2025-06-09T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-06-10T12:00:00",
                    "action": "replace",
                    "replacement_local": "2025-06-05T12:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["utc"] == "2025-06-05T16:00:00Z"
    assert o["action"] == "replace"
    assert o["original_local"] == "2025-06-10T12:00:00"


def test_cancel_of_outside_window_instance_is_still_valid_but_changes_nothing():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-30T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=5",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
            "overrides": [
                {"original_local": "2024-12-30T09:00:00", "action": "cancel"}
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    # Dec 30/31 instances are outside the window; the cancel is still accepted
    # (the original exists in the set) and simply changes nothing visible.
    assert [o["utc"] for o in occ] == [
        "2025-01-01T09:00:00Z",
        "2025-01-02T09:00:00Z",
        "2025-01-03T09:00:00Z",
    ]


# ---------------------------------------------------- UTC sort & de-dupe ------

def test_results_sorted_by_utc_after_reschedule():
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-04T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-01T06:00:00",
        },
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-03T18:00:00",
        },
    ]})
    assert resp.status_code == 200
    utcs = [o["utc"] for o in resp.json()["occurrences"]]
    assert utcs == sorted(utcs)
    assert utcs == [
        "2025-01-01T06:00:00Z",
        "2025-01-01T09:00:00Z",
        "2025-01-03T09:00:00Z",
        "2025-01-03T18:00:00Z",
        "2025-01-05T09:00:00Z",
    ]


def test_rescheduled_instance_wins_collision_with_regular_rrule_instance():
    # Replacing 01-02 09:00 with 01-03 09:00 collides with the regular
    # 01-03 instance; the rescheduled one must win and carry original 01-02.
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-03T09:00:00",
        },
    ]})
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    at = [o for o in occ if o["utc"] == "2025-01-03T09:00:00Z"]
    assert len(at) == 1
    assert at[0]["action"] == "replace"
    assert at[0]["original_local"] == "2025-01-02T09:00:00"
    assert resp.json()["count"] == 4


def test_rescheduled_instance_wins_collision_with_rdate():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rdate": ["2025-01-02T12:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-01-01T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-02T12:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    assert len(occ) == 1
    assert occ[0]["utc"] == "2025-01-02T12:00:00Z"
    assert occ[0]["action"] == "replace"
    assert occ[0]["original_local"] == "2025-01-01T09:00:00"


def test_two_rescheduled_instances_colliding_keep_earlier_original():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-01-03T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-05T10:00:00",
                },
                {
                    "original_local": "2025-01-02T09:00:00",
                    "action": "replace",
                    "replacement_local": "2025-01-05T10:00:00",
                },
            ],
        }
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"]
    at = [o for o in occ if o["utc"] == "2025-01-05T10:00:00Z"]
    assert len(at) == 1
    assert at[0]["original_local"] == "2025-01-02T09:00:00"


# ------------------------------------------------------------ EXDATE rules ----

def test_exdate_and_cancel_both_remove():
    resp = post({**BASE, "exdate": ["2025-01-02T09:00:00"], "overrides": [
        {"original_local": "2025-01-03T09:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 200
    assert [o["utc"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T09:00:00Z",
        "2025-01-04T09:00:00Z",
        "2025-01-05T09:00:00Z",
    ]


def test_override_on_exdated_instance_is_structured_error():
    resp = post({**BASE, "exdate": ["2025-01-02T09:00:00"], "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "invalid_override"
    (detail,) = err["details"]
    assert detail["field"] == "overrides[0].original_local"
    assert "EXDATE" in detail["reason"]


def test_override_on_exdated_rdate_is_error():
    resp = post(
        {
            "timezone": "UTC",
            "start_local": "2024-12-01T00:00:00",
            "rdate": ["2025-02-01T08:00:00"],
            "exdate": ["2025-02-01T08:00:00"],
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-03-01T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-02-01T08:00:00",
                    "action": "replace",
                    "replacement_local": "2025-02-01T09:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_exdate_does_not_match_replacement_wall_time():
    # EXDATE at the *replacement* wall must not remove the rescheduled
    # instance; EXDATE only matches the originals during the regular pass.
    resp = post({**BASE, "exdate": ["2025-01-02T14:00:00"], "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-02T14:00:00",
        },
    ]})
    assert resp.status_code == 200
    assert any(
        o["utc"] == "2025-01-02T14:00:00Z" and o["action"] == "replace"
        for o in resp.json()["occurrences"]
    )


# ----------------------------------------------------- unknown / duplicates ----

def test_unknown_original_local_is_structured_error():
    resp = post({**BASE, "overrides": [
        {"original_local": "2025-01-02T10:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "invalid_override"
    (detail,) = err["details"]
    assert detail["field"] == "overrides[0].original_local"
    assert detail["value"] == "2025-01-02T10:00:00"


def test_unknown_original_matching_no_instances_anywhere():
    # A wall time the daily-at-09:00 rule can never produce, even outside the
    # window, must be rejected.
    resp = post({**BASE, "overrides": [
        {"original_local": "2030-01-02T09:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_override"


def test_duplicate_original_local_is_structured_error():
    resp = post({**BASE, "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "2025-01-02T11:00:00",
        },
    ]})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "invalid_override"
    reasons = [d["reason"] for d in err["details"]]
    assert any("duplicate" in r for r in reasons)
    assert [d["field"] for d in err["details"]] == ["overrides[1].original_local"]


def test_multivalidated_errors_reported_together():
    resp = post({**BASE, "exdate": ["2025-01-03T09:00:00"], "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
        {"original_local": "2025-01-02T09:00:00", "action": "cancel"},
        {"original_local": "2025-01-03T09:00:00", "action": "cancel"},
        {"original_local": "2025-09-09T09:00:00", "action": "cancel"},
    ]})
    assert resp.status_code == 400
    details = resp.json()["error"]["details"]
    fields = {d["field"] for d in details}
    assert fields == {
        "overrides[1].original_local",
        "overrides[2].original_local",
        "overrides[3].original_local",
    }


# ----------------------------------------------------- request schema errors ----

def test_replace_without_replacement_local_is_422():
    resp = post({**BASE, "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "replace"},
    ]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_cancel_with_replacement_local_is_422():
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "cancel",
            "replacement_local": "2025-01-02T11:00:00",
        },
    ]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_action_value_is_422():
    resp = post({**BASE, "overrides": [
        {"original_local": "2025-01-02T09:00:00", "action": "move"},
    ]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_original_datetime_is_invalid_datetime():
    resp = post({**BASE, "overrides": [
        {"original_local": "not-a-date", "action": "cancel"},
    ]})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_datetime"


def test_bad_replacement_datetime_is_invalid_datetime():
    resp = post({**BASE, "overrides": [
        {
            "original_local": "2025-01-02T09:00:00",
            "action": "replace",
            "replacement_local": "nonsense",
        },
    ]})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "invalid_datetime"
    assert err["details"][0]["field"] == "overrides[0].replacement_local"


def test_override_accepts_aware_strings_converted_to_zone_wall():
    # 2025-01-02T01:00:00Z == 2025-01-02T09:00:00 in Shanghai wall time.
    resp = post(
        {
            "timezone": "Asia/Shanghai",
            "start_local": "2025-01-01T09:00:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-01-01T00:00:00Z",
            "interval_end": "2025-01-10T00:00:00Z",
            "overrides": [
                {"original_local": "2025-01-02T01:00:00Z", "action": "cancel"}
            ],
        }
    )
    assert resp.status_code == 200
    assert [o["local"] for o in resp.json()["occurrences"]] == [
        "2025-01-01T09:00:00",
        "2025-01-03T09:00:00",
    ]


# --------------------------------------------------------------- dst_policy ----

def test_replacement_into_fold_returns_both_passes():
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
    moved = [o for o in resp.json()["occurrences"] if o["action"] == "replace"]
    assert [(o["utc"], o["fold"]) for o in moved] == [
        ("2025-11-02T05:30:00Z", 0),
        ("2025-11-02T06:30:00Z", 1),
    ]
    assert all(o["original_local"] == "2025-11-01T09:00:00" for o in moved)


def test_replacement_into_fold_respects_fold_policy():
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
    moved = [o for o in resp.json()["occurrences"] if o["action"] == "replace"]
    assert [(o["utc"], o["fold"]) for o in moved] == [("2025-11-02T06:30:00Z", 1)]


def test_replacement_into_gap_is_skipped_by_default():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T12:00:00",
            "rrule": "FREQ=DAILY;COUNT=2",
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-08T12:00:00",
                    "action": "replace",
                    "replacement_local": "2025-03-09T02:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    assert all(o["action"] == "keep" for o in resp.json()["occurrences"])
    assert "2025-03-08T12:00:00Z" not in [o["utc"] for o in resp.json()["occurrences"]]


def test_replacement_into_gap_shift_forward():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T12:00:00",
            "rrule": "FREQ=DAILY;COUNT=2",
            "dst_policy": {"gap": "shift_forward"},
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-08T12:00:00",
                    "action": "replace",
                    "replacement_local": "2025-03-09T02:30:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    moved = [o for o in resp.json()["occurrences"] if o["action"] == "replace"]
    assert [(o["local"], o["utc"]) for o in moved] == [
        ("2025-03-09T03:30:00", "2025-03-09T07:30:00Z")
    ]


def test_cancel_removes_both_fold_passes_of_repeated_wall_time():
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-11-02T01:30:00",
            "rdate": ["2025-11-02T09:00:00"],
            "interval_start": "2025-11-02T00:00:00Z",
            "interval_end": "2025-11-03T00:00:00Z",
            "overrides": [
                {"original_local": "2025-11-02T01:30:00", "action": "cancel"}
            ],
        }
    )
    assert resp.status_code == 200
    (o,) = resp.json()["occurrences"]
    assert o["utc"] == "2025-11-02T14:00:00Z"


def test_original_can_be_a_gap_skipped_instance_even_though_it_normally_drops():
    # 02:30 on 2025-03-09 does not exist in New York and is skipped during
    # normal expansion, but it is still the named RRULE instance: an override
    # may reschedule it to an existing wall time.
    resp = post(
        {
            "timezone": "America/New_York",
            "start_local": "2025-03-08T02:30:00",
            "rrule": "FREQ=DAILY;COUNT=3",
            "interval_start": "2025-03-01T00:00:00Z",
            "interval_end": "2025-03-20T00:00:00Z",
            "overrides": [
                {
                    "original_local": "2025-03-09T02:30:00",
                    "action": "replace",
                    "replacement_local": "2025-03-09T10:00:00",
                }
            ],
        }
    )
    assert resp.status_code == 200
    moved = [o for o in resp.json()["occurrences"] if o["action"] == "replace"]
    assert [(o["local"], o["original_local"]) for o in moved] == [
        ("2025-03-09T10:00:00", "2025-03-09T02:30:00")
    ]
