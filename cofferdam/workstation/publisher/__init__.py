"""Publishing a finished worker branch. A host authority, never the worker's.

Three credentials exist in this system and they are deliberately three:

``Claude provider`` (:mod:`...worker.session`)
    Lets Cofferdam run a model. Lives in the worker's own config root, is bound
    into the Claude namespace, and is the only one that ever goes near a model.
``Git publisher`` (:mod:`.credential`)
    Lets Cofferdam push a branch and open a pull request. Lives here, and is
    **never** bound into any namespace a worker or project code can reach.
``Human approval`` (``planner.authority``)
    Lets a dispatch happen at all. Not a secret, and not delegable.

Collapsing any two of them would mean a model that can edit a repository could
also publish to it, or a publisher that could start work. The whole shape of this
package is that the publisher runs **after** the worker is finished and reads
only durable facts about what it did.

What the publisher may do
-------------------------

Push exactly one code-owned worker branch to exactly the remote its project
already points at, and open exactly one pull request for it. No merge, no
deploy, no force, no branch deletion, no tags, no settings.
"""

from __future__ import annotations

from . import credential, github, remote, service
from .credential import PublisherCredentialUnavailable, PublisherStatus
from .github import PublishRefused
from .remote import GitHubRepository, RemoteUnresolved
from .service import GitPublisher, PublicationView

__all__ = [
    "GitHubRepository",
    "GitPublisher",
    "PublicationView",
    "PublishRefused",
    "PublisherCredentialUnavailable",
    "PublisherStatus",
    "RemoteUnresolved",
    "credential",
    "github",
    "remote",
    "service",
]
