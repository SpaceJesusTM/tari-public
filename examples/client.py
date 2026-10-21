#!/usr/bin/env python3
"""Minimal trusted-backend client. Keep TARI_GATEWAY_TOKEN server-side."""
import argparse
import json
import os
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("message")
parser.add_argument("--conversation", default="voice-demo-one")
parser.add_argument("--port", type=int, default=18899)
args = parser.parse_args()
request = urllib.request.Request(
    f"http://127.0.0.1:{args.port}/v1/chat/completions",
    data=json.dumps({"model": "openclaw/tari", "user": args.conversation,
                     "messages": [{"role": "user", "content": args.message}]}).encode(),
    headers={"Authorization": "Bearer " + os.environ["TARI_GATEWAY_TOKEN"],
             "Content-Type": "application/json"},
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request, timeout=300) as response:
    print(json.load(response)["choices"][0]["message"]["content"])
