#!/usr/bin/env bash
# ==============================================================================
# AlphaChain - AWS EC2 Automated Provisioning Script
# Region: ap-southeast-2 (Sydney)
# OS: Ubuntu 22.04 LTS (x86_64)
# Instance Type: t3.small (burstable 2 vCPU, 2GB RAM, optimal for AlphaChain)
# ==============================================================================

set -euo pipefail

REGION="ap-southeast-2"
INSTANCE_TYPE="t3.small"
KEY_NAME="alphachain-sydney-key"
KEY_FILE="${KEY_NAME}.pem"
SG_NAME="alphachain-trading-sg"
INSTANCE_TAG="AlphaChain-Trading-Daemon"

echo "=============================================================================="
echo "⚡ Provisioning AlphaChain EC2 Instance in Sydney (${REGION})"
echo "=============================================================================="

# 1. Verify AWS CLI and active credentials
if ! command -v aws &> /dev/null; then
    echo "❌ Error: AWS CLI is not installed or not in PATH."
    echo "   Install AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

echo "🔍 Validating AWS credentials in region ${REGION}..."
CALLER_ID=$(aws sts get-caller-identity --region "${REGION}" --output json)
echo "✅ Authenticated as: $(echo "${CALLER_ID}" | jq -r .Arn)"

# 2. Retrieve Default VPC
echo "🌐 Resolving default VPC..."
VPC_ID=$(aws ec2 describe-vpcs --region "${REGION}" --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)
if [ "${VPC_ID}" == "None" ] || [ -z "${VPC_ID}" ]; then
    echo "❌ Error: No default VPC found in ${REGION}. Please create or specify a VPC."
    exit 1
fi
echo "✅ Using Default VPC: ${VPC_ID}"

# 3. Create or reuse Security Group
echo "🛡️ Configuring Security Group (${SG_NAME})..."
SG_ID=$(aws ec2 describe-security-groups --region "${REGION}" --filters "Name=group-name,Values=${SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)

if [ "${SG_ID}" == "None" ] || [ -z "${SG_ID}" ]; then
    echo "Creating security group ${SG_NAME}..."
    SG_ID=$(aws ec2 create-security-group \
        --group-name "${SG_NAME}" \
        --description "Security group for AlphaChain 24/7 Trading Daemon" \
        --vpc-id "${VPC_ID}" \
        --region "${REGION}" \
        --query "GroupId" --output text)
    
    # Authorize SSH from deployer public IP (strictly fail-closed, never default to 0.0.0.0/0)
    if [ -n "${DEPLOYER_CIDR:-}" ]; then
        SSH_CIDR="${DEPLOYER_CIDR}"
        echo "Using user-specified deployer CIDR: ${SSH_CIDR}"
    else
        MY_IP=$(curl -s --connect-timeout 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)
        if [ -z "${MY_IP}" ] || [ "${MY_IP}" == "0.0.0.0/0" ]; then
            echo "❌ [SECURITY FATAL] Failed to determine deployer public IP via checkip.amazonaws.com."
            echo "In strict accordance with the user security directive, AlphaChain refuses to open port 22 to 0.0.0.0/0."
            echo "Please provide your authorized CIDR explicitly: export DEPLOYER_CIDR='<YOUR_IP>/32' and re-run."
            exit 1
        fi
        SSH_CIDR="${MY_IP}/32"
    fi

    echo "Authorizing inbound SSH (port 22) strictly from ${SSH_CIDR}..."
    aws ec2 authorize-security-group-ingress \
        --group-id "${SG_ID}" \
        --protocol tcp \
        --port 22 \
        --cidr "${SSH_CIDR}" \
        --region "${REGION}"
else
    echo "✅ Reusing existing Security Group: ${SG_ID}"
fi

# 4. Create or reuse Key Pair
echo "🔑 Checking SSH Key Pair (${KEY_NAME})..."
KEY_EXISTS=$(aws ec2 describe-key-pairs --region "${REGION}" --key-names "${KEY_NAME}" --query "KeyPairs[0].KeyName" --output text 2>/dev/null || true)

if [ "${KEY_EXISTS}" != "${KEY_NAME}" ]; then
    echo "Creating new EC2 Key Pair: ${KEY_NAME}..."
    aws ec2 create-key-pair \
        --key-name "${KEY_NAME}" \
        --key-type rsa \
        --key-format pem \
        --region "${REGION}" \
        --query "KeyMaterial" \
        --output text > "${KEY_FILE}"
    chmod 400 "${KEY_FILE}"
    echo "✅ Key saved to: ${KEY_FILE} (chmod 400)"
else
    echo "✅ Key pair ${KEY_NAME} already exists in AWS."
    if [ ! -f "${KEY_FILE}" ]; then
        echo "⚠️ Note: Local private key file ${KEY_FILE} not found. Ensure you have the original PEM key."
    fi
fi

# 5. Resolve official Ubuntu 22.04 LTS AMI ID via AWS SSM Parameter
echo "🔎 Querying latest Ubuntu 22.04 LTS AMI in ${REGION}..."
AMI_ID=$(aws ssm get-parameter \
    --name "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id" \
    --region "${REGION}" \
    --query "Parameter.Value" \
    --output text)
echo "✅ Resolved AMI ID: ${AMI_ID}"

# 6. Launch EC2 Instance with encrypted gp3 root volume
echo "🚀 Launching EC2 instance (${INSTANCE_TYPE}) in Sydney..."
INSTANCE_DATA=$(aws ec2 run-instances \
    --image-id "${AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids "${SG_ID}" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_TAG}},{Key=Project,Value=AlphaChain},{Key=Environment,Value=Production}]" \
    --region "${REGION}" \
    --output json)

INSTANCE_ID=$(echo "${INSTANCE_DATA}" | jq -r '.Instances[0].InstanceId')
echo "✅ Instance created! Instance ID: ${INSTANCE_ID}"

echo "⏳ Waiting for instance to transition to running state..."
aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}" --region "${REGION}"

# 7. Retrieve Public IP Address
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "${INSTANCE_ID}" \
    --region "${REGION}" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text)

echo "=============================================================================="
echo "🎉 EC2 Instance Successfully Launched!"
echo "   Instance ID:     ${INSTANCE_ID}"
echo "   Region:          ${REGION} (Sydney)"
echo "   Public IPv4:     ${PUBLIC_IP}"
echo "   SSH Command:     ssh -i ${KEY_FILE} ubuntu@${PUBLIC_IP}"
echo ""
echo "   Deployment Commands:"
echo "   1. Deploy Code & Bootstrap:"
echo "      scp -i ${KEY_FILE} deploy/bootstrap_ec2.sh ubuntu@${PUBLIC_IP}:/home/ubuntu/"
echo "      ssh -i ${KEY_FILE} ubuntu@${PUBLIC_IP} 'chmod +x /home/ubuntu/bootstrap_ec2.sh && sudo /home/ubuntu/bootstrap_ec2.sh'"
echo "   2. Securely Deploy .env:"
echo "      scp -i ${KEY_FILE} .env ubuntu@${PUBLIC_IP}:/opt/alphachain/.env"
echo "      ssh -i ${KEY_FILE} ubuntu@${PUBLIC_IP} 'sudo chmod 600 /opt/alphachain/.env && sudo chown ubuntu:ubuntu /opt/alphachain/.env'"
echo "   3. Start Daemon:"
echo "      ssh -i ${KEY_FILE} ubuntu@${PUBLIC_IP} 'sudo systemctl start alphachain && sudo systemctl status alphachain'"
echo "=============================================================================="
