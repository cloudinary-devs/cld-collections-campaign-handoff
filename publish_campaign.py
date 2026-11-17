"""Publish a campaign's approved assets as a protected share page.

Finds the assets approved for a campaign, gathers them into a collection,
publishes a public page for it, and restricts that page to named addresses.

Run it twice: it converges on one collection and one page rather than
creating a second set.

Environment (read from the shell, or from a .env file if python-dotenv
is installed):
    CLOUDINARY_URL  cloudinary://<api_key>:<api_secret>@<cloud_name>
    CAMPAIGN        the campaign tag to publish      (default: summer-campaign)
    SHARE_EMAILS    comma-separated addresses allowed to open the page
"""

import os
from datetime import datetime, timedelta, timezone

try:  # optional convenience: pick up a .env file if there is one
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import cloudinary
import cloudinary.api
import cloudinary.collections
import cloudinary.search
from cloudinary.exceptions import NotFound

cloudinary.config(secure=True)  # reads CLOUDINARY_URL from the environment

CAMPAIGN = os.environ.get("CAMPAIGN", "summer-campaign")
SHARE_EMAILS = [e.strip() for e in os.environ.get("SHARE_EMAILS", "").split(",") if e.strip()]

LINK_DAYS = 30
DOWNLOAD_TRANSFORMATION = "web_1200"
DOWNLOAD_TRANSFORMATION_DEF = "c_limit,w_1200,q_auto"


def _iso(dt):
    """Format a datetime the way the Collections API expects it."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def find_approved_assets(campaign):
    """Return the asset IDs tagged for this campaign and approved.

    Use `tags:` (token match), not `tags=` (exact match). Exact match is
    case-sensitive, so `tags="summer"` silently skips an asset tagged
    "Summer" -- and you cannot paper over that by adding the lowercase tag
    too, because Cloudinary dedupes tags case-insensitively and keeps
    whichever casing was there first.

    Paginate. `max_results` caps a single page of results, so a campaign
    with more approved assets than the cap would otherwise be truncated
    without any error.
    """
    asset_ids = []
    cursor = None

    while True:
        search = (
            cloudinary.search.Search()
            .expression(f'tags:"{campaign}" AND tags:"approved"')
            .max_results(500)
        )
        if cursor:
            search = search.next_cursor(cursor)

        result = search.execute()
        asset_ids += [asset["asset_id"] for asset in result["resources"]]

        cursor = result.get("next_cursor")
        if not cursor:
            return asset_ids


def ensure_download_transformation(name, definition):
    """Make sure `name` exists as a named transformation before a page offers it.

    `create_download_transformations=True` only promotes an existing named
    transformation into a download template. It cannot build one from a bare
    name -- a name that does not exist yet fails the page call with
    "400 Unknown named transformation".
    """
    try:
        cloudinary.api.transformation(name)
    except NotFound:
        print(f"Creating named transformation {name} ({definition})")
        cloudinary.api.create_transformation(name, definition)


def find_or_create_collection(campaign, name):
    """Return the campaign's collection, creating it only if it is not there yet."""
    for existing in cloudinary.collections.collections(query=campaign)["data"]:
        if existing["name"] == name:
            return existing

    return cloudinary.collections.create_collection(
        name,
        description=f"Approved assets for {campaign}.",
    )["data"]


def current_asset_ids(collection_id):
    """Return the IDs of every asset the collection holds right now.

    Paginate here too. `max_results` caps one page, so a collection bigger
    than the cap would look emptier than it is, and the reconcile in step 3
    would leave the assets on the later pages in place for good.
    """
    asset_ids = []
    options = {"max_results": 100, "sort_by": "created_at desc"}

    while True:
        page = cloudinary.collections.collection_assets(collection_id, **options)
        asset_ids += [asset["asset_id"] for asset in page["data"]]

        cursor = page.get("next_cursor")
        if not cursor:
            return asset_ids
        options["next_cursor"] = cursor


def find_or_create_page(collection_id, name, available_from, expires_at):
    """Return the collection's share page, and whether this call created it.

    The caller needs that flag: a page this run created has never been
    protected, so it has to be deleted if the protection step fails. A page
    an earlier run left behind was already protected, because this script
    never leaves an unprotected page in place.
    """
    for existing in cloudinary.collections.collection_public_pages(collection_id)["data"]:
        if existing["name"] == name:
            return existing, False

    created = cloudinary.collections.create_collection_public_page(
        collection_id,
        name=name,
        available_from=available_from,
        expires_at=expires_at,
        show_download_original_option=False,
        download_transformations=[{"name": DOWNLOAD_TRANSFORMATION, "types": ["image"]}],
        create_download_transformations=True,
    )["data"]
    return created, True


def discard_unprotected_page(collection_id, page_id, cause):
    """Delete a page that could not be protected, so it never becomes available.

    If the delete fails as well, the page is still scheduled to open with no
    protection and nothing in this script can stop it, so stop with the IDs
    needed to unpublish it by hand.
    """
    try:
        cloudinary.collections.delete_collection_public_page(collection_id, page_id)
    except Exception as cleanup_error:
        raise SystemExit(
            f"UNPROTECTED PAGE LEFT BEHIND: could not protect page {page_id} of "
            f"collection {collection_id} ({cause}), and deleting it failed too "
            f"({cleanup_error}). Unpublish it now, either from the Media Library "
            f"or with delete_collection_public_page("
            f"'{collection_id}', '{page_id}')."
        )

    print(f"Could not protect page {page_id} ({cause}); deleted it before it opened.")


def publish_campaign(campaign, allowed_emails):
    if not allowed_emails:
        raise SystemExit("Set SHARE_EMAILS to the addresses allowed to open the page")

    # 1. The page will offer this transformation for download, so it has to
    #    exist as a named transformation first.
    ensure_download_transformation(DOWNLOAD_TRANSFORMATION, DOWNLOAD_TRANSFORMATION_DEF)

    # 2. Find the approved assets for the campaign.
    #
    #    Bail out on an empty result rather than carrying on with it. Search
    #    lags writes to tags, so "nothing is approved" is more often a stale
    #    index than a real answer, and step 3 would read it as an instruction
    #    to empty the collection.
    asset_ids = find_approved_assets(campaign)
    if not asset_ids:
        raise SystemExit(f'No assets tagged "{campaign}" and "approved"')

    # 3. Reconcile the campaign's collection against that list, rather than
    #    only adding to it. On a rerun, an asset that has lost its "approved"
    #    tag has to leave the collection as well, or the page keeps showing it.
    #
    #    For the same index lag, don't run this straight after tagging assets
    #    for the campaign: an asset whose tag hasn't been indexed yet looks
    #    unapproved here and gets removed.
    name = f"{campaign} handoff"
    collection = find_or_create_collection(campaign, name)

    wanted = set(asset_ids)
    current = set(current_asset_ids(collection["id"]))
    if wanted - current:
        cloudinary.collections.add_collection_assets(
            collection["id"], list(wanted - current)
        )
    if current - wanted:
        cloudinary.collections.remove_collection_assets(
            collection["id"], list(current - wanted)
        )

    # 4. Publish a page for it.
    #
    #    `protection_mode` is accepted only by the update call, never by the
    #    create call, so a page always exists unprotected for as long as it
    #    takes to get to step 5. Opening it in the near future instead of
    #    immediately keeps the URL dark until the restriction is in place.
    now = datetime.now(timezone.utc)
    expires_at = _iso(now + timedelta(days=LINK_DAYS))
    page, page_is_new = find_or_create_page(
        collection["id"],
        name,
        available_from=_iso(now + timedelta(minutes=5)),
        expires_at=expires_at,
    )

    # 5. Restrict it, and open it now that the restriction is on.
    #
    #    `allowed_emails` and `allowed_domains` are additive, and setting one
    #    does not clear the other, so state both: otherwise a domain left over
    #    from an earlier run keeps letting that whole domain in.
    #
    #    Fail closed. A page created in step 4 and never protected here would
    #    still open to anyone holding the URL once its `available_from` passes,
    #    so delete it rather than leave it scheduled.
    try:
        page = cloudinary.collections.update_collection_public_page(
            collection["id"],
            page["id"],
            protection_mode="email_otp",
            allowed_emails=allowed_emails,
            allowed_domains=[],
            available_from=_iso(now),
            expires_at=expires_at,
        )["data"]
    except Exception as error:
        if page_is_new:
            discard_unprotected_page(collection["id"], page["id"], error)
        raise

    return collection, page, asset_ids


def main():
    collection, page, asset_ids = publish_campaign(CAMPAIGN, SHARE_EMAILS)

    print(f"{len(asset_ids)} assets in \"{collection['name']}\"")
    print(f"Share this link with {', '.join(SHARE_EMAILS)}: {page['url']}")

    # Worth knowing before you hand the link over: once a page is past
    # `expires_at`, opening it reports "collection not found" rather than
    # anything about expiry. Re-run this script to issue a fresh window.
    print(f"The link stops working on {page['expires_at']}.")

if __name__ == "__main__":
    main()
