"""
Aldi scraper configuration — constants that may need updating.

The GraphQL hashes are the most fragile values here.  When Aldi
updates their frontend, these hashes change and requests start
returning PersistedQueryNotFound errors.  See session.py comments
for how to grab the new hashes from DevTools.
"""

# Item ID prefix — appears to be constant for Aldi US region.
# If scraping breaks, check DevTools for the current prefix.
ITEM_PREFIX = "items_23277"

# Default store config (Orlando area)
DEFAULT_SHOP_ID = "518104"
DEFAULT_ZONE_ID = "178"
DEFAULT_ZIP = "32825"

# GraphQL endpoint
GRAPHQL_URL = "https://www.aldi.us/graphql"

# Persisted query hashes — Apollo server-side cached queries.
# The client sends a hash instead of the full query text.
# If these become invalid you'll get PersistedQueryNotFound errors;
# grab fresh hashes from DevTools (Network tab > graphql requests > Payload).
ITEMS_QUERY_HASH = "5116339819ff07f207fd38f949a8a7f58e52cc62223b535405b087e3076ebf2f"
SEARCH_QUERY_HASH = "6e6b53b10516829d9b7b9fae0cbc9b65bcbbc8792d77836f65b9db6a606057a7"

# Rotate through these to reduce fingerprinting.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# Delay between requests
MIN_DELAY = 1.0
MAX_DELAY = 2.5

# Max items per GraphQL request (batching)
BATCH_SIZE = 10

# Default max results returned by a keyword search
DEFAULT_SEARCH_LIMIT = 24
