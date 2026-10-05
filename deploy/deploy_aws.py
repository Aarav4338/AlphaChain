"""
AlphaChain AWS EC2 Deployment Entrypoint.
Delegates to the hackathon launch template deployment pipeline.
"""
from deploy.launch_hackathon_ec2 import deploy_to_ec2

if __name__ == "__main__":
    deploy_to_ec2()
