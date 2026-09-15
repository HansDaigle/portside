# Browser compatibility

Portside changes where a browser connects. It does not give localhost the original domain's identity.

## Options

- **Local HTTPS:** Caddy issues a local certificate. Portside disables automatic trust installation. After starting an HTTPS mapping, the root certificate is under `~/.local/state/portside/caddy/data/caddy/pki/authorities/local/root.crt`. Trust it in your browser/OS explicitly if needed; never share `root.key`.
- **Local hostname:** for example, `website.localhost` separates its cookies from another mapping using `admin.localhost`. Verify `.localhost` resolution on your platform; Portside does not edit `/etc/hosts`.
- **Translate cookies:** remove Domain only if it matches the exact upstream hostname, creating host-only local cookies. Preserve Secure, HttpOnly, SameSite, Path, and expiry. Parent-domain cookies need site-specific handling.
- **Translate origin:** replace Origin or Referer only when it matches this mapping's local origin. Preserve unrelated origins. This does not guarantee CSRF/CORS checks will succeed.
- **Redirects:** translate absolute or scheme-relative Location values for the same upstream authority. Keep relative and unrelated-domain redirects unchanged.

## Limits

- Cookies are shared across ports on the same hostname; use distinct local hostnames for concurrent logged-in sites.
- URLs embedded in HTML, CSS, or JavaScript can still contact the original site. Response bodies are not rewritten.
- OAuth/SSO callbacks may need server-side allowlisting; existing public-site sessions do not automatically transfer.
- Passkeys/WebAuthn and other origin-bound features may not work locally.
- Use local HTTPS for browser login testing with Secure cookies.
- WebSockets work at the transport layer; application origin checks still apply.
- Running indicates a local listener, not upstream availability or verified login compatibility.

## References

- [Caddy proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Local HTTPS](https://caddyserver.com/docs/automatic-https#local-https)
- [Browser origins](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)
- [Cookie isolation](https://www.rfc-editor.org/rfc/rfc6265.html#section-8.5)
