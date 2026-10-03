<center align="center" style="text-align: center;justify-content:center;">
<div align="center" style="text-align: center;justify-content:center;">
<h1 align="center" style="text-align: center;justify-content:center;">

Gmail second account MCP server

<img style="justify-content:center;text-align: center;width: 95px; height: auto;" width="793" height="411" alt="image" src="https://github.com/user-attachments/assets/abed1a04-d69b-4ab4-a490-d606064df72d" />
<img style="justify-content:center;text-align: center;width: 190px; height: auto;" alt="image" src="assets/gmail-logo-white.png" />

</h1>

![Version](https://img.shields.io/badge/version-1.0.1-EA4335.svg?style=for-the-badge) ![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white) ![Node](https://img.shields.io/badge/Node.js-339933?style=for-the-badge&logo=node.js&logoColor=white) ![OAuth](https://img.shields.io/badge/OAuth_2.1-EB5424?style=for-the-badge&logo=auth0&logoColor=white)

</div>
</center>

<hr>

Read and write **one** Gmail mailbox from Claude.ai and Claude Code. Claude.ai's own Gmail connector handles a single account, so this exists for the second one: run an instance per mailbox and each connector reaches exactly the address it was given.

<hr>

> [!WARNING]
> Using this server with a paid AI service costs money. Tool definitions and results are billed as input tokens, and an agent can call tools repeatedly on its own. You are responsible for every charge, so set spending limits with your provider. The author accepts no liability for any costs. See [DISCLAIMER.md](DISCLAIMER.md).

## One mailbox per instance

Which account an instance serves comes from `GMAIL_CREDENTIALS_DIR`, and nothing else is reachable from that process. That separation is the point rather than a limitation: a draft cannot land in the wrong mailbox when the server only ever holds one set of credentials.

## Tools

### Reading

| Tool | What you get |
| --- | --- |
| `gmail_profile` | Which mailbox this instance serves, and its counts |
| `gmail_search` | Search with Gmail's own query syntax |
| `gmail_get_message` | One message, with its body |
| `gmail_get_thread` | A whole conversation, oldest first |
| `gmail_list_attachments` | Filenames, types and sizes on a message |
| `gmail_list_labels` | Every label with its id |
| `gmail_list_drafts` / `gmail_get_draft` | Drafts waiting in the mailbox |

### Writing

| Tool | What it does |
| --- | --- |
| `gmail_create_draft` | Write a draft, optionally threaded as a reply |
| `gmail_update_draft` | Replace a draft's contents |
| `gmail_delete_draft` | Discard a draft |
| `gmail_modify_labels` | Add or remove labels on a message |
| `gmail_archive` | Take a message out of the inbox |
| `gmail_mark_read` | Mark read or unread |
| `gmail_trash` / `gmail_untrash` | Move to trash and back |
| `gmail_create_label` / `gmail_delete_label` | Manage labels |
| `gmail_send_draft` | Send an existing draft |
| `gmail_send_message` | Compose and send in one step |

The two send tools are marked destructive and say so in their descriptions: they reach other people and cannot be undone. The safe shape is `gmail_create_draft`, read it, then `gmail_send_draft` once a person has approved the text.

### Searching

`gmail_search` takes the same operators as the Gmail search box:

```
is:unread from:client.com
subject:invoice has:attachment newer_than:7d
label:important -in:trash
```

It returns headers and a snippet per message rather than bodies, so a wide search stays small. Follow up with `gmail_get_message` or `gmail_get_thread`.

## How it fits together

```
Claude.ai / Claude Code
        |  HTTPS
   Cloudflare Tunnel, or any proxy that gives you HTTPS
        |
   nginx  127.0.0.1:8461
        |
   auth-server.js  :8462    handles the login and the tokens
        |
   gmail-mcp  :8460         the server itself, local only
        |
   gmail.googleapis.com
```

The MCP has no login of its own and refuses to listen on anything but the local machine, so everything reaching it has already passed the login.

## Setup

You need a Google Cloud OAuth client with the Gmail API enabled, and a refresh token for the mailbox. The credentials directory holds two files:

```
credentials.json        {"refresh_token": "..."}
gcp-oauth.keys.json     the downloaded OAuth client
```

`gmail.modify` is the scope to grant. It covers reading, labelling, drafting and sending; drop to `gmail.readonly` plus `gmail.compose` if you would rather the credentials themselves could not send.

```bash
git clone https://github.com/rollecode/gmail-second-account-mcp.git
cd gmail-second-account-mcp
uv venv && uv pip install -e .
npm install --omit=dev

CONFIG_DIR=~/.config/gmail-mcp node set-password.js 'your-password-here'
openssl rand -hex 32 > ~/.config/gmail-mcp/token
chmod 600 ~/.config/gmail-mcp/token
```

Fill in `YOUR_USER`, the hostname and the credentials directory in `systemd/*.service` and `nginx/gmail-mcp.conf`, then:

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gmail-mcp gmail-mcp-auth

sudo cp nginx/gmail-mcp.conf /etc/nginx/sites-enabled/gmail-mcp
sudo nginx -t && sudo systemctl reload nginx
```

Point a tunnel or an HTTPS proxy at `127.0.0.1:8461`. OAuth needs HTTPS.

Check from outside: discovery returns metadata, and `/mcp` without a token must return `401`.

```bash
curl https://your-host/.well-known/oauth-authorization-server
curl -o /dev/null -w '%{http_code}\n' -X POST https://your-host/mcp
```

## Connecting

Claude.ai: Settings, Connectors, Add custom connector, `https://your-host/mcp`, client ID and secret blank.

Claude Code:

```bash
claude mcp add --transport http gmail https://your-host/mcp \
  --header "Authorization: Bearer $(cat ~/.config/gmail-mcp/token)" --scope user
```

Locally over stdio, with no server at all:

```bash
GMAIL_CREDENTIALS_DIR=/path/to/creds claude mcp add gmail -- /path/to/.venv/bin/gmail-mcp
```

## Settings

| Variable | What it is for |
| --- | --- |
| `GMAIL_CREDENTIALS_DIR` | Directory holding `credentials.json` and `gcp-oauth.keys.json` |
| `GMAIL_ACCOUNT_LABEL` | Name for the mailbox, shown in the server's instructions |
| `MCP_PUBLIC_URL` | Public address, used to advertise the icon |
| `ISSUER` | Public origin of the login server |
| `PORT` | Login server port, 8462 by default |
| `UPSTREAM` | MCP server URL, `http://127.0.0.1:8460` by default |
| `CONFIG_DIR` | Where the password, token and OAuth database live |

Access tokens are refreshed in memory as they expire; only the refresh token is read from disk.

## Credits

The login layer comes from [rollecode/obsidian-remote-mcp](https://github.com/rollecode/obsidian-remote-mcp).
