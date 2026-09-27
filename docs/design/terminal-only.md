# Planned terminal-only operations

General terminal-only launch is planned. The presence of a terminal widget or a
prepared shell fixture does not mean every configured project supports it.
Current supported routes and limits are in the
[support matrix](../setup-support-matrix.md).

Different operations need different names and permissions:

| Operation | Required distinction |
| --- | --- |
| New container shell | Starts a new container using a reviewed project environment |
| Shell in a running container | Starts an additional process in an exact existing container |
| New compute shell | Requests scheduler resources and starts in the resulting allocation |
| Shell in an existing allocation | Uses verified existing resources; may share them with other work |
| Host or login-node shell | Runs without container isolation and requires prominent warnings |

A shell can receive mounts, credentials and helper services even when it starts
no AI agent. The selected Botainer version must define and display its native
disclosures, consent and cleanup. The dashboard must identify what is actually
running, separately from the agent environment used to configure it.

Before adding a route, qualify native prompts, concurrent sessions, input,
resize, viewer loss, reconnect, natural exit and explicit Stop. Reconnection
must retain the original owner. Stopping one process must not silently cancel a
shared allocation. A failed container operation must never fall back to a host
shell, and an uncertain launch must not be automatically retried.

Implementation should start with a fixed new-container-shell operation, then
qualify the other routes independently. This design does not authorize new
dependencies, credential access or runtime launches.
