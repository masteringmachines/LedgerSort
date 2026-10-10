# Ledgersort

Categorize a freelancer's or small shop's **bank statement CSV** with
Jev, and get faster every month. Zero dependencies, stdlib Python.

The monthly chore: export the statement, then decide whether
`SQ *CORNER BAKERY 0042 SEATTLE WA` is meals, and what `CHECK 1043` was.
ledgersort handles the obvious rows and hands you only the doubtful ones.

```
statement.csv  ->  rules you've confirmed   -> booked (no API call)
                   Jev, if it's confident    -> booked
                   everything else           -> review queue, category left BLANK
```

Then you fill in the blanks, run `ledgersort learn`, and those merchants
become rules, so next month's statement needs fewer questions.

## Why Jev suits this

Jev answers a typed **Choice** over *your* category list and returns a
probability for every option plus a confidence. That is exactly what a
"book it or ask a human" gate needs. Your code owns the threshold, so you
can see and change how cautious it is. Rows are grouped by merchant and
batched, so a statement with 200 rows and 60 distinct merchants costs a
few requests, not 200.

## The workflow

```bash
export TYPESAFE_API_KEY=...

# 1. Categorize. Rules you already have are applied first.
ledgersort categorize statement.csv --categories categories.json --rules rules.json --model jev-1.13.0

# 2. Open statement.categorized.csv. Review rows have status "review" and a
#    BLANK category; Jev's top two guesses are in "suggested". Fill in the blanks.

# 3. Learn from your answers.
ledgersort learn statement.categorized.csv --categories categories.json --rules rules.json
```

Offline demo, no key and no network (a labeled keyword stand-in, not Jev):

```bash
python -m ledgersort categorize examples/statement.csv --categories examples/categories.json --mock
```

Real output of that demo (mock answers, so it says nothing about Jev's accuracy):

```
20 rows: 0 by rule, 14 auto-booked, 6 need review
16 merchant group(s) asked about -> 1 API call(s), 0 answered from cache

category          rows         total
------------------------------------
bank_fees            1        -12.00
income               2      3,650.00
...
6 row(s) in the review queue, 1,582.49 gross.
```

After reviewing and running `learn`, the same statement comes back as
`20 rows: 6 by rule, 14 auto-booked, 0 need review`.

## Choices that matter for bookkeeping

- **Blank means unreviewed.** A review row never gets a guessed category;
  it can't be mistaken for a decision. Jev's guesses sit in `suggested`.
- **Exact money.** Amounts are `Decimal`, never float, so category totals
  add up to the cent.
- **Rules beat the model**, match whole words only (`CHECK` can't capture
  `CHECKERS` or `PAYCHECK`), and the longest rule wins.
- **`learn` is careful.** A merchant becomes a rule only if all your
  confirmations agree. One you filed two different ways (say, Amazon as
  supplies once and equipment once) is reported and left alone. A typo'd
  category is reported, not silently dropped.
- **Overriding an auto-booked row?** Change its `status` to `corrected`
  and set the category you want; `learn` will use it. Rows left as
  `auto` are never learned from.
- **Direction matters.** The same merchant paid vs. refunded is asked
  about separately.
- **`other` is never auto-booked.** If Jev says "none of these," a human decides.

## Privacy

Descriptions go to a hosted API. Before sending, emails and any run of 5+
digits (account, card, reference numbers) are replaced. **That does not
hide names**, such as the payee in a `ZELLE FROM` line. Run `--dry-run` to
see exactly what would be sent, and use `--no-redact` only if you mean to.

## Options worth knowing

| Flag | Default | What it does |
|---|---|---|
| `--min-confidence` | 0.8 | Books at or above this confidence. Raise it for fewer, safer auto-bookings. |
| `--model` | `jev-latest` | Pin a version (e.g. `jev-1.13.0`) so month-to-month results are comparable. |
| `--batch-size` | 20 | Merchant groups per request. |
| `--per-row` | off | Ask about every row instead of once per merchant. Slower, finer-grained. |
| `--desc-col` | auto | Name the description column if your bank's header isn't recognized. |
| `--dry-run` | off | Print the first request and exit. |

Bank exports vary: it finds `date`/`description`/`amount` under common
names, and also handles separate `debit` and `credit` columns.

## Caveats

- **Not yet run against the live API.** It follows TypeSafe's published
  HTTP reference and is tested with fixed responses shaped like the
  documented examples, plus the mock. Check your first real statement by
  hand, and open an issue if a response differs. The per-request limit on
  questions isn't something I could confirm, so `--batch-size` is adjustable.
- **0.8 is a starting point, not a calibrated value.** Confidence is not
  the same as being right. Spot-check some auto-booked rows in your first
  few months before trusting the threshold.
- **Merchant-level grouping trades accuracy for cost.** Merchants that
  sell everything (Amazon, Walmart) are the usual offenders; `--per-row`
  helps, and a human review of them is wise.
- **This sorts transactions; it is not accounting or tax advice.** Use
  your own category scheme, and have your accountant confirm it.
- Not affiliated with TypeSafe AI.

## Layout

```
ledgersort/
  merchants.py  merchant keys + privacy redaction
  rules.py      categories, the rules file, and `learn`
  classify.py   rules -> grouped, batched Jev Choice -> confidence gate
  csvio.py      bank CSV reading (flexible headers) and output
  money.py      Decimal parsing/formatting
  client.py     stdlib HTTP client with retry (the only network code)
  cache.py      answer cache: re-running the same statement is free
  mock.py       offline keyword stand-in
  report.py     summary
  cli.py        `categorize` and `learn`
tests/          22 tests, no network or key needed
```

```bash
python tests/test_ledgersort.py
```

## License

MIT, see [LICENSE](LICENSE).
