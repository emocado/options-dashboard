# Deploying the dashboard to your phone (securely)

Goal: view the dashboard on your phone from anywhere, with top-notch security and
**no ports opened on your router**.

## Why it runs on your PC (not the cloud)

The Sync feature talks to **moomoo OpenD on `127.0.0.1:11111`**, which only runs on
your PC where you're logged into moomoo. So the dashboard must run on your PC too.
We make it reachable from your phone through **Tailscale** — a private, encrypted
(WireGuard) mesh network between *your own* devices. Traffic never touches the
public internet, and nothing is exposed to the world.

Security model, in layers:
1. **Network:** only devices on *your* tailnet can reach the app (device-authenticated).
2. **Transport:** all traffic is end-to-end encrypted by WireGuard.
3. **App:** a password gate (PBKDF2-hashed) guards the dashboard itself.
4. **Trading:** the app is read-only and never places orders; keep trading **locked**
   in OpenD (read queries don't need it unlocked).

---

## One-time setup

### 1. Set the app password
From the project folder:
```powershell
python tools/set_password.py
```
Enter a strong password (10+ chars; a passphrase is great). It's saved as a salted
hash in `.streamlit/secrets.toml` (gitignored — never committed).

### 2. Install Tailscale on your PC
- Download from <https://tailscale.com/download/windows>, install, and **sign in**
  (Google/Microsoft/GitHub/email). A free personal account is plenty.
- In the Tailscale admin console, make sure **MagicDNS** is enabled (Settings → DNS).

### 3. Install Tailscale on your phone
- Install the **Tailscale** app (iOS App Store / Google Play), sign in with the
  **same account**, and toggle it **Connected**.
- Both devices now share a private network. Find your PC's tailnet IP with
  `tailscale ip -4` (looks like `100.x.y.z`) or its MagicDNS name in the admin console.

---

## Run it

### Start the dashboard (bound to your tailnet only)
```powershell
.\run_dashboard.ps1
```
This binds Streamlit to your Tailscale address, so it listens **only** on the
tailnet — not on your home Wi-Fi or the internet. It prints the exact URL.

### Open it on your phone
With Tailscale **Connected** on the phone, open:
```
http://<your-PC-tailscale-ip>:8501
```
(e.g. `http://100.101.102.103:8501`). Sign in with your password. Done.

> Traffic over the tailnet is WireGuard-encrypted, so plain `http://` here is still
> end-to-end encrypted between your phone and PC. Phones won't show a padlock for
> `http`; if that bothers you, use the optional HTTPS step below.

### Tip: add it to your phone's home screen
In your mobile browser, choose **Add to Home Screen** to get an app-like icon.

---

## Optional: a clean HTTPS URL (real padlock)

Tailscale can put valid HTTPS in front of the app on your tailnet:
```powershell
# 1) Run the app bound to localhost (so only Tailscale proxies to it):
.\run_dashboard.ps1 -Mode local
# 2) In another terminal, serve it over HTTPS on your tailnet:
tailscale serve --bg 8501
```
Then open `https://<your-pc>.<your-tailnet>.ts.net` on your phone — a real cert,
no browser warning. (Enable **HTTPS Certificates** + **MagicDNS** in the Tailscale
admin console first. Command syntax varies by version — see `tailscale serve --help`.)

If the HTTPS/proxy route ever shows an endless "Please wait…" spinner (a Streamlit
websocket/XSRF quirk behind proxies), just use the direct tailnet method above
(`run_dashboard.ps1` + `http://100.x:8501`) — same encryption, no proxy.

---

## Run it in the background

Use the controller script (runs detached — survives closing the terminal):
```powershell
.\dashboardctl.ps1 start            # start hidden in the background (tailnet)
.\dashboardctl.ps1 start -Mode local  # bind to localhost only
.\dashboardctl.ps1 status           # RUNNING / STOPPED
.\dashboardctl.ps1 logs             # tail the log
.\dashboardctl.ps1 stop             # stop it
.\dashboardctl.ps1 restart          # restart
```
Logs are written to `logs\dashboard.out.log` / `dashboard.err.log`.

> If PowerShell blocks the script ("running scripts is disabled"), launch it with:
> `powershell -ExecutionPolicy Bypass -File .\dashboardctl.ps1 start`

## Optional: auto-start on login

Run the dashboard automatically (hidden) every time you log in:
```powershell
$act = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PWD\run_dashboard.ps1`""
$trg = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "OptionsDashboard" -Action $act -Trigger $trg `
  -Description "Options Wheel Dashboard"
```
Remove it later with `Unregister-ScheduledTask -TaskName OptionsDashboard -Confirm:$false`.
Make sure **OpenD also starts and you're logged in** for Sync to work.

---

## Security checklist (do these)

- [x] **App password set** (`tools/set_password.py`) — already wired into the app.
- [ ] **Never port-forward** 8501 or 11111 on your router. Tailscale replaces that.
- [ ] **Keep OpenD on localhost.** Don't change its bind from `127.0.0.1`; never
      expose port `11111`.
- [ ] **Keep trading LOCKED in OpenD** unless you're actively placing orders. The
      dashboard only *reads* (deals/positions/account) and never trades.
- [ ] **Don't commit secrets/data.** `.streamlit/secrets.toml`, `config.toml`, and
      `data/` are gitignored. The `data/dashboard.db` holds your trade history —
      keep it local.
- [ ] **Lock down your tailnet (optional, stronger):** in the Tailscale admin you can
      add ACLs so only your phone may reach your PC on port 8501, and enable
      **device approval** so new devices can't join without your OK.
- [ ] **Use a unique, strong dashboard password** and your phone's screen lock.

## Updating the password later
Re-run `python tools/set_password.py` and restart the app.

## Local testing (no phone)
```powershell
.\run_dashboard.ps1 -Mode local      # http://localhost:8501 on this PC only
```
