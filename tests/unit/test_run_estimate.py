"""How long a run will take, from earlier runs."""
from types import SimpleNamespace as NS

from webapp.services.run_estimate import estimate, format_duration


def run(system, env, *results):
    return NS(config=NS(system=system, env=env),
              results={i: NS(test_id=t, outcome=o, duration=d) for i, (t, o, d) in enumerate(results)})


def test_format_duration():
    assert format_duration(0) == "0 s" and format_duration(45) == "45 s" and format_duration(89) == "89 s"
    assert format_duration(90) == "2 min" and format_duration(3000) == "50 min"
    assert format_duration(7800) == "2 h 10 min" and format_duration(7200) == "2 h"


def test_a_test_takes_the_median_of_its_finished_results_here():
    runs = [run("S", "dev", ("a", "passed", 100), ("b", "failed", 60)),
            run("S", "dev", ("a", "passed", 300), ("a", "running", 9999)),
            run("S", "qa", ("a", "passed", 9000)),      # another environment
            run("T", "dev", ("a", "passed", 9000))]     # another system
    seconds, known, count = estimate(runs, "S", "dev", ["a", "b"])
    assert (seconds, known, count) == (200 + 60, 2, 2)


def test_a_test_with_no_history_is_given_the_typical_time_of_the_others():
    runs = [run("S", "dev", ("a", "passed", 100), ("b", "passed", 300))]
    assert estimate(runs, "S", "dev", ["a", "b", "new1", "new2"]) == (100 + 300 + 200 + 200, 2, 4)


def test_no_history_means_no_estimate():
    assert estimate([], "S", "dev", ["a"]) is None
    assert estimate([run("S", "dev", ("x", "passed", 5))], "S", "dev", ["a"]) is None
    assert estimate([run("S", "dev", ("a", "passed", 0))], "S", "dev", ["a"]) is None


def test_repeated_ids_count_once():
    assert estimate([run("S", "dev", ("a", "passed", 10))], "S", "dev", ["a", "a"]) == (10, 1, 1)
