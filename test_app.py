import os
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

temp=tempfile.TemporaryDirectory()
os.environ['CADENCE_DB']=str(Path(temp.name)/'test.db')
from fastapi.testclient import TestClient
import app

class CadenceTests(unittest.TestCase):
    def setUp(self):
        app.DB=str(Path(temp.name)/(self.id().split('.')[-1]+'.db'))
        app.init_db()
        self.admin=self.login('admin@cadence.demo')
    def login(self,email):
        c=TestClient(app.app)
        self.assertEqual(c.post('/api/login',json={'email':email,'password':'CadenceDemo!2026'}).status_code,200)
        return c
    def test_permissions_and_customer_isolation(self):
        anon=TestClient(app.app)
        self.assertEqual(anon.get('/api/workspace').status_code,401)
        c=self.login('olivia@acme.demo'); d=c.get('/api/workspace').json()
        self.assertEqual(len(d['subscriptions']),1)
        self.assertTrue(all(s['customer_id']==3 for s in d['subscriptions']))
        self.assertNotIn('cost',d['products'][0]); self.assertNotIn('customers',d)
        self.assertEqual(c.post('/api/subscriptions/2/transition',json={'action':'pause'}).status_code,404)
        self.assertEqual(c.post('/api/billing/run',json={}).status_code,403)
        self.assertEqual(c.post('/api/catalog/staff',json={}).status_code,403)
        staff=self.login('staff@cadence.demo')
        self.assertEqual(staff.post('/api/catalog/plans',json={}).status_code,403)
    def test_billing_atomic_idempotency_and_preview(self):
        preview=self.admin.get('/api/rehearsal?days=0').json()['items']
        before=len(self.admin.get('/api/workspace').json()['invoices'])
        self.assertEqual(len(self.admin.get('/api/workspace').json()['invoices']),before)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:app.process_billing(),range(2)))
        self.assertEqual(sum(r['created'] for r in results),len(preview))
        self.assertEqual(app.process_billing()['created'],0)
        d=self.admin.get('/api/workspace').json()
        for p in preview:
            i=next(i for i in d['invoices'] if i['subscription_id']==p['subscription_id'] and i['period']==p['period'])
            self.assertEqual(i['total'],p['total']); self.assertEqual(i['status'],'Draft')
        discounted=next(p for p in preview if p['discount'])
        self.assertEqual(discounted['subtotal'],39200)
        self.assertEqual(discounted['discount'],7840)
        self.assertEqual(discounted['tax'],5645)
    def test_subscription_lifecycle_to_payment(self):
        payload={'customer_id':3,'plan_id':2,'product_id':1,'quantity':3,'start_date':app.today().isoformat(),'end_date':(app.today()+app.timedelta(days=365)).isoformat(),'tax_id':1,'payment_terms':14}
        r=self.admin.post('/api/subscriptions',json=payload); self.assertEqual(r.status_code,200); sid=r.json()['id']
        self.assertEqual(self.admin.post(f'/api/subscriptions/{sid}/transition',json={'action':'activate'}).status_code,409)
        for a in ('quote','confirm','activate'):
            self.assertEqual(self.admin.post(f'/api/subscriptions/{sid}/transition',json={'action':a}).status_code,200)
        app.process_billing()
        i=next(i for i in self.admin.get('/api/workspace').json()['invoices'] if i['subscription_id']==sid)
        self.assertEqual(self.admin.post(f'/api/invoices/{i["id"]}/confirm',json={}).status_code,200)
        data={'amount':100,'method':'UPI','date':app.today().isoformat(),'idempotency_key':'payment-test'}
        self.assertEqual(self.admin.post(f'/api/invoices/{i["id"]}/pay',json=data).status_code,200)
        self.assertTrue(self.admin.post(f'/api/invoices/{i["id"]}/pay',json=data).json()['duplicate'])
        data['idempotency_key']='too-much';data['amount']=999999
        self.assertEqual(self.admin.post(f'/api/invoices/{i["id"]}/pay',json=data).status_code,400)
        data['idempotency_key']='remaining';data['amount']=(i['total']-10000)/100
        self.assertEqual(self.admin.post(f'/api/invoices/{i["id"]}/pay',json=data).status_code,200)
        updated=next(v for v in self.admin.get('/api/workspace').json()['invoices'] if v['id']==i['id'])
        self.assertEqual(updated['status'],'Paid');self.assertEqual(updated['paid'],i['total'])
        self.assertEqual(self.admin.post(f'/api/invoices/{i["id"]}/cancel',json={}).status_code,400)
    def test_calendar_anchor(self):
        self.assertEqual(app.advance('2027-01-31','monthly','2027-01-31'),'2027-02-28')
        self.assertEqual(app.advance('2027-02-28','monthly','2027-01-31'),'2027-03-31')
        self.assertEqual(app.advance('2024-02-29','yearly','2024-02-29'),'2025-02-28')
    def test_signup_reset_and_session_revocation(self):
        c=TestClient(app.app)
        b={'name':'Test Person','email':'test@example.com','password':'StrongPass!2026'}
        self.assertEqual(c.post('/api/signup',json={**b,'password':'short'}).status_code,400)
        self.assertEqual(c.post('/api/signup',json=b).status_code,200)
        self.assertEqual(c.post('/api/signup',json={**b,'email':'TEST@example.com'}).status_code,409)
        c.post('/api/login',json=b)
        c.post('/api/reset-request',json={'email':b['email']})
        mail=self.admin.get('/api/workspace').json()['outbox'][0]
        token=mail['body'].split('token=')[1]
        self.assertEqual(c.post('/api/reset',json={'token':token,'password':'NewStrongPass!2026'}).status_code,200)
        self.assertEqual(c.get('/api/workspace').status_code,401)
        self.assertEqual(c.post('/api/reset',json={'token':token,'password':'AnotherPass!2026'}).status_code,400)
    def test_pause_and_discount_exhaustion(self):
        self.admin.post('/api/subscriptions/1/transition',json={'action':'pause'})
        self.assertFalse(any(i['subscription_id']==1 for i in self.admin.get('/api/rehearsal?days=7').json()['items']))
        self.admin.post('/api/subscriptions/1/transition',json={'action':'resume'})
        with app.database() as c:c.execute('UPDATE discounts SET uses=max_uses WHERE id=1')
        i=next(i for i in self.admin.get('/api/rehearsal?days=0').json()['items'] if i['subscription_id']==1)
        self.assertEqual(i['discount'],0);self.assertTrue(i['warning'])
    def test_multiple_lines_and_product_discount(self):
        with app.database() as c: c.execute('UPDATE discounts SET product_id=2 WHERE id=1')
        body={'customer_id':3,'plan_id':1,'product_id':1,'quantity':2,'discount_id':1,'template_id':1,'lines':[{'product_id':2,'quantity':3}]}
        r=self.admin.post('/api/subscriptions',json=body);self.assertEqual(r.status_code,200);sid=r.json()['id']
        for action in ('quote','confirm','activate'): self.admin.post(f'/api/subscriptions/{sid}/transition',json={'action':action})
        preview=next(i for i in self.admin.get('/api/rehearsal?days=0').json()['items'] if i['subscription_id']==sid)
        self.assertEqual(preview['subtotal'],2*4900+3*2900)
        self.assertEqual(preview['discount'],1740)
        app.process_billing()
        invoice=next(i for i in self.admin.get('/api/workspace').json()['invoices'] if i['subscription_id']==sid)
        self.assertEqual(len(invoice['lines']),2)
        self.assertEqual(invoice['total'],preview['total'])
    def test_expired_quote_and_invalid_fields(self):
        sid=self.admin.post('/api/subscriptions',json={'customer_id':3,'plan_id':1,'product_id':1}).json()['id']
        self.admin.post(f'/api/subscriptions/{sid}/transition',json={'action':'quote'})
        with app.database() as c: c.execute('UPDATE subscriptions SET quote_expires=? WHERE id=?',('2020-01-01',sid))
        self.assertEqual(self.admin.post(f'/api/subscriptions/{sid}/transition',json={'action':'confirm'}).status_code,400)
        self.assertEqual(self.admin.post('/api/subscriptions',json={}).status_code,422)
    def test_scheduler_auth_and_cross_origin(self):
        self.assertEqual(self.admin.post('/api/internal/billing').status_code,401)
        os.environ['CADENCE_BILLING_TOKEN']='unit-test-only-token'
        try:
            self.assertEqual(self.admin.post('/api/internal/billing',headers={'Authorization':'Bearer wrong'}).status_code,401)
            self.assertEqual(self.admin.post('/api/internal/billing',headers={'Authorization':'Bearer unit-test-only-token'}).status_code,200)
        finally: del os.environ['CADENCE_BILLING_TOKEN']
        self.assertEqual(self.admin.post('/api/billing/run',headers={'Origin':'https://unrelated.example'},json={}).status_code,403)
    def test_public_demo_restrictions(self):
        previous=app.PUBLIC_DEMO;app.PUBLIC_DEMO=True
        try:
            for route in ('/api/signup','/api/reset-request','/api/reset','/api/catalog/staff'):
                self.assertEqual(self.admin.post(route,json={}).status_code,403)
        finally: app.PUBLIC_DEMO=previous

if __name__=='__main__': unittest.main(verbosity=2)
