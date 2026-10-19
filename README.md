# CloudBox starter — Windows host, one friend VM

This starter provides a small authenticated web dashboard that can start one named VirtualBox VM in headless mode and request a graceful ACPI shutdown. It deliberately does not provide arbitrary command execution, disk browsing, snapshots, VM creation, or forced power-off.

## Before you begin

- Confirm Windows and the laptop are stable. Back up important files first.
- Do not configure router port forwarding, Tailscale Funnel, or public DNS for this prototype.
- Keep your existing Tailscale tailnet policy restrictive. Shared users should receive only TCP 443 on the two shared devices; broad wildcard rules can unintentionally grant more.
- The app should run as a normal Windows user, not from an Administrator PowerShell window.

## 1. Install prerequisites

Install VirtualBox from the official download page: https://www.oracle.com/virtualization/technologies/vm/downloads/virtualbox-downloads.html

Install Tailscale on Windows from: https://tailscale.com/download/windows

Download Ubuntu Server 24.04 LTS from: https://ubuntu.com/download/server

Create a VM in VirtualBox Manager named exactly `Friend1-VM`:

- Linux / Ubuntu (64-bit)
- 1 virtual CPU
- 2048 MB RAM to start
- 25 GB dynamically allocated virtual disk
- Network adapter: NAT
- Skip unattended install and create an administrator account during Ubuntu setup
- OpenSSH is optional; Cockpit will provide the browser terminal later

Install Ubuntu, then sign in to the VM console and run:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y cockpit curl
sudo systemctl enable --now cockpit.socket
sudo adduser friend1
```

`friend1` should be a normal account, not a sudoer. Do not add it to the `sudo` group. Use a strong unique password. The account is for the friend’s interactive browser terminal.

## 2. Join both machines to your private Tailscale network

On Windows, install and sign in to Tailscale using the account that owns the tailnet. On Ubuntu, run:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

Note the VM's Tailscale IPv4 address.

On Windows PowerShell, run:

```powershell
tailscale ip -4
```

Record the Windows host Tailscale IP too. Do not post these addresses publicly.

## 3. Install and run the dashboard

Place `cloudbox_dashboard.py` in a folder such as `C:\CloudBox`. In ordinary (non-Administrator) PowerShell:

```powershell
mkdir C:\CloudBox -ErrorAction SilentlyContinue
cd C:\CloudBox
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn
```

Copy `cloudbox_dashboard.py` into `C:\CloudBox`. Once the VM Serve URL is known from step 5, replace the `CLOUDBOX_TERMINAL_URL` placeholder below. Then set the environment variables in that same PowerShell window and run the server:

```powershell
$env:CLOUDBOX_USER = 'friend1'
$env:CLOUDBOX_VM_NAME = 'Friend1-VM'
$env:CLOUDBOX_TERMINAL_URL = 'https://REPLACE-WITH-VM-SERVE-HOSTNAME/'
$env:VBOXMANAGE = "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe"
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$env:CLOUDBOX_PASSWORD = [Convert]::ToBase64String($bytes)
Write-Host "CloudBox password (share privately with your friend): $env:CLOUDBOX_PASSWORD"
.\.venv\Scripts\python.exe -m uvicorn cloudbox_dashboard:app --host 127.0.0.1 --port 8765
```

Keep that PowerShell window open. On the host, confirm `http://127.0.0.1:8765` opens and asks for the Basic Auth username/password. Sign in using `friend1` and the generated password. Confirm the VM name matches exactly; VirtualBox names are case-sensitive in the dashboard's configuration.

## 4. Publish the dashboard only to Tailscale

In a separate **Administrator PowerShell** window, run:

```powershell
tailscale serve --bg 8765
tailscale serve status
```

Tailscale Serve should show an HTTPS MagicDNS URL. If it asks to enable HTTPS certificates, follow the consent flow. Do not run `tailscale funnel` and do not add any router port forwarding.

## 5. Provide a browser terminal inside the VM

In the Ubuntu VM console, run:

```bash
sudo tailscale serve --bg https+insecure://localhost:9090
sudo tailscale serve status
```

Copy the HTTPS MagicDNS URL shown for the VM. Test it while signed into Tailscale on your own device; log in to Cockpit as `friend1`. Because that account is not a sudoer, it should not have root administration privileges.

Use the VM's actual HTTPS MagicDNS URL (the one reported by `tailscale serve status`) in `$env:CLOUDBOX_TERMINAL_URL` before launching the dashboard. If you have already started the dashboard, stop it with Ctrl+C and rerun it with that variable set. The dashboard only creates a clickable link when this value begins with `https://`.

## 6. Share only the two web services

In the Tailscale admin console, share the Windows host and the Ubuntu VM with the friend's Tailscale account. Device sharing grants access to the shared machine, not the entire tailnet; access policies can further restrict ports. See https://tailscale.com/docs/features/sharing

Before sharing, inspect your tailnet's access policy. Add grants for the friend's email to the Windows host's Tailscale IP on **TCP 443 only**, and the VM's Tailscale IP on **TCP 443 only**. Preserve an explicit full-access rule for your own account. Remove or narrow broad wildcard allow rules that would also permit the friend to reach other ports. Tailscale documents grants and port-level permissions here: https://tailscale.com/docs/features/access-control/grants

A fresh-policy example shape (replace every example value; do not blindly overwrite an existing policy) is:

```json
{
  "grants": [
    { "src": ["YOUR_TAILSCALE_EMAIL"], "dst": ["*"], "ip": ["*"] },
    { "src": ["FRIEND_TAILSCALE_EMAIL"], "dst": ["WINDOWS_HOST_TAILSCALE_IP"], "ip": ["tcp:443"] },
    { "src": ["FRIEND_TAILSCALE_EMAIL"], "dst": ["VM_TAILSCALE_IP"], "ip": ["tcp:443"] }
  ],
  "tests": [
    {
      "src": "FRIEND_TAILSCALE_EMAIL",
      "accept": ["WINDOWS_HOST_TAILSCALE_IP:443", "VM_TAILSCALE_IP:443"],
      "deny": ["WINDOWS_HOST_TAILSCALE_IP:22", "WINDOWS_HOST_TAILSCALE_IP:3389", "VM_TAILSCALE_IP:22"]
    }
  ]
}
```

Merge this with the existing policy rather than overwriting it if you already use Tailscale for other devices. Make sure no broad wildcard rule in either `grants` or legacy `acls` (for example, all sources to all destinations and all ports) grants the shared friend extra access. Preserve access for your own account. Run policy tests before accepting the share invitations. Only invite a person you trust.

## 7. Test end-to-end

1. Friend installs Tailscale and accepts both machine shares.
2. Friend opens the Windows host's HTTPS MagicDNS URL and authenticates with the CloudBox dashboard password.
3. Click Start VM and wait for Ubuntu to boot.
4. Open the VM's HTTPS MagicDNS URL and sign in to Cockpit as `friend1`.
5. Click Request graceful shutdown from CloudBox. Wait for the VM status to change.
6. From a non-Tailscale connection, confirm neither URL is reachable. There should be no router port forwarding.

## Limitations and security notes

- This is a trusted-friend learning prototype, not a production cloud or a strong multi-tenant boundary.
- The dashboard controls one fixed VM only. It has no arbitrary command endpoint and no hard power-off button.
- The friend can use their normal Linux account; do not grant `sudo` unless you intend to grant system-administration rights inside that VM.
- Keep Windows, VirtualBox, Tailscale, Ubuntu, Cockpit and Python dependencies updated.
- Do not run this dashboard as Windows Administrator. Keep your host firewall enabled and avoid sharing the Windows VM management GUI directly.
- If the laptop sleeps, loses power/internet, or reboots, the cloud is unavailable. This is not high-availability hosting.
