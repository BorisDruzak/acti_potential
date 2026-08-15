import argparse
import sys

import pyodbc


def build_connection_string(args: argparse.Namespace) -> str:
    parts = [
        f"DRIVER={{{args.driver}}}",
        f"SERVER={args.server}",
        f"DATABASE={args.database}",
        f"UID={args.user}",
        f"PWD={args.password}",
        f"TrustServerCertificate={'yes' if args.trust_server_certificate else 'no'}",
        f"Encrypt={'yes' if args.encrypt else 'no'}",
        f"Connection Timeout={args.timeout}",
    ]
    return ";".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal smoke test for SQL Server connectivity."
    )
    parser.add_argument("--server", default="192.168.100.11,1433")
    parser.add_argument("--database", default="acti_v2")
    parser.add_argument("--user", default="acti_user")
    parser.add_argument("--password", required=True)
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument(
        "--trust-server-certificate",
        action="store_true",
        default=True,
        help="Allow self-signed server certificates.",
    )
    parser.add_argument(
        "--encrypt",
        action="store_true",
        default=True,
        help="Require TLS encryption.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn_str = build_connection_string(args)

    print("Available ODBC drivers:")
    for driver in pyodbc.drivers():
        print(f"  - {driver}")

    try:
        with pyodbc.connect(conn_str) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT @@SERVERNAME, DB_NAME(), SUSER_SNAME()")
            server_name, database_name, login_name = cursor.fetchone()
            print("Connection successful")
            print(f"Server: {server_name}")
            print(f"Database: {database_name}")
            print(f"Login: {login_name}")
        return 0
    except pyodbc.Error as exc:
        print("Connection failed")
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
