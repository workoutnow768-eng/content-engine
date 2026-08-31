"""
Thin client for Buffer's public GraphQL API (api.buffer.com), used to
create scheduled posts directly from GitHub Actions with no browser.

Docs referenced: https://developers.buffer.com/examples/create-image-post.html

Multi-image (carousel) posts: `assets` is documented as an ordered list
where each entry is exactly one of image/video/document/link, which
supports N images by passing N `{"image": {"url": ...}}` entries --
same idea as attaching multiple photos in the Buffer web composer.
Confirmed against the live docs 2026-08-26 -- shape matches what's below.

IMPORTANT: the recipe (food) and workout (fitness) pipelines live on TWO
SEPARATE Buffer accounts (podcasterclips vs workoutnow768). FIX 2026-08-26:
this file used to read a single global BUFFER_ACCESS_TOKEN env var, which
meant BOTH pipelines shared one token -- confirmed via a live Actions run
log (run #5, 2026-08-25) that this makes the recipe pipeline's channel
lookup return the FITNESS account's channels (['30secfitness',
'30sec_fitness', 'Crunch time']) and fail 100% of the time with
"No Buffer channel found named 'ai_facts4u'". Every function here now
takes an explicit `token_env` (the name of the env var holding that
pipeline's own Buffer access token) rather than assuming a single global
token -- main.py passes the right one per pipeline. You need TWO repo
secrets now: BUFFER_ACCESS_TOKEN_RECIPE and BUFFER_ACCESS_TOKEN_WORKOUT
(see README).
"""
import os
import requests

API_URL = "https://api.buffer.com"


def _headers(token_env):
    token = os.environ[token_env]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _graphql(query, token_env, variables=None):
    resp = requests.post(API_URL, headers=_headers(token_env), json={"query": query, "variables": variables or {}}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"Buffer GraphQL error: {data['errors']}")
    return data["data"]


_ORGANIZATIONS_QUERY = """
query GetOrganizations {
  account {
    organizations {
      id
      name
    }
  }
}
"""

_CHANNELS_QUERY = """
query GetChannels($organizationId: OrganizationId!) {
  channels(input: { organizationId: $organizationId }) {
    id
    name
    displayName
    service
  }
}
"""

_org_id_cache = {}  # keyed by token_env, since one process could (in principle) touch both accounts


def get_organization_id(token_env):
    """
    Returns the first organization id on the Buffer account identified by
    token_env. Confirmed 2026-08-24 (after a live GraphQL error) that
    `channels` requires an organizationId -- fetched via this separate
    `account.organizations` query, per
    developers.buffer.com/examples/get-organizations.html.
    If the account has multiple organizations/workspaces, this picks the
    first one -- fine here since both bot accounts (workoutnow768,
    podcasterclips) are single-organization Buffer Free-plan accounts.
    """
    if token_env in _org_id_cache:
        return _org_id_cache[token_env]
    data = _graphql(_ORGANIZATIONS_QUERY, token_env)
    orgs = (data.get("account") or {}).get("organizations") or []
    if not orgs:
        raise RuntimeError(f"No organizations found on the Buffer account for {token_env}.")
    _org_id_cache[token_env] = orgs[0]["id"]
    return _org_id_cache[token_env]


def list_channels(token_env):
    """Returns [{"id": ..., "name": ..., "displayName": ..., "service": ...}, ...]
    for the account identified by token_env."""
    org_id = get_organization_id(token_env)
    data = _graphql(_CHANNELS_QUERY, token_env, {"organizationId": org_id})
    return data.get("channels", [])


_channel_cache = {}  # keyed by token_env -> {label: channel_id}


def get_channel(channel_name, token_env):
    """Looks up a channel's full record ({id, name, displayName, service}) by
    exact display name, case-sensitive match first, falling back to
    case-insensitive. Raises if not found or ambiguous.

    Matches against BOTH `name` (Buffer's handle/username field) and
    `displayName` (a custom label users can set -- names like "Crunch time"
    / "Factual days" look like custom labels rather than raw handles), since
    it wasn't clear which field holds the label shown in the Buffer web UI
    (what the channel names in PIPELINES are copied from)."""
    if token_env not in _channel_cache:
        by_label = {}
        for ch in list_channels(token_env):
            for label in (ch.get("name"), ch.get("displayName")):
                if label:
                    by_label[label] = ch
        _channel_cache[token_env] = by_label
    cache = _channel_cache[token_env]

    if channel_name in cache:
        return cache[channel_name]

    lowered = channel_name.strip().lower()
    matches = {name: ch for name, ch in cache.items() if name.strip().lower() == lowered}
    if len(matches) == 1:
        return next(iter(matches.values()))
    if not matches:
        raise RuntimeError(
            f"No Buffer channel found named '{channel_name}' on the account for {token_env}. "
            f"Available channels: {sorted(set(cache.keys()))}"
        )
    raise RuntimeError(f"Multiple channels matched '{channel_name}': {matches}")


def get_channel_id(channel_name, token_env):
    """Back-compat wrapper -- returns just the id."""
    return get_channel(channel_name, token_env)["id"]


# FIX 2026-08-26 (round 2): Instagram and Facebook both reject a post with
# "Invalid post: Instagram/Facebook posts require a type (post, story, or
# reel)." unless a channel-specific `metadata` block is sent -- confirmed via
# a live Actions run log (run #7) where every Instagram/Facebook channel
# failed with exactly that message while the TikTok channel in the same
# pipeline succeeded with no metadata at all. Per
# developers.buffer.com/types/InstagramPostMetadataInput.html and
# .../FacebookPostMetadataInput.html, both need a required `type` field
# (PostType / PostTypeFacebook enum -- "post" is the plain-feed-post-value
# for both), and Instagram additionally requires `shouldShareToFeed` (a
# non-nullable Boolean) whenever a feed post is being created.
_METADATA_BY_SERVICE = {
    "instagram": lambda: {"instagram": {"type": "post", "shouldShareToFeed": True}},
    "facebook": lambda: {"facebook": {"type": "post"}},
}


def _metadata_for_service(service):
    builder = _METADATA_BY_SERVICE.get((service or "").lower())
    return builder() if builder else None


_CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post { id text }
    }
    ... on MutationError {
      message
    }
  }
}
"""


def create_post(channel_name, text, image_urls, scheduled_at_iso8601, token_env):
    """
    Schedules one post to one channel with one or more images, using the
    Buffer account identified by token_env (see module docstring -- recipe
    and workout pipelines are on two different Buffer accounts, each with
    its own access token).
    scheduled_at_iso8601: e.g. "2026-08-26T19:00:00Z"

    Confirmed 2026-08-24 (after a live GraphQL error) that "custom"/
    "scheduledAt" are wrong -- the actual shape per
    developers.buffer.com/guides/posts-and-scheduling.html is
    schedulingType: automatic (always this, regardless of timing) with
    mode: customScheduled and a "dueAt" field (not scheduledAt) for the
    specific timestamp.
    """
    channel = get_channel(channel_name, token_env)
    assets = [{"image": {"url": url}} for url in image_urls]
    post_input = {
        "text": text,
        "channelId": channel["id"],
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": scheduled_at_iso8601,
        "assets": assets,
    }
    metadata = _metadata_for_service(channel.get("service"))
    if metadata:
        post_input["metadata"] = metadata
    variables = {"input": post_input}
    result = _graphql(_CREATE_POST_MUTATION, token_env, variables)
    payload = result.get("createPost", {})
    if "message" in payload:
        raise RuntimeError(f"Buffer rejected the post for '{channel_name}': {payload['message']}")
    return payload.get("post")
