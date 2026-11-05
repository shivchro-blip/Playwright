"""Stands in for a secret store command: prints the secret for the name it is asked about."""
import sys

SECRETS = {"VAULT_USER": "vault-user", "VAULT_PASS": "s3cr3t-from-vault"}
name = sys.argv[1]
if name in SECRETS:
    print(SECRETS[name])
else:
    sys.stderr.write(f"secret {name} not found in the store\n")
    sys.exit(3)
