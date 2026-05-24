#!/usr/bin/env python3
"""
Verify production TP/SL math.

Expected:
  LONG  entry 100 -> TP 102, SL 99
  SHORT entry 100 -> TP 98,  SL 101
"""

import agent


def assert_close(actual: float, expected: float, label: str):
    if abs(actual - expected) > 0.000001:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def main():
    long_exits = agent._compute_exits("LONG", 100, 100)
    short_exits = agent._compute_exits("SHORT", 100, 100)

    assert_close(long_exits["entry_price"], 100.0, "LONG entry")
    assert_close(long_exits["target"], 102.0, "LONG take profit")
    assert_close(long_exits["stop_loss"], 99.0, "LONG stop loss")
    assert long_exits["rr"] == "2.0:1", long_exits

    assert_close(short_exits["entry_price"], 100.0, "SHORT entry")
    assert_close(short_exits["target"], 98.0, "SHORT take profit")
    assert_close(short_exits["stop_loss"], 101.0, "SHORT stop loss")
    assert short_exits["rr"] == "2.0:1", short_exits

    print("OK: exits are fixed at 2% take profit and 1% stop loss")
    print("LONG ", long_exits)
    print("SHORT", short_exits)


if __name__ == "__main__":
    main()
