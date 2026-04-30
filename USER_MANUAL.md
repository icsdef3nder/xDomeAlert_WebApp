# xDomeAlert WebApp - User Manual

## 1. Introduction

xDomeAlert is a Flask-based web application that serves as a front-end dashboard
for the xDome OT/ICS security platform. It proxies data from the xDome API and
presents alerts, affected devices, and OT activity events through a filterable,
column-customizable interface.

The application is designed for two primary audiences:

- **SOC analysts and operators** who monitor alerts and investigate OT activity
  on a daily basis (the *viewer* role).
- **IT and security administrators** who configure the connection to the xDome
  API, manage branding, and operate the underlying Linux host (the *admin*
  role).

This manual describes the features available in the web UI, the workflow for
typical investigation tasks, and the configuration tasks reserved for
administrators.

---

## 2. Prerequisites and Access Requirements

### 2.1 Who can log in

xDomeAlert authenticates users via **Linux PAM** - that is, against the
underlying operating system's user accounts on the host where the application
runs. There is no separate user database in the application.

To log in, a user must:

1. Have a valid Linux user account on the application host.
2. Be authorized to authenticate via PAM (the standard system login policy
   applies).
3. Be assigned a role in the role mapping file (typically
   `/etc/xdome-roles`) or via the system groups configured by the
   administrator.

### 2.2 Roles

| Role     | Capabilities                                                                                          |
|----------|-------------------------------------------------------------------------------------------------------|
| viewer   | View the alerts dashboard, drill into devices and OT activity events, view the tree view.            |
| admin    | All viewer capabilities, plus access to the Settings pages (xDome API configuration and logo upload). |

If a user without the appropriate role attempts to access an admin-only page,
they will receive an HTTP 403 (Forbidden) response.

### 2.3 Browser requirements

The UI is built for modern, evergreen browsers (recent versions of Chromium,
Firefox, and Edge). JavaScript and cookies must be enabled. The Content
Security Policy applied by the server is strict, so browser extensions that
inject scripts may interfere with the application.

---

## 3. Logging In and Out

### 3.1 Logging in

1. Navigate to the application URL provided by your administrator
   (for example, `https://xdomealert.example.local/`).
2. You will be redirected to `/login` if you are not yet authenticated.
3. Enter your **Linux username** and **password** for the application host.
4. Submit the form. On success, you are redirected to the main dashboard at
   `/`.

If your credentials are rejected, see Troubleshooting (Section 11).

### 3.2 Logging out

Click the **Logout** control in the application header. This issues a POST
request to `/logout`, invalidates your server-side session, and returns you to
the login page.

For shared workstations, always log out at the end of your shift. Closing the
browser tab does not necessarily clear the session cookie immediately.

### 3.3 Session behaviour

- Session cookies are HttpOnly and use SameSite=Lax. They are not accessible
  to JavaScript and are not sent on cross-site navigations from third-party
  origins.
- When the application is served over HTTPS (recommended), session cookies
  carry the Secure flag and are never transmitted over plain HTTP.
- Sessions persist until you log out, the server is restarted, or the cookie
  expires per the server-side configuration. Treat your session as
  trust-equivalent to your Linux account.

---

## 4. Main Dashboard Walkthrough

The main dashboard is served at `/` and is the daily working surface for SOC
analysts.

### 4.1 Layout

The dashboard consists of:

- A header with the (optionally branded) logo and user controls.
- A toolbar with filters, pagination, and column customization controls.
- An alerts table that lists alerts retrieved from xDome.

### 4.2 Alerts table columns

The default columns are:

| Column                       | Meaning                                                              |
|------------------------------|----------------------------------------------------------------------|
| id                           | xDome's unique identifier for the alert.                             |
| alert_type_name              | Human-readable name of the alert type (e.g. policy violation).       |
| category                     | Category bucket the alert belongs to.                                |
| status                       | Current alert status (e.g. Unresolved, Resolved).                    |
| detected_time                | Timestamp at which xDome detected the underlying condition.          |
| devices_count                | Total number of devices implicated in the alert.                     |
| unresolved_devices_count     | Number of devices still in an unresolved state.                      |
| description                  | Free-text description of the alert.                                  |

You can add or remove columns from the available catalogue using the column
customization control in the toolbar. The catalogue is sourced from
`/api/meta/fields`. Your column selections are saved client-side so they
persist across page reloads on the same browser.

### 4.3 Unresolved-only filter

A toggle in the toolbar restricts the table to alerts whose status is
**Unresolved**. When enabled, the application calls `/api/alerts` with
`filter_status=Unresolved`. Disable the toggle to see all alerts regardless of
status.

### 4.4 Pagination

The alert list is paginated. The toolbar exposes page navigation and a page
size selector. Internally the application calls `/api/alerts` with `offset`
and `limit` parameters. Larger page sizes consume more browser memory and
make filtering and column changes feel slower; for routine triage, the
default page size is recommended.

### 4.5 Sorting and inspection

Click a column header to sort by that column, where supported. Click a row
to expand it into the device drill-down view (see Section 5).

---

## 5. Device Drill-Down

When you click a row in the alerts table, the row expands to reveal the
**devices affected** by that alert. The data is fetched from
`/api/alerts/<alert_id>/devices`.

### 5.1 Default device columns

| Column            | Meaning                                                  |
|-------------------|----------------------------------------------------------|
| asset_id          | xDome's unique identifier for the device/asset.          |
| device_name       | Human-readable device name.                              |
| device_type       | Device type (e.g. PLC, HMI, workstation).                |
| device_category   | Higher-level grouping (e.g. ICS, IT, IoT).               |
| ip_list           | Known IP addresses associated with the device.           |
| site_name         | Physical or logical site the device belongs to.          |
| risk_score        | xDome-assigned risk score.                               |
| is_resolved       | Whether the device has been marked resolved for this alert. |

As with the alerts table, you can customize the visible columns from the
field catalogue.

### 5.2 Typical workflow

1. Triage an alert in the main table.
2. Expand it to see which devices are involved.
3. For unresolved alerts, prioritize devices with higher `risk_score` and
   confirm whether they are in `is_resolved = false` state.
4. Click a device row to drill further into the OT activity events for that
   device under that alert (see Section 6).

---

## 6. OT Activity Events Drill-Down

Clicking a device row inside an expanded alert opens the **OT activity
events** view for that device-and-alert combination. Data is fetched from
`/api/alerts/<alert_id>/devices/<asset_id>/events`. The endpoint
`/api/ot_activity_events` is used when you query events filtered by
`asset_id` and/or `alert_id` independently.

### 6.1 Default event columns

| Column              | Meaning                                                       |
|---------------------|---------------------------------------------------------------|
| detection_time      | Timestamp at which xDome observed the event.                  |
| event_type          | Type of OT activity (e.g. write, scan, anomalous traffic).    |
| source_ip           | Source IP address of the event.                               |
| source_device_type  | Type of the source device.                                    |
| dest_ip             | Destination IP address.                                       |
| dest_device_type    | Type of the destination device.                               |
| protocol            | Industrial or network protocol involved (e.g. Modbus, OPC-UA).|
| description         | Human-readable description of the event.                      |

### 6.2 Investigation tips

- Use `detection_time` to align events with corroborating evidence in your
  SIEM and historian.
- Look at protocol distribution in `event_type` and `protocol` to assess
  whether the activity is expected for that asset's role.
- Source/destination device types help quickly distinguish IT-to-OT
  crossings from intra-zone activity.

---

## 7. Tree View

The Tree view, available at `/tree`, presents the same alert/device/event
data as a hierarchical structure rather than a paginated table. It is useful
for:

- Getting a structural overview of how many devices and events a single
  alert spans.
- Walking down from an alert into its devices and from each device into its
  events without losing context.

The Tree view shares the same authentication and role requirements as the
main dashboard - any authenticated user can use it.

---

## 8. Admin: Configuring the xDome API Connection

The **Settings** page is reachable at `/settings` and is restricted to users
with the `admin` role. Non-admins receive HTTP 403.

### 8.1 Fields

| Field      | Meaning                                                                                  |
|------------|------------------------------------------------------------------------------------------|
| Base URI   | The base URL of the xDome API server (e.g. `https://xdome.example.com`). Validated as a URL. |
| API Token  | A bearer token used to authenticate against the xDome API.                               |

### 8.2 Behaviour

- Settings are stored server-side at `instance/settings.json`. The file is
  written by the application process and should be readable only by that
  service account.
- The API token is **never reflected back to the browser**. When you reload
  the Settings page, the token field appears empty. Submitting the form
  with an empty token keeps the existing stored token; submitting a new
  value replaces it.
- The Base URI is validated as a URL before being saved. Trailing slashes,
  scheme, and host are normalized server-side.
- If the application is unable to reach xDome with the configured Base URI
  and token, the proxy API endpoints will return errors and the dashboard
  will show empty or error states. Verify connectivity from the application
  host before troubleshooting in the UI.

### 8.3 Operational guidance

- Treat the API token as a secret. Anyone with admin access to the
  application can change it, but only the server process can read it back.
- Rotate the token whenever an admin user with access leaves the team.
- Avoid pasting the token into chat or ticket systems. Use a secret
  manager when transferring it between humans.

---

## 9. Admin: Uploading a Logo

Admins may upload a custom logo at `/settings/logo`. The logo is served
publicly at `/logo` and replaces the default branding in the application
header.

### 9.1 Constraints

| Constraint     | Value                                                                |
|----------------|----------------------------------------------------------------------|
| Maximum size   | 2 MB                                                                 |
| Allowed types  | `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/svg+xml` |

### 9.2 Behaviour

- Uploading a new logo replaces the existing one. The previous file is
  deleted from disk to avoid orphaned assets.
- The `/logo` endpoint is public so that the logo is reachable from the
  login page (which is itself public).
- SVG uploads are accepted but should be reviewed before upload to ensure
  they do not contain embedded scripts or external references. The
  application's CSP mitigates active script execution in the browser, but
  it is still good practice to use sanitized SVGs.

### 9.3 Recommended assets

- A square or wide PNG/WebP at roughly 256-512 px on the long edge gives
  the best balance of quality and load time.
- Transparent backgrounds work well with both light and dark themes.

---

## 10. Theming

The application exposes a theme registry at `/api/themes`, which lists
built-in themes plus any custom theme overrides shipped with the
deployment. Themes affect colors and surface styling but not the layout or
field set.

For end users:

- Theme selection (where exposed) is remembered client-side per browser.
- If a custom theme has been deployed by your administrator, it will appear
  alongside the built-ins.

For administrators:

- Custom theme overrides are deployed alongside the application; they are
  not configured through the UI in the current version.

---

## 11. Troubleshooting

### 11.1 "Settings not configured" or empty dashboard

**Symptoms**: The alerts table is empty, or the UI shows a configuration
error.

**Likely cause**: The xDome Base URI or API token has not been set, or the
application cannot reach the xDome API.

**Resolution**:

1. As an admin, open `/settings` and confirm the Base URI is correct.
2. Re-enter the API token if you suspect it is wrong or has been rotated.
3. From the application host, verify network connectivity to the Base URI
   (DNS, routing, firewall, TLS trust).
4. Check the application's structured JSON logs for outbound request
   failures.

### 11.2 Login fails with valid credentials

**Symptoms**: The login form rejects credentials that work for SSH or
console login on the same host.

**Likely causes and resolutions**:

- The PAM service used by the application requires a different policy than
  SSH. Check with your administrator that the application's PAM
  configuration permits your account.
- Your account is locked, expired, or password-expired at the OS level.
  Resolve via the OS (`passwd`, `chage`, etc.) and try again.
- The application is behind a reverse proxy that strips or rewrites the
  POST body. Confirm the proxy is forwarding form submissions intact.

### 11.3 HTTP 403 on Settings pages

**Symptoms**: Logging in succeeds, but `/settings` or `/settings/logo`
returns 403 Forbidden.

**Cause**: Your account is not assigned the `admin` role.

**Resolution**: Ask an administrator to add your account to the role
mapping (typically `/etc/xdome-roles`) or to the appropriate system group.
Log out and back in for the role change to take effect.

### 11.4 CSRF or "session expired" errors on form submit

**Symptoms**: Submitting a settings form returns a CSRF validation error.

**Cause**: Your session expired between page load and submit, the form was
left open across a server restart, or cookies are blocked.

**Resolution**: Reload the page to obtain a fresh CSRF token, then resubmit.
Ensure your browser accepts first-party cookies from the application
domain.

### 11.5 Logo upload rejected

**Symptoms**: Upload returns an error.

**Causes**: File exceeds 2 MB, MIME type is not in the allow-list, or the
file is malformed.

**Resolution**: Compress or convert the asset to PNG/JPEG/WebP/GIF/SVG
within the 2 MB limit and try again.

### 11.6 UI looks broken or themed incorrectly

**Symptoms**: Layout is off, fonts missing, or interactive controls do not
work.

**Likely causes**:

- A browser extension is being blocked by the application's strict CSP.
  Disable extensions for this domain or use a clean browser profile.
- An outdated cached asset. Hard-reload the page (Ctrl+Shift+R) to clear
  cached static files.

---

## 12. Security Notes for End Users

- **Do not share credentials.** Each analyst should log in with their own
  Linux account so that audit trails attribute actions correctly.
- **Log out** at the end of your session, especially on shared
  workstations. Closing a tab does not always end the session.
- **Verify the URL and TLS padlock** before entering your password. The
  application should always be served over HTTPS in production.
- **Treat the API token as a secret.** Even though only admins can view
  the Settings page, the token grants full proxy access to xDome and
  should never be copied into chat, tickets, or screenshots.
- **Report anomalies.** If you see alerts disappearing, sessions ending
  unexpectedly, or unfamiliar logos/themes after a deploy, escalate to
  your security administrator. The application emits structured JSON logs
  that your SOC can correlate to investigate.
- **Use a supported browser** with current security patches. Avoid using
  the application from untrusted devices or networks.
- **Be careful with browser plugins** that capture screen content or
  keystrokes. The dashboard contains sensitive OT data and credentials
  pass through the login form.

---

*End of manual.*
