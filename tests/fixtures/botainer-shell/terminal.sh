#!/bin/sh
# Repository-authored local prototype entrypoint. No downloads or host commands.
printf '\033[36mBotainer local workspace\033[0m\n'
printf 'Interactive Alpine shell · network off · project at /workspace\n'
cd /workspace || exit 1
export PS1='workspace:\w \$ '
exec /bin/sh -i
