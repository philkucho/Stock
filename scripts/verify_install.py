"""의존성 설치 검증 스크립트.

실행: venv/Scripts/python.exe scripts/verify_install.py

Webull credentials 없이도 통과해야 하는 import 검증.
"""

from __future__ import annotations

import sys
from importlib import import_module

# Windows console (cp1252) compatibility for unicode in error messages
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


CHECKS = [
    ("nautilus_trader", "NautilusTrader 코어"),
    ("nautilus_trader.live.node", "TradingNode"),
    ("nautilus_trader.adapters", "어댑터 패키지"),
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("pydantic", "Pydantic v2"),
    ("yfinance", "yfinance"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("paho.mqtt.client", "paho-mqtt (Webull MQTT 시세)"),
    ("grpc", "grpcio (Webull 주문 이벤트)"),
    ("dotenv", "python-dotenv"),
    ("sqlalchemy", "SQLAlchemy"),
    ("aiosqlite", "aiosqlite"),
    # Webull SDK (5개 모듈 모두)
    ("webullsdkcore", "webull-python-sdk-core"),
    ("webullsdkmdata", "webull-python-sdk-mdata"),
    ("webullsdkquotescore", "webull-python-sdk-quotes-core"),
    ("webullsdktrade", "webull-python-sdk-trade"),
    ("webullsdktradeeventscore", "webull-python-sdk-trade-events-core"),
]


def main() -> int:
    failed: list[tuple[str, str, str]] = []
    print(f"Python: {sys.version}")
    print(f"Executable: {sys.executable}\n")

    for mod, label in CHECKS:
        try:
            m = import_module(mod)
            version = getattr(m, "__version__", "?")
            print(f"  OK   {label:40s} ({mod} {version})")
        except Exception as e:
            print(f"  FAIL {label:40s} ({mod}) -> {type(e).__name__}: {e}")
            failed.append((mod, label, str(e)))

    print()
    if failed:
        print(f"FAILED: {len(failed)} / {len(CHECKS)}")
        for mod, label, err in failed:
            print(f"  - {label} ({mod})")
        return 1
    print(f"PASSED: {len(CHECKS)} / {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
