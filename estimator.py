#!/usr/bin/env python3
"""Telemetry-cost estimator CLI.

Reads a local telemetry JSON file and a pricing JSON file, applies the
documented rate card, and prints a deterministic cost report to stdout.

Usage:
    python3 estimator.py <telemetry-file> <pricing-file>
"""

import json
import sys
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP


def _is_int_value(value):
    """Return True for JSON ints accepted as integer fields (bool excluded)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _coerce_integer_field(value):
    """Validate bytes/calls field; return int or None if invalid."""
    if isinstance(value, bool):
        return None
    if _is_int_value(value):
        if value < 0:
            return None
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value < 0:
            return None
        try:
            integral = value.to_integral_value()
        except (InvalidOperation, ValueError, ArithmeticError):
            return None
        if value != integral:
            return None
        return int(value)
    if isinstance(value, float):
        # Only non-finite floats reach here (parse_float=Decimal handles
        # finite ones); reject unconditionally.
        return None
    return None


def _validate_record(record):
    """Return True if a telemetry record satisfies R-IN-1 (extra ignored)."""
    if not isinstance(record, dict):
        return False
    service = record.get("service")
    operation = record.get("operation")
    for field in (service, operation):
        if not isinstance(field, str):
            return False
        if field == "":
            return False
        if "/" in field or "\n" in field or "\r" in field:
            return False
    if _coerce_integer_field(record.get("bytes")) is None:
        # Missing key yields None -> invalid (get returns None, not valid).
        return False
    if _coerce_integer_field(record.get("calls")) is None:
        return False
    return True


def _coerce_rate(value):
    """Validate per_call/per_byte; return Decimal or None if invalid."""
    if isinstance(value, bool):
        return None
    if _is_int_value(value):
        if value < 0:
            return None
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value < 0:
            return None
        return value
    if isinstance(value, float):
        return None
    return None


def _validate_pricing(data):
    """Return (per_call, per_byte, currency) or None if invalid per R-IN-3."""
    if not isinstance(data, dict):
        return None
    if "per_call" not in data or "per_byte" not in data or "currency" not in data:
        return None
    per_call = _coerce_rate(data.get("per_call"))
    if per_call is None:
        return None
    per_byte = _coerce_rate(data.get("per_byte"))
    if per_byte is None:
        return None
    currency = data.get("currency")
    if not isinstance(currency, str):
        return None
    if currency == "":
        return None
    if "\n" in currency or "\r" in currency:
        return None
    return per_call, per_byte, currency


def _read_file_bytes(path):
    """Return (data_bytes, os_error)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(), None
    except OSError as exc:
        return None, exc


def main(argv):
    if len(argv) != 3:
        sys.stderr.write(
            "usage: python3 estimator.py <telemetry-file> <pricing-file>\n"
        )
        return 2
    telemetry_path = argv[1]
    pricing_path = argv[2]

    telemetry_bytes, telemetry_os_error = _read_file_bytes(telemetry_path)
    pricing_bytes, pricing_os_error = _read_file_bytes(pricing_path)

    errors = []
    if telemetry_os_error is not None:
        errors.append("telemetry error in " + telemetry_path + ": file unreadable\n")
    if pricing_os_error is not None:
        errors.append("pricing error in " + pricing_path + ": file unreadable\n")
    if errors:
        for line in errors:
            sys.stderr.write(line)
        return 1

    # Decode UTF-8 strictly; undecodable bytes count as malformed JSON.
    try:
        telemetry_text = telemetry_bytes.decode("utf-8")
    except UnicodeDecodeError:
        telemetry_text = None
    try:
        pricing_text = pricing_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pricing_text = None

    telemetry_data = None
    pricing_data = None
    telemetry_syntax_error = False
    pricing_syntax_error = False
    if telemetry_text is None:
        telemetry_syntax_error = True
    else:
        try:
            telemetry_data = json.loads(
                telemetry_text,
                parse_float=Decimal,
                parse_int=int,
            )
        except (json.JSONDecodeError, ValueError):
            telemetry_syntax_error = True
    if pricing_text is None:
        pricing_syntax_error = True
    else:
        try:
            pricing_data = json.loads(
                pricing_text,
                parse_float=Decimal,
                parse_int=int,
            )
        except (json.JSONDecodeError, ValueError):
            pricing_syntax_error = True

    telemetry_fault_indices = None  # None = not a record-index case
    telemetry_bad_top_level = False
    pricing_invalid = False

    if telemetry_syntax_error:
        pass
    elif not isinstance(telemetry_data, list):
        telemetry_bad_top_level = True
    else:
        faulty = []
        for idx, record in enumerate(telemetry_data):
            if not _validate_record(record):
                faulty.append(idx)
        if faulty:
            telemetry_fault_indices = faulty

    if pricing_syntax_error:
        pricing_invalid = True
    else:
        pricing_result = _validate_pricing(pricing_data)
        if pricing_result is None:
            pricing_invalid = True
        else:
            per_call_valid, per_byte_valid, currency_valid = pricing_result

    telemetry_has_error = (
        telemetry_syntax_error
        or telemetry_bad_top_level
        or (telemetry_fault_indices is not None)
    )

    if telemetry_has_error or pricing_invalid:
        if telemetry_has_error:
            if telemetry_fault_indices is not None:
                lowest = min(telemetry_fault_indices)
                sys.stderr.write(
                    "telemetry error in "
                    + telemetry_path
                    + ": invalid record at index "
                    + str(lowest)
                    + "\n"
                )
            else:
                sys.stderr.write(
                    "telemetry error in " + telemetry_path + ": invalid telemetry file\n"
                )
        if pricing_invalid:
            sys.stderr.write(
                "pricing error in " + pricing_path + ": invalid pricing file\n"
            )
        return 1

    per_call = per_call_valid
    per_byte = per_byte_valid
    currency = currency_valid
    records = telemetry_data

    with localcontext() as ctx:
        ctx.prec = 50
        group_costs = {}
        for record in records:
            nbytes = _coerce_integer_field(record.get("bytes"))
            ncalls = _coerce_integer_field(record.get("calls"))
            cost = Decimal(ncalls) * per_call + Decimal(nbytes) * per_byte
            key = (record.get("service"), record.get("operation"))
            group_costs[key] = group_costs.get(key, Decimal(0)) + cost
        grand_total = Decimal(0)
        for value in group_costs.values():
            grand_total += value

        quant_two = Decimal("0.01")
        quant_one = Decimal("0.1")
        total_display = grand_total.quantize(quant_two, rounding=ROUND_HALF_UP)

        lines = []
        lines.append("TOTAL: " + format(total_display, "f") + " " + currency)
        lines.append("RECORDS: " + str(len(records)))
        if len(records) == 0:
            lines.append("RECOMMENDATION: NO_DATA")
        else:
            ranked = sorted(
                group_costs.items(),
                key=lambda kv: (
                    -kv[1],
                    kv[0][0].encode("utf-8"),
                    kv[0][1].encode("utf-8"),
                ),
            )
            top_share_str = None
            group_lines = []
            for (service, operation), cost in ranked:
                if grand_total == 0:
                    share_q = Decimal("0.0")
                else:
                    share_raw = Decimal(100) * cost / grand_total
                    share_q = share_raw.quantize(quant_one, rounding=ROUND_HALF_UP)
                share_str = format(share_q, "f")
                cost_q = cost.quantize(quant_two, rounding=ROUND_HALF_UP)
                cost_str = format(cost_q, "f")
                group_lines.append(
                    "GROUP "
                    + service
                    + "/"
                    + operation
                    + " cost="
                    + cost_str
                    + " share="
                    + share_str
                    + "%"
                )
            lines.extend(group_lines)
            top_key, top_cost = ranked[0]
            if grand_total == 0:
                top_share_str = format(Decimal("0.0"), "f")
            else:
                top_raw = Decimal(100) * top_cost / grand_total
                top_share_str = format(
                    top_raw.quantize(quant_one, rounding=ROUND_HALF_UP), "f"
                )
            lines.append(
                "RECOMMENDATION: "
                + top_key[0]
                + "/"
                + top_key[1]
                + " accounts for "
                + top_share_str
                + "% of cost"
            )

    report = "\n".join(lines) + "\n"
    sys.stdout.buffer.write(report.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
