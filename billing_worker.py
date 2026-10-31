"""Run in a separate terminal, or invoke --once from a scheduler."""
import argparse
import time
from datetime import datetime
from app import process_billing

parser=argparse.ArgumentParser(description='Generate due Cadence invoice drafts safely.')
parser.add_argument('--once',action='store_true')
parser.add_argument('--interval',type=int,default=300)
args=parser.parse_args()
while True:
    result=process_billing()
    print(datetime.now().isoformat(),result,flush=True)
    if args.once: break
    time.sleep(max(10,args.interval))
