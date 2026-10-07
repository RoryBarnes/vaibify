# Tab completion for vaibify and its push/pull helpers, in fish 3 or newer.
#
# vaibify's first-run setup adds a line to ~/.config/fish/config.fish that
# sources this file. To add it yourself:
#   test -f "/path/to/vaibify/completions/vaibify.fish"; and source "/path/to/vaibify/completions/vaibify.fish"
#
# This file knows POSITIONS only: which word is a subcommand, which
# argument of push/pull is a container path and which is a file on this
# machine. Everything else -- which project, where its workspace is, what
# is in it -- is answered by `vaibify complete-path`, so nothing here can
# drift from how the commands themselves resolve a path.
#
# The container paths are printed as data, one per line; fish escapes
# whatever the shell needs when it inserts a candidate, so a file named
# `a b; touch x` arrives on the command line as ONE word and runs nothing.

set -g __vaibify_subcommands build cat config connect destroy do doctor generate-standards gui init ls open pull push reconcile register remote remote-helper repair reproduce revoke run secret sessions setup start status stop test verify verify-step workflow

# Print, one per line: the direction ("push" or "pull"), how many paths
# have been typed before the cursor, and the project named with -p.
# Prints nothing when the cursor is not on an argument of push or pull.
function __vaibify_scan_transfer_arguments
    set -l tokens (commandline -opc)
    set -l first_argument 2
    set -l direction ""
    switch (basename -- $tokens[1])
        case vaibify_push vaib_push
            set direction push
        case vaibify_pull vaib_pull
            set direction pull
        case vaibify vaib
            set first_argument 3
            set direction $tokens[2]
    end
    if not contains -- "$direction" push pull
        return 1
    end
    set -l typed_count 0
    set -l project ""
    set -l skip_next 0
    set -l typed_arguments
    if test (count $tokens) -ge $first_argument
        set typed_arguments $tokens[$first_argument..-1]
    end
    for token in $typed_arguments
        if test $skip_next -eq 1
            set project $token
            set skip_next 0
            continue
        end
        switch $token
            case -p --project
                set skip_next 1
            case '--project=*'
                set project (string replace -- '--project=' '' $token)
            case '-p*'
                set project (string replace -- '-p' '' $token)
            case '-*'
            case '*'
                set typed_count (math $typed_count + 1)
        end
    end
    printf '%s\n' $direction $typed_count $project
end

function __vaibify_needs_subcommand
    test (count (commandline -opc)) -eq 1
end

function __vaibify_in_transfer
    __vaibify_scan_transfer_arguments > /dev/null
end

function __vaibify_wants_container_path
    set -l scan (__vaibify_scan_transfer_arguments)
    or return 1
    string match -qr -- '^-' (commandline -ct)
    and return 1
    if test "$scan[1]" = pull; and test "$scan[2]" -eq 0
        return 0
    end
    test "$scan[1]" = push; and test "$scan[2]" -ge 1
end

function __vaibify_container_paths
    set -l scan (__vaibify_scan_transfer_arguments)
    set -l helper_arguments complete-path --side container
    if test -n "$scan[3]"
        set -a helper_arguments --project=$scan[3]
    end
    vaibify $helper_arguments -- (commandline -ct | string unescape) 2>/dev/null
end

complete -c vaibify -n __vaibify_needs_subcommand -f -a "$__vaibify_subcommands"
complete -c vaibify -n __vaibify_needs_subcommand -l help -l version
complete -c vaibify -n __vaibify_wants_container_path -f -a '(__vaibify_container_paths)'
complete -c vaibify -n __vaibify_in_transfer -s p -l project -x
complete -c vaibify -n __vaibify_in_transfer -s h -l help

complete -c vaib -w vaibify
complete -c vaibify_push -w 'vaibify push'
complete -c vaib_push -w 'vaibify push'
complete -c vaibify_pull -w 'vaibify pull'
complete -c vaib_pull -w 'vaibify pull'
