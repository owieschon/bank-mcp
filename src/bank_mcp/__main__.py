"""Console entry point: `python -m bank_mcp <command>`.

Commands:
  demo          Build and print a digest from bundled synthetic data (no bank needed).
  demo --json   Print the synthetic transaction dataset as JSON.
  analytics     Run the SQL analytical read-models (monthly cash flow, category
                mix, top merchants) — defaults to synthetic data; see queries.sql.
"""
import sys

from bank_mcp import demo
from bank_mcp.store import analytics

USAGE = (
    "usage: bank-mcp <command>\n\n"
    "commands:\n"
    "  demo [--json]                              build + print a digest from synthetic data\n"
    "  analytics [--db F] [--owner N] [--top K]   SQL reporting rollups\n"
    "  -h, --help                                 show this message\n"
)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0
    if not args or args[0] == "demo":
        demo.main(args[1:])
        return 0
    if args[0] == "analytics":
        analytics.main(args[1:])
        return 0
    sys.stderr.write(f"unknown command: {args[0]!r}\n\n{USAGE}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
