"""Webull OpenAPI 인증 연결 테스트.

발급받은 App Key/Secret이 실제로 동작하는지 검증.
파라미터가 없는 get_app_subscriptions() 호출로 인증 + IP whitelist + 권한을 한번에 점검.

실행:
    cd C:\\Users\\philk\\Documents\\Stock
    venv\\Scripts\\python.exe scripts\\test_webull_auth.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from webullsdkcore.client import ApiClient
from webullsdkcore.exception.exceptions import ClientException, ServerException
from webullsdktrade.api import API


def mask(value: str, head: int = 4, tail: int = 2) -> str:
    if len(value) <= head + tail:
        return "*" * len(value)
    return f"{value[:head]}{'*' * (len(value) - head - tail)}{value[-tail:]}"


def main() -> int:
    app_key = os.environ.get("WEBULL_APP_KEY")
    app_secret = os.environ.get("WEBULL_APP_SECRET")
    region = os.environ.get("WEBULL_REGION", "us")

    if not app_key or not app_secret:
        print("[ERROR] .env에 WEBULL_APP_KEY / WEBULL_APP_SECRET이 없습니다.", file=sys.stderr)
        return 1

    print(f"[INFO] App Key:  {mask(app_key)}  (length={len(app_key)})")
    print(f"[INFO] Secret:   {mask(app_secret)}  (length={len(app_secret)})")
    print(f"[INFO] Region:   {region}")
    print()

    client = ApiClient(app_key=app_key, app_secret=app_secret, region_id=region)
    api = API(client)

    print("[TEST] api.account.get_app_subscriptions() ...")
    try:
        response = api.account.get_app_subscriptions()
    except ClientException as exc:
        print(f"[FAIL] ClientException — 클라이언트측 오류 (서명/파라미터): {exc}", file=sys.stderr)
        return 2
    except ServerException as exc:
        print(f"[FAIL] ServerException — 서버측 거부: {exc}", file=sys.stderr)
        print("       체크: IP whitelist 일치 여부, 권한(Quotes/Trading), 키 활성화 상태", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    status = getattr(response, "status_code", None)
    print(f"[OK]   HTTP {status}")
    try:
        body = response.json()
        print("[OK]   Response body:")
        print(json.dumps(body, indent=2, ensure_ascii=False))
    except Exception:
        print(f"[OK]   Raw body: {response.text!r}")

    print()
    print("✅ 인증 성공 — App Key/Secret + IP whitelist + 권한 모두 정상")
    return 0


if __name__ == "__main__":
    sys.exit(main())
