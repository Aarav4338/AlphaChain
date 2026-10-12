import os
import sys
import time
import base64
import json
import io
import tarfile
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import boto3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DEPLOY_STATE_PATH = PROJECT_ROOT / "deploy" / "deployment_state.json"

load_dotenv(dotenv_path=ENV_PATH, override=True)

REGION = "ap-southeast-2"
LAUNCH_TEMPLATE_ID = "lt-0551233d212ccb731"
INSTANCE_TAG = "AlphaChain-Trading-Daemon"
REPO_URL = "https://github.com/Aarav4338/Carrotcuant.git"
DEPLOY_BRANCH = os.getenv("DEPLOY_BRANCH", "main")

def log(msg: str):
    print(f"[AWS-DEPLOY] {msg}", flush=True)

def get_boto_session():
    ak = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("aws_access_key_id")
    sk = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("aws_secret_access_key")
    st = os.getenv("AWS_SESSION_TOKEN") or os.getenv("aws_session_token")
    return boto3.Session(
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        aws_session_token=st,
        region_name=REGION
    )

def get_env_content() -> str:
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out_lines = []
    has_mode = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            continue
        if stripped.startswith("TRADING_MODE="):
            out_lines.append("TRADING_MODE=competition\n")
            has_mode = True
        elif not stripped.startswith("AWS_") and not stripped.startswith("aws_"):
            out_lines.append(line)

    if not has_mode:
        out_lines.append("TRADING_MODE=competition\n")

    out_lines.append("TRADING_PROFILE=bulletproof_fortress\n")
    return "".join(out_lines)

def get_clone_url() -> str:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        log("Using authenticated GitHub clone URL from GITHUB_TOKEN.")
        return f"https://x-access-token:{token}@github.com/Aarav4338/Carrotcuant.git"
    return REPO_URL

def build_user_data_script(env_content: str, clone_url: str = REPO_URL) -> str:
    env_b64 = base64.b64encode(env_content.encode("utf-8")).decode("ascii")

    script = f"""#!/bin/bash
set -euxo pipefail

# 1. System packages & Chrony Amazon Time Sync
dnf update -y
dnf install -y git python3 python3-pip tmux sqlite chrony

# Configure Chrony against Amazon Time Sync Service (169.254.169.123)
if ! grep -q "169.254.169.123" /etc/chrony.conf; then
    echo "server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4" >> /etc/chrony.conf
fi
systemctl restart chronyd || systemctl restart chrony
systemctl enable chronyd || systemctl enable chrony

# 2. Swap allocation for memory stability
if [ ! -f /swapfile ]; then
    dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
fi

# 3. Clone repository
mkdir -p /opt
rm -rf /opt/alphachain
git clone --branch {DEPLOY_BRANCH} {clone_url} /opt/alphachain
mkdir -p /opt/alphachain/data /opt/alphachain/logs

# 4. Write Production .env (strictly isolated, chmod 600)
cat << 'EOF' | base64 -d > /opt/alphachain/.env
{env_b64}
EOF
chmod 600 /opt/alphachain/.env

# 5. Setup Python Virtual Environment and Dependencies
python3 -m venv /opt/alphachain/venv
/opt/alphachain/venv/bin/pip install --upgrade pip
/opt/alphachain/venv/bin/pip install -r /opt/alphachain/requirements.txt
chown -R ec2-user:ec2-user /opt/alphachain
chmod 750 /opt/alphachain

# 6. Install AlphaChain 24/7 Systemd Service
cat << 'EOF' > /etc/systemd/system/alphachain.service
[Unit]
Description=AlphaChain Autonomous 24/7 Quantitative Trading Daemon
Documentation=https://github.com/Aarav4338/Carrotcuant
After=network.target network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
Type=simple
User=ec2-user
Group=ec2-user
WorkingDirectory=/opt/alphachain
EnvironmentFile=/opt/alphachain/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/opt/alphachain
ExecStart=/opt/alphachain/venv/bin/python3 scripts/run_bot.py
Restart=always
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=10
TimeoutStopSec=30
KillSignal=SIGTERM
SendSIGKILL=yes
KillMode=mixed
LimitNOFILE=65536
MemoryHigh=1800M
MemoryMax=2048M
ReadWritePaths=/opt/alphachain
StandardOutput=journal
StandardError=journal
SyslogIdentifier=alphachain

[Install]
WantedBy=multi-user.target
EOF

# 7. Enable and Start Daemon
systemctl daemon-reload
systemctl enable alphachain.service
systemctl start alphachain.service

# 8. Write bootstrap completion marker
echo '{{"status": "BOOTSTRAP_COMPLETE", "timestamp": "'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'"}}' > /opt/alphachain/logs/bootstrap_complete.json
chown ec2-user:ec2-user /opt/alphachain/logs/bootstrap_complete.json
"""
    return script

def terminate_previous_instances(ec2):
    existing = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_TAG]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]}
        ]
    )
    old_ids = []
    for r in existing.get("Reservations", []):
        for i in r.get("Instances", []):
            old_ids.append(i["InstanceId"])

    if old_ids:
        log(f"Terminating old instances to replace with fresh deployment: {old_ids}")
        ec2.terminate_instances(InstanceIds=old_ids)
        log("Waiting 10s for termination signal...")
        time.sleep(10)

def deploy_to_ec2():
    log("=== AlphaChain AWS EC2 Hackathon Deployment Starting ===")
    session = get_boto_session()
    ec2 = session.client("ec2")
    ssm = session.client("ssm")

    # 1. Clean up old instances if any
    terminate_previous_instances(ec2)

    # 2. Build UserData script
    env_content = get_env_content()
    clone_url = get_clone_url()
    user_data_script = build_user_data_script(env_content, clone_url)
    log(f"UserData script size: {len(user_data_script)} bytes (Limit: 25,600)")

    # 3. Launch EC2 instance via Hackathon Launch Template
    log(f"Launching EC2 instance using Launch Template '{LAUNCH_TEMPLATE_ID}' in {REGION}...")
    res = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": LAUNCH_TEMPLATE_ID},
        UserData=user_data_script,
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": INSTANCE_TAG},
                {"Key": "Project", "Value": "AlphaChain"},
                {"Key": "Environment", "Value": "Production"},
                {"Key": "Mode", "Value": "Competition"}
            ]
        }]
    )

    instance_id = res["Instances"][0]["InstanceId"]
    log(f"EC2 Instance successfully created! ID: {instance_id}")

    # 4. Wait for instance to enter 'running' state
    log("Waiting for EC2 instance to transition to 'running' state...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    desc = ec2.describe_instances(InstanceIds=[instance_id])
    inst = desc["Reservations"][0]["Instances"][0]
    public_ip = inst.get("PublicIpAddress", "N/A")
    private_ip = inst.get("PrivateIpAddress", "N/A")
    az = inst.get("Placement", {}).get("AvailabilityZone", "ap-southeast-2")
    inst_type = inst.get("InstanceType", "t3.medium")

    log(f"Instance is RUNNING!")
    log(f"  Instance ID: {instance_id}")
    log(f"  Type:        {inst_type}")
    log(f"  Public IP:   {public_ip}")
    log(f"  Private IP:  {private_ip}")
    log(f"  Zone:        {az}")

    # 5. Save deployment state
    state = {
        "instance_id": instance_id,
        "region": REGION,
        "instance_type": inst_type,
        "public_ip": public_ip,
        "private_ip": private_ip,
        "availability_zone": az,
        "launch_time": inst.get("LaunchTime", "").isoformat() if hasattr(inst.get("LaunchTime", ""), "isoformat") else str(inst.get("LaunchTime", "")),
        "status": "RUNNING",
        "launch_template": LAUNCH_TEMPLATE_ID,
        "trading_mode": "competition",
        "branch": DEPLOY_BRANCH,
        "profile": "bulletproof_fortress"
    }

    DEPLOY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DEPLOY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    log(f"Saved deployment state to {DEPLOY_STATE_PATH}")

    # 6. Poll SSM agent readiness
    log("Polling AWS Systems Manager (SSM) agent registration (waiting for cloud bootstrap)...")
    for attempt in range(1, 35):
        time.sleep(10)
        try:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            ).get("InstanceInformationList", [])
            if info:
                ssm_status = info[0].get("PingStatus")
                agent_version = info[0].get("AgentVersion")
                log(f"SSM Agent Connected! PingStatus: {ssm_status} | AgentVersion: {agent_version}")
                break
        except Exception:
            pass
        if attempt % 3 == 0:
            log(f"Waiting for SSM agent check-in... (elapsed: {attempt*10}s)")

    log("=== AlphaChain Cloud Deployment Complete & Operational! ===")
    return instance_id

if __name__ == "__main__":
    deploy_to_ec2()
