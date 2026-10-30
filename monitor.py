"""A股研究台独立监控进程。

用法：
  python3 monitor.py --once
  python3 monitor.py --interval 300
"""
from __future__ import annotations

import argparse
import time

from monitor_service import scan_rules


def main() -> None:
    parser = argparse.ArgumentParser(description="A股研究台预警扫描器")
    parser.add_argument("--once", action="store_true", help="只扫描一次")
    parser.add_argument("--interval", type=int, default=300, help="循环间隔秒数，默认300")
    parser.add_argument("--no-webhook", action="store_true", help="只写站内通知")
    args = parser.parse_args()
    while True:
        result = scan_rules(send_webhook=not args.no_webhook)
        print(f"[{result['finished_at']}] checked={result['checked']} "
              f"triggered={result['triggered']} errors={','.join(result['errors']) or '-'}",
              flush=True)
        if args.once:
            return
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
