# Systems, Environments and Login

`workspace/Data/systemData.json` defines every system the framework can test:

```json
{
  "systems": {
    "GOOGLE": {
      "dev": { "base_url": "https://www.google.com", "logged_in_check": "textarea[name='q']" }
    },
    "MYAPP": {
      "dev": {
        "base_url": "https://myapp.example.com",
        "logged_in_check": "h1:has-text('Dashboard')",
        "loginType": "urlEmbed",
        "username_env": "MYAPP_DEV_USERNAME",
        "password_env": "MYAPP_DEV_PASSWORD",
        "login_pre": { "type": "steps", "path": "../Tests/MYAPP/auth/login.json" }
      }
    }
  }
}
```

| Key | Required | Meaning |
|---|---|---|
| `base_url` | yes | Opened first; page `path` values are appended to it. |
| `logged_in_check` | yes | Selector that is visible only when logged in. If visible after opening `base_url`, login is skipped (public sites need nothing else). |
| `login_pre` | no | Login steps file (same step format as a test case), relative to the workspace. |
| `username_env` / `password_env` | no | Names of the `.env` variables holding the credentials. |
| `loginType: "urlEmbed"` | no | Send the credentials as HTTP authentication. |

## Adding a system

1. Add the system and environment to `systemData.json`.
2. Add credentials to `.env` under the names you chose (never in JSON).
3. Create pages in **Generator → Pages** and tests in **Generator → Test cases**. The folders `Pages/<SYSTEM>` and `Tests/<SYSTEM>/MasterRegressionSuite` are created on first save.
4. The system appears in the Control tab after a page refresh.

## Environment selection

The Control tab's **Environment** applies to test cases without their own `"env"`. A test case with `"env": "dev"` always uses `dev`.
