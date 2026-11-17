# Collections API: campaign handoff

A Python sample app for the Cloudinary [Collections API](https://cloudinary.com/documentation/collections_api). It runs a campaign handoff end to end: it finds the assets approved for a campaign, reconciles them into a collection, publishes a public page that expires in 30 days and offers a web-sized download, restricts that page to named email addresses, and prints the link to send.

For the walkthrough that explains each step, see the [Collections API guide](https://cloudinary.com/documentation/collections_api_guide).

## What it does

1. **Makes sure the download transformation exists.** The page offers a web-sized download, and `create_download_transformations` can only promote a named transformation that already exists, so the script creates it first if it has to.
2. **Finds the approved assets.** Searches for assets tagged with the campaign and with `approved`, following the search cursor so a campaign larger than one page isn't truncated.
3. **Reconciles the collection.** Adds newly approved assets and removes assets that have lost their approval, so a rerun never leaves a rejected asset reachable through the page.
4. **Publishes and protects the page.** Creates the page with `available_from` a few minutes ahead, then sets `protection_mode` to `email_otp` with the allowlist and opens it.

## Running it twice

The script converges rather than duplicating: it reuses the campaign's collection and page instead of creating a second set, and the reconcile in step 3 means the collection reflects what is approved *now*, not the union of every run.

## Safety behavior

Two things in this script are deliberate, and worth keeping if you adapt it:

- **Publication fails closed.** `protection_mode` is accepted only by the update call, never by the create call, so a page that has to be protected is always created unprotected first. If the protect step fails, the script deletes the page it just created rather than leave it scheduled to open to anyone holding the URL. If that delete also fails, it exits with the collection ID and page ID so you can unpublish it by hand.
- **An empty search result stops the run.** Search is a separate index that lags writes to tags, so "nothing is approved" is more often a stale index than a real answer. Because step 3 removes whatever the search doesn't return, acting on an empty result would empty the collection. Don't run this straight after tagging assets for the campaign.

## Setup

1. Clone the repo and install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and set `CLOUDINARY_URL` to the environment variable shown on the [API Keys](https://console.cloudinary.com/app/settings/api-keys) page of your Console Settings. Set `SHARE_EMAILS` to the addresses allowed to open the page. (The script reads plain environment variables too, so the `.env` file is a convenience, not a requirement.)

3. Tag a few assets in your product environment with both `summer-campaign` and `approved`, or set `CAMPAIGN` to a tag you already use.

4. Run it:

   ```bash
   python publish_campaign.py
   ```

## Permissions

Collections enforce [collection roles and permissions](https://cloudinary.com/documentation/dam_admin_system_roles_permissions#collection_roles). The API key you authenticate with needs the **Create collections** permission to create a collection, and a collection role such as **Manager** or **Distributor** to change or publish an existing one. A collection role can only be given to an API key through the [Permissions API](https://cloudinary.com/documentation/permissions_assign_roles_api), not in the Console.

## Plan availability

Collections are a premium **Assets Enterprise** feature. On the **Assets Free** plan you can create up to 3 collections with up to 50 assets each. Email verification for public pages is a premium feature that may need to be enabled for your account.
