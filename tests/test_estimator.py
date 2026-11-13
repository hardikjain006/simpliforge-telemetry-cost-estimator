"""Repository-native tests for the telemetry-cost estimator CLI."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTIMATOR = os.path.join(REPO_ROOT, "estimator.py")


def run_estimator(telemetry_path, pricing_path, extra_args=None):
    cmd = [sys.executable, ESTIMATOR]
    if extra_args is not None:
        cmd.extend(extra_args)
    else:
        cmd.extend([telemetry_path, pricing_path])
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    return proc


def write_json(path, data, raw=None):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        if raw is not None:
            handle.write(raw)
        else:
            json.dump(data, handle)


def stderr_without_invoked_paths(err, *paths):
    """Strip invoked paths so index tokens cannot match digits inside them."""
    scrubbed = err
    for path in paths:
        scrubbed = scrubbed.replace(path, "")
    return scrubbed


class EstimatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.telemetry = os.path.join(self.tmp.name, "telemetry.json")
        self.pricing = os.path.join(self.tmp.name, "pricing.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_pricing(self, per_call=1, per_byte=0.01, currency="USD", raw=None, data=None):
        if raw is not None:
            write_json(self.pricing, None, raw=raw)
        elif data is not None:
            write_json(self.pricing, data)
        else:
            write_json(self.pricing, {
                "per_call": per_call,
                "per_byte": per_byte,
                "currency": currency,
            })

    def test_happy_path_totals_ranking_recommendation(self):
        write_json(self.telemetry, [
            {"service": "api", "operation": "read", "bytes": 100, "calls": 2},
            {"service": "api", "operation": "write", "bytes": 0, "calls": 0},
        ])
        self.write_pricing(per_call=1, per_byte=0.01, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        self.assertEqual(text, (
            "TOTAL: 3.00 USD\n"
            "RECORDS: 2\n"
            "GROUP api/read cost=3.00 share=100.0%\n"
            "GROUP api/write cost=0.00 share=0.0%\n"
            "RECOMMENDATION: api/read accounts for 100.0% of cost\n"
        ))

    def test_empty_array(self):
        write_json(self.telemetry, [])
        self.write_pricing(currency="EUR")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout.decode("utf-8"),
            "TOTAL: 0.00 EUR\nRECORDS: 0\nRECOMMENDATION: NO_DATA\n",
        )

    def test_all_zero_usage_lists_groups_and_recommends_top(self):
        write_json(self.telemetry, [
            {"service": "b", "operation": "a", "bytes": 0, "calls": 0},
            {"service": "a", "operation": "b", "bytes": 0, "calls": 0},
        ])
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        self.assertIn("GROUP a/b cost=0.00 share=0.0%", text)
        self.assertIn("GROUP b/a cost=0.00 share=0.0%", text)
        self.assertTrue(text.index("GROUP a/b") < text.index("GROUP b/a"))
        self.assertIn("RECOMMENDATION: a/b accounts for 0.0% of cost", text)

    def test_integer_acceptance_forms(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 1000, "calls": 1},
        ], raw='[{"service":"s","operation":"o","bytes":1e3,"calls":1.0}]')
        self.write_pricing(per_call=1, per_byte=1, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("TOTAL: 1001.00 USD", proc.stdout.decode("utf-8"))

    def test_fractional_bytes_rejected_with_index(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 1.5, "calls": 0},
        ])
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        err = proc.stderr.decode("utf-8", errors="replace")
        self.assertIn(self.telemetry, err)
        # Index 0 must appear as a standalone token outside the path itself.
        self.assertRegex(
            stderr_without_invoked_paths(err, self.telemetry),
            r"(?<!\d)0(?!\d)",
        )

    def test_lowest_faulty_index_reported(self):
        write_json(self.telemetry, [
            {"service": "a", "operation": "b", "bytes": 1, "calls": 1},
            {"service": "", "operation": "x", "bytes": 1, "calls": 1},
            {"service": "c", "operation": "y", "bytes": -1, "calls": 0},
        ])
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        err = proc.stderr.decode("utf-8", errors="replace")
        self.assertIn(self.telemetry, err)
        # Lowest faulty index is 1; it must appear as a standalone token
        # outside the invoked path substring.
        self.assertRegex(
            stderr_without_invoked_paths(err, self.telemetry),
            r"(?<!\d)1(?!\d)",
        )

    def test_missing_telemetry_file(self):
        self.write_pricing()
        missing = os.path.join(self.tmp.name, "missing.json")
        proc = run_estimator(missing, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(missing, proc.stderr.decode("utf-8", errors="replace"))

    def test_malformed_telemetry_json_needs_only_path(self):
        write_json(self.telemetry, None, raw="{oops")
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(self.telemetry, proc.stderr.decode("utf-8", errors="replace"))

    def test_top_level_non_array_needs_only_path(self):
        write_json(self.telemetry, None, raw="{}")
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(self.telemetry, proc.stderr.decode("utf-8", errors="replace"))

    def test_invalid_pricing_reports_pricing_path(self):
        write_json(self.telemetry, [])
        self.write_pricing(data={"per_call": -1, "per_byte": 0, "currency": "USD"})
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(self.pricing, proc.stderr.decode("utf-8", errors="replace"))

    def test_missing_pricing_file(self):
        write_json(self.telemetry, [])
        missing = os.path.join(self.tmp.name, "missing-pricing.json")
        proc = run_estimator(self.telemetry, missing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(missing, proc.stderr.decode("utf-8", errors="replace"))

    def test_malformed_pricing_json(self):
        write_json(self.telemetry, [])
        self.write_pricing(raw="{oops")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(self.pricing, proc.stderr.decode("utf-8", errors="replace"))

    def test_both_files_at_fault_reports_both_paths(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 1.5, "calls": 0},
        ])
        self.write_pricing(data={"per_call": -1, "per_byte": 0, "currency": "USD"})
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        err = proc.stderr.decode("utf-8", errors="replace")
        self.assertIn(self.telemetry, err)
        self.assertIn(self.pricing, err)

    def test_extra_fields_ignored(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 1, "calls": 1,
             "timestamp": "2026-01-01"},
        ])
        write_json(self.pricing, {"per_call": 1, "per_byte": 1,
                                  "currency": "USD", "extra": 1})
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("TOTAL: 2.00 USD", proc.stdout.decode("utf-8"))

    def test_grouping_sums_and_tie_break(self):
        write_json(self.telemetry, [
            {"service": "b", "operation": "a", "bytes": 10, "calls": 0},
            {"service": "a", "operation": "b", "bytes": 5, "calls": 0},
            {"service": "a", "operation": "b", "bytes": 5, "calls": 0},
        ])
        self.write_pricing(per_call=0, per_byte=1, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        self.assertTrue(text.index("GROUP a/b") < text.index("GROUP b/a"))
        self.assertIn("RECOMMENDATION: a/b accounts for 50.0% of cost", text)

    def test_grouping_is_bytewise_case_and_whitespace(self):
        write_json(self.telemetry, [
            {"service": "API", "operation": "op", "bytes": 30, "calls": 0},
            {"service": "api", "operation": "op", "bytes": 20, "calls": 0},
            {"service": "svc", "operation": "op", "bytes": 10, "calls": 0},
            {"service": "svc ", "operation": "op", "bytes": 5, "calls": 0},
        ])
        self.write_pricing(per_call=0, per_byte=1, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        self.assertIn("GROUP API/op cost=30.00 share=46.2%", text)
        self.assertIn("GROUP api/op cost=20.00 share=30.8%", text)
        self.assertIn("GROUP svc/op cost=10.00 share=15.4%", text)
        self.assertIn("GROUP svc /op cost=5.00 share=7.7%", text)
        self.assertTrue(text.index("GROUP API/op") < text.index("GROUP api/op"))
        self.assertTrue(text.index("GROUP api/op") < text.index("GROUP svc/op"))
        self.assertTrue(
            text.index("GROUP svc/op") < text.index("GROUP svc /op"))
        self.assertIn("RECOMMENDATION: API/op accounts for 46.2% of cost", text)

    def test_money_rounds_half_up(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 1, "calls": 0},
        ])
        self.write_pricing(per_call=0, per_byte=0.005, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("TOTAL: 0.01 USD", proc.stdout.decode("utf-8"))

    def test_share_rounds_half_up(self):
        write_json(self.telemetry, [
            {"service": "a", "operation": "x", "bytes": 1225, "calls": 0},
            {"service": "b", "operation": "y", "bytes": 8775, "calls": 0},
        ])
        self.write_pricing(per_call=0, per_byte=0.01, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        self.assertIn("TOTAL: 100.00 USD", text)
        # 12.25% and 87.75% discriminate half-up from banker's/truncation.
        self.assertIn("GROUP b/y cost=87.75 share=87.8%", text)
        self.assertIn("GROUP a/x cost=12.25 share=12.3%", text)
        self.assertTrue(text.index("GROUP b/y") < text.index("GROUP a/x"))
        self.assertIn("RECOMMENDATION: b/y accounts for 87.8% of cost", text)

    def test_ranking_uses_unrounded_costs(self):
        write_json(self.telemetry, [
            {"service": "b", "operation": "z", "bytes": 5022, "calls": 0},
            {"service": "a", "operation": "a", "bytes": 5020, "calls": 0},
        ])
        self.write_pricing(per_call=0, per_byte=0.0002, currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        text = proc.stdout.decode("utf-8")
        # Both groups display as cost=1.00, but unrounded costs differ, so
        # b/z (1.0044) must rank ahead of a/a (1.0040) even though the
        # byte-wise tie-break would order a/a first.
        self.assertIn("TOTAL: 2.01 USD", text)
        self.assertIn("GROUP b/z cost=1.00 share=50.0%", text)
        self.assertIn("GROUP a/a cost=1.00 share=50.0%", text)
        self.assertTrue(text.index("GROUP b/z") < text.index("GROUP a/a"))
        self.assertIn("RECOMMENDATION: b/z accounts for 50.0% of cost", text)

    def test_service_with_slash_rejected(self):
        write_json(self.telemetry, [
            {"service": "a/b", "operation": "o", "bytes": 0, "calls": 0},
        ])
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_faulty_operation_rejected_with_index(self):
        for faulty_operation in ("a/b", "", "a\nb", "a\rb"):
            with self.subTest(operation=faulty_operation):
                write_json(self.telemetry, [
                    {"service": "s", "operation": faulty_operation,
                     "bytes": 0, "calls": 0},
                ])
                self.write_pricing()
                proc = run_estimator(self.telemetry, self.pricing)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, b"")
                err = proc.stderr.decode("utf-8", errors="replace")
                self.assertIn(self.telemetry, err)
                self.assertRegex(
                    stderr_without_invoked_paths(err, self.telemetry),
                    r"(?<!\d)0(?!\d)",
                )

    def test_bool_bytes_rejected(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": True, "calls": 0},
        ])
        self.write_pricing()
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_faulty_calls_rejected_with_index(self):
        for faulty_calls in (1.5, -1, "1", True):
            with self.subTest(calls=faulty_calls):
                write_json(self.telemetry, [
                    {"service": "s", "operation": "o", "bytes": 0,
                     "calls": faulty_calls},
                ])
                self.write_pricing()
                proc = run_estimator(self.telemetry, self.pricing)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, b"")
                err = proc.stderr.decode("utf-8", errors="replace")
                self.assertIn(self.telemetry, err)
                self.assertRegex(
                    stderr_without_invoked_paths(err, self.telemetry),
                    r"(?<!\d)0(?!\d)",
                )
        with self.subTest(calls="missing"):
            write_json(self.telemetry, [
                {"service": "s", "operation": "o", "bytes": 0},
            ])
            self.write_pricing()
            proc = run_estimator(self.telemetry, self.pricing)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, b"")
            err = proc.stderr.decode("utf-8", errors="replace")
            self.assertIn(self.telemetry, err)
            self.assertRegex(
                stderr_without_invoked_paths(err, self.telemetry),
                r"(?<!\d)0(?!\d)",
            )

    def test_faulty_service_newline_rejected_with_index(self):
        for faulty_service in ("a\nb", "a\rb"):
            with self.subTest(service=repr(faulty_service)):
                write_json(self.telemetry, [
                    {"service": faulty_service, "operation": "o",
                     "bytes": 0, "calls": 0},
                ])
                self.write_pricing()
                proc = run_estimator(self.telemetry, self.pricing)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, b"")
                err = proc.stderr.decode("utf-8", errors="replace")
                self.assertIn(self.telemetry, err)
                self.assertRegex(
                    stderr_without_invoked_paths(err, self.telemetry),
                    r"(?<!\d)0(?!\d)",
                )

    def test_invalid_pricing_semantics_reports_pricing_path(self):
        faulty_pricing_cases = [
            {"per_call": 1, "per_byte": -1, "currency": "USD"},
            {"per_call": 1, "per_byte": 0.01, "currency": ""},
            {"per_call": 1, "per_byte": 0.01, "currency": "A\nB"},
            {"per_call": 1, "per_byte": 0.01, "currency": "A\rB"},
            {"per_call": 1, "per_byte": 0.01, "currency": 123},
            {"per_byte": 0.01, "currency": "USD"},
            {"per_call": 1, "currency": "USD"},
            {"per_call": 1, "per_byte": 0.01},
            {"per_call": "1", "per_byte": 0.01, "currency": "USD"},
            {"per_call": 1, "per_byte": "0.01", "currency": "USD"},
            {"per_call": True, "per_byte": 0.01, "currency": "USD"},
            {"per_call": 1, "per_byte": True, "currency": "USD"},
        ]
        for faulty_pricing in faulty_pricing_cases:
            with self.subTest(pricing=faulty_pricing):
                write_json(self.telemetry, [])
                self.write_pricing(data=faulty_pricing)
                proc = run_estimator(self.telemetry, self.pricing)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, b"")
                err = proc.stderr.decode("utf-8", errors="replace")
                self.assertIn(self.pricing, err)

    def test_stdout_uses_lf_and_final_newline(self):
        write_json(self.telemetry, [])
        self.write_pricing(currency="USD")
        proc = run_estimator(self.telemetry, self.pricing)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(b"\r", proc.stdout)
        self.assertTrue(proc.stdout.endswith(b"\n"))
        self.assertFalse(proc.stdout.endswith(b"\n\n"))

    def test_determinism(self):
        write_json(self.telemetry, [
            {"service": "s", "operation": "o", "bytes": 3, "calls": 4},
        ])
        self.write_pricing()
        first = run_estimator(self.telemetry, self.pricing).stdout
        second = run_estimator(self.telemetry, self.pricing).stdout
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
