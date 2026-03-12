"""Autonomous execution engine for Options GPS.
Consumes pipeline.py data classes and exchange.py pricing functions.
Supports Deribit, Aevo, and dry-run (simulated) execution."""

import hashlib
import hmac
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from exchange import best_execution_price, leg_divergences


# --- Data classes ---

@dataclass
class OrderRequest:
    instrument: str       # "BTC-26FEB26-67500-C" (Deribit) or "BTC-67500-C" (Aevo)
    action: str           # "BUY" | "SELL"
    quantity: int
    order_type: str       # "limit" | "market"
    price: float          # limit price from best_execution_price
    exchange: str         # "deribit" | "aevo" | "dry_run"
    leg_index: int        # index into strategy.legs
    strike: float = 0.0   # strike price for quote lookup
    option_type: str = ""  # "call" | "put" for quote lookup


@dataclass
class OrderResult:
    order_id: str
    status: str           # "filled" | "open" | "rejected" | "error" | "simulated"
    fill_price: float
    fill_quantity: int
    instrument: str
    action: str
    exchange: str
    error: str | None = None


@dataclass
class ExecutionPlan:
    strategy_description: str
    strategy_type: str
    exchange: str
    asset: str
    expiry: str
    orders: list[OrderRequest] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_max_loss: float = 0.0
    dry_run: bool = False


@dataclass
class ExecutionReport:
    plan: ExecutionPlan
    results: list[OrderResult] = field(default_factory=list)
    all_filled: bool = False
    net_cost: float = 0.0
    summary: str = ""


# --- Instrument name builders ---

def deribit_instrument_name(asset: str, expiry: str, strike: float, option_type: str) -> str:
    """Build Deribit instrument name like BTC-26FEB26-67500-C.
    Parses ISO 8601 expiry string to DDMonYY format."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    date_part = _format_deribit_date(expiry)
    return f"{asset}-{date_part}-{strike_str}-{ot}"


def aevo_instrument_name(asset: str, strike: float, option_type: str) -> str:
    """Build Aevo instrument name like BTC-67500-C. No date in Aevo names."""
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    ot = "C" if option_type.lower() == "call" else "P"
    return f"{asset}-{strike_str}-{ot}"


def _format_deribit_date(expiry: str) -> str:
    """Parse ISO 8601 expiry to Deribit DDMonYY format (e.g. 26FEB26).
    Falls back to UNKNOWN if parsing fails."""
    if not expiry:
        return "UNKNOWN"
    try:
        expiry = expiry.replace("Z", "+00:00")
        dt = datetime.fromisoformat(expiry)
        return dt.strftime("%d%b%y").upper()
    except (ValueError, TypeError):
        return "UNKNOWN"


# --- Executors ---

class BaseExecutor(ABC):
    @abstractmethod
    def authenticate(self) -> bool:
        ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> str:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...


class DryRunExecutor(BaseExecutor):
    """Simulates order execution using exchange quote data. No network calls."""

    def __init__(self, exchange_quotes: list):
        self.exchange_quotes = exchange_quotes

    def authenticate(self) -> bool:
        return True

    def place_order(self, order: OrderRequest) -> OrderResult:
        if not order.strike or not order.option_type:
            return OrderResult(
                order_id=f"dry-{uuid.uuid4().hex[:8]}",
                status="error", fill_price=0.0, fill_quantity=0,
                instrument=order.instrument, action=order.action,
                exchange="dry_run", error="Missing strike or option_type on order",
            )
        quote = best_execution_price(
            self.exchange_quotes, order.strike, order.option_type, order.action,
        )
        if quote is None:
            fill_price = order.price
        else:
            fill_price = quote.ask if order.action == "BUY" else quote.bid
        return OrderResult(
            order_id=f"dry-{uuid.uuid4().hex[:8]}",
            status="simulated",
            fill_price=fill_price,
            fill_quantity=order.quantity,
            instrument=order.instrument,
            action=order.action,
            exchange="dry_run",
        )

    def get_order_status(self, order_id: str) -> str:
        return "simulated"

    def cancel_order(self, order_id: str) -> bool:
        return True


class DeribitExecutor(BaseExecutor):
    """Executes orders on Deribit via REST API."""

    def __init__(self, client_id: str, client_secret: str, testnet: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://test.deribit.com/api/v2"
            if testnet
            else "https://www.deribit.com/api/v2"
        )
        self.token: str | None = None

    def authenticate(self) -> bool:
        import requests
        try:
            resp = requests.get(
                f"{self.base_url}/public/auth",
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self.token = data.get("result", {}).get("access_token")
            return self.token is not None
        except Exception:
            return False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def place_order(self, order: OrderRequest) -> OrderResult:
        import requests
        endpoint = "buy" if order.action == "BUY" else "sell"
        params = {
            "instrument_name": order.instrument,
            "amount": order.quantity,
            "type": order.order_type,
        }
        if order.order_type == "limit":
            params["price"] = order.price
        try:
            resp = requests.get(
                f"{self.base_url}/private/{endpoint}",
                params=params,
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("result", {})
            order_data = data.get("order", {})
            return OrderResult(
                order_id=order_data.get("order_id", ""),
                status=order_data.get("order_state", "error"),
                fill_price=float(order_data.get("average_price", 0)),
                fill_quantity=int(order_data.get("filled_amount", 0)),
                instrument=order.instrument,
                action=order.action,
                exchange="deribit",
            )
        except Exception as e:
            return OrderResult(
                order_id="", status="error", fill_price=0.0,
                fill_quantity=0, instrument=order.instrument,
                action=order.action, exchange="deribit", error=str(e),
            )

    def get_order_status(self, order_id: str) -> str:
        import requests
        try:
            resp = requests.get(
                f"{self.base_url}/private/get_order_state",
                params={"order_id": order_id},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("result", {}).get("order_state", "unknown")
        except Exception:
            return "unknown"

    def cancel_order(self, order_id: str) -> bool:
        import requests
        try:
            resp = requests.get(
                f"{self.base_url}/private/cancel",
                params={"order_id": order_id},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False


class AevoExecutor(BaseExecutor):
    """Executes orders on Aevo via REST API with HMAC-SHA256 signing."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://api-testnet.aevo.xyz"
            if testnet
            else "https://api.aevo.xyz"
        )

    def _sign(self, timestamp: str, body: str) -> str:
        message = f"{timestamp}{body}"
        return hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256,
        ).hexdigest()

    def _headers(self, body: str = "") -> dict:
        ts = str(int(time.time()))
        return {
            "AEVO-KEY": self.api_key,
            "AEVO-TIMESTAMP": ts,
            "AEVO-SIGNATURE": self._sign(ts, body),
            "Content-Type": "application/json",
        }

    def authenticate(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def place_order(self, order: OrderRequest) -> OrderResult:
        import json
        import requests
        payload = {
            "instrument": order.instrument,
            "side": order.action.lower(),
            "quantity": order.quantity,
            "order_type": order.order_type,
        }
        if order.order_type == "limit":
            payload["price"] = order.price
        body = json.dumps(payload)
        try:
            resp = requests.post(
                f"{self.base_url}/orders",
                data=body,
                headers=self._headers(body),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(
                order_id=data.get("order_id", ""),
                status=data.get("status", "error"),
                fill_price=float(data.get("avg_price", 0)),
                fill_quantity=int(data.get("filled", 0)),
                instrument=order.instrument,
                action=order.action,
                exchange="aevo",
            )
        except Exception as e:
            return OrderResult(
                order_id="", status="error", fill_price=0.0,
                fill_quantity=0, instrument=order.instrument,
                action=order.action, exchange="aevo", error=str(e),
            )

    def get_order_status(self, order_id: str) -> str:
        import requests
        try:
            resp = requests.get(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("status", "unknown")
        except Exception:
            return "unknown"

    def cancel_order(self, order_id: str) -> bool:
        import requests
        try:
            resp = requests.delete(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False


# --- Orchestration ---

def build_execution_plan(scored, asset: str, exchange: str | None,
                         exchange_quotes: list, synth_options: dict) -> ExecutionPlan:
    """Build an ExecutionPlan from a ScoredStrategy.
    When exchange is None, auto-routes each leg to the best exchange via leg_divergences."""
    strategy = scored.strategy
    plan = ExecutionPlan(
        strategy_description=strategy.description,
        strategy_type=strategy.strategy_type,
        exchange=exchange or "auto",
        asset=asset,
        expiry=strategy.expiry or "",
        dry_run=False,
    )

    # Get per-leg routing when auto-routing
    leg_routes = {}
    if exchange is None:
        leg_routes = leg_divergences(strategy, exchange_quotes, synth_options)

    for i, leg in enumerate(strategy.legs):
        # Determine exchange for this leg
        if exchange is not None:
            leg_exchange = exchange
        elif i in leg_routes:
            leg_exchange = leg_routes[i]["best_exchange"]
        else:
            # Fallback: find best execution price across all quotes
            quote = best_execution_price(
                exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
            )
            leg_exchange = quote.exchange if quote else "deribit"

        # Build instrument name
        if leg_exchange == "aevo":
            instrument = aevo_instrument_name(asset, leg.strike, leg.option_type)
        else:
            instrument = deribit_instrument_name(
                asset, strategy.expiry or "", leg.strike, leg.option_type,
            )

        # Get execution price
        quote = best_execution_price(
            exchange_quotes, leg.strike, leg.option_type.lower(), leg.action,
        )
        if quote is not None:
            price = quote.ask if leg.action == "BUY" else quote.bid
        else:
            price = leg.premium

        plan.orders.append(OrderRequest(
            instrument=instrument,
            action=leg.action,
            quantity=leg.quantity,
            order_type="limit",
            price=price,
            exchange=leg_exchange,
            leg_index=i,
            strike=leg.strike,
            option_type=leg.option_type.lower(),
        ))

    # Estimated cost: sum of buy prices - sum of sell prices
    buy_total = sum(o.price * o.quantity for o in plan.orders if o.action == "BUY")
    sell_total = sum(o.price * o.quantity for o in plan.orders if o.action == "SELL")
    plan.estimated_cost = buy_total - sell_total
    plan.estimated_max_loss = strategy.max_loss

    return plan


def validate_plan(plan: ExecutionPlan) -> tuple[bool, str]:
    """Validate an execution plan before submission.
    Returns (is_valid, error_message)."""
    if not plan.orders:
        return False, "No orders in plan"
    for i, order in enumerate(plan.orders):
        if not order.instrument:
            return False, f"Order {i}: empty instrument name"
        if order.price <= 0:
            return False, f"Order {i}: price must be > 0 (got {order.price})"
        if order.quantity <= 0:
            return False, f"Order {i}: quantity must be > 0 (got {order.quantity})"
        if order.action not in ("BUY", "SELL"):
            return False, f"Order {i}: invalid action '{order.action}'"
    return True, ""


def execute_plan(plan: ExecutionPlan, executor: BaseExecutor) -> ExecutionReport:
    """Execute all orders in a plan sequentially.
    On partial failure, warns about filled legs that need manual closing."""
    report = ExecutionReport(plan=plan)

    if not executor.authenticate():
        report.summary = "Authentication failed"
        return report

    for order in plan.orders:
        result = executor.place_order(order)
        report.results.append(result)
        if result.status in ("error", "rejected"):
            filled_legs = [r for r in report.results if r.status in ("filled", "simulated")]
            if filled_legs:
                instruments = ", ".join(r.instrument for r in filled_legs)
                report.summary = (
                    f"Partial fill — order {order.leg_index} failed: "
                    f"{result.error or result.status}. "
                    f"WARNING: manually close filled legs: {instruments}"
                )
            else:
                report.summary = (
                    f"Order {order.leg_index} failed: {result.error or result.status}"
                )
            report.all_filled = False
            report.net_cost = _compute_net_cost(report.results)
            return report

    report.all_filled = all(
        r.status in ("filled", "simulated") for r in report.results
    )
    report.net_cost = _compute_net_cost(report.results)

    if report.all_filled:
        mode = "simulated" if plan.dry_run else "live"
        report.summary = (
            f"All {len(report.results)} legs {mode} successfully. "
            f"Net cost: ${report.net_cost:,.2f}"
        )
    else:
        report.summary = f"Execution completed with {len(report.results)} orders"

    return report


def _compute_net_cost(results: list[OrderResult]) -> float:
    """Net cost = sum(buy fills) - sum(sell fills)."""
    cost = 0.0
    for r in results:
        if r.status in ("filled", "simulated"):
            if r.action == "BUY":
                cost += r.fill_price * r.fill_quantity
            else:
                cost -= r.fill_price * r.fill_quantity
    return cost


def get_executor(exchange: str | None, exchange_quotes: list,
                 dry_run: bool = False) -> BaseExecutor:
    """Factory: return the appropriate executor.
    dry_run=True always returns DryRunExecutor.
    Otherwise reads credentials from environment variables."""
    if dry_run:
        return DryRunExecutor(exchange_quotes)

    if exchange == "deribit":
        client_id = os.environ.get("DERIBIT_CLIENT_ID", "")
        client_secret = os.environ.get("DERIBIT_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise ValueError(
                "Deribit credentials required: set DERIBIT_CLIENT_ID and "
                "DERIBIT_CLIENT_SECRET environment variables"
            )
        testnet = os.environ.get("DERIBIT_TESTNET", "").strip() == "1"
        return DeribitExecutor(client_id, client_secret, testnet)

    if exchange == "aevo":
        api_key = os.environ.get("AEVO_API_KEY", "")
        api_secret = os.environ.get("AEVO_API_SECRET", "")
        if not api_key or not api_secret:
            raise ValueError(
                "Aevo credentials required: set AEVO_API_KEY and "
                "AEVO_API_SECRET environment variables"
            )
        testnet = os.environ.get("AEVO_TESTNET", "").strip() == "1"
        return AevoExecutor(api_key, api_secret, testnet)

    raise ValueError(
        f"Unknown exchange '{exchange}'. Use --exchange deribit or --exchange aevo"
    )
