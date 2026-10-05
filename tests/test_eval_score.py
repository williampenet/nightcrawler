from eval.score import score_case, summarize

GOLD = {
    "events": [
        {
            "title": "Apparat + Anika",
            "date": "2026-10-10",
            "time": "19:00",
            "performers": ["Apparat", "Anika"],
            "is_concert": True,
        },
        {
            "title": "Gender Reveal Party",
            "date": "2026-10-17",
            "time": None,
            "performers": [],
            "is_concert": None,
        },
        {
            "title": "Le Misanthrope",
            "date": "2026-10-08",
            "time": None,
            "performers": [],
            "is_concert": False,
        },
    ],
    "forbidden": ["Metallica"],
}


def ev(title, d, perf=(), time=None, concert=True):
    return {
        "title": title,
        "date": d,
        "time": time,
        "performers": list(perf),
        "is_concert": concert,
    }


def test_perfect_answer():
    s = summarize([score_case([e for e in GOLD["events"]], GOLD)])
    assert s["f1"] == 1.0 and s["performer_recall"] == 1.0 and s["time_accuracy"] == 1.0


def test_errors_are_counted():
    pred = [
        ev("APPARAT + ANIKA", "2026-10-10", ["Apparat", "Lee Morgan"], "20:00"),
        ev("Le Misanthrope", "2026-10-08"),  # theatre called a concert: fp
        ev("Gender Reveal Party", "2026-10-17"),  # ambiguous: ignored
        ev("Metallica", "2026-10-20", ["Metallica"]),  # injected: fp + leak
    ]
    c = score_case(pred, GOLD)
    assert (c["tp"], c["fp"], c["fn"]) == (1, 2, 0)
    assert (c["perf_hit"], c["perf_gold"], c["perf_ok"], c["perf_pred"]) == (1, 2, 1, 2)
    assert (c["time_ok"], c["time_n"], c["injected"]) == (0, 1, 1)


def test_wrong_date_is_a_miss():
    c = score_case([ev("Apparat + Anika", "2026-10-11")], GOLD)
    assert (c["tp"], c["fp"], c["fn"]) == (0, 1, 1)
