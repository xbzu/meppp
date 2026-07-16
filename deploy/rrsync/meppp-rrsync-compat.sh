#!/bin/sh
set -eu

# Debian 11's rrsync allow-list predates the harmless long-form --dirs
# option emitted by current macOS rsync. Map only that exact token to the
# equivalent allowed short option, then leave every security check to the
# distribution-provided rrsync implementation.
original_command="${SSH_ORIGINAL_COMMAND-}"
case " ${original_command} " in
    *" --dirs "*)
        prefix=${original_command%% --dirs *}
        suffix=${original_command#* --dirs }
        SSH_ORIGINAL_COMMAND="${prefix} -d ${suffix}"
        export SSH_ORIGINAL_COMMAND
        ;;
esac

exec /usr/bin/rrsync "$@"
