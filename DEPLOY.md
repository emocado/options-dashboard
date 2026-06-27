# Deploying the dashboard to your phone (securely)

Goal: view the dashboard on your phone from anywhere, with top-notch security and
**no ports opened on your router**.

> **Two free ways to do this:**
> - **Option A — Tailscale (this section):** the dashboard runs on your PC and is
>   reachable over a private VPN. All data stays on your machine, but the PC must be on
>   to view. Best for privacy.
> - **Option B — Cloud hosting:** host the UI on Streamlit Community Cloud (free)
>   reading from a free Turso DB, with a small sync agent on your PC. View anytime, even
>   with the PC asleep. Jump to [**Cloud hosting**](#cloud-hosting-streamlit-community-cloud--turso).

## Why Option A runs on your PC (not the cloud)

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

---

# Cloud hosting (Streamlit Community Cloud + Turso)

Host the **dashboard** for free in the cloud so you can open it from anywhere — even
when your PC is off — while a small **sync agent** on your PC keeps the data fresh.

**How it works:** your PC runs the sync (the only place moomoo OpenD can run) and writes
the results to a free **Turso** database. The cloud dashboard reads from Turso. OpenD is
never exposed to the internet.

```
PC (when on):  OpenD → tools/sync_to_cloud.py ─┐
                                                ├─► Turso (free DB) ◄─ Streamlit Cloud (UI)
manual entry (cloud or PC) ─────────────────────┘
```

> **Privacy note:** with this option your trade history and account NAV live in Turso
> (a third party), behind your dashboard password. To keep *all* data on your own
> machine, use **Option A (Tailscale)** above instead.

### 1. Create a Turso database
Install the Turso CLI (<https://docs.turso.tech>), then:
```bash
turso auth signup                          # free account
turso db create options-dashboard
turso db show options-dashboard --url      # -> libsql://...     (TURSO_DATABASE_URL)
turso db tokens create options-dashboard   # -> the token        (TURSO_AUTH_TOKEN)
```
> **Optional hardening:** make a **read-only** token for the cloud app
> (`turso db tokens create options-dashboard --read-only`) and keep a read-write token
> only on your PC — do this if you'll just *view* on your phone. If you want to add
> manual trades / tag assignments from your phone, give the cloud app a read-write token.

### 2. Save the secrets on your PC
Add these to `.streamlit/secrets.toml` (gitignored — never committed), next to your
existing `APP_PASSWORD_HASH`:
```toml
APP_PASSWORD_HASH  = "pbkdf2_sha256$..."   # from tools/set_password.py
TURSO_DATABASE_URL = "libsql://options-dashboard-<you>.turso.io"
TURSO_AUTH_TOKEN   = "<your token>"
```

### 3. Install local deps and migrate your data
```powershell
pip install -r requirements-local.txt
python tools/migrate_to_turso.py     # copies your existing data/dashboard.db up to Turso
```

### 4. Push the repo to GitHub (private)
Secrets and `data/` are gitignored, so this is safe:
```powershell
git add -A; git commit -m "Add cloud hosting"; git push
```

### 5. Deploy on Streamlit Community Cloud
- Go to <https://share.streamlit.io>, sign in with GitHub, **New app**, pick your repo
  and `app.py`.
- In **Advanced settings → Secrets**, paste (TOML):
  ```toml
  APP_PASSWORD_HASH  = "pbkdf2_sha256$..."
  TURSO_DATABASE_URL = "libsql://options-dashboard-<you>.turso.io"
  TURSO_AUTH_TOKEN   = "<read-only or read-write token>"
  DASHBOARD_MODE     = "cloud"
  ```
- Deploy, open the public URL, and sign in with your dashboard password.
  `DASHBOARD_MODE = "cloud"` hides the moomoo Sync/Settings controls (they can't reach
  OpenD from the cloud) and shows a "Last synced" time instead.

### 6. Schedule the sync agent on your PC
Run it once to confirm it works (OpenD running and logged in):
```powershell
.\tools\sync_to_cloud.ps1
```
Then schedule it (at logon + every 6 hours). It logs and exits cleanly whenever OpenD
isn't running, so off-hours runs are harmless:
```powershell
$act = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PWD\tools\sync_to_cloud.ps1`""
$trg1 = New-ScheduledTaskTrigger -AtLogOn
$trg2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName "OptionsDashboardSync" -Action $act -Trigger $trg1, $trg2 `
  -Description "Push moomoo data to Turso for the cloud dashboard"
```
Remove it later with `Unregister-ScheduledTask -TaskName OptionsDashboardSync -Confirm:$false`.
Logs go to `logs\sync_to_cloud.log`.

> Data is only as fresh as the last successful sync (which needs OpenD running). For the
> wheel that's fine — positions move over days/weeks — and the sidebar shows the last
> sync time.

---

# Google sign-in (lock the cloud app to your account)

Instead of the shared password, the cloud app can use **Google sign-in restricted to
your email** — nicer on a phone and no secret to leak. When an `[auth]` block is present
in the app's secrets the dashboard uses Google automatically; otherwise it falls back to
the password. Anyone can *authenticate* with Google, but only emails in `ALLOWED_EMAILS`
(and email-verified) are let in — everyone else is signed straight back out.

### 1. Create a Google OAuth client
1. [Google Cloud Console](https://console.cloud.google.com/) → create or pick a project.
2. **APIs & Services → OAuth consent screen**: choose **External**, fill the basics, and
   under **Test users** add your own Google address. (Leaving the app in "Testing" is
   fine for personal use — only listed test users can sign in.)
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URIs** — add your app URL + `/oauth2callback`:
     `https://<your-app>.streamlit.app/oauth2callback`
     (also add `http://localhost:8501/oauth2callback` if you run it locally).
4. Copy the **Client ID** and **Client secret**.

### 2. Add the auth secrets
Generate a random cookie secret:
`python -c "import secrets; print(secrets.token_hex(32))"`

In **Streamlit Cloud → your app → ⋮ → Settings → Secrets** (and in local
`.streamlit/secrets.toml` if you also run locally), add:
```toml
ALLOWED_EMAILS = "weikhiang92000@gmail.com"   # comma-separated to allow more than one

[auth]
redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
cookie_secret = "<random hex from above>"
client_id = "<google client id>"
client_secret = "<google client secret>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```
Keep your existing `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, and `DASHBOARD_MODE = "cloud"`.
`APP_PASSWORD_HASH` is no longer needed once `[auth]` is set (it stays as the local
fallback if you keep it).

### 3. Redeploy
Push the updated `requirements.txt` (it now pins `streamlit>=1.42` and adds `Authlib`),
then **Reboot** the app in Streamlit Cloud so the new deps install. You'll get a **Sign
in with Google** button; only allowlisted, verified emails get through.

> **Security:** the `ALLOWED_EMAILS` check is what restricts *access* to you. If it's
> empty the app fails **closed** (locks out everyone). Your Turso token still controls
> database access separately, and for max safety the cloud app can use a **read-only**
> Turso token (see step 1 of Cloud hosting).
