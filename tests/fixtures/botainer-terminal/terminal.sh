#!/bin/sh
# Fixed in-container protocol. User input is data, never shell syntax.
set -eu
stty -echo
token=$(cat /proc/sys/kernel/random/uuid)
start_ticks=$(awk '{print $22}' /proc/$$/stat)
identity() {
    printf 'IDENTITY token=%s pid=%s start=%s cwd=%s\n' "$token" "$$" "$start_ticks" "$PWD"
}
trap 'printf "STOPPED token=%s\n" "$token"; exit 0' TERM INT
printf 'READY\n'
identity
while IFS= read -r line; do
    case "$line" in
        identity) identity ;;
        size) printf 'SIZE '; stty size ;;
        'echo '*) printf 'ECHO %s\n' "${line#echo }" ;;
        quit) printf 'BYE\n'; exit 0 ;;
        *) printf 'UNKNOWN\n' ;;
    esac
done
printf 'EOF\n'
