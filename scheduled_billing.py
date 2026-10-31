"""External scheduler entry point. Does not collect payments or confirm invoices."""
import os
import time
import httpx

base=os.environ['CADENCE_URL'].rstrip('/')
token=os.environ['CADENCE_BILLING_TOKEN']
if not token: raise SystemExit('Configure CADENCE_BILLING_TOKEN before scheduling billing.')
with httpx.Client(base_url=base,timeout=120) as client:
    for attempt in range(3):
        try:
            health=client.get('/api/health')
            health.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt==2: raise
            time.sleep(10)
    response=client.post('/api/internal/billing',headers={'Authorization':'Bearer '+token})
    response.raise_for_status()
    print('Billing completed:',response.json())
