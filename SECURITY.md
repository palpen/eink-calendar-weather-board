# Security and private data

## Data flow

The publisher fetches configured calendar feeds over HTTPS with certificate verification. Redirects are disabled so a credential-bearing URL cannot be redirected to another service. Weather requests send configured coordinates and timezone to Open-Meteo. The Pi receives a rendered PNG over SSH, including the event titles and locations visible on the display, but no calendar feed credentials.

`config.json`, `.secrets/`, `.state/`, `.venv/`, logs, rendered images, and calendar caches are private local deployment data. They must not be added to Git, public issues, screenshots, release attachments, or support logs. Calendar subscription URLs are read-only bearer credentials: anyone possessing a link may be able to read the calendar. Rotate the link if it leaks. Keep private files owner-readable only and private directories owner-accessible only (`600` and `700`). The physical display also shows private event information; place it accordingly.

## Connection restrictions

Each display needs its own dedicated SSH key and independently verified pinned host key. The documented `restrict,command=...` authorized-key entry confines the display key to the receiver and disables terminal access and forwarding. The publisher ignores ambient SSH configuration and existing multiplexed sessions, and disables agent/X11 forwarding and local commands. Do not substitute an administrator key or weaken host-key checking.

The receiver reads at most 1 MiB with a 30-second upload deadline and accepts only indexed seven-color, 600 × 448 PNGs. It ignores client-supplied commands, shares a refresh lock, and enforces the panel cooldown. Possession of a display key still permits submitting replacement images and attempting denial of service; protect and revoke it like a credential. The receiver uses the existing SSH service and opens no additional network listener. Keep SSH on a trusted network or private VPN rather than forwarding it from the public internet.

The optional calendar setup form binds only to `127.0.0.1`, checks its random session route, Host, and Origin, limits request sizes and read time, and uses SSH to save the credential. It exits after saving. Its destination must be an absolute file path inside `.secrets/` without dot segments. Use a trusted administrator SSH alias for setup and stop the form if abandoning a session.

Calendar parsing has a 30-second deadline on the Unix main thread and accepts at most 4096 input/expanded events per feed and 4096 combined events. Titles and locations are bounded to 2048 characters, and rendering truncates text efficiently. Recurrence expansion occurs inside the calendar library before the expanded count is checked; these limits do not provide a hard memory sandbox for malicious feeds. Configure feeds you trust and keep calendar invitations under your control.

## Dependencies and updates

Use `requirements.lock.txt` with `pip --require-hashes`; the lock records exact publisher versions and hashes from PyPI. Use pip 26.2.1 or newer. CI runs synthetic behavior/security tests and a known-vulnerability audit without deployment credentials. Dependabot reviews publisher and GitHub Action updates. Review and test updates before installing them.

The Pi uses separately installed Python, Pillow, Inky, OS libraries, and an SSH server. Publisher dependency checks do not audit or upgrade those components, firewall rules, router forwarding, or SSH server settings. Keep them supported and patched. A source review and known-vulnerability scan cannot establish that an entire network has no exploitable weaknesses.

## Reporting a vulnerability

Use this repository's private GitHub vulnerability reporting feature. Do not post calendar links, keys, real event data, private addresses, or deployment logs in a public issue. Include a minimal reproduction using synthetic data.
