import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import urllib.error
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ledgersort.cache import Cache
from ledgersort.classify import build_payload, classify, decide
from ledgersort.cli import main
from ledgersort.client import JevError, make_http_transport
from ledgersort.csvio import Row, read_statement
from ledgersort.merchants import merchant_key, redact
from ledgersort.money import fmt, parse_amount
from ledgersort.rules import ConfigError, Rules, learn, load_categories

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "examples")
CATS = {"software": "SaaS", "meals": "Food", "travel": "Trips", "other": "None of the above"}


def row(desc, amount="-10.00", line=2):
    return Row(line, "2026-09-01", desc, amount, parse_amount(amount))


def choice(top, p=0.95, conf=None, rest=None):
    probs = {c: (1 - p) / (len(CATS) - 1) for c in CATS}
    probs[top] = p
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": p if conf is None else conf}


def fake(answer_for):
    """A transport whose answers come from a function of the description."""
    calls = []

    def transport(payload):
        calls.append(payload)
        answers = {}
        for i, tx in enumerate(payload["state"]["transactions"]):
            answers[f"q{i}"] = answer_for(tx["description"])
        return {"model": "jev-1.13.0", "answers": answers}

    transport.calls = calls
    return transport


# ---------- money ----------

def test_money_parsing_and_exact_arithmetic():
    assert parse_amount("$1,234.50") == Decimal("1234.50")
    assert parse_amount("(54.99)") == Decimal("-54.99")
    assert parse_amount("") == Decimal("0")
    assert parse_amount("0.1") + parse_amount("0.2") == parse_amount("0.3")  # floats would fail this
    assert fmt(Decimal("-1234.5")) == "-1,234.50"
    try:
        parse_amount("twelve")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# ---------- merchants & privacy ----------

def test_merchant_key_strips_noise():
    assert merchant_key("SQ *CORNER BAKERY 0042 SEATTLE WA") == "SQ CORNER BAKERY"
    assert merchant_key("AMZN Mktp US*2K4TT0") == "AMZN MKTP US"
    assert merchant_key("DELTA AIR 0062345") == "DELTA AIR"
    assert merchant_key("98765") == "98765"  # nothing but digits still yields a key


def test_redact_hides_emails_and_long_digit_runs():
    out = redact("ZELLE jo@example.com ACCT 123456789 STORE 42")
    assert "example.com" not in out and "123456789" not in out
    assert "STORE 42" in out  # short numbers are left alone


def test_payload_is_redacted_by_default_and_uses_documented_shape():
    p = build_payload([row("PAYMENT TO 123456789")], CATS, "jev-1.13.0", redact_text=True)
    assert p["state"]["transactions"][0]["description"] == "PAYMENT TO #####"
    q = p["questions"]["q0"]
    assert q["type"] == "choice" and q["criteria"] == CATS
    assert "`transactions[0]`" in q["instructions"]


# ---------- rules ----------

def test_rules_match_whole_words_and_prefer_longest():
    r = Rules([{"match": "CHECK", "category": "software"}, {"match": "CHECK CASHING", "category": "meals"}])
    assert r.match("CHECK") == "software"
    assert r.match("CHECKERS DRIVE IN") is None      # not a whole word
    assert r.match("PAYCHECK PLUS") is None
    assert r.match("CHECK CASHING STORE") == "meals"  # longest wins


def test_rules_reject_unknown_category_on_load():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rules.json")
        json.dump({"rules": [{"match": "X", "category": "nonsense"}]}, open(path, "w"))
        try:
            Rules.load(path, CATS)
        except ConfigError:
            return
        raise AssertionError("expected ConfigError")


def test_learn_skips_ambiguous_unknown_and_covered():
    rules = Rules([{"match": "ADOBE", "category": "software"}])
    report = learn(
        [("NEW CAFE", "meals"), ("NEW CAFE", "meals"),            # consistent -> rule
         ("AMZN MKTP US", "software"), ("AMZN MKTP US", "meals"),  # conflicting -> ambiguous
         ("MYSTERY", "sofware"),                                   # typo -> unknown
         ("ADOBE CREATIVE CLOUD", "software")],                    # already covered
        rules, CATS)
    assert report.added == [("NEW CAFE", "meals")]
    assert "AMZN MKTP US" in report.ambiguous
    assert report.unknown_category == [("MYSTERY", "sofware")]
    assert report.already_covered == 1


# ---------- decision gate ----------

def test_decide_books_only_confident_real_categories():
    assert decide(choice("software", 0.95), CATS, 0.8)[:2] == ("software", "auto")
    cat, status, conf, suggested = decide(choice("software", 0.6), CATS, 0.8)
    assert (cat, status) == ("", "review") and "software (0.60)" in suggested
    assert decide(choice("other", 0.99), CATS, 0.8)[:2] == ("", "review")       # 'other' is never auto-booked
    assert decide(choice("bogus", 0.99), CATS, 0.8)[:2] == ("", "review")       # unknown id
    assert decide(None, CATS, 0.8)[:2] == ("", "review")                         # missing answer
    assert decide({"type": "noul", "noul": 0.9}, CATS, 0.8)[:2] == ("", "review")  # wrong type
    # confidence absent -> falls back to the top probability
    no_conf = choice("meals", 0.9)
    del no_conf["confidence"]
    assert decide(no_conf, CATS, 0.8)[:2] == ("meals", "auto")


# ---------- pipeline ----------

def test_rules_skip_the_model_entirely():
    t = fake(lambda d: choice("travel"))
    results, stats = classify([row("ADOBE *CC")], CATS, Rules([{"match": "ADOBE CC", "category": "software"}]),
                              t, Cache(None), "m")
    assert results[0].status == "rule" and results[0].category == "software"
    assert t.calls == [] and stats.rule_hits == 1


def test_same_merchant_is_asked_once_but_direction_matters():
    t = fake(lambda d: choice("meals"))
    rows = [row("CAFE 1", "-5.00"), row("CAFE 2", "-7.00"), row("CAFE 3", "+4.00")]  # keys all 'CAFE'
    results, stats = classify(rows, CATS, Rules(), t, Cache(None), "m")
    assert stats.groups == 2  # CAFE (money out) and CAFE (money in) are separate questions
    assert len(t.calls[0]["questions"]) == 2
    assert all(r.status == "auto" for r in results)


def test_per_row_mode_asks_about_every_row():
    t = fake(lambda d: choice("meals"))
    _, stats = classify([row("CAFE 1"), row("CAFE 2")], CATS, Rules(), t, Cache(None), "m", dedupe=False)
    assert stats.groups == 2


def test_batching_splits_requests_and_maps_answers_back_correctly():
    t = fake(lambda d: choice("travel") if d.startswith("T") else choice("meals"))
    rows = [row(f"{'T' if i % 2 else 'M'}ERCHANT{chr(65 + i)}") for i in range(7)]
    results, stats = classify(rows, CATS, Rules(), t, Cache(None), "m", batch_size=3, workers=1)
    assert len(t.calls) == 3 and stats.calls == 3
    for r in results:
        assert r.category == ("travel" if r.row.description.startswith("T") else "meals")


def test_low_confidence_goes_to_review_with_blank_category():
    t = fake(lambda d: choice("software", 0.5))
    results, _ = classify([row("MYSTERY CO")], CATS, Rules(), t, Cache(None), "m")
    r = results[0]
    assert r.status == "review" and r.category == "" and "software (0.50)" in r.suggested


def test_second_run_is_answered_from_cache():
    t = fake(lambda d: choice("meals"))
    cache = Cache(None)
    classify([row("CAFE")], CATS, Rules(), t, cache, "m")
    _, stats = classify([row("CAFE")], CATS, Rules(), t, cache, "m")
    assert len(t.calls) == 1 and stats.cache_hits == 1 and stats.calls == 0


def test_malformed_response_raises_clearly():
    try:
        classify([row("X")], CATS, Rules(), lambda p: {"oops": 1}, Cache(None), "m")
    except JevError as err:
        assert "answers" in str(err)
    else:
        raise AssertionError("expected JevError")


# ---------- CSV ----------

def write_csv(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


def test_reads_amount_column_and_debit_credit_columns():
    a = read_statement(write_csv("Posted,Memo,Amt\n2026-09-01,CAFE,($4.50)\n"))
    assert a[0].amount == Decimal("-4.50") and a[0].description == "CAFE"
    b = read_statement(write_csv("Date,Description,Debit,Credit\n2026-09-01,CAFE,4.50,\n2026-09-02,CLIENT,,100.00\n"))
    assert [r.amount for r in b] == [Decimal("-4.50"), Decimal("100.00")]


def test_csv_errors_are_specific():
    for text, fragment in [("Date,Foo,Amount\n1,2,3\n", "description column"),
                           ("Date,Description\n1,2\n", "amount column"),
                           ("Date,Description,Amount\n1,CAFE,abc\n", "line 2")]:
        try:
            read_statement(write_csv(text))
        except ValueError as err:
            assert fragment in str(err), (fragment, str(err))
        else:
            raise AssertionError(f"expected ValueError for {fragment}")


# ---------- client retry (no real network) ----------

class FakeResp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "e", {}, io.BytesIO(b"{}"))


def test_client_retries_overload_but_not_auth_errors():
    n, sleeps = [], []

    def flaky(request, timeout):
        n.append(1)
        if len(n) < 3:
            raise http_error(529)
        return FakeResp({"model": "jev-1.13.0", "answers": {}})

    out = make_http_transport("k", opener=flaky, sleep=sleeps.append)({})
    assert out["model"] == "jev-1.13.0" and sleeps == [1.0, 2.0]

    def denied(request, timeout):
        raise http_error(401)

    try:
        make_http_transport("bad", opener=denied, sleep=lambda s: None)({})
    except JevError as err:
        assert "401" in str(err)
    else:
        raise AssertionError("expected JevError")


# ---------- CLI: the whole loop, offline ----------

def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_full_loop_review_then_learn_then_fewer_reviews():
    cats = os.path.join(EX, "categories.json")
    statement = os.path.join(EX, "statement.csv")
    with tempfile.TemporaryDirectory() as d:
        out1, rules = os.path.join(d, "out1.csv"), os.path.join(d, "rules.json")

        code, text, _ = run("categorize", statement, "--categories", cats, "--rules", rules, "--out", out1, "--mock")
        assert code == 0 and "MOCK MODE" in text
        rows = list(csv.DictReader(open(out1, newline="")))
        review = [r for r in rows if r["status"] == "review"]
        assert review and all(r["category"] == "" for r in review)  # never silently decided

        # The human fills in the blanks.
        answers = {"AMZN MKTP US": "office_supplies", "VENMO PAYMENT JMILLER": "professional_fees",
                   "CHECK": "professional_fees", "TRANSFER TO SAVINGS": "owner_transfer",
                   "SEATTLE LICENSING DEPT": "taxes_licenses"}
        for r in review:
            r["category"] = answers[r["merchant_key"]]
        with open(out1, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

        code, text, _ = run("learn", out1, "--categories", cats, "--rules", rules)
        assert code == 0 and "Added 5 rule(s)" in text

        out2 = os.path.join(d, "out2.csv")
        code, text, _ = run("categorize", statement, "--categories", cats, "--rules", rules, "--out", out2, "--mock")
        assert code == 0 and "0 need review" in text
        rows2 = list(csv.DictReader(open(out2, newline="")))
        assert sum(r["status"] == "rule" for r in rows2) == len(review)  # exactly the rows you reviewed
        assert all(r["category"] for r in rows2)  # nothing left blank


def test_corrected_status_lets_you_fix_an_auto_booked_row():
    cats = os.path.join(EX, "categories.json")
    with tempfile.TemporaryDirectory() as d:
        reviewed, rules = os.path.join(d, "r.csv"), os.path.join(d, "rules.json")
        with open(reviewed, "w", newline="") as f:
            f.write("date,description,amount,category,status,confidence,suggested,merchant_key\n"
                    "2026-09-01,GITHUB INC,-4.00,professional_fees,corrected,0.95,,GITHUB INC\n"
                    "2026-09-02,ADOBE,-5.00,software,auto,0.9,,ADOBE\n")  # auto rows are NOT learned from
        code, text, _ = run("learn", reviewed, "--categories", cats, "--rules", rules)
        assert code == 0 and "Added 1 rule(s)" in text
        assert json.load(open(rules))["rules"] == [{"match": "GITHUB INC", "category": "professional_fees"}]


def test_dry_run_sends_nothing_and_shows_redacted_payload():
    cats = os.path.join(EX, "categories.json")
    old = os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        code, out, _ = run("categorize", os.path.join(EX, "statement.csv"), "--categories", cats, "--dry-run")
    finally:
        if old is not None:
            os.environ["TYPESAFE_API_KEY"] = old
    payload = json.loads(out)
    assert code == 0 and payload["model"] == "jev-latest"
    descriptions = " ".join(t["description"] for t in payload["state"]["transactions"])
    assert "8842119" not in descriptions and "0062345" not in descriptions  # long digit runs hidden


def test_missing_key_is_a_clear_error():
    cats = os.path.join(EX, "categories.json")
    old = os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        code, _, err = run("categorize", os.path.join(EX, "statement.csv"), "--categories", cats)
    finally:
        if old is not None:
            os.environ["TYPESAFE_API_KEY"] = old
    assert code == 2 and "TYPESAFE_API_KEY" in err


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"All {len(tests)} tests passed.")
