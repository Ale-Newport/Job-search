# Stable local identity and Keychain access

Meridian stores persistent credentials in macOS Keychain. It does not change Keychain ACLs to allow every application, store credentials in plaintext, or turn off macOS password protection.

Release builds now use `scripts/build_signed_app.py`, invoked by `npm run build` and `npm run build:app`. The script selects the single installed Apple Development identity on first use, or an explicitly supplied `MERIDIAN_SIGNING_IDENTITY`. The choice is retained in the ignored local build directory. It fails instead of silently falling back to ad-hoc signing. Private keys remain in Keychain.

The sidecar's designated requirement was compared across two different signed binaries and remained identical. Stable signing lets Keychain recognize updates as the same application. The previous ad-hoc builds depended on each executable's changing identity.

Credentials successfully read from Keychain are also cached in memory for the running service. Writes update the cache only after successful persistence; deletion invalidates it. No credential cache is written to disk.

macOS owns access prompts. If an existing item requests authorization after the signing transition, the user can select **Always Allow / Permitir siempre** for Meridian. This is per Keychain item; it is not a universal permanent grant. A locked/reset Keychain, revoked access, certificate changes or macOS policy may require authorization again. Meridian cannot and does not enter the user's macOS password.

Apple documentation: [Allow apps to access your keychain](https://support.apple.com/en-mt/guide/mac-help/kychn002/mac), [Understanding the Code Signature](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/AboutCS/AboutCS.html).
