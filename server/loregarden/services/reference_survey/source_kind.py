"""What kind of source a URL is: a repository on a known code host, or a site.

Kept apart from `bulk` so anything that only needs to classify a URL (research,
the claim writer) does not import the whole enqueue path with it.
"""

from __future__ import annotations

from loregarden.models.domain.survey_enums import SurveySourceKind
from loregarden.services.reference_repo_service import ReferenceRepoError, parse_repo_url

#: Hosts whose URLs are repositories. Any other https URL is read as a site,
#: since `parse_repo_url` would accept a docs page's owner/name-shaped path too.
CODE_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"})


def infer_kind(url: str) -> SurveySourceKind:
    """A repository on a known code host, or reached over ssh; otherwise a site."""
    try:
        parsed = parse_repo_url(url)
    except ReferenceRepoError:
        return SurveySourceKind.SITE
    if parsed.host in CODE_HOSTS or url.strip().startswith("git@"):
        return SurveySourceKind.REPO
    return SurveySourceKind.SITE
