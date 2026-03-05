#!/usr/bin/env python3
"""
Format "label iso_date" pairs from stdin into aligned age + date rows.
Reads lines of: <label> <iso8601_datetime>
Outputs:        <label> <age> (<locale_date>)

Usage: <cmd> | python3 format-dates.py <apple_locale> <now_epoch> [label_width]
  apple_locale: e.g. nb_NO — used to format the date per locale
  now_epoch:    current Unix timestamp (seconds)
  label_width:  column width for the label (default: 30)
"""
import locale, datetime, sys

apple_locale = sys.argv[1] if len(sys.argv) > 1 else ""
now = int(sys.argv[2]) if len(sys.argv) > 2 else int(datetime.datetime.now().timestamp())
label_width = int(sys.argv[3]) if len(sys.argv) > 3 else 30

try:
    locale.setlocale(locale.LC_TIME, f"{apple_locale}.UTF-8")
except locale.Error:
    locale.setlocale(locale.LC_TIME, "")

for line in sys.stdin:
    parts = line.strip().split(" ", 1)
    if len(parts) != 2:
        continue
    label, iso = parts
    try:
        dt = datetime.datetime.strptime(iso.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        continue
    secs = now - int(dt.timestamp())
    days = secs // 86400
    age = f"{days}d {(secs % 86400) // 3600}h ago" if days < 7 else f"{days} days ago"
    print(f"{label:<{label_width}} {age:<18} ({dt.strftime('%x %H:%M')})")
