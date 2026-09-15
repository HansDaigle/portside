#!/bin/zsh
# Double-click in Finder. Keep this file beside pyproject.toml.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
cd -- "${0:A:h}" || exit 1

for dependency in uv caddy; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    print "Portside needs $dependency. Run this once, then double-click again:"
    print "  brew install uv caddy"
    read -r "reply?Press Return to close. "
    exit 1
  fi
done

print "Starting Portside… Keep this Terminal window open while using the app."
uv run --locked portside ui --open
result=$?
if (( result != 0 )); then
  print "Portside could not start. See the message above."
  read -r "reply?Press Return to close. "
fi
exit "$result"
