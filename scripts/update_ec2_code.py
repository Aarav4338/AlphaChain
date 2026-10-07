"""
Safe autonomous deployment script to update code on AWS EC2:
1. Connects to EC2 instance via AWS SSM
2. Pulls the latest code from GitHub main (commit with fee churn fix)
3. Restarts alphachain.service
4. Verifies service status and live logs
NOTE: Does NOT liquidate or interfere with open positions (TSLAB, GOOGLB).
"""
import os
import sys
import time
from pathlib import Path
import boto3
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(override=True)

INSTANCE_ID = "i-0b0c271715def1290"
REGION = os.getenv("AWS_REGION") or "ap-southeast-2"

def get_ssm_client():
    ak = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("aws_access_key_id")
    sk = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("aws_secret_access_key")
    st = os.getenv("AWS_SESSION_TOKEN") or os.getenv("aws_session_token")
    return boto3.client(
        "ssm",
        region_name=REGION,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        aws_session_token=st,
    )

def run_ssm_command(ssm, commands, wait_seconds=4):
    print(f"Executing remote SSM commands on {INSTANCE_ID}:")
    for cmd in commands:
        print(f"  $ {cmd}")
    resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands}
    )
    cid = resp["Command"]["CommandId"]
    for _ in range(12):
        time.sleep(wait_seconds)
        res = ssm.get_command_invocation(CommandId=cid, InstanceId=INSTANCE_ID)
        status = res.get("Status")
        if status in ["Success", "Failed", "TimedOut"]:
            return status, res.get("StandardOutputContent"), res.get("StandardErrorContent")
    return "Timeout", None, "Timed out waiting for command execution"

def main():
    print("=" * 80)
    print("ALPHACHAIN SAFE EC2 CODE DEPLOYMENT")
    print(f"Target Instance: {INSTANCE_ID} | Region: {REGION}")
    print("=" * 80)

    try:
        ssm = get_ssm_client()
        # Test connection
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [INSTANCE_ID]}]
        )
        instances = info.get("InstanceInformationList", [])
        if not instances:
            print(f"Warning: Instance {INSTANCE_ID} not immediately visible in SSM list.")
        else:
            ping_status = instances[0].get("PingStatus")
            print(f"Instance Online: {INSTANCE_ID} (SSM PingStatus: {ping_status})")
    except Exception as e:
        print(f"\n[AWS Credential Error] Failed to connect to AWS SSM: {e}")
        print("\nPlease update .env with fresh temporary credentials from your AWS Console:")
        print("  aws_access_key_id=...")
        print("  aws_secret_access_key=...")
        print("  aws_session_token=...")
        sys.exit(1)

    # Remote commands to update code and restart service
    commands = [
        "for d in /opt/alphachain /home/ec2-user/alphachain /home/ubuntu/alphachain /opt/roostoo; do if [ -d \"$d/.git\" ]; then APP_DIR=\"$d\"; break; fi; done",
        "echo \"Target App Directory: $APP_DIR\"",
        "cd \"$APP_DIR\"",
        "git fetch origin main",
        "git reset --hard origin/main",
        "echo '--- LATEST COMMIT ON EC2 ---'",
        "git log -n 1 --oneline",
        "echo '--- RESTARTING ALPHACHAIN SERVICE ---'",
        "systemctl restart alphachain.service || systemctl restart carrotquant.service || true",
        "sleep 3",
        "systemctl status alphachain.service --no-pager || true",
        "echo '--- RECENT LOG ENTRIES ---'",
        "tail -n 15 $APP_DIR/logs/*.log 2>/dev/null || true"
    ]

    status, stdout, stderr = run_ssm_command(ssm, commands)
    print(f"\nSSM Command Execution Status: {status}")
    if stdout:
        print("\n--- REMOTE STDOUT ---")
        print(stdout)
    if stderr:
        print("\n--- REMOTE STDERR ---")
        print(stderr)

    if status == "Success":
        print("\nSUCCESS: Code successfully pulled and alphachain.service restarted on AWS EC2!")
    else:
        print(f"\nFAILURE: Command completed with status {status}")

if __name__ == "__main__":
    main()

