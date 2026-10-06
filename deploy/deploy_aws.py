import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy.launch_hackathon_ec2 import deploy_to_ec2

if __name__ == "__main__":
    deploy_to_ec2()

