"""Run Windows Django tests against a NEW, isolated PostgreSQL cluster in WSL.

Never uses native PostgreSQL or connects to production. Preserves the stopped
fixture cluster/logs for inspection. No system service is started or stopped.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    from local_pos.runtime import private_dir, port_free
    from local_pos.wsl_postgres import WslPostgres
    import psycopg2
    port_free(args.port)
    identity = str(uuid4())
    root = private_dir(Path(os.environ["LOCALAPPDATA"]) / "NovaPOS-TestTools" / ("wsl-ci-" + identity))
    password = secrets.token_hex(32)
    config = {"instance_id": identity, "port": args.port, "password": password,
        "pg_bin": args.pg_bin, "backend": {"kind": "wsl", "distribution": args.distribution,
        "user": args.user, "data_dir": f"/home/{args.user}/.local/share/NovaPOS/integrated/{identity}"}}
    pg = WslPostgres(root, args.pg_bin, config)
    print(f"Fictional test cluster: {config['backend']['data_dir']}", flush=True)
    pg.initialize(password)
    pg.start(initializing_password=password)
    try:
        connection = psycopg2.connect(host="127.0.0.1", port=args.port, dbname="postgres",
            user="nova_integrated_owner", password=password, connect_timeout=3)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("CREATE ROLE nova_hybrid_ci LOGIN CREATEDB PASSWORD %s", [password])
                cursor.execute("CREATE DATABASE nova_hybrid_ci OWNER nova_hybrid_ci")
        finally:
            connection.close()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in
            {"DJANGO_SETTINGS_MODULE", "NOVA_INTEGRATED_CONFIG", "NOVA_LOCAL_CONFIG", "DATABASE_URL", "RUN_MAIN"}}
        env.update(HYBRID_TEST_POSTGRES="yes-local-disposable", HYBRID_TEST_POSTGRES_PORT=str(args.port),
            HYBRID_TEST_POSTGRES_PASSWORD=password)
        suites = [
            ("NovaSoft.integrated_test_postgres_settings", ["local_pos.test_integrated_activation", "local_pos.test_integrated_requests", "local_pos.test_integrated_replica", "local_pos.test_integrated_journal", "local_pos.test_integrated_offline_sales", "local_pos.test_connectivity", "local_pos.test_integrated_bootstrap", "local_pos.test_wsl_postgres"]),
            ("NovaSoft.hybrid_test_postgres_settings", ["mainApp.test_hybrid_backup", "mainApp.test_hybrid_activation_checks", "mainApp.test_pos_request_receipts", "mainApp.test_pos_receipt_migration",
                "mainApp.test_cash_receipt", "mainApp.test_hybrid", "mainApp.test_hybrid_postgres", "mainApp.test_nequi_selection", "mainApp.test_close_payments"]),
        ]
        if args.ptm_only:
            suites = [("NovaSoft.hybrid_test_postgres_settings", ["mainApp.test_ptm_cash_pool", "mainApp.test_ptm", "mainApp.test_ptm_reader", "mainApp.test_close_payments"]),
                ("NovaSoft.integrated_test_postgres_settings", ["local_pos.test_integrated_business.IntegratedBusinessTests"])]
        elif args.production_only:
            suites = [("NovaSoft.hybrid_test_postgres_settings", ["mainApp.test_production_compatibility", "mainApp.test_pos_receipt_migration", "mainApp.test_pos_request_receipts", "mainApp.test_close_payments"])]
        elif args.business_only:
            suites = [("NovaSoft.integrated_test_postgres_settings", ["local_pos.test_integrated_business.IntegratedBusinessTests", "local_pos.test_integrated_navigation.IntegratedNavigationTests"])]
        elif args.activation_only:
            suites = [("NovaSoft.integrated_test_postgres_settings", ["local_pos.test_integrated_activation"])]
        else:
            suites[0][1].append("local_pos.test_integrated_business.IntegratedBusinessTests")
            suites[0][1].append("local_pos.test_integrated_assets")
            suites[0][1].append("local_pos.test_integrated_navigation.IntegratedNavigationTests")
        for settings, labels in suites:
            command = ["-B", "manage.py", "test", *labels, "--settings=" + settings, "--noinput"]
            if args.linux_python:
                if not args.production_only:
                    raise RuntimeError("Linux execution is limited to the cloud compatibility suite.")
                project = subprocess.run(["wsl.exe", "-d", args.distribution, "-u", args.user,
                    "--", "wslpath", "-a", "-u", ROOT.as_posix()], check=True, text=True, capture_output=True).stdout.strip()
                # Pass the randomly generated test password in stdin, not in
                # process arguments or logs. The Linux process uses loopback.
                payload = {"cwd": project, "env": {k: v for k, v in env.items() if k.startswith("HYBRID_TEST_POSTGRES")}, "args": command}
                bootstrap = ("import json,os,sys; p=json.load(sys.stdin); os.chdir(p['cwd']); "
                    "os.environ.update(p['env']); os.environ.pop('DJANGO_SETTINGS_MODULE',None); "
                    "os.execv(sys.executable,[sys.executable,*p['args']])")
                subprocess.run(["wsl.exe", "-d", args.distribution, "-u", args.user, "--",
                    args.linux_python, "-c", bootstrap], input=json.dumps(payload), text=True,
                    env=env, cwd=ROOT, check=True, timeout=300)
            else:
                subprocess.run([sys.executable, *command], env=env, cwd=ROOT, check=True, timeout=300)
    finally:
        pg.stop()
    platform = "Linux Django" if args.linux_python else "Windows Django"
    print(f"PASS: {platform} -> WSL PostgreSQL suites; isolated engine stopped.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-22.04")
    parser.add_argument("--user", default="novapos-test")
    parser.add_argument("--pg-bin", default="/usr/lib/postgresql/16/bin")
    parser.add_argument("--port", default=55485, type=int)
    parser.add_argument("--activation-only", action="store_true")
    parser.add_argument("--business-only", action="store_true")
    parser.add_argument("--production-only", action="store_true")
    parser.add_argument("--ptm-only", action="store_true", help="Cash-pool, reader, closing and offline regressions on a new disposable cluster")
    parser.add_argument("--linux-python", help="Existing WSL Python environment for the production-only suite")
    run(parser.parse_args())
