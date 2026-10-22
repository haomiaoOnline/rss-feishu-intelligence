#!/usr/bin/env python3
"""Install and verify persistent, localhost-only Mac SSH access to the RSS admin.

Does not read or store the server's admin token or any SSH private key.
"""
import argparse
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
from urllib.request import urlopen

LABEL = "com.haomiao.rss-feishu-admin-tunnel"
URL = "http://127.0.0.1:8765/login"

def desired_plist():
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/ssh", "-N", "-T",
            "-L", "127.0.0.1:8765:127.0.0.1:8765",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=12",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "aliyunVBS",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 20,
        "ProcessType": "Background",
    }

def run(*args):
    return subprocess.run(args,capture_output=True,text=True,timeout=20)

def launch_domain():
    return "gui/"+str(os.getuid())

def plist_path():
    return Path.home()/"Library"/"LaunchAgents"/(LABEL+".plist")

def is_loaded():
    return run("launchctl","print",launch_domain()+"/"+LABEL).returncode==0

def install():
    path=plist_path()
    path.parent.mkdir(mode=0o700,exist_ok=True,parents=True)
    config=desired_plist()
    if path.exists():
        if path.is_symlink() or plistlib.loads(path.read_bytes()) != config:
            raise RuntimeError("Existing LaunchAgent differs: review manually, no overwrite")
    else:
        with path.open("wb") as f:plistlib.dump(config,f,sort_keys=True)
        path.chmod(0o600)
    lint=run("plutil","-lint",str(path))
    if lint.returncode:raise RuntimeError("Invalid LaunchAgent plist")
    if not is_loaded():
        q=run("launchctl","bootstrap",launch_domain(),str(path))
        if q.returncode:raise RuntimeError("Unable to register SSH LaunchAgent")
    print("MAC_ADMIN_TUNNEL_REGISTERED")
    status()

def status():
    exists=plist_path().is_file()
    loaded=is_loaded()
    try:
        with socket.create_connection(("127.0.0.1",8765),timeout=3):
            tcp=True
    except OSError:
        tcp=False
    http_ok=False
    if tcp:
        try:
            with urlopen(URL,timeout=5) as r:
                http_ok=(r.status==200 and b"RSS" in r.read(4000))
        except OSError:
            pass
    print("PLIST_INSTALLED",exists)
    print("LAUNCH_AGENT_RUNNING",loaded)
    print("LOCAL_TUNNEL_CONNECTED",tcp)
    print("RSS_ADMIN_LOGIN_HTTP200",http_ok)
    if not (exists and loaded and tcp and http_ok):
        raise RuntimeError("Tunnel is not healthy; check SSH alias/auth and VPS service")

def open_page():
    status()
    browser=run("/usr/bin/open","-a","Google Chrome",URL)
    if browser.returncode:
        browser=run("/usr/bin/open",URL)
    if browser.returncode:raise RuntimeError("Could not open default browser")
    print("BROWSER_OPENED",URL)

def uninstall():
    p=plist_path()
    if not p.exists():print("NOT_INSTALLED");return
    if p.is_symlink() or plistlib.loads(p.read_bytes())!=desired_plist():
        raise RuntimeError("LaunchAgent does not match; refusing to remove")
    if is_loaded():
        q=run("launchctl","bootout",launch_domain()+"/"+LABEL)
        if q.returncode:raise RuntimeError("Could not unload SSH LaunchAgent")
    p.unlink()
    print("MAC_ADMIN_TUNNEL_UNINSTALLED")

def main():
    if sys.platform!="darwin":raise SystemExit("macOS only")
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("install","status","open","uninstall"))
    cmd=parser.parse_args().command
    {"install":install,"status":status,"open":open_page,"uninstall":uninstall}[cmd]()

if __name__=="__main__":main()
