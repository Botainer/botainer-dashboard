#!/usr/bin/env python3
"""Explicit isolated workspace preparation/status; never installs packages."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from botainer_dashboard.cluster_backend import ClusterBackend

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',type=Path,required=True)
    p.add_argument('action',choices=('prepare','status'));args=p.parse_args()
    backend=ClusterBackend(Path(__file__).resolve().parents[1],args.profile)
    if args.action=='prepare':result=backend.prepare_remote()
    else:result=backend._call('status')
    print(json.dumps(result))

if __name__=='__main__':main()
