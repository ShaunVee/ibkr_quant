"""IBKR Flex Web Service adapter.

Flex Web Service is the retail-friendly, token-based path (no gateway, no OAuth):
  1. POST/GET SendRequest?t=TOKEN&q=QUERY_ID&v=3  -> reference code
  2. GET  GetStatement?t=TOKEN&q=REFERENCE_CODE&v=3 -> statement XML (may need retries
     while IBKR generates it)

Set up once in Client Portal -> Performance & Reports -> Flex Queries:
  - Create an Activity/Custom Flex Query including *Open Positions* + *Account Information*.
  - Enable Flex Web Service and generate a token.

The XML parsing is deliberately split into pure functions so it can be unit-tested
against a saved fixture with no network access.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import requests

from quantbot.config import Config
from quantbot.ingestion.brokers.base import BrokerAdapter
from quantbot.models import AccountSummary, Holding, Portfolio

log = logging.getLogger(__name__)

_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
_SEND_URL = f"{_BASE}/SendRequest"
_GET_URL = f"{_BASE}/GetStatement"
_API_VERSION = "3"

# IBKR "Statement generation in progress" status code -> retry.
_IN_PROGRESS_CODE = "1019"


class FlexError(RuntimeError):
    """Raised when the Flex Web Service returns an error or unparseable response."""


def _to_float(val: str | None) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _to_int(val: str | None) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None


def parse_send_response(xml_text: str) -> str:
    """Parse a SendRequest response, returning the reference code.

    Raises FlexError if the status is not Success.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:  # pragma: no cover - defensive
        raise FlexError(f"Could not parse SendRequest XML: {exc}") from exc

    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        code = root.findtext("ErrorCode") or "?"
        msg = root.findtext("ErrorMessage") or "unknown error"
        raise FlexError(f"SendRequest failed (status={status}, code={code}): {msg}")

    reference_code = (root.findtext("ReferenceCode") or "").strip()
    if not reference_code:
        raise FlexError("SendRequest succeeded but no ReferenceCode returned")
    return reference_code


def parse_statement(xml_text: str) -> Portfolio:
    """Parse a GetStatement response into a normalized Portfolio.

    Raises FlexError if the statement is still generating or reports an error.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FlexError(f"Could not parse statement XML: {exc}") from exc

    # A not-yet-ready or errored statement comes back as a bare status document.
    status = root.findtext("Status")
    if status is not None and status.strip() != "Success":
        code = (root.findtext("ErrorCode") or "").strip()
        msg = root.findtext("ErrorMessage") or "unknown error"
        if code == _IN_PROGRESS_CODE:
            raise FlexError("STATEMENT_IN_PROGRESS")
        raise FlexError(f"GetStatement error (code={code}): {msg}")

    stmt = root.find(".//FlexStatement")
    if stmt is None:
        raise FlexError("No FlexStatement found in response")

    account_id = stmt.get("accountId", "UNKNOWN")

    # Account information (currency, name).
    acct_info = stmt.find(".//AccountInformation")
    base_currency = "USD"
    if acct_info is not None:
        base_currency = acct_info.get("currency") or base_currency

    holdings = _parse_open_positions(stmt, account_id)

    # Net liquidation: prefer EquitySummaryInBase 'total' if present.
    net_liq = _parse_net_liquidation(stmt)
    total_cash = _parse_total_cash(stmt)

    # The session the numbers actually belong to. IBKR's Flex batch runs overnight, so a
    # statement fetched shortly after a close often still reflects the *previous* session;
    # capturing this lets the pipeline label the brief honestly and detect stale data.
    report_date = _parse_report_date(stmt)

    now = datetime.now(timezone.utc)
    account = AccountSummary(
        account_id=account_id,
        base_currency=base_currency,
        net_liquidation=net_liq,
        total_cash=total_cash,
        as_of=now,
    )
    return Portfolio(
        account=account, holdings=holdings, as_of=now, report_date=report_date
    )


def _parse_open_positions(stmt: ET.Element, account_id: str) -> list[Holding]:
    holdings: list[Holding] = []
    for pos in stmt.findall(".//OpenPosition"):
        symbol = pos.get("symbol")
        if not symbol:
            continue
        holdings.append(
            Holding(
                symbol=symbol,
                quantity=_to_float(pos.get("position")) or 0.0,
                avg_cost=_to_float(pos.get("costBasisPrice")),
                market_price=_to_float(pos.get("markPrice")),
                market_value=_to_float(pos.get("positionValue")),
                currency=pos.get("currency") or "USD",
                asset_class=pos.get("assetCategory") or "STK",
                con_id=_to_int(pos.get("conid")),
                account_id=pos.get("accountId") or account_id,
            )
        )
    return holdings


def _yyyymmdd(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.strptime(val.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _parse_report_date(stmt: ET.Element) -> date | None:
    """The trading session the statement reflects.

    Prefer the latest equity-summary ``reportDate`` (the marks/P&L belong to that day);
    fall back to the statement's ``toDate``. Both are IBKR's ``YYYYMMDD`` format.
    """
    rows = stmt.findall(".//EquitySummaryByReportDateInBase")
    if not rows:
        rows = stmt.findall(".//EquitySummaryInBase")
    dates = [d for r in rows if (d := _yyyymmdd(r.get("reportDate"))) is not None]
    if dates:
        return max(dates)
    return _yyyymmdd(stmt.get("toDate"))


def _parse_net_liquidation(stmt: ET.Element) -> float | None:
    # EquitySummaryInBase rows carry a 'total' column; take the latest if multiple.
    rows = stmt.findall(".//EquitySummaryByReportDateInBase")
    if not rows:
        rows = stmt.findall(".//EquitySummaryInBase")
    if rows:
        return _to_float(rows[-1].get("total"))
    return None


def _parse_total_cash(stmt: ET.Element) -> float | None:
    # Prefer the base-currency summary row of the cash report, if present.
    for cash in stmt.findall(".//CashReportCurrency"):
        if (cash.get("currency") or "").upper() in ("BASE_SUMMARY", "BASE"):
            return _to_float(cash.get("endingCash") or cash.get("slbEndingCash"))
    return None


class IBKRFlexAdapter(BrokerAdapter):
    """Fetches an IBKR portfolio snapshot via the Flex Web Service."""

    def __init__(
        self,
        config: Config,
        *,
        max_retries: int = 8,
        retry_wait_s: float = 5.0,
        session: requests.Session | None = None,
    ) -> None:
        self._token = config.secrets.get("IBKR_FLEX_TOKEN")
        self._query_id = config.secrets.get("IBKR_FLEX_QUERY_ID")
        self._max_retries = max_retries
        self._retry_wait_s = retry_wait_s
        self._session = session or requests.Session()

    def get_portfolio(self) -> Portfolio:
        reference_code = self._send_request()
        log.info("Flex request accepted, reference=%s", reference_code)
        return self._get_statement(reference_code)

    def _send_request(self) -> str:
        resp = self._session.get(
            _SEND_URL,
            params={"t": self._token, "q": self._query_id, "v": _API_VERSION},
            timeout=30,
        )
        resp.raise_for_status()
        return parse_send_response(resp.text)

    def _get_statement(self, reference_code: str) -> Portfolio:
        for attempt in range(1, self._max_retries + 1):
            resp = self._session.get(
                _GET_URL,
                params={"t": self._token, "q": reference_code, "v": _API_VERSION},
                timeout=60,
            )
            resp.raise_for_status()
            try:
                return parse_statement(resp.text)
            except FlexError as exc:
                if str(exc) == "STATEMENT_IN_PROGRESS" and attempt < self._max_retries:
                    log.info(
                        "Statement not ready (attempt %d/%d), waiting %.0fs",
                        attempt,
                        self._max_retries,
                        self._retry_wait_s,
                    )
                    time.sleep(self._retry_wait_s)
                    continue
                raise
        raise FlexError("Statement did not become ready within retry budget")
