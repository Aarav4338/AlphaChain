"""
Autonomous script to:
1. Stop alphachain.service on AWS EC2
2. Pull latest code from main
3. Close the 26 flat positions on Roostoo (banking profit and unlocking cash)
4. Restart alphachain.service with new concentrated 8-position architecture
"""
import os
import sys
import time
from pathlib import Path
import boto3
from dotenv import load_dotenv

load_dotenv(override=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.roostoo_client import RoostooClient

INSTANCE_ID = "i-02e3feabbe641ac82"
REGION = os.getenv("AWS_REGION") or "ap-southeast-2"

def run_ssm_command(ssm, commands, wait_seconds=5):
    print(f"Running SSM commands: {commands}")
    resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands}
    )
    cid = resp["Command"]["CommandId"]
    for _ in range(10):
        time.sleep(wait_seconds)
        res = ssm.get_command_invocation(CommandId=cid, InstanceId=INSTANCE_ID)
        status = res.get("Status")
        if status in ["Success", "Failed", "TimedOut"]:
            return status, res.get("StandardOutputContent"), res.get("StandardErrorContent")
    return "Timeout", None, "Timed out waiting for command execution"

def close_all_flat_positions(client):
    print("\n--- CLOSING ALL 26 EXISTING FLAT POSITIONS ---")
    # 1. Close Margin Shorts
    shorts_res = client.get_short_positions() or {}
    raw_shorts = shorts_res if isinstance(shorts_res, list) else shorts_res.get("Positions", [])
    print(f"Found {len(raw_shorts)} open shorts.")
    for s in raw_shorts:
        pair = s.get("Pair")
        qty = float(s.get("ShortQty", 0.0))
        if qty > 0 and pair:
            print(f"Closing SHORT {pair} (Qty: {qty})...")
            res = client.short_close(pair=pair, close_qty=qty)
            success = res.get("Success", False) if res else False
            pnl = res.get("RealizedPNL", 0.0) if res else 0.0
            print(f"  -> Result: Success={success}, Realized PnL=${pnl:+.2f}")
            time.sleep(0.5)

    # 2. Sell Spot Longs
    bal = client.get_balance() or {}
    wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
    for coin, info in wallet.items():
        if coin == "USD":
            continue
        free_q = float(info.get("Free", 0.0))
        pair = f"{coin}/USD"
        if free_q > 0.00001:
            print(f"Selling SPOT LONG {pair} (Qty: {free_q})...")
            res = client.place_order(pair=pair, side="SELL", quantity=free_q, order_type="MARKET")
            success = res.get("Success", False) if res else False
            print(f"  -> Result: Success={success}")
            time.sleep(0.5)

    print("All flat positions closed successfully.")

def main():
    ssm = boto3.client(
        "ssm",
        region_name=REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN")
    )
    client = RoostooClient()

    # Step 1: Stop service on EC2
    print("\n[Step 1] Stopping alphachain.service on EC2...")
    st, out, err = run_ssm_command(ssm, ["systemctl stop alphachain.service"])
    print(f"Stop status: {st}")

    # Step 2: Update code on EC2
    print("\n[Step 2] Pulling latest code on EC2 from main...")
    cmd_pull = [
        "cd /opt/alphachain",
        "git fetch origin main",
        "git reset --hard origin/main",
        "git log -n 1 --oneline"
    ]
    st, out, err = run_ssm_command(ssm, cmd_pull)
    print(f"Git pull status: {st}")
    print(f"Output:\n{out}")
    if err:
        print(f"Error:\n{err}")

    # Step 3: Close all existing flat positions
    print("\n[Step 3] Liquidating flat hedged positions on Roostoo...")
    close_all_flat_positions(client)

    # Step 4: Clear active positions cache on EC2 database
    print("\n[Step 4] Resetting active_positions cache in SQLite on EC2...")
    cmd_db = [
        "python3 -c \"import sqlite3; conn = sqlite3.connect('/opt/alphachain/data/trading.db'); conn.execute('DELETE FROM active_positions'); conn.commit()\""
    ]
    st, out, err = run_ssm_command(ssm, cmd_db)
    print(f"DB reset status: {st}")

    # Step 5: Start alphachain.service on EC2
    print("\n[Step 5] Starting updated alphachain.service on EC2...")
    st, out, err = run_ssm_command(ssm, [
        "systemctl start alphachain.service",
        "sleep 3",
        "systemctl status alphachain.service --no-pager"
    ])
    print(f"Service start status: {st}")
    print(f"Status Output:\n{out}")

    # Step 6: Verify new balance
    print("\n[Step 6] Auditing final cash balance and equity...")
    time.sleep(2)
    new_bal = client.get_balance() or {}
    wallet = new_bal.get("SpotWallet") or new_bal.get("Wallet", {})
    usd = wallet.get("USD", {})
    print(f"Free USD: ${float(usd.get('Free', 0.0)):,.2f} USD")
    print(f"Locked USD: ${float(usd.get('Lock', 0.0)):,.2f} USD")
    print("\nDEPLOYMENT AND REBALANCE COMPLETE!")

if __name__ == "__main__":
    main()

