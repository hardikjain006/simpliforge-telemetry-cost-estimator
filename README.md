# Telemetry-cost estimator

Estimates serving cost from generated telemetry data and surfaces one
actionable cost-saving result through a CLI or a static page.

## Usage

CLI report command (run from the repository root):

`python3 estimator.py <telemetry-file> <pricing-file>`

The first positional argument is the telemetry file, the second is the
pricing file. On success it exits 0 and prints the report to stdout.
Run the test suite with `python3 -m unittest discover -s tests -v`.

## Scope

- In scope: reading a local file of generated telemetry records, applying a
  documented rate card, and reporting total cost, a ranked breakdown, and one
  actionable recommendation.
- Non-goals: live telemetry collection, cloud billing APIs, authentication,
  persistence, and multi-currency settlement.

## Inputs

- R-IN-1: Telemetry input is a local UTF-8 JSON file containing an array of
  records. Each record MUST be a JSON object containing at least these fields
  with these types:
  - `service`: non-empty string containing no `/`, U+000A (LF), or U+000D
    (CR). A record whose `service` is missing, not a string, empty, or
    contains one of those characters violates R-IN-1.
  - `operation`: non-empty string containing no `/`, U+000A (LF), or
    U+000D (CR), with the same violation rule as `service`.
  - `bytes`: finite JSON number `>= 0` with zero fractional part. `1.0` and
    `1e3` are accepted as the integers `1` and `1000`. Fractional values
    (for example `1.5`), negative values, and non-number JSON values are
    rejected. `-0` is accepted as `0`.
  - `calls`: finite JSON number `>= 0` with zero fractional part, with the
    same acceptance rule as `bytes` (`1.0`/`1e3` accepted; fractional,
    negative, and non-number values rejected; `-0` accepted as `0`).
- R-IN-2: Records MAY contain additional fields (for example `timestamp`);
  the estimator MUST ignore them. Extra fields never cause rejection.
- R-IN-3: Pricing input is a local UTF-8 JSON file of the form
  `{"per_call": <number >= 0>, "per_byte": <number >= 0>,
  "currency": <non-empty string>}`. `per_call` and `per_byte` MUST each be
  a finite JSON number `>= 0` (fractional values allowed; `1e3` accepted;
  negative, non-number, and non-finite values rejected). `currency` is a
  non-empty string containing no U+000A (LF) or U+000D (CR) and is used
  verbatim in the report. Unknown fields MUST be ignored.
- R-IN-4: On a single-CPU worker with at least 2 GiB RAM (reference CI
  worker class), using a valid 100,000-record telemetry file (100,000 valid
  records per R-IN-1, for example 50 services x 20 operations with `bytes`
  in `0`..`1000000` and `calls` in `0`..`100`) plus a valid pricing file per
  R-IN-3, the conformance CLI invocation MUST complete from the first
  input-byte read to the last stdout-byte flush in under 60 seconds of
  wall-clock time, excluding process, interpreter, and container startup.
  For external conformance testing, whole-invocation wall-clock time under
  60 seconds is a sufficient PASS; the startup exclusion is
  implementation-measured and needs no separate external measurement.

## Cost model

- R-COST-1: `cost(record) = calls * per_call + bytes * per_byte`.
- R-COST-2: Records are grouped by (`service`, `operation`). Grouping uses
  exact byte-wise equality of the UTF-8 encodings with no case folding,
  trimming, or Unicode normalization (`"API"` and `"api"` are different
  groups). Group cost is the sum of its records' costs using unrounded
  values. Service totals are the sum of their groups. The grand total is
  the sum of all records.
- R-COST-3: Displayed money values are rounded half up to 2 decimals
  and always formatted with exactly 2 decimals with trailing zeros shown
  (for example `5.00`, `0.00`). Ranking MUST use unrounded values. Ties
  break by `service` ascending, then `operation` ascending (byte-wise).

## Output

At least one of the two surfaces below is required. If both exist, their
reported totals, ranking, and recommendation MUST agree.

### CLI surface

- R-CLI-1: The README documents one exact command that reads the two input
  files and writes the report. The conformance invocation pattern is
  `<cmd> <telemetry-file> <pricing-file>`, where `<cmd>` is the documented
  executable, `<telemetry-file>` is the telemetry path as invoked in first
  position, and `<pricing-file>` is the pricing path as invoked in second
  position, with exactly these two positional arguments and no required
  flags. On success it exits 0 and prints a UTF-8 report to stdout with LF
  line endings. Stdout on success MUST consist of exactly the R-CLI-2 lines
  (or exactly the R-CLI-3 lines when the telemetry array is empty) in
  order, each terminated by LF (U+000A) including the final line, with no
  extra, blank, or whitespace-variant lines. Field spacing is exactly one
  ASCII space as shown in the templates.
- R-CLI-2: The report MUST contain, in order:
  1. `TOTAL: <amount> <currency>` (`<amount>` is always formatted with
     exactly 2 decimals per R-COST-3; `<currency>` is the pricing file's
     `currency` verbatim).
  2. `RECORDS: <n>` (`<n>` is the canonical base-10 record count with no
     leading zeros, sign, or surrounding spaces).
  3. One line per group, most expensive first:
     `GROUP <service>/<operation> cost=<amount> share=<pct>%`.
     `cost=<amount>` is always formatted with exactly 2 decimals per
     R-COST-3. `share = 100 * unrounded_group_cost / unrounded_grand_total`,
     rounded half up to 1 decimal and always formatted with exactly one
     decimal (for example `0.0%`, `12.5%`, `100.0%`).
  4. Exactly one line of the form
     `RECOMMENDATION: <service>/<operation> accounts for <pct>% of cost`
     naming the top-ranked group, where `<pct>` equals that group's `share`
     string from item 3.
- R-CLI-3: When the telemetry array is empty, the report MUST show
  `TOTAL: 0.00 <currency>` (pricing file's `currency`), `RECORDS: 0`, no
  `GROUP` lines, and exactly `RECOMMENDATION: NO_DATA` as the last line.
  The R-CLI-1 stdout-exactness rule applies.

### Static-page surface

- R-PAGE-1: The implementation writes one self-contained HTML file (no
  network fetches) containing the same four data items as R-CLI-2: total
  with currency, record count, ranked group rows with cost and share, and
  the single recommendation naming the top-ranked group. The page's text
  content MUST contain the same literal data strings as the corresponding
  CLI report lines: the `TOTAL: <amount> <currency>` string, the
  `RECORDS: <n>` string, one `GROUP <service>/<operation> cost=<amount>
  share=<pct>%` string per group in the same ranked order, and the
  `RECOMMENDATION: ...` string. "Text content" means the HTML-entity-decoded
  text of the page (DOM `textContent`), not raw file bytes: required
  HTML-escaping of `&`, `<`, `>`, `"`, and `'` in `service`, `operation`,
  or other fields does not break equivalence. "Same ranked order" means
  the decoded-text first occurrences of the `GROUP` strings appear in
  document order, most expensive group first. When the page surface is provided, the
  README MUST document one exact page-generation command giving the
  executable and the argument positions of the telemetry file, the pricing
  file, and the output HTML file, with exactly those three file arguments
  and no required flags; that exact documented form is the page
  conformance invocation.
- R-PAGE-2: The empty-input page MUST show total `TOTAL: 0.00 <currency>`
  (pricing file's `currency`), `RECORDS: 0`, no `GROUP` strings or group
  rows, and `RECOMMENDATION: NO_DATA` as text.

## Determinism

- R-DET-1: Identical input files MUST produce byte-identical output.
  Output MUST NOT embed timestamps, random values, PIDs, or absolute paths.

## Errors and boundary cases

- R-ERR-1: Missing or unreadable input file (telemetry or pricing): the CLI
  MUST exit non-zero with no report lines on stdout (no lines starting
  with `TOTAL:`, `RECORDS:`, `GROUP `, or `RECOMMENDATION:`); the stderr
  diagnostic MUST contain the affected path exactly as invoked. Page mode
  MUST also exit non-zero with the same stderr diagnostic and MUST NOT
  create the page output file if absent, or MUST leave it byte-identical
  if a file already exists at that path.
- R-ERR-2: Malformed JSON, a record violating R-IN-1, or a pricing file
  violating R-IN-3: fail closed with a non-zero exit (CLI) and no report
  lines on stdout (same no-report rule as R-ERR-1). The stderr diagnostic
  MUST contain the affected file path exactly as invoked; when both input
  files are at fault, it MUST contain each affected invoked path. For
  telemetry, when one or more records are at fault it MUST also contain
  the lowest faulty record's 0-based array index as a canonical token:
  base-10 with no leading zeros (`0` for zero), bounded on both sides by
  a non-digit character or string boundary, at an occurrence outside the
  invoked-path substring (so an index digit inside a path such as
  `file1.json` does not satisfy the rule); additional faulty indices MAY
  also appear. A top-level JSON syntax error with no attributable record,
  and valid JSON whose top level is not an array, each require only the
  path and no index. Page mode uses
  the same exit and stderr rule as R-ERR-1 and MUST NOT create a new page
  file or update a pre-existing one (a pre-existing file MUST remain
  byte-identical).
- R-ERR-3: Zero and empty cases are valid: `bytes: 0` / `calls: 0` cost
  nothing; an empty array follows R-CLI-3 / R-PAGE-2.
- R-ERR-4: A grand total of zero with a non-empty input (all-zero usage)
  MUST list the groups with `share=0.0%` and still recommend the
  top-ranked group per R-COST-3.

## Acceptance

- A developer with no private context can implement and test this contract
  from this file alone.
- Feeding a generated telemetry file plus a rate card yields a total, a
  deterministic ranking, and one actionable recommendation on the CLI, the
  static page, or both.

## Unresolved requirements

None. Every clause above is implementable and testable as written.
