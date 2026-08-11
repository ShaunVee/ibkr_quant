"""Unit tests for the IBKR Flex XML parsing — no network involved."""

from __future__ import annotations

import pytest

from quantbot.ingestion.brokers.ibkr_flex import (
    FlexError,
    parse_send_response,
    parse_statement,
)

SEND_SUCCESS = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="01 August, 2026 07:00 AM EDT">
  <Status>Success</Status>
  <ReferenceCode>1234567890</ReferenceCode>
  <Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
</FlexStatementResponse>"""

SEND_FAIL = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="01 August, 2026 07:00 AM EDT">
  <Status>Fail</Status>
  <ErrorCode>1015</ErrorCode>
  <ErrorMessage>Token has expired.</ErrorMessage>
</FlexStatementResponse>"""

STATEMENT_IN_PROGRESS = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp="01 August, 2026 07:00 AM EDT">
  <Status>Warn</Status>
  <ErrorCode>1019</ErrorCode>
  <ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage>
</FlexStatementResponse>"""

STATEMENT_OK = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="quantbot" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20260731" toDate="20260731">
      <AccountInformation accountId="U1234567" currency="USD" name="Test Account"/>
      <EquitySummaryInBase accountId="U1234567" reportDate="20260731" total="152340.55"/>
      <CashReport>
        <CashReportCurrency accountId="U1234567" currency="BASE_SUMMARY" endingCash="12500.00"/>
        <CashReportCurrency accountId="U1234567" currency="USD" endingCash="12500.00"/>
      </CashReport>
      <OpenPositions>
        <OpenPosition accountId="U1234567" symbol="AAPL" conid="265598" assetCategory="STK"
          position="200" markPrice="230.15" positionValue="46030.00"
          costBasisPrice="150.00" costBasisMoney="30000.00" currency="USD"/>
        <OpenPosition accountId="U1234567" symbol="MSFT" conid="272093" assetCategory="STK"
          position="100" markPrice="420.00" positionValue="42000.00"
          costBasisPrice="300.00" costBasisMoney="30000.00" currency="USD"/>
        <OpenPosition accountId="U1234567" symbol="NVDA" conid="4815747" assetCategory="STK"
          position="50" markPrice="118.20" positionValue="5910.00"
          costBasisPrice="90.00" costBasisMoney="4500.00" currency="USD"/>
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_parse_send_response_success():
    assert parse_send_response(SEND_SUCCESS) == "1234567890"


def test_parse_send_response_failure_raises():
    with pytest.raises(FlexError, match="Token has expired"):
        parse_send_response(SEND_FAIL)


def test_parse_statement_in_progress_signals_retry():
    with pytest.raises(FlexError, match="STATEMENT_IN_PROGRESS"):
        parse_statement(STATEMENT_IN_PROGRESS)


def test_parse_statement_holdings():
    portfolio = parse_statement(STATEMENT_OK)

    assert portfolio.account.account_id == "U1234567"
    assert portfolio.account.base_currency == "USD"
    assert portfolio.account.net_liquidation == pytest.approx(152340.55)
    assert portfolio.account.total_cash == pytest.approx(12500.00)

    assert len(portfolio.holdings) == 3
    aapl = next(h for h in portfolio.holdings if h.symbol == "AAPL")
    assert aapl.quantity == pytest.approx(200)
    assert aapl.market_price == pytest.approx(230.15)
    assert aapl.market_value == pytest.approx(46030.00)
    assert aapl.avg_cost == pytest.approx(150.00)
    assert aapl.con_id == 265598
    assert aapl.asset_class == "STK"

    # Derived properties.
    assert aapl.cost_basis == pytest.approx(30000.00)
    assert aapl.unrealized_pnl == pytest.approx(16030.00)

    # Total invested value = sum of position values.
    assert portfolio.invested_value == pytest.approx(46030.00 + 42000.00 + 5910.00)
