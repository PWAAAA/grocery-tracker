"""Publix scraper constants and configuration."""

# API base URLs
API_BASE = "https://services.publix.com/api"
SAVINGS_ENDPOINT = f"{API_BASE}/v4/savings"
SAVINGS_SEARCH_ENDPOINT = f"{SAVINGS_ENDPOINT}/search"
DEAL_DETAIL_ENDPOINT = f"{SAVINGS_ENDPOINT}/weeklyaddealdetail"
STORE_LOCATOR_ENDPOINT = f"{API_BASE}/v1/storelocation"

# Cookie warmup URL (must visit before API calls to get Akamai cookies)
WARMUP_URL = "https://www.publix.com/savings/weekly-ad"

# Request delays (polite scraping)
MIN_DELAY = 0.5
MAX_DELAY = 1.5

# Retry behavior
MAX_RETRIES = 3
BACKOFF_BASE = 3

# Search/results defaults
DEFAULT_SEARCH_LIMIT = 40
DEFAULT_ZIP = "32801"

# Image sizes for savings API
SMALL_IMAGE_SIZE = 235
LARGE_IMAGE_SIZE = 368

# Savings types
SAVING_TYPE_WEEKLY_AD = "WeeklyAd"
SAVING_TYPE_TPR = "Tpr"
SAVING_TYPE_DIGITAL_COUPON = "DigitalCoupon"

# Headers for API requests
API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.publix.com",
    "Referer": "https://www.publix.com/",
}
