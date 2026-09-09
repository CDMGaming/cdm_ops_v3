import os, sys, json, datetime, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

EMAIL=os.environ["STERN_EMAIL"]
PASSWORD=os.environ["STERN_PASSWORD"]
STERN_LOCATION_URL=os.getenv("STERN_LOCATION_URL","https://insider.sternpinball.com/pro/locations/33613#")
CDM_API_URL=os.environ["CDM_API_URL"].rstrip("/")
API_KEY=os.environ["SCRAPER_API_KEY"]
RAW_DIR=Path(os.getenv("SCRAPER_RAW_DIR","/data/raw"))
RAW_DIR.mkdir(parents=True,exist_ok=True)

# Trailing-digits-in-the-title parsing below is a guess about Stern's markup and
# WILL misfire if the title format changes. STERN_TITLE_ID_MAP is a known-good
# override: set it in .env as JSON, e.g.
#   STERN_TITLE_ID_MAP={"Dungeons & Dragons":"397061","Star Wars Pro":"279284"}
# and a title that starts with one of those keys always resolves to that ID,
# skipping the guesswork entirely. Add new machines here as you learn their IDs.
TITLE_ID_MAP=json.loads(os.getenv("STERN_TITLE_ID_MAP","{}"))

def resolve_stern_id(title:str) -> str|None:
    for known_title, sid in TITLE_ID_MAP.items():
        if title.strip().lower().startswith(known_title.strip().lower()):
            return sid
    digits="".join(c for c in title if c.isdigit())
    if digits:
        print(f"WARNING: no STERN_TITLE_ID_MAP entry for title '{title}' -- "
              f"guessing ID {digits[-6:]} from digits in the title text. "
              f"Add this machine to STERN_TITLE_ID_MAP to remove the guesswork.",
              file=sys.stderr)
        return digits[-6:]
    return None

def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        page.goto("https://insider.sternpinball.com/login",wait_until="domcontentloaded")
        page.fill('input[name="emailAddress"]',EMAIL)
        page.fill('input[name="password"]',PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
        page.goto(STERN_LOCATION_URL,wait_until="networkidle")
        page.wait_for_selector(".min-w-full",timeout=30000)
        headers=[x.inner_text().strip() for x in page.query_selector_all(".min-w-full thead th")]
        rows=[]
        for tr in page.query_selector_all(".min-w-full tbody tr"):
            cells=[x.inner_text().strip() for x in tr.query_selector_all("td")]
            if cells: rows.append(cells)
        now=datetime.datetime.now(datetime.timezone.utc)
        # Keep raw HTML table locally before normalization.
        raw=RAW_DIR/f"stern_table_{now.strftime('%Y%m%d_%H%M%S')}.json"
        raw.write_text(json.dumps({"captured_at":now.isoformat(),"headers":headers,"rows":rows},indent=2))
        payload=[]
        # Current Stern table has Title/Today/7-days/28-days. Find columns by header name.
        idx={h.lower():i for i,h in enumerate(headers)}
        for r in rows:
            title=r[idx.get("title",1)]
            sid=resolve_stern_id(title)
            if not sid: continue
            today=int(float(r[idx.get("today",2)] or 0))
            seven=int(float(r[idx.get("7-days",3)] or 0))
            twenty8=int(float(r[idx.get("28-days",4)] or 0))
            payload.append({"stern_machine_id":sid,"title":title,"captured_at":now.isoformat(),
                            "today":today,"seven_days":seven,"twenty_eight_days":twenty8})
        resp=requests.post(f"{CDM_API_URL}/api/plays/ingest",json={"rows":payload},
                           headers={"X-API-Key":API_KEY},timeout=30)
        resp.raise_for_status()
        print(resp.json())
        browser.close()

if __name__=="__main__": main()
