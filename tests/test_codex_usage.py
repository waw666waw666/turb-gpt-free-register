# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import core.chatgpt_plan as plan


class _Response:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text
        self.headers = {}

    def json(self):
        return self._data


class _HttpSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _UsageSession:
    """先回套餐查询、再回 wham 额度的假会话。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.proxy = kwargs.get("proxy")
        self.device_id = "device-one"
        self.oai_session_id = "session-one"
        self.session = _HttpSession()
        self.reset_count = 0
        self.requests = []
        self.responses = [
            _Response(200, data={
                "accounts": {
                    "acc-1": {
                        "account": {"account_id": "acc-1", "plan_type": "plus"},
                        "entitlement": {"subscription_plan": "chatgptplusplan"},
                    }
                }
            }),
            _Response(200, data={
                "plan_type": "plus",
                "rate_limit": {
                    "allowed": True,
                    "limit_reached": False,
                    "primary_window": {
                        "used_percent": 23,
                        "limit_window_seconds": 18000,
                        "reset_after_seconds": 3600,
                        "reset_at": 1893456000,
                    },
                    "secondary_window": {
                        "used_percent": 5,
                        "limit_window_seconds": 604800,
                        "reset_at": 1894000000,
                    },
                },
            }),
        ]

    def get_chatgpt_headers(self, **_kwargs):
        return {
            "content-type": "application/json",
            "User-Agent": "Mozilla/5.0 (browser)",
            "oai-device-id": self.device_id,
            "oai-session-id": self.oai_session_id,
        }

    def get(self, url, headers=None, **_kwargs):
        self.requests.append((url, dict(headers or {})))
        return self.responses.pop(0)

    def reset_circuit_breaker(self):
        self.reset_count += 1

    def js_timezone_offset_min(self):
        return -540

    def fingerprint_summary_text(self):
        return "lang=ja-JP tz=Asia/Tokyo"


class ParseWhamUsageTests(unittest.TestCase):
    def test_parse_windows_and_plan(self):
        parsed = plan.parse_wham_usage({
            "plan_type": "plus",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": {"used_percent": 23, "limit_window_seconds": 18000, "reset_at": 1893456000},
                "secondary_window": {"used_percent": 5, "limit_window_seconds": 604800},
            },
            "additional_rate_limits": [{
                "limit_name": "code_review",
                "rate_limit": {"primary_window": {"used_percent": 10, "limit_window_seconds": 18000}},
            }],
        })
        self.assertEqual(parsed["plan_type"], "plus")
        self.assertEqual(parsed["primary_window"]["used_percent"], 23)
        self.assertEqual(parsed["secondary_window"]["used_percent"], 5)
        self.assertEqual(parsed["additional_rate_limits"][0]["limit_name"], "code_review")

    def test_parse_tolerates_missing_rate_limit(self):
        parsed = plan.parse_wham_usage({"plan_type": "free"})
        self.assertIsNone(parsed["primary_window"])
        self.assertNotIn("additional_rate_limits", parsed)


class CheckAccountPlanUsageTests(unittest.TestCase):
    def test_plan_check_attaches_codex_usage_with_codex_ua(self):
        claims = {
            "payload": {}, "email": "one@example.com", "account_id": "acc-1",
            "token_expired": False, "claim_plan_type": "plus",
        }
        created = []

        class _Session(_UsageSession):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                created.append(self)

        with patch.object(plan, "BrowserSession", _Session), patch.object(
            plan, "token_claims", return_value=claims
        ), patch.object(plan, "resolve_plan_check_route", return_value={
            "proxy": "socks5://proxy:1080", "proxy_mode": "proxy",
            "network_route": "proxy", "proxy_used": "socks5://***:***@proxy:1080",
            "proxy_fallback_reason": None,
        }), patch.object(plan, "_warm_plan_session"), patch.object(plan.time, "sleep"):
            result = plan.check_account_plan("token", max_attempts=1, retry_delay=0)

        self.assertTrue(result["ok"])
        usage = result["codex_usage"]
        self.assertTrue(usage["ok"])
        self.assertEqual(usage["primary_window"]["used_percent"], 23)
        self.assertEqual(usage["secondary_window"]["used_percent"], 5)
        session = created[0]
        self.assertEqual(len(session.requests), 2)
        usage_url, usage_headers = session.requests[1]
        self.assertIn("/backend-api/wham/usage", usage_url)
        self.assertEqual(usage_headers["User-Agent"], plan.CODEX_USAGE_USER_AGENT)
        self.assertEqual(usage_headers["x-openai-target-path"], plan.WHAM_USAGE_PATH)
        self.assertEqual(usage_headers["chatgpt-account-id"], "acc-1")
        # 成功路径不许动会话熔断状态。
        self.assertEqual(session.reset_count, 0)

    def test_usage_failure_never_breaks_plan_result(self):
        session = _UsageSession(proxy="socks5://live-proxy:1080")
        session.responses = [
            _Response(200, data={
                "accounts": {
                    "acc-1": {
                        "account": {"account_id": "acc-1", "plan_type": "free"},
                        "entitlement": {},
                    }
                }
            }),
            _Response(403, text="blocked"),
        ]
        with patch.object(plan.time, "sleep"):
            result = plan.check_account_plan("fresh-token", session=session, max_attempts=1, retry_delay=0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["codex_usage"]["ok"])
        self.assertEqual(result["codex_usage"]["http_status"], 403)
        # wham 的 403 可能触发熔断，借用会话时必须清掉，不能留给调用方。
        self.assertEqual(session.reset_count, 1)
        self.assertFalse(session.session.closed)


if __name__ == "__main__":
    unittest.main()
