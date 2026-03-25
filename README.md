# mattermost-mcp

MCP server for Mattermost REST API v4.

## Setup

### 1. Get a Personal Access Token

System Console → Integrations → Enable Personal Access Tokens must be on.

```bash
# Login (session token is in the "Token" response header)
curl -i -X POST https://mm.example.com/api/v4/users/login \
  -d '{"login_id":"your_username","password":"your_password"}'

# Get your user ID
curl -H 'Authorization: Bearer <session_token>' \
  https://mm.example.com/api/v4/users/me
# → {"id":"abc123...","username":"your_username",...}

# Create PAT (save the token — it's shown only once!)
curl -X POST -H 'Authorization: Bearer <session_token>' \
  https://mm.example.com/api/v4/users/<user_id>/tokens \
  -d '{"description":"MCP server token"}'
# → {"id":"...","token":"YOUR_PAT_HERE",...}
```

### 2. Get team ID

```bash
curl -H 'Authorization: Bearer <token>' \
  https://mm.example.com/api/v4/users/me/teams
# Returns id, name, display_name for each team
# Use the "id" field value as MATTERMOST_TEAM_ID
```

### 3. Add to Claude Code

```bash
claude mcp add mattermost --scope project \
  -e MATTERMOST_URL=https://mm.example.com \
  -e MATTERMOST_TOKEN=your_pat_token \
  -e MATTERMOST_TEAM_ID=your-team-id \
  -- uvx --from git+https://github.com/mainpart/mattermost-mcp mattermost-mcp
```

All tools: add `-e MATTERMOST_TOOLS=all`

Selective: `-e MATTERMOST_TOOLS=get_me,get_posts,create_post`

### 4. Verify

```bash
claude mcp list
# mattermost: ... - ✓ Connected
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MATTERMOST_URL` | yes | Instance URL (e.g. `https://mm.example.com`) |
| `MATTERMOST_TOKEN` | yes | Personal Access Token |
| `MATTERMOST_TEAM_ID` | no | Default team ID for tools that need team_id |
| `MATTERMOST_TOOLS` | no | Comma-separated tool list, `all`, or empty for defaults |
| `MATTERMOST_LOG_LEVEL` | no | Logging level (default: `WARNING`) |

## Tools

**Enabled by default (20):** `get_me`, `get_user`, `search_users`, `get_my_teams`, `get_channels`, `get_my_channels`, `get_channel_by_name`, `search_channels`, `create_channel`, `create_direct_channel`, `get_posts`, `get_post_thread`, `search_posts`, `get_unread`, `get_pinned_posts`, `create_post`, `add_reaction`, `get_file_info`, `get_file`, `upload_file`

**Disabled by default (8):** `get_user_status`, `set_user_status`, `get_reactions`, `remove_reaction`, `update_post`, `delete_post`, `pin_post`, `unpin_post`
