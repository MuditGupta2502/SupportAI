"""Provision local support-admin accounts; there is deliberately no admin sign-up route."""

import getpass

from supportai.auth import AuthManager, AuthenticationError


def main() -> None:
    print("Create a SupportAI admin account. Password input will not be shown.")
    email = input("Admin email: ").strip()
    name = input("Admin name: ").strip()
    password = getpass.getpass("Admin password (12 characters minimum): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match; no account was created.")
    try:
        AuthManager().create_local_admin(email, name, password)
    except AuthenticationError as error:
        raise SystemExit(str(error)) from error
    print(f"Admin account created for {email.casefold()}.")


if __name__ == "__main__":
    main()
