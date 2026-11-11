"""Small safety checks the desk adds on top of the skills. Each returns a
plain sentence when it refuses, or None. Nothing here places an order."""

# The only Order Executor command the desk may run. ticket.py has others
# (approve, result, flatten) that talk about a broker; the desk never calls them.
ALLOWED_COMMANDS = {"broker": ("show",)}


class NotAllowed(ValueError):
    pass


def check_command(tool, args):
    """Raises NotAllowed unless the first argument is on the tool's allowlist."""
    allowed = ALLOWED_COMMANDS.get(tool)
    if allowed is None:
        return
    first = str(args[0]) if args else ""
    if first not in allowed:
        raise NotAllowed("the desk may only run '%s %s', not %r" % (tool, " | ".join(allowed), first))


def stop_too_close(entry, risk_per_share, min_stop_pct):
    """A stop very close to the entry gets hit by ordinary noise, and it makes
    the share count balloon because size = allowed loss / stop distance."""
    if not min_stop_pct or not entry or entry <= 0:
        return None
    pct = 100.0 * risk_per_share / entry
    if pct + 1e-9 < min_stop_pct:
        return ("The stop is only %.2f%% under the entry (%.2f a share). Your floor is %.2f%%: "
                "a stop that close is hit by normal wiggle." % (pct, risk_per_share, min_stop_pct))
    return None
