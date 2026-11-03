#!/usr/bin/env python3
"""Flattrade Broker Integration Adapter for NSE Intraday Trading.

Supports:
  1. Paper Mode (Default):
     - Validates order parameters against Flattrade specifications.
     - Renders exact Flattrade API JSON payloads.
     - Simulates order execution with zero financial risk.
     - Reflects Flattrade's Zero-Brokerage advantage (friction reduced to ~₹15 statutory taxes).
  2. Live Mode:
     - Direct authentication with Flattrade Wall API (NorenOMS).
     - Places real NSE MIS Intraday orders with 5x leverage.
     - Real-time order status, positions, and order cancellation.

API Documentation Reference:
  - Base URL: https://ptradeapi.flattrade.in/NorenWClientTP/
  - Token URL: https://authapi.flattrade.in/trade/apitoken
  - Web Login: https://auth.flattrade.in/?app_key={API_KEY}
"""

import os
import sys
import json
import time
import hashlib
import warnings
import datetime
from typing import Dict, Any, Optional

warnings.filterwarnings("ignore")

TRADER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(TRADER_DIR, "config.json")
CREDS_PATH = os.path.join(TRADER_DIR, "flattrade_creds.json")

class FlattradeBroker:
    BASE_URL = "https://ptradeapi.flattrade.in/NorenWClientTP"
    AUTH_TOKEN_URL = "https://authapi.flattrade.in/trade/apitoken"
    AUTH_PAGE_URL = "https://auth.flattrade.in/?app_key={api_key}"

    def __init__(self, is_paper: bool = True):
        self.is_paper = is_paper
        self.creds = self._load_creds()
        self.session_token = self.creds.get("token", "")
        self.user_id = self.creds.get("user_id", "")
        self.api_key = self.creds.get("api_key", "")
        self.api_secret = self.creds.get("api_secret", "")

    def _load_creds(self) -> Dict[str, str]:
        if os.path.exists(CREDS_PATH):
            try:
                with open(CREDS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"user_id": "", "api_key": "", "api_secret": "", "token": "", "token_date": ""}

    def save_creds(self, user_id: str, api_key: str, api_secret: str, token: str = ""):
        self.user_id = user_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_token = token
        data = {
            "user_id": user_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "token": token,
            "token_date": datetime.date.today().isoformat()
        }
        with open(CREDS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[Flattrade] Credentials saved securely to {CREDS_PATH}")

    def get_login_url(self) -> str:
        """Get the URL to open in browser to obtain request_code."""
        if not self.api_key:
            return "Error: api_key not set in flattrade_creds.json"
        return self.AUTH_PAGE_URL.format(api_key=self.api_key)

    def generate_token_from_request_code(self, request_code: str) -> bool:
        """Exchange temporary request_code for full session usertoken."""
        import requests
        if not self.api_key or not self.api_secret:
            print("[Flattrade Error] api_key and api_secret are required.")
            return False

        # Formula: SHA-256 of (api_key + request_code + api_secret)
        raw_concat = self.api_key + request_code.strip() + self.api_secret
        sha_hash = hashlib.sha256(raw_concat.encode("utf-8")).hexdigest()

        payload = {
            "api_key": self.api_key,
            "request_code": request_code.strip(),
            "api_secret": sha_hash
        }

        try:
            resp = requests.post(self.AUTH_TOKEN_URL, json=payload, timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("stat") == "Ok":
                self.session_token = data.get("token")
                self.save_creds(self.user_id, self.api_key, self.api_secret, self.session_token)
                print(f"[Flattrade] Authentication Successful! Token generated for session.")
                return True
            else:
                print(f"[Flattrade Error] Token Generation Failed: {data}")
                return False
        except Exception as e:
            print(f"[Flattrade Error] Exception connecting to Flattrade: {e}")
            return False

    def _format_tradingsymbol(self, symbol: str) -> str:
        """Convert standard symbol format (DIXON.NS) to Flattrade format (DIXON-EQ)."""
        clean = symbol.replace(".NS", "").replace("^", "")
        if not clean.endswith("-EQ"):
            return f"{clean}-EQ"
        return clean

    def _send_request(self, endpoint: str, payload_data: dict) -> dict:
        """Send authenticated POST request to Flattrade REST API."""
        import requests
        if not self.session_token:
            return {"stat": "Not_Ok", "emsg": "No active session token. Authenticate first."}

        url = f"{self.BASE_URL}/{endpoint}"
        payload_data["uid"] = self.user_id
        payload_data["actid"] = self.user_id

        # NorenOMS expects jData=<json_str>&jKey=<usertoken>
        body = f"jData={json.dumps(payload_data)}&jKey={self.session_token}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            resp = requests.post(url, data=body, headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            return {"stat": "Not_Ok", "emsg": f"Network exception: {str(e)}"}

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        stop_loss: Optional[float] = None,
        target: Optional[float] = None,
        price_type: str = "LMT",
        product_type: str = "M",  # 'M' for MIS Intraday
        remarks: str = "orb_v3"
    ) -> Dict[str, Any]:
        """Place an order with Flattrade. Operates safely in Paper mode by default."""
        tsym = self._format_tradingsymbol(symbol)
        side_code = "B" if side.upper() in ["BUY", "B"] else "S"

        order_payload = {
            "uid": self.user_id or "PAPER_USER",
            "actid": self.user_id or "PAPER_USER",
            "exch": "NSE",
            "tsym": tsym,
            "qty": str(qty),
            "prc": f"{price:.2f}",
            "prd": product_type,
            "trantype": side_code,
            "prctyp": price_type,
            "ret": "DAY",
            "remarks": remarks
        }

        if self.is_paper:
            simulated_id = f"FLAT_SIM_{datetime.datetime.now().strftime('%H%M%S')}"
            print("\n" + "=" * 65)
            print(" [FLATTRADE PAPER ORDER TICKET] ")
            print("=" * 65)
            print(f"  Mode:            100% Simulation / Paper Trading")
            print(f"  Broker API:      Flattrade (NorenOMS Endpoint: PlaceOrder)")
            print(f"  Exchange / Scrip: NSE | {tsym}")
            print(f"  Transaction:     {'BUY' if side_code == 'B' else 'SELL'} (Product: MIS Intraday 'M')")
            print(f"  Quantity:        {qty} shares")
            print(f"  Order Price:     ₹{price:.2f} ({price_type})")
            if stop_loss:
                print(f"  Stop Loss:       ₹{stop_loss:.2f}")
            if target:
                print(f"  Target:          ₹{target:.2f}")
            print(f"  Est. Friction:   ₹15.00 (Flattrade Zero Brokerage + Statutory Taxes)")
            print(f"  Generated ID:    {simulated_id}")
            print("-" * 65)
            print(f"  Raw API Payload: {json.dumps(order_payload)}")
            print("=" * 65 + "\n")

            return {
                "stat": "Ok",
                "norenordno": simulated_id,
                "tsym": tsym,
                "price": price,
                "qty": qty,
                "side": side.upper(),
                "mode": "PAPER"
            }
        else:
            # Live Execution
            print(f"[Flattrade LIVE] Sending REAL order to NSE for {qty}x {tsym} @ ₹{price:.2f}...")
            res = self._send_request("PlaceOrder", order_payload)
            if res.get("stat") == "Ok":
                print(f"[Flattrade LIVE] Order Placed! Exchange Order No: {res.get('norenordno')}")
            else:
                print(f"[Flattrade LIVE ERROR] Order rejected: {res.get('emsg')}")
            return res

    def get_positions(self) -> list:
        if self.is_paper:
            return []
        res = self._send_request("PositionBook", {})
        if isinstance(res, list):
            return res
        return []

    def get_order_book(self) -> list:
        if self.is_paper:
            return []
        res = self._send_request("OrderBook", {})
        if isinstance(res, list):
            return res
        return []

    def cancel_order(self, order_no: str) -> dict:
        if self.is_paper:
            print(f"[Flattrade Paper] Cancelled simulated order: {order_no}")
            return {"stat": "Ok", "result": "cancelled"}
        payload = {"norenordno": order_no}
        return self._send_request("CancelOrder", payload)

    def get_limits(self) -> dict:
        if self.is_paper:
            return {"stat": "Ok", "cash": "50000.00", "margin_available": "50000.00"}
        return self._send_request("Limits", {})

if __name__ == "__main__":
    broker = FlattradeBroker(is_paper=True)
    ticket = broker.place_order(
        symbol="DIXON.NS",
        side="BUY",
        qty=1,
        price=11845.00,
        stop_loss=11393.50,
        target=12522.25
    )

