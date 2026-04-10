"""Tests for report commands and budget check."""

from unittest.mock import MagicMock, patch
from uuid import UUID

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.commands.report import (
    _change_pct,
    _detect_subscriptions,
    _filter_income_txs,
    _filter_spending_txs,
    _month_key,
    _since_date,
    _trend_arrow,
)

runner = CliRunner()


# ── Mock factories ───────────────────────────────────────────────────────────


def _mock_tx(
    payee="Amazon",
    amount=-45990,
    category="Shopping",
    category_group="Lifestyle",
    account="Checking",
    tx_date="2026-01-15",
    tid="b3cc2552-e3a3-4c1c-a7c0-127fbe8509d0",
    transfer_account_id=None,
):
    t = MagicMock()
    t.id = UUID(tid)
    t.var_date = tx_date
    t.amount = amount
    t.payee_name = payee
    t.category_name = category
    t.category_group_name = category_group
    t.account_name = account
    t.deleted = False
    t.transfer_account_id = transfer_account_id
    t.to_dict.return_value = {
        "id": tid,
        "date": tx_date,
        "amount": amount,
        "payee_name": payee,
        "category_name": category,
    }
    return t


def _mock_month(to_be_budgeted=0, age_of_money=None):
    m = MagicMock()
    m.to_be_budgeted = to_be_budgeted
    m.age_of_money = age_of_money
    return m


def _mock_account(name="Checking", acct_type="checking", balance=100000, closed=False, last_reconciled_at=None):
    a = MagicMock()
    a.name = name
    a.type = acct_type
    a.closed = closed
    a.balance = balance
    a.last_reconciled_at = last_reconciled_at
    return a


def _mock_category(name="Groceries", balance=50000, hidden=False, deleted=False, goal_under_funded=0):
    c = MagicMock()
    c.name = name
    c.balance = balance
    c.hidden = hidden
    c.deleted = deleted
    c.goal_under_funded = goal_under_funded
    return c


def _mock_category_group(name="Bills", categories=None):
    g = MagicMock()
    g.name = name
    g.categories = categories or []
    return g


# ── Helper unit tests ────────────────────────────────────────────────────────


class TestHelpers:
    def test_month_key(self):
        assert _month_key("2026-01-15") == "2026-01"
        assert _month_key("2025-12-01") == "2025-12"

    def test_since_date_with_explicit(self):
        assert _since_date(6, "2025-06-01") == "2025-06-01"

    def test_since_date_months_back(self):
        result = _since_date(3)
        # Should return a date string (YYYY-MM-DD format), day=01
        assert result.endswith("-01")

    def test_change_pct(self):
        assert _change_pct(100000, 120000) == 20.0
        assert _change_pct(100000, 80000) == -20.0
        assert _change_pct(0, 100000) is None

    def test_trend_arrow_increase(self):
        result = _trend_arrow(100000, 130000)
        assert "↑" in result
        assert "red" in result

    def test_trend_arrow_decrease(self):
        result = _trend_arrow(100000, 70000)
        assert "↓" in result
        assert "green" in result

    def test_trend_arrow_stable(self):
        result = _trend_arrow(100000, 105000)
        assert "→" in result

    def test_trend_arrow_zero_both(self):
        result = _trend_arrow(0, 0)
        assert "—" in result

    def test_trend_arrow_new(self):
        result = _trend_arrow(0, 50000)
        assert "new" in result


class TestFilters:
    _cat_map = {"Shopping": "Lifestyle", "Groceries": "Variable Expenses"}

    def test_filter_spending_txs_basic(self):
        txs = [_mock_tx(amount=-10000)]
        result = _filter_spending_txs(txs, self._cat_map)
        assert len(result) == 1

    def test_filter_spending_excludes_income(self):
        txs = [_mock_tx(amount=50000)]
        result = _filter_spending_txs(txs, self._cat_map)
        assert len(result) == 0

    def test_filter_spending_excludes_transfers(self):
        txs = [_mock_tx(amount=-10000, transfer_account_id="abc")]
        result = _filter_spending_txs(txs, self._cat_map)
        assert len(result) == 0

    def test_filter_spending_excludes_internal(self):
        cat_map = {"Uncategorized": "Internal Master Category"}
        txs = [_mock_tx(amount=-10000, category="Uncategorized")]
        result = _filter_spending_txs(txs, cat_map)
        assert len(result) == 0

    def test_filter_spending_excludes_credit_card_payments(self):
        cat_map = {"CC Payment": "Credit Card Payments"}
        txs = [_mock_tx(amount=-10000, category="CC Payment")]
        result = _filter_spending_txs(txs, cat_map)
        assert len(result) == 0

    def test_filter_spending_excludes_deleted(self):
        tx = _mock_tx(amount=-10000)
        tx.deleted = True
        result = _filter_spending_txs([tx], self._cat_map)
        assert len(result) == 0

    def test_filter_income_txs_basic(self):
        txs = [_mock_tx(amount=500000, payee="Employer")]
        result = _filter_income_txs(txs)
        assert len(result) == 1

    def test_filter_income_excludes_starting_balance(self):
        txs = [_mock_tx(amount=500000, payee="Starting Balance")]
        result = _filter_income_txs(txs)
        assert len(result) == 0

    def test_filter_income_excludes_manual_adjustment(self):
        txs = [_mock_tx(amount=500000, payee="Manual Balance Adjustment")]
        result = _filter_income_txs(txs)
        assert len(result) == 0

    def test_filter_income_excludes_negative(self):
        txs = [_mock_tx(amount=-50000)]
        result = _filter_income_txs(txs)
        assert len(result) == 0

    def test_filter_income_excludes_transfers(self):
        txs = [_mock_tx(amount=50000, transfer_account_id="abc")]
        result = _filter_income_txs(txs)
        assert len(result) == 0


# ── CLI command tests ────────────────────────────────────────────────────────


def _mock_categories_for_reports():
    """Return category groups that map test category names to groups."""
    return [
        _mock_category_group("Lifestyle", [_mock_category("Shopping")]),
        _mock_category_group("Needs", [_mock_category("Groceries"), _mock_category("Rent")]),
        _mock_category_group("Income", [_mock_category("Income")]),
    ]


@patch("ynab_tools.commands.report.client")
def test_report_spending(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Amazon", amount=-45990, category="Shopping", tx_date="2026-01-15"),
        _mock_tx(
            payee="Walmart",
            amount=-30000,
            category="Groceries",
            tx_date="2026-02-10",
            tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "spending", "--months", "3"])
    assert result.exit_code == 0
    assert "Spending by Category" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_spending_json(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Amazon", amount=-45990, category="Shopping", tx_date="2026-01-15"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "spending", "--months", "3", "--json"])
    assert result.exit_code == 0
    assert "Shopping" in result.output
    assert "Lifestyle" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_spending_no_data(mock_client):
    mock_client.list_transactions.return_value = []
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "spending"])
    assert result.exit_code == 0
    assert "No spending" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_income(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Employer", amount=500000, category="Income", tx_date="2026-01-01"),
        _mock_tx(payee="Amazon", amount=-45990, category="Shopping", tx_date="2026-01-15"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "income", "--months", "3"])
    assert result.exit_code == 0
    assert "Income vs Expenses" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_income_json(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Employer", amount=500000, category="Income", tx_date="2026-01-01"),
        _mock_tx(payee="Amazon", amount=-45990, category="Shopping", tx_date="2026-01-15"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "income", "--months", "3", "--json"])
    assert result.exit_code == 0
    assert "savings_rate" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_income_savings_rate_calculation(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Employer", amount=1000000, category="Income", tx_date="2026-01-01"),
        _mock_tx(payee="Rent", amount=-500000, category="Rent", tx_date="2026-01-05"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "income", "--months", "3", "--json"])
    assert result.exit_code == 0
    # Income: 1000000, Expenses: 500000, Net: 500000, Rate: 50%
    assert "50.0" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_payees(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Amazon", amount=-45990, tx_date="2026-01-15"),
        _mock_tx(payee="Amazon", amount=-20000, tx_date="2026-01-20", tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        _mock_tx(payee="Starbucks", amount=-5000, tx_date="2026-01-18", tid="cccccccc-dddd-eeee-ffff-111111111111"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "payees", "--months", "3"])
    assert result.exit_code == 0
    assert "Amazon" in result.output
    assert "Starbucks" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_payees_json(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Amazon", amount=-45990, tx_date="2026-01-15"),
        _mock_tx(payee="Amazon", amount=-20000, tx_date="2026-01-20", tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "payees", "--months", "3", "--json"])
    assert result.exit_code == 0
    assert "top_category" in result.output
    assert "average" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_payees_limit(mock_client):
    txs = [
        _mock_tx(
            payee=f"Payee{i}",
            amount=-(i * 1000),
            tx_date="2026-01-15",
            tid=f"{'0' * 7}{i:01x}-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        for i in range(1, 10)
    ]
    mock_client.list_transactions.return_value = txs
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "payees", "--limit", "3"])
    assert result.exit_code == 0
    assert "Top 3" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_trends(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(amount=-50000, category="Groceries", tx_date="2026-01-15"),
        _mock_tx(
            amount=-70000,
            category="Groceries",
            tx_date="2026-02-15",
            tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
        _mock_tx(
            amount=-30000,
            category="Shopping",
            tx_date="2026-01-20",
            tid="cccccccc-dddd-eeee-ffff-111111111111",
        ),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "trends", "--months", "3"])
    assert result.exit_code == 0
    assert "Spending Trends" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_trends_json(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_tx(amount=-50000, category="Groceries", tx_date="2026-01-15"),
        _mock_tx(
            amount=-70000,
            category="Groceries",
            tx_date="2026-02-15",
            tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "trends", "--months", "3", "--json"])
    assert result.exit_code == 0
    assert "change_pct" in result.output


# ── Budget check tests ───────────────────────────────────────────────────────


@patch("ynab_tools.commands.budget.client")
def test_budget_check_all_pass(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = [
        _mock_account(last_reconciled_at="2026-03-20"),
    ]
    mock_client.list_categories.return_value = [
        _mock_category_group("Bills", [_mock_category("Rent", balance=50000)]),
    ]
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "PASS" in result.output


@patch("ynab_tools.commands.budget.client")
def test_budget_check_overspent(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = [
        _mock_account(last_reconciled_at="2026-03-20"),
    ]
    mock_client.list_categories.return_value = [
        _mock_category_group("Bills", [_mock_category("Groceries", balance=-25000)]),
    ]
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "FAIL" in result.output
    assert "overspent" in result.output


@patch("ynab_tools.commands.budget.client")
def test_budget_check_rta_positive(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=150000)
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "assign" in result.output.lower()


@patch("ynab_tools.commands.budget.client")
def test_budget_check_rta_negative(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=-50000)
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "FAIL" in result.output
    assert "overbudgeted" in result.output.lower()


@patch("ynab_tools.commands.budget.client")
def test_budget_check_stale_reconciliation(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = [
        _mock_account(name="Checking", last_reconciled_at="2026-01-01"),
    ]
    mock_client.list_categories.return_value = []
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "reconcil" in result.output.lower()


@patch("ynab_tools.commands.budget.client")
def test_budget_check_cc_mismatch(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = [
        _mock_account(name="Visa", acct_type="creditCard", balance=-250000, last_reconciled_at="2026-03-20"),
    ]
    mock_client.list_categories.return_value = [
        _mock_category_group("Credit Card Payments", [_mock_category("Visa", balance=200000)]),
    ]
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "mismatch" in result.output.lower()


# ── Subscription detection tests ──────────────────────────────────────────


def _monthly_txs(payee, amounts, start="2025-04-15"):
    """Generate monthly transactions for a payee."""
    from datetime import date as d
    from datetime import timedelta

    txs = []
    dt = d.fromisoformat(start)
    for i, amt in enumerate(amounts):
        txs.append(
            _mock_tx(
                payee=payee,
                amount=-abs(amt),
                tx_date=dt.isoformat(),
                tid=f"{'0' * 7}{i:01x}-aaaa-bbbb-cccc-{'0' * 12}",
            )
        )
        dt = dt + timedelta(days=30)
    return txs


class TestSubscriptionDetection:
    def test_detects_monthly_fixed(self):
        """3+ transactions at same amount with ~30 day gaps = Fixed subscription."""
        txs = _monthly_txs("Netflix", [15990, 15990, 15990, 15990])
        result = _detect_subscriptions(txs, min_count=3)
        assert len(result) == 1
        assert result[0]["payee"] == "Netflix"
        assert result[0]["status"] == "Fixed"
        assert result[0]["frequency"] == "Monthly"

    def test_detects_monthly_variable(self):
        """Varying amounts = Variable subscription."""
        txs = _monthly_txs("Electric Co", [85000, 120000, 95000, 110000])
        result = _detect_subscriptions(txs, min_count=3)
        assert len(result) == 1
        assert result[0]["status"] == "Variable"

    def test_skips_few_transactions(self):
        """Payee with < min_count transactions is excluded."""
        txs = _monthly_txs("OneTime", [10000, 10000])
        result = _detect_subscriptions(txs, min_count=3)
        assert len(result) == 0

    def test_skips_irregular_intervals(self):
        """Transactions not at monthly intervals are excluded."""
        txs = [
            _mock_tx(payee="Random", amount=-5000, tx_date="2025-04-01", tid="00000001-aaaa-bbbb-cccc-000000000000"),
            _mock_tx(payee="Random", amount=-5000, tx_date="2025-04-05", tid="00000002-aaaa-bbbb-cccc-000000000000"),
            _mock_tx(payee="Random", amount=-5000, tx_date="2025-04-10", tid="00000003-aaaa-bbbb-cccc-000000000000"),
            _mock_tx(payee="Random", amount=-5000, tx_date="2025-07-15", tid="00000004-aaaa-bbbb-cccc-000000000000"),
        ]
        result = _detect_subscriptions(txs, min_count=3)
        assert len(result) == 0

    def test_annual_cost_calculation(self):
        """Annual cost = avg_amount * 12."""
        txs = _monthly_txs("Spotify", [9990, 9990, 9990])
        result = _detect_subscriptions(txs, min_count=3)
        assert len(result) == 1
        assert result[0]["annual_cost"] == result[0]["monthly_cost"] * 12

    def test_multiple_payees(self):
        """Multiple subscriptions detected independently."""
        txs = _monthly_txs("Netflix", [15990, 15990, 15990])
        # Need different UUIDs for second payee
        t2 = []
        from datetime import date as d
        from datetime import timedelta

        dt = d.fromisoformat("2025-04-20")
        for i in range(3):
            t2.append(
                _mock_tx(
                    payee="Gym",
                    amount=-50000,
                    tx_date=dt.isoformat(),
                    tid=f"{'1' * 7}{i:01x}-aaaa-bbbb-cccc-{'0' * 12}",
                )
            )
            dt = dt + timedelta(days=30)
        result = _detect_subscriptions(txs + t2, min_count=3)
        assert len(result) == 2
        payees = {r["payee"] for r in result}
        assert payees == {"Netflix", "Gym"}


# ── Subscription CLI tests ──────────────────────────────────────────────


@patch("ynab_tools.commands.report.client")
def test_report_subscriptions_table(mock_client):
    mock_client.list_transactions.return_value = _monthly_txs("Netflix", [15990] * 4)
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "subscriptions", "--months", "12"])
    assert result.exit_code == 0
    assert "Recurring Charges" in result.output
    assert "Netflix" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_subscriptions_json(mock_client):
    mock_client.list_transactions.return_value = _monthly_txs("Netflix", [15990] * 4)
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "subscriptions", "--json"])
    assert result.exit_code == 0
    assert "Netflix" in result.output
    assert "annual_cost" in result.output
    assert "status" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_subscriptions_no_recurring(mock_client):
    # Single transaction per payee — nothing recurring
    mock_client.list_transactions.return_value = [
        _mock_tx(payee="Amazon", amount=-10000, tx_date="2026-01-15"),
    ]
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "subscriptions"])
    assert result.exit_code == 0
    assert "No recurring" in result.output


@patch("ynab_tools.commands.report.client")
def test_report_subscriptions_min_count(mock_client):
    # 3 transactions but min_count=4 should exclude
    mock_client.list_transactions.return_value = _monthly_txs("Netflix", [15990] * 3)
    mock_client.list_categories.return_value = _mock_categories_for_reports()

    result = runner.invoke(app, ["report", "subscriptions", "--min-count", "4"])
    assert result.exit_code == 0
    assert "No recurring" in result.output


# ── Budget check — new checks ─────────────────────────────────────────────


def _mock_category_with_goal(name="Savings", balance=50000, goal_under_funded=0, hidden=False, deleted=False):
    c = MagicMock()
    c.name = name
    c.balance = balance
    c.goal_under_funded = goal_under_funded
    c.hidden = hidden
    c.deleted = deleted
    return c


@patch("ynab_tools.commands.budget.client")
def test_budget_check_underfunded_goals(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = [
        _mock_account(last_reconciled_at="2026-03-20"),
    ]
    mock_client.list_categories.return_value = [
        _mock_category_group(
            "Savings",
            [
                _mock_category_with_goal("Emergency Fund", balance=100000, goal_under_funded=50000),
            ],
        ),
    ]
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "underfunded" in result.output.lower()
    assert "Emergency Fund" in result.output


@patch("ynab_tools.commands.budget.client")
def test_budget_check_age_of_money_good(mock_client):
    month = _mock_month(to_be_budgeted=0)
    month.age_of_money = 45
    mock_client.get_month.return_value = month
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "Age of Money: 45 days" in result.output
    assert "PASS" in result.output


@patch("ynab_tools.commands.budget.client")
def test_budget_check_age_of_money_low(mock_client):
    month = _mock_month(to_be_budgeted=0)
    month.age_of_money = 15
    mock_client.get_month.return_value = month
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    mock_client.list_transactions.return_value = []

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "Age of Money: 15 days" in result.output
    assert "WARN" in result.output


@patch("ynab_tools.commands.budget.client")
def test_budget_check_uncategorized(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    # Transaction without category_id
    tx = _mock_tx(payee="Unknown", amount=-5000, tx_date="2026-03-15")
    tx.category_id = None
    mock_client.list_transactions.return_value = [tx]

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "uncategorized" in result.output.lower()


@patch("ynab_tools.commands.budget.client")
def test_budget_check_unapproved(mock_client):
    mock_client.get_month.return_value = _mock_month(to_be_budgeted=0)
    mock_client.list_accounts.return_value = []
    mock_client.list_categories.return_value = []
    tx = _mock_tx(payee="Store", amount=-10000, tx_date="2026-03-15")
    tx.approved = False
    tx.category_id = "some-cat-id"
    mock_client.list_transactions.return_value = [tx]

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "unapproved" in result.output.lower()


@patch("ynab_tools.commands.budget.client")
def test_budget_check_all_new_checks_pass(mock_client):
    month = _mock_month(to_be_budgeted=0)
    month.age_of_money = 60
    mock_client.get_month.return_value = month
    mock_client.list_accounts.return_value = [
        _mock_account(last_reconciled_at="2026-03-20"),
    ]
    mock_client.list_categories.return_value = [
        _mock_category_group(
            "Bills",
            [
                _mock_category_with_goal("Rent", balance=50000, goal_under_funded=0),
            ],
        ),
    ]
    tx = _mock_tx(payee="Store", amount=-10000, tx_date="2026-03-15")
    tx.approved = True
    tx.category_id = "some-cat-id"
    mock_client.list_transactions.return_value = [tx]

    result = runner.invoke(app, ["budget", "check"])
    assert result.exit_code == 0
    assert "All checks passed" in result.output
