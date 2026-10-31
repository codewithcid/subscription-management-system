"""Explicit smoke test against a synthetic public demo; creates one test subscription."""
import sys
import uuid
import httpx

if len(sys.argv)!=2: raise SystemExit('Usage: python verify_live.py https://your-demo-host')
with httpx.Client(base_url=sys.argv[1],timeout=120) as c:
    def call(method,path,body=None):
        r=c.request(method,path,json=body)
        if r.status_code>=400: raise RuntimeError(f'{path}: HTTP {r.status_code}: {r.text[:150]}')
        return r.json()
    health=call('GET','/api/health')
    assert health['demo'] and health['storage']=='postgresql',health
    call('POST','/api/login',{'email':'admin@cadence.demo','password':'CadenceDemo!2026'})
    d=call('GET','/api/workspace')
    sid=call('POST','/api/subscriptions',{'customer_id':3,'plan_id':2,'product_id':1,'quantity':2,'lines':[{'product_id':2,'quantity':1}]})['id']
    for action in ('quote','confirm','activate'):call('POST',f'/api/subscriptions/{sid}/transition',{'action':action})
    preview=next(i for i in call('GET','/api/rehearsal?days=0')['items'] if i['subscription_id']==sid)
    call('POST','/api/billing/run',{})
    assert call('POST','/api/billing/run',{})['created']==0
    inv=next(i for i in call('GET','/api/workspace')['invoices'] if i['subscription_id']==sid)
    assert inv['total']==preview['total'] and len(inv['lines'])==2
    call('POST',f'/api/invoices/{inv["id"]}/confirm',{})
    receipt={'amount':inv['total']/100,'method':'UPI','note':'Deployment verification · synthetic payment','idempotency_key':str(uuid.uuid4())}
    call('POST',f'/api/invoices/{inv["id"]}/pay',receipt)
    assert call('POST',f'/api/invoices/{inv["id"]}/pay',receipt)['duplicate']
    call('POST',f'/api/subscriptions/{sid}/transition',{'action':'close'})
    final=next(i for i in call('GET','/api/workspace')['invoices'] if i['id']==inv['id'])
    assert final['status']=='Paid' and final['paid']==final['total']
    call('POST','/api/logout',{})
    call('POST','/api/login',{'email':'olivia@acme.demo','password':'CadenceDemo!2026'})
    own=call('GET','/api/workspace')
    assert all(s['customer_id']==3 for s in own['subscriptions'])
    assert c.post('/api/billing/run',json={}).status_code==403
    print({'storage':health['storage'],'subscription_id':sid,'invoice_id':inv['id'],'status':final['status'],'duplicate_billing_prevented':True,'duplicate_payment_prevented':True,'customer_isolation':True})
