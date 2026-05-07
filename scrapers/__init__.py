# scrapers — store-specific product data fetchers.
#
# Each sub-package (walmart, aldi) isolates its own API/page interaction
# and exposes a clean search interface that returns storefront-neutral
# product dicts.  The shared data models live in scrapers.models.
