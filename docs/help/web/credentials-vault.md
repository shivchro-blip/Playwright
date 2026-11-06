# Logins from a secret store

By default the usernames and passwords a system needs (the `username_env` and `password_env` names in `systemData.json`) come from the environment or the workspace `.env`. If you would rather not keep passwords in a file, the app can fetch the ones that are missing from a secret store when a run starts.

For each credential that is not in the environment, not in `.env` and not typed under *Login credentials*, the app asks the store for a secret with that name. The value is kept in memory for that one run, is hidden from the console like a typed value, and is never saved. Values typed in the app win over the store, and the store wins over nothing: what is already in `.env` is never replaced.

## Set it up

Add the settings to the workspace `.env` (they are read when a run starts and never shown in the app).

**Azure Key Vault** (needs the Azure CLI, signed in with `az login`):

```
SECRET_PROVIDER=azure-keyvault
SECRET_AZURE_VAULT=my-vault
SECRET_NAME_PREFIX=             # optional, put in front of the secret name
```

The secret for `PORTAL_DEV_PASSWORD` is read as `PORTAL-DEV-PASSWORD`, because Key Vault names cannot contain underscores.

**Any other store** (Windows Credential Manager through PowerShell SecretManagement, the 1Password or Bitwarden CLI, HashiCorp Vault, a script of your own): give the command as a JSON list. `{name}` is replaced by the credential's name, and whatever the command prints is the secret.

```
SECRET_PROVIDER=command
SECRET_COMMAND=["pwsh","-NoProfile","-Command","Get-Secret -Name '{name}' -AsPlainText"]
```

The command is started directly (no shell), may run for 20 seconds, and must exit with 0 and print the value. Only plain names (letters, digits, underscore) are ever passed to it.

## Where it applies

- **Runs from the Control tab**, from the Generator (*Run this test case*), from schedules and from the REST API.
- **Remote runners** use their own store: set the same `SECRET_*` settings in the runner machine's `.env` (or environment) and the agent fetches what that machine lacks, so passwords never travel from the app to the runner. A run sent to a runner does not use the app's store.
- The Control tab says when a store is set up and warns when its settings are wrong. If a value cannot be fetched, a warning names the credential and the reason (for example *the store said: secret not found*) and the run goes on, so the login step reports the problem.
